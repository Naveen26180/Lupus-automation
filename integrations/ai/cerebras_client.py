"""Cerebras AI client for resume field extraction.

Uses the Cerebras Cloud SDK (OpenAI-compatible) with llama-3.3-70b to
extract structured data from resume text per the two-pass Phase 1 schema.

Why Cerebras?
- Does NOT use your data for model training (free tier)
- Native JSON mode support (response_format=json_object)
- Llama 3.3 70B runs at 1000+ tokens/sec (faster than Groq)
- Free tier, no credit card required
- 128k context window, 8192 max output tokens
"""

import logging
import re

from cerebras.cloud.sdk import Cerebras, APIError, RateLimitError, APIConnectionError

from core.exceptions import AIProviderError
from integrations.ai.base_client import BaseAIClient

logger = logging.getLogger(__name__)

# Best model for structured JSON extraction on Cerebras free tier.
# llama3.3-70b = Llama 3.3 70B — note: Cerebras uses dots in the version number.
_DEFAULT_MODEL = "llama3.3-70b"

# Token-limit error fragments (same detection logic as GroqClient).
_TOKEN_LIMIT_FRAGMENTS = frozenset([
    "413",
    "payload too large",
    "request too large",
    "rate_limit_exceeded",
    "tokens per minute",
    "context window",
    "max_tokens",
])


class CerebrasClient(BaseAIClient):
    """Cerebras-backed AI client for resume extraction.

    Args:
        api_key: Cerebras Cloud API key.
        model: Model identifier. Defaults to llama3.3-70b.
    """

    def __init__(self, api_key: str, model: str = _DEFAULT_MODEL) -> None:
        super().__init__(api_key=api_key, provider_name="cerebras")
        self._client = Cerebras(api_key=api_key)
        self._model = model
        logger.info("CerebrasClient initialized with model '%s'", model)

    @staticmethod
    def _extract_json(text: str) -> str:
        """Pull the first JSON object or array out of a free-text response.

        Safety net in case the model adds wrapper text despite JSON mode.
        """
        text = re.sub(r"```(?:json)?\s*", "", text).strip()
        for start_char, end_char in (("{", "}"), ("[", "]")):
            idx = text.find(start_char)
            if idx != -1:
                rdx = text.rfind(end_char)
                if rdx != -1 and rdx >= idx:
                    return text[idx: rdx + 1]
        return text

    def _call_api(self, prompt: str) -> str:
        """Send the extraction prompt to Cerebras and return the raw response.

        Args:
            prompt: Complete prompt with resume text embedded.

        Returns:
            Raw text content from the Cerebras completion (JSON string).

        Raises:
            AIProviderError: On rate limit, connection, or API errors.
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
                temperature=0.0,   # deterministic extraction
                max_tokens=8192,   # 128k context; 8192 covers any real resume
                response_format={"type": "json_object"},  # native JSON mode
            )

            content = response.choices[0].message.content
            if not content:
                raise AIProviderError("cerebras", "Empty response from model")

            content = self._extract_json(content)
            logger.debug("Cerebras raw response length: %d chars", len(content))
            return content

        except RateLimitError as exc:
            raise AIProviderError(
                "cerebras", f"Rate limit exceeded: {exc}"
            ) from exc
        except APIConnectionError as exc:
            raise AIProviderError(
                "cerebras", f"Connection error: {exc}"
            ) from exc
        except APIError as exc:
            raise AIProviderError(
                "cerebras", f"API error (status {exc.status_code}): {exc}"
            ) from exc
        except AIProviderError:
            raise
        except Exception as exc:
            raise AIProviderError(
                "cerebras", f"Unexpected error: {exc}"
            ) from exc

    def _is_token_limit_error(self, exc: AIProviderError) -> bool:
        """Detect Cerebras token-budget / payload-size errors."""
        msg = str(exc).lower()
        return any(frag in msg for frag in _TOKEN_LIMIT_FRAGMENTS)
