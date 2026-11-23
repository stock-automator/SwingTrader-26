"""Send a file to the configured Telegram chat.

Usage: python3 scripts/send_telegram_file.py <path> [caption]

Uses `sendVideo` for video files up to 50 MB, otherwise `sendDocument`.
Credentials come from the app Settings (.env); they are never printed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.app.config import get_settings  # noqa: E402

MAX_VIDEO_BYTES = 50 * 1024 * 1024
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}


def _scrub(text: str, token: str) -> str:
    return text.replace(token, "***")


def send_file(path: Path, caption: str = "") -> bool:
    settings = get_settings()
    token, chat_id = settings.telegram_bot_token, settings.telegram_chat_id
    if not token or not chat_id:
        print("Telegram is not configured (token/chat id missing).")
        return False
    as_video = (
        path.suffix.lower() in VIDEO_SUFFIXES and path.stat().st_size <= MAX_VIDEO_BYTES
    )
    method, field = ("sendVideo", "video") if as_video else ("sendDocument", "document")
    data = {"chat_id": chat_id, "caption": caption}
    if as_video:
        data["supports_streaming"] = "true"
    try:
        with path.open("rb") as fh:
            resp = requests.post(
                f"https://api.telegram.org/bot{token}/{method}",
                data=data,
                files={field: fh},
                timeout=300,
            )
        ok = bool(resp.json().get("ok"))
    except (requests.RequestException, ValueError) as exc:
        print(f"Telegram error: {_scrub(str(exc), token)}")
        return False
    print(f"{method} ok={ok}")
    return ok


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print(f"File not found: {path}")
        return 2
    caption = sys.argv[2] if len(sys.argv) > 2 else ""
    return 0 if send_file(path, caption) else 1


if __name__ == "__main__":
    raise SystemExit(main())
