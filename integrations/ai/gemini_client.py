"""Gemini AI client for resume field extraction.

Uses the Google Generative AI SDK with Gemini Flash to extract
structured data from resume text per the Phase 1 schema.
"""

import json
import logging

import google.generativeai as genai

from core.exceptions import AIProviderError
from integrations.ai.base_client import BaseAIClient

logger = logging.getLogger(__name__)

# Default model — Gemini 2.0 Flash (free tier)
_DEFAULT_MODEL = "gemini-2.0-flash"


class GeminiClient(BaseAIClient):
    """Gemini-backed AI client for resume extraction.

    Args:
        api_key: Google AI (Gemini) API key.
        model: Model identifier. Defaults to Gemini 2.0 Flash.
    """

    def __init__(self, api_key: str, model: str = _DEFAULT_MODEL) -> None:
        super().__init__(api_key=api_key, provider_name="gemini")
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(
            model_name=model,
            generation_config={
                "temperature": 0.0,
                "max_output_tokens": 2048,
                "response_mime_type": "application/json",
            },
        )
        logger.info("GeminiClient initialized with model '%s'", model)

    def _call_api(self, prompt: str) -> str:
        """Send the extraction prompt to Gemini and return the raw response.

        Args:
            prompt: Complete prompt with resume text embedded.

        Returns:
            Raw text content from the Gemini response.

        Raises:
            AIProviderError: On API or network errors.
        """
        try:
            response = self._model.generate_content(prompt)

            if not response.parts:
                # Safety filter or empty response
                block_reason = getattr(
                    response.prompt_feedback, "block_reason", "unknown"
                )
                raise AIProviderError(
                    "gemini",
                    f"Empty response — possible safety block: {block_reason}",
                )

            content = response.text
            if not content:
                raise AIProviderError("gemini", "Empty text in response")

            logger.debug("Gemini raw response length: %d chars", len(content))
            return content

        except AIProviderError:
            raise  # don't re-wrap our own errors
        except Exception as exc:
            raise AIProviderError(
                "gemini", f"API call failed: {exc}"
            ) from exc
