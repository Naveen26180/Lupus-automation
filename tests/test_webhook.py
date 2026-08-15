"""Tests for the serverless Telegram webhook (api/webhook.py).

All Telegram API and pipeline interactions are mocked — nothing touches
the network or real services.
"""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import api.webhook as wh  # noqa: E402
from core.pipeline import PipelineResult  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    """Isolate module state and env between tests."""
    wh._pipeline = None
    wh._seen_update_ids.clear()
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setenv("GOOGLE_DRIVE_CREDENTIALS", "")
    monkeypatch.delenv("GOOGLE_DRIVE_CREDENTIALS_JSON", raising=False)
    yield


class FakePipeline:
    """Stand-in for core.pipeline.Pipeline with a configurable result."""

    def __init__(self, result: PipelineResult | None = None, exc: Exception | None = None):
        self.result = result or PipelineResult(success=True, message="Extraction complete ✔")
        self.exc = exc
        self.calls = []

    def process(self, resume_file_path, recruiter_metadata, source):
        self.calls.append(
            {
                "path": str(resume_file_path),
                "user_id": recruiter_metadata.user_id,
                "username": recruiter_metadata.username,
                "source": source,
            }
        )
        if self.exc:
            raise self.exc
        return self.result


@pytest.fixture
def fake_pipeline(monkeypatch):
    pipeline = FakePipeline()
    monkeypatch.setattr(wh, "get_pipeline", lambda: pipeline)
    return pipeline


def _update(
    update_id: int = 1,
    *,
    text: str | None = None,
    document: dict | None = None,
    message_id: int = 100,
) -> dict:
    msg = {"message_id": message_id, "chat": {"id": 424242}}
    if text is not None:
        msg["text"] = text
    if document is not None:
        msg["document"] = document
    msg["from"] = {"id": 777, "username": "recruiter"}
    return {"update_id": update_id, "message": msg}


def _document(filename: str = "resume.pdf", file_id: str = "FILE123") -> dict:
    return {"file_name": filename, "file_id": file_id}


def _send_ok(**overrides) -> dict:
    payload = {"message_id": 200, "chat": {"id": 424242}}
    payload.update(overrides)
    return {"ok": True, "result": payload}


# ---------------------------------------------------------------------------
# Commands and prompts
# ---------------------------------------------------------------------------
class TestCommands:
    def test_start_sends_welcome(self, monkeypatch):
        calls = []
        monkeypatch.setattr(wh, "send_message", lambda token, chat, text, **kw: calls.append((text, kw)))
        wh.process_update(_update(text="/start"))
        assert len(calls) == 1
        assert calls[0][0].startswith("👋 Welcome")
        assert calls[0][1]["reply_to_message_id"] == 100

    def test_help_sends_help(self, monkeypatch):
        calls = []
        monkeypatch.setattr(wh, "send_message", lambda token, chat, text, **kw: calls.append((text, kw)))
        wh.process_update(_update(text="/help"))
        assert len(calls) == 1
        assert calls[0][0].startswith("📋 *Resume Bot Help*")
        assert calls[0][1]["parse_mode"] == "Markdown"

    def test_plain_text_prompts_for_file(self, monkeypatch):
        calls = []
        monkeypatch.setattr(wh, "send_message", lambda token, chat, text, **kw: calls.append(text))
        wh.process_update(_update(text="hello there"))
        assert calls == ["📄 Please send a resume file (.pdf or .docx).\nType /help for more information."]

    def test_update_without_message_is_ignored(self, monkeypatch):
        called = []
        monkeypatch.setattr(wh, "send_message", lambda *a, **k: called.append(a))
        wh.process_update({"update_id": 9})
        assert called == []


# ---------------------------------------------------------------------------
# Document intake
# ---------------------------------------------------------------------------
class TestDocumentFlow:
    def test_happy_path_processes_and_replies(self, fake_pipeline, monkeypatch):
        sent, edited = [], []
        monkeypatch.setattr(wh, "send_message", lambda token, chat, text, **kw: (sent.append(text), _send_ok())[1])
        monkeypatch.setattr(wh, "edit_message", lambda token, chat, mid, text: edited.append((mid, text)))
        monkeypatch.setattr(wh, "telegram_api", lambda token, method, payload=None: {"result": {"file_path": "docs/resume.pdf"}})
        monkeypatch.setattr(wh, "download_file", lambda token, path, dest: dest.write_bytes(b"%PDF-fake"))

        wh.process_update(_update(document=_document()))

        assert sent == ["⏳ Processing *resume.pdf*..."]
        assert edited == [(200, "Extraction complete ✔")]
        assert len(fake_pipeline.calls) == 1
        call = fake_pipeline.calls[0]
        assert call["path"].endswith("resume.pdf")
        assert call["user_id"] == "777"
        assert call["username"] == "recruiter"
        assert call["source"] == "telegram-webhook"

    def test_pipeline_error_replies_with_failure(self, monkeypatch):
        fake_pipeline = FakePipeline(exc=RuntimeError("boom"))
        monkeypatch.setattr(wh, "get_pipeline", lambda: fake_pipeline)
        edited = []
        monkeypatch.setattr(wh, "send_message", lambda *a, **k: _send_ok())
        monkeypatch.setattr(wh, "edit_message", lambda token, chat, mid, text: edited.append(text))
        monkeypatch.setattr(wh, "telegram_api", lambda *a, **k: {"result": {"file_path": "x.pdf"}})
        monkeypatch.setattr(wh, "download_file", lambda token, path, dest: dest.write_bytes(b"%PDF-fake"))

        wh.process_update(_update(document=_document()))
        assert edited == ["❌ An unexpected error occurred — please try again later."]

    def test_missing_file_path_errors_gracefully(self, monkeypatch):
        edited = []
        monkeypatch.setattr(wh, "send_message", lambda *a, **k: _send_ok())
        monkeypatch.setattr(wh, "edit_message", lambda token, chat, mid, text: edited.append(text))
        monkeypatch.setattr(wh, "telegram_api", lambda *a, **k: {"result": {}})

        wh.process_update(_update(document=_document()))
        assert edited and "error" in edited[0].lower()

    def test_download_failure_after_retries_errors(self, monkeypatch):
        edited = []
        monkeypatch.setattr(wh, "send_message", lambda *a, **k: _send_ok())
        monkeypatch.setattr(wh, "edit_message", lambda token, chat, mid, text: edited.append(text))
        monkeypatch.setattr(wh, "telegram_api", lambda *a, **k: {"result": {"file_path": "x.pdf"}})
        monkeypatch.setattr(wh, "download_file", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no net")))

        wh.process_update(_update(document=_document()))
        assert edited and "error" in edited[0].lower()

    def test_download_retries_then_succeeds(self, fake_pipeline, monkeypatch):
        """Exercise the real retry loop inside download_file via mocked HTTP."""
        monkeypatch.setattr(wh, "send_message", lambda *a, **k: _send_ok())
        monkeypatch.setattr(wh, "edit_message", lambda *a, **k: None)
        monkeypatch.setattr(wh, "telegram_api", lambda *a, **k: {"result": {"file_path": "x.pdf"}})
        attempts = {"n": 0}

        class _Resp:
            def __init__(self, ok: bool):
                self.ok = ok
                self.content = b""

            def raise_for_status(self):
                if not self.ok:
                    raise RuntimeError("transient")

        def flaky_get(url, timeout=60):
            attempts["n"] += 1
            if attempts["n"] < 3:
                return _Resp(ok=False)
            resp = _Resp(ok=True)
            resp.content = b"%PDF-fake"
            return resp

        monkeypatch.setattr(wh.requests, "get", flaky_get)
        wh.process_update(_update(document=_document()))
        assert attempts["n"] == 3
        assert len(fake_pipeline.calls) == 1

    def test_temp_file_is_cleaned_up(self, monkeypatch, tmp_path):
        fake_pipeline = FakePipeline()
        monkeypatch.setattr(wh, "get_pipeline", lambda: fake_pipeline)
        monkeypatch.setattr(wh, "send_message", lambda *a, **k: _send_ok())
        monkeypatch.setattr(wh, "edit_message", lambda *a, **k: None)
        monkeypatch.setattr(wh, "telegram_api", lambda *a, **k: {"result": {"file_path": "x.pdf"}})
        monkeypatch.setattr(wh.tempfile, "mkdtemp", lambda prefix="": str(tmp_path / "sub"))
        monkeypatch.setattr(wh, "download_file", lambda token, path, dest: dest.write_bytes(b"%PDF-fake"))
        (tmp_path / "sub").mkdir(exist_ok=True)

        wh.process_update(_update(document=_document()))
        assert not (tmp_path / "sub" / "resume.pdf").exists()


# ---------------------------------------------------------------------------
# Duplicate / retry protection
# ---------------------------------------------------------------------------
class TestDedup:
    def test_same_update_id_processed_once(self, fake_pipeline, monkeypatch):
        monkeypatch.setattr(wh, "send_message", lambda *a, **k: _send_ok())
        monkeypatch.setattr(wh, "edit_message", lambda *a, **k: None)
        monkeypatch.setattr(wh, "telegram_api", lambda *a, **k: {"result": {"file_path": "x.pdf"}})
        monkeypatch.setattr(wh, "download_file", lambda token, path, dest: dest.write_bytes(b"%PDF-fake"))

        update = _update(update_id=42, document=_document())
        wh.process_update(update)
        wh.process_update(update)  # Telegram retry with the same update_id
        assert len(fake_pipeline.calls) == 1


# ---------------------------------------------------------------------------
# Credentials materialization (ephemeral disk)
# ---------------------------------------------------------------------------
class TestCredentialsFile:
    def test_writes_json_env_var_to_temp_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_DRIVE_CREDENTIALS", "")
        monkeypatch.setenv("GOOGLE_DRIVE_CREDENTIALS_JSON", json.dumps({"type": "service_account"}))
        monkeypatch.setattr(wh.tempfile, "gettempdir", lambda: str(tmp_path))

        wh._ensure_credentials_file()

        written = tmp_path / "lupus-credentials.json"
        assert written.exists()
        assert json.loads(written.read_text(encoding="utf-8")) == {"type": "service_account"}
        assert os.getenv("GOOGLE_DRIVE_CREDENTIALS") == str(written)

    def test_existing_path_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("GOOGLE_DRIVE_CREDENTIALS", "/some/real/path.json")
        monkeypatch.setenv("GOOGLE_DRIVE_CREDENTIALS_JSON", "{}")
        monkeypatch.setattr(wh.tempfile, "gettempdir", lambda: str(tmp_path))

        wh._ensure_credentials_file()
        assert os.getenv("GOOGLE_DRIVE_CREDENTIALS") == "/some/real/path.json"
        assert not (tmp_path / "lupus-credentials.json").exists()

    def test_no_credentials_is_noop(self):
        wh._ensure_credentials_file()  # should not raise
        assert os.getenv("GOOGLE_DRIVE_CREDENTIALS", "") == ""


# ---------------------------------------------------------------------------
# Endpoint behavior
# ---------------------------------------------------------------------------
class TestEndpoint:
    def test_endpoint_returns_ok_and_processes(self, fake_pipeline, monkeypatch):
        monkeypatch.setattr(wh, "send_message", lambda *a, **k: _send_ok())
        monkeypatch.setattr(wh, "edit_message", lambda *a, **k: None)
        monkeypatch.setattr(wh, "telegram_api", lambda *a, **k: {"result": {"file_path": "x.pdf"}})
        monkeypatch.setattr(wh, "download_file", lambda token, path, dest: dest.write_bytes(b"%PDF-fake"))

        update = _update(document=_document())
        body = json.dumps(update)

        class FakeRequest:
            def json(self):
                return json.loads(body)

        assert wh.webhook(FakeRequest()) == {"ok": True}
        assert len(fake_pipeline.calls) == 1

    def test_endpoint_tolerates_invalid_payload(self):
        class BadRequest:
            def json(self):
                raise ValueError("not json")

        assert wh.webhook(BadRequest()) == {"ok": True}

    def test_handler_is_mangum_instance(self):
        assert wh.handler.__class__.__name__ == "Mangum"
