"""Groq AI client for resume field extraction.

Uses the Groq SDK with llama-3.3-70b-versatile to extract structured
data from resume text per the two-pass Phase 1 schema.

Model choice rationale
----------------------
llama-3.3-70b-versatile is a standard instruction-tuned model (NOT a
reasoning model) that fully supports response_format={"type":"json_object"}.
We previously used openai/gpt-oss-20b but that is a reasoning model whose
<think> token injection and incompatibility with JSON mode caused persistent
400 json_validate_failed errors even with reasoning_format="hidden".
"""

import logging
import re

from groq import Groq, APIError, RateLimitError, APIConnectionError

from core.exceptions import AIProviderError
from integrations.ai.base_client import BaseAIClient

logger = logging.getLogger(__name__)

# Default model — Llama 3.3 70B Versatile (non-reasoning, native JSON mode).
# Free tier, 128k context, 32k max output.
_DEFAULT_MODEL = "llama-3.3-70b-versatile"

# Fallback chain — tried in order if a model returns 404 (not on this account).
# openai/gpt-oss-20b is a reasoning model and needs different API params.
_REASONING_MODELS = frozenset(["openai/gpt-oss-20b", "openai/gpt-oss-120b"])

_MODEL_FALLBACK_CHAIN = [
    "llama-3.3-70b-versatile",           # best: non-reasoning, native JSON mode
    "openai/gpt-oss-20b",                # fallback: reasoning model (confirmed available)
    "meta-llama/llama-4-scout-17b-16e-instruct",  # fallback: Llama 4 Scout
]

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
                temperature=0.0,          # deterministic extraction
                max_tokens=8192,          # 128k context / 32k output; 8192 covers any real resume
                response_format={"type": "json_object"},  # enforced — works on non-reasoning models
            )

            content = response.choices[0].message.content
            if not content:
                raise AIProviderError("groq", "Empty response from model")

            # _extract_json is a safety net in case the model adds wrapper text
            # despite JSON mode — rare but possible on older SDK versions.
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
