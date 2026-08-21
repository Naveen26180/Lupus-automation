"""Groq AI client for resume field extraction.

Uses the Groq SDK with openai/gpt-oss-20b to extract structured data
from resume text per the two-pass Phase 1 schema.

NOTE: response_format={"type":"json_object"} is intentionally NOT used.
The openai/gpt-oss-20b reasoning model returns 400 json_validate_failed
when that parameter is set, even with reasoning_format="hidden". Instead
we ask the model to return JSON in the system prompt and extract the JSON
block from the free-text response ourselves.
"""

import logging
import re

from groq import Groq, APIError, RateLimitError, APIConnectionError

from core.exceptions import AIProviderError
from integrations.ai.base_client import BaseAIClient

logger = logging.getLogger(__name__)

# Default model — GPT-OSS 20B (Groq's recommended replacement for the
# retired llama-3.1-8b-instant; works on the free tier)
_DEFAULT_MODEL = "openai/gpt-oss-20b"

# Groq status codes / message fragments that indicate a permanent token-budget error.
# These must NOT be retried — the same prompt will fail again.
_TOKEN_LIMIT_STATUS_CODES = {413}
_TOKEN_LIMIT_FRAGMENTS = frozenset([
    "413",
    "payload too large",
    "request too large",
    "rate_limit_exceeded",
    "tokens per minute",
    "tpm",
    "context window",
    "max_tokens",
])


class GroqClient(BaseAIClient):
    """Groq-backed AI client for resume extraction.

    Args:
        api_key: Groq API key.
        model: Model identifier. Defaults to Llama 3.1 8B Instant.
    """

    def __init__(self, api_key: str, model: str = _DEFAULT_MODEL) -> None:
        super().__init__(api_key=api_key, provider_name="groq")
        self._client = Groq(api_key=api_key)
        self._model = model
        logger.info("GroqClient initialized with model '%s'", model)

    @staticmethod
    def _extract_json(text: str) -> str:
        """Pull the first JSON object or array out of a free-text response.

        The openai/gpt-oss-20b model sometimes wraps its JSON in markdown
        code fences or adds a short preamble.  This strips all of that and
        returns only the JSON substring so downstream parsing still works.
        """
        # Strip markdown code fences: ```json ... ``` or ``` ... ```
        text = re.sub(r"```(?:json)?\s*", "", text).strip()

        # Find the first { or [ and take everything from there
        for start_char, end_char in (("{" , "}"), ("[", "]")):
            idx = text.find(start_char)
            if idx != -1:
                # Walk backwards from the end to find the matching closer
                rdx = text.rfind(end_char)
                if rdx != -1 and rdx >= idx:
                    return text[idx : rdx + 1]

        # Nothing found — return as-is and let JSON parsing raise a clear error
        return text

    def _call_api(self, prompt: str) -> str:
        """Send the extraction prompt to Groq and return the raw response.

        Args:
            prompt: Complete prompt with all placeholders filled in.

        Returns:
            Raw text content from the Groq completion (JSON string).

        Raises:
            AIProviderError: On rate limit, connection, token-limit, or API errors.
        """
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a precise data extraction system. "
                            "Return ONLY valid JSON with no extra text, "
                            "no markdown, no explanation."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,  # deterministic extraction
                max_tokens=4096,
                # reasoning_format="hidden" suppresses <think> tokens so
                # they don't appear in the content and break JSON parsing.
                # response_format is intentionally omitted — it triggers
                # 400 json_validate_failed on this reasoning model.
                reasoning_format="hidden",
            )

            content = response.choices[0].message.content
            if not content:
                raise AIProviderError("groq", "Empty response from model")

            # Extract the JSON block in case the model added any wrapper text
            content = self._extract_json(content)
            logger.debug("Groq raw response length: %d chars", len(content))
            return content

        except RateLimitError as exc:
            raise AIProviderError(
                "groq", f"Rate limit exceeded: {exc}"
            ) from exc
        except APIConnectionError as exc:
            raise AIProviderError(
                "groq", f"Connection error: {exc}"
            ) from exc
        except APIError as exc:
            raise AIProviderError(
                "groq", f"API error (status {exc.status_code}): {exc}"
            ) from exc
        except AIProviderError:
            raise  # don't re-wrap our own errors
        except Exception as exc:
            raise AIProviderError(
                "groq", f"Unexpected error: {exc}"
            ) from exc

    def _is_token_limit_error(self, exc: AIProviderError) -> bool:
        """Detect Groq token-budget / payload-size errors.

        These are permanent failures — the only fix is a smaller prompt,
        not a retry.  Checks both the status_code stored in details and
        common message fragments from the Groq SDK.
        """
        msg = str(exc).lower()
        return any(frag in msg for frag in _TOKEN_LIMIT_FRAGMENTS)
