"""Shared pairing token store — JSON file-based for cross-process (bot + web) access.

Bot and web server run as separate processes, so in-memory dicts won't work.
This module uses data/pairing_tokens.json with atomic writes.
"""

import os
import json
import secrets
import tempfile
import time
from datetime import datetime, timedelta
from typing import Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
PAIRING_FILE = os.path.join(DATA_DIR, "pairing_tokens.json")

# Token expiry: 5 minutes
TOKEN_EXPIRY_SECONDS = 300
# Cleanup threshold: 10 minutes
CLEANUP_THRESHOLD_SECONDS = 600


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _load() -> dict:
    """Load pairing tokens from JSON file."""
    _ensure_dir()
    if os.path.exists(PAIRING_FILE):
        try:
            with open(PAIRING_FILE, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"tokens": {}}


def _save(data: dict):
    """Atomic write to prevent corruption."""
    _ensure_dir()
    fd, tmp_path = tempfile.mkstemp(dir=DATA_DIR, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp_path, PAIRING_FILE)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def create_pairing_token() -> str:
    """Generate a new pairing token and store it. Returns the token string."""
    token = secrets.token_urlsafe(16)
    data = _load()
    data["tokens"][token] = {
        "status": "pending",
        "chat_id": None,
        "created_at": datetime.now().isoformat(),
        "consumed": False,
    }
    _save(data)
    return token


def is_token_valid(token: str) -> bool:
    """Check if token exists, is pending, not consumed, and not expired."""
    data = _load()
    entry = data["tokens"].get(token)
    if not entry:
        return False
    if entry.get("consumed") or entry.get("status") != "pending":
        return False
    try:
        created = datetime.fromisoformat(entry["created_at"])
        if datetime.now() - created > timedelta(seconds=TOKEN_EXPIRY_SECONDS):
            return False
    except (ValueError, KeyError):
        return False
    return True


def consume_token(token: str, chat_id: int) -> bool:
    """Mark a pairing token as linked to a chat_id. Returns True on success."""
    data = _load()
    entry = data["tokens"].get(token)
    if not entry:
        return False
    if entry.get("consumed") or entry.get("status") != "pending":
        return False
    try:
        created = datetime.fromisoformat(entry["created_at"])
        if datetime.now() - created > timedelta(seconds=TOKEN_EXPIRY_SECONDS):
            return False
    except (ValueError, KeyError):
        return False

    entry["status"] = "linked"
    entry["chat_id"] = chat_id
    entry["consumed"] = True
    entry["linked_at"] = datetime.now().isoformat()
    _save(data)
    return True


def get_token_status(token: str) -> Optional[dict]:
    """Return token entry status dict, or None if not found."""
    data = _load()
    entry = data["tokens"].get(token)
    if not entry:
        return None
    return {
        "status": entry.get("status", "unknown"),
        "chat_id": entry.get("chat_id"),
        "created_at": entry.get("created_at"),
        "consumed": entry.get("consumed", False),
    }


def cleanup_expired():
    """Remove tokens older than CLEANUP_THRESHOLD_SECONDS."""
    data = _load()
    now = datetime.now()
    to_remove = []
    for token, entry in data["tokens"].items():
        try:
            created = datetime.fromisoformat(entry["created_at"])
            if (now - created).total_seconds() > CLEANUP_THRESHOLD_SECONDS:
                to_remove.append(token)
        except (ValueError, KeyError):
            to_remove.append(token)
    for token in to_remove:
        del data["tokens"][token]
    if to_remove:
        _save(data)
