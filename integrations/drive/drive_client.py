"""Google Drive integration for resume file management.

Handles upload, move, and organize operations for the four Drive
folders: Incoming, Processed, Duplicates, Rejected.
"""

import logging
import time
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError

from core.exceptions import DriveError

logger = logging.getLogger(__name__)

# Required OAuth scopes for Drive operations
_SCOPES = ["https://www.googleapis.com/auth/drive"]

# Retry config (per master prompt: 3 retries for Drive)
_MAX_RETRIES = 3
_RETRY_DELAY_SECONDS = 2


class DriveClient:
    """Google Drive client for resume file operations.

    Args:
        credentials_path: Path to the service account JSON credentials file.
        incoming_folder_id: Drive folder ID for incoming resumes.
        processed_folder_id: Drive folder ID for successfully processed resumes.
        duplicate_folder_id: Drive folder ID for duplicate candidates.
        rejected_folder_id: Drive folder ID for invalid/rejected files.
    """

    def __init__(
        self,
        credentials_path: str,
        incoming_folder_id: str,
        processed_folder_id: str,
        duplicate_folder_id: str,
        rejected_folder_id: str,
    ) -> None:
        self._folder_ids = {
            "incoming": incoming_folder_id,
            "processed": processed_folder_id,
            "duplicates": duplicate_folder_id,
            "rejected": rejected_folder_id,
        }

        try:
            creds = service_account.Credentials.from_service_account_file(
                credentials_path, scopes=_SCOPES
            )
            self._service = build("drive", "v3", credentials=creds)
            logger.info("DriveClient initialized successfully")
        except Exception as exc:
            raise DriveError("init", f"Failed to initialize: {exc}") from exc

    def upload_file(
        self, file_path: Path, folder: str = "incoming"
    ) -> str:
        """Upload a file via the Apps Script Webhook."""
        import requests
        import base64
        import os
        from config.settings import load_settings

        apps_script_url = os.getenv("APPS_SCRIPT_URL")
        
        folder_id = self._folder_ids.get(folder)
        if not folder_id:
            raise DriveError("upload", f"Unknown folder: '{folder}'")

        if not apps_script_url:
            # Fall back to standard Service Account approach
            return self._upload_file_service_account(file_path, folder_id)

        filename = file_path.name
        mime_type = self._get_mime_type(file_path)

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                logger.info(
                    "Uploading '%s' via Apps Script to Drive/%s (attempt %d/%d)",
                    filename,
                    folder,
                    attempt,
                    _MAX_RETRIES,
                )
                
                with open(file_path, "rb") as f:
                    file_data = base64.b64encode(f.read()).decode("utf-8")

                payload = {
                    "action": "upload",
                    "folderId": folder_id,
                    "filename": filename,
                    "mimeType": mime_type,
                    "fileData": file_data
                }

                response = requests.post(apps_script_url, data=payload)
                response.raise_for_status()
                
                res_json = response.json()
                if not res_json.get("success"):
                    raise DriveError("upload", f"Apps Script error: {res_json.get('error')}")

                file_id = res_json.get("fileId")
                logger.info(
                    "Upload successful: '%s' → Drive ID %s", filename, file_id
                )
                return file_id

            except Exception as exc:
                if attempt < _MAX_RETRIES:
                    logger.warning("Upload attempt %d failed, retrying...", attempt)
                    time.sleep(_RETRY_DELAY_SECONDS * attempt)
                else:
                    raise DriveError("upload", str(exc)) from exc

        raise DriveError("upload", f"All {_MAX_RETRIES} attempts failed")

    def _upload_file_service_account(self, file_path: Path, folder_id: str) -> str:
        """Legacy 0GB quota service account upload"""
        filename = file_path.name
        mime_type = self._get_mime_type(file_path)

        file_metadata = {
            "name": filename,
            "parents": [folder_id],
        }
        media = MediaFileUpload(str(file_path), mimetype=mime_type)

        result = (
            self._service.files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id",
                supportsAllDrives=True,
            )
            .execute()
        )
        return result.get("id")

    def move_file(self, file_id: str, target_folder: str) -> None:
        """Move a file to target folder via Apps Script Webhook."""
        import requests
        import os

        apps_script_url = os.getenv("APPS_SCRIPT_URL")
        target_folder_id = self._folder_ids.get(target_folder)
        
        if not target_folder_id:
            raise DriveError("move", f"Unknown folder: '{target_folder}'")

        if not apps_script_url:
            return self._move_file_service_account(file_id, target_folder_id)

        try:
            logger.info("Moving file %s to '%s' folder via Apps Script", file_id, target_folder)
            payload = {
                "action": "move",
                "fileId": file_id,
                "targetFolderId": target_folder_id
            }
            response = requests.post(apps_script_url, data=payload)
            response.raise_for_status()
            
            res_json = response.json()
            if not res_json.get("success"):
                raise DriveError("move", f"Apps Script error: {res_json.get('error')}")

        except Exception as exc:
            raise DriveError("move", str(exc)) from exc

    def _move_file_service_account(self, file_id: str, target_folder_id: str) -> None:
        """Legacy 0GB quota service account move"""
        try:
            file_info = (
                self._service.files()
                .get(fileId=file_id, fields="parents", supportsAllDrives=True)
                .execute()
            )
            current_parents = ",".join(file_info.get("parents", []))

            self._service.files().update(
                fileId=file_id,
                addParents=target_folder_id,
                removeParents=current_parents,
                fields="id, parents",
                supportsAllDrives=True,
            ).execute()
        except HttpError as exc:
            raise DriveError("move", f"HTTP {exc.resp.status}: {exc}") from exc
        except Exception as exc:
            raise DriveError("move", str(exc)) from exc

    def get_file_link(self, file_id: str) -> str:
        """Generate a web view link for a Drive file.

        Args:
            file_id: Drive file ID.

        Returns:
            A URL to view the file in Google Drive.
        """
        return f"https://drive.google.com/file/d/{file_id}/view"

    @staticmethod
    def _get_mime_type(file_path: Path) -> str:
        """Determine MIME type from file extension.

        Args:
            file_path: Path to the file.

        Returns:
            Appropriate MIME type string.
        """
        suffix = file_path.suffix.lower()
        mime_map = {
            ".pdf": "application/pdf",
            ".docx": (
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
        }
        return mime_map.get(suffix, "application/octet-stream")
