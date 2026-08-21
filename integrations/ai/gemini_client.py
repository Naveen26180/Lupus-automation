"""Gemini AI client for resume field extraction and enrichment helpers.

Uses the Google Generative AI SDK with Gemini Flash to extract
structured data from resume text per the Phase 1 schema.

This is the SOLE AI provider in the project — all resume extraction,
SaaS classification, and company profiling go through Gemini.
"""

import json
import logging
import os

import google.generativeai as genai

from core.exceptions import AIProviderError
from integrations.ai.base_client import BaseAIClient

logger = logging.getLogger(__name__)

# Default model — Gemini Flash.
# gemini-2.5-flash is no longer available to new API keys; Google directs
# new users to gemini-3.6-flash. Override with the GEMINI_MODEL env var.
_DEFAULT_MODEL = "gemini-3.6-flash"

# Shared generation config for resume extraction (Pass 1 / Pass 2).
_GENERATION_CONFIG = {
    "temperature": 0,
    "max_output_tokens": 8192,  # model supports much more; 8192 covers any real resume
    "response_mime_type": "application/json",
}


class GeminiClient(BaseAIClient):
    """Gemini-backed AI client for resume extraction.

    Args:
        api_key: Google AI (Gemini) API key.
        model: Model identifier. Defaults to Gemini 2.5 Flash.
    """

    def __init__(self, api_key: str, model: str = _DEFAULT_MODEL) -> None:
        super().__init__(api_key=api_key, provider_name="gemini")
        genai.configure(api_key=api_key)
        self._model = genai.GenerativeModel(
            model_name=model,
            generation_config=dict(_GENERATION_CONFIG),
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


def generate_text(prompt: str, *, max_output_tokens: int = 1024, json_mode: bool = False) -> str:
    """One-shot Gemini call for enrichment/classification helpers.

    Centralizes the Gemini configuration so every non-resume AI call in the
    project uses the same provider, model, and temperature.

    Args:
        prompt: Complete prompt.
        max_output_tokens: Output token cap (default 1024).
        json_mode: If True, force ``response_mime_type="application/json"``.

    Returns:
        Raw text content from the Gemini response.

    Raises:
        ValueError: If GEMINI_API_KEY is not set.
        Exception: Propagates API/network errors so callers can fall back.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")

    model = os.getenv("GEMINI_MODEL", _DEFAULT_MODEL)
    genai.configure(api_key=api_key)

    config = {"temperature": 0, "max_output_tokens": max_output_tokens}
    if json_mode:
        config["response_mime_type"] = "application/json"

    model_obj = genai.GenerativeModel(model_name=model, generation_config=config)
    response = model_obj.generate_content(prompt)
    return response.text or ""
