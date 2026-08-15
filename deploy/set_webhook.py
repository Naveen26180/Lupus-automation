"""Register (or delete) the Telegram webhook for the serverless deployment.

Usage (from the project root):
    python deploy/set_webhook.py https://your-project.vercel.app/
    python deploy/set_webhook.py --delete

Reads BOT_TOKEN from .env (or the environment). Setting a webhook
disables polling for that token until the webhook is deleted.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402


def _bot_api(token: str, method: str, payload: dict | None = None) -> dict:
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/{method}",
        json=payload or {},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def main() -> int:
    load_dotenv(_PROJECT_ROOT / ".env", encoding="utf-8-sig")

    parser = argparse.ArgumentParser(description="Set or delete the Telegram webhook")
    parser.add_argument("url", nargs="?", help="Public HTTPS URL of the deployment")
    parser.add_argument("--delete", action="store_true", help="Delete the webhook (back to polling)")
    args = parser.parse_args()

    token = os.getenv("BOT_TOKEN")
    if not token:
        print("FATAL: BOT_TOKEN not set (check .env or environment)", file=sys.stderr)
        return 1

    if args.delete:
        result = _bot_api(token, "deleteWebhook")
    elif args.url:
        if not args.url.startswith("https://"):
            print("FATAL: webhook URL must be HTTPS (Telegram requirement)", file=sys.stderr)
            return 1
        result = _bot_api(
            token,
            "setWebhook",
            {"url": args.url, "allowed_updates": ["message"]},
        )
    else:
        parser.print_help()
        return 1

    print(result.get("description", json.dumps(result)))
    if not result.get("ok"):
        return 1

    info = _bot_api(token, "getWebhookInfo")
    print("--- Webhook info ---")
    print(json.dumps(info.get("result", {}), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
