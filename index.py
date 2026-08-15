"""Root FastAPI entrypoint for Vercel framework detection.

`main.py` (the polling bot entry point) has no FastAPI `app`, which
confuses Vercel's detection. This module re-exports the webhook app at
a *default* supported entrypoint (`index.py`) as a belt-and-braces
fallback — `pyproject.toml` pins `api.webhook:app` as the primary
entrypoint, and this file exists so detection succeeds either way.
"""

from api.webhook import app  # noqa: F401
