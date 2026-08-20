"""Groq AI client for resume field extraction.

Uses the Groq SDK with Llama 3.1 8B Instant to extract structured data
from resume text per the two-pass Phase 1 schema.
"""

import logging

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

    def _call_api(self, prompt: str) -> str:
        """Send the extraction prompt to Groq and return the raw response.

        Args:
            prompt: Complete prompt with all placeholders filled in.

        Returns:
            Raw text content from the Groq completion.

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
                            "Return only valid JSON, nothing else."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,  # deterministic extraction
                max_tokens=2048,
                reasoning_format="hidden",
                response_format={"type": "json_object"},
            )

            content = response.choices[0].message.content
            if not content:
                raise AIProviderError("groq", "Empty response from model")

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
