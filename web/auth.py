"""Session cookie management for web dashboard."""

import secrets
import time
from typing import Optional

COOKIE_NAME = "spada_session"
SESSION_EXPIRY_SECONDS = 7 * 24 * 3600  # 7 days

_sessions: dict[str, dict] = {}


def create_session(chat_id: int) -> str:
    """Generate a new session token tied to a chat_id."""
    token = secrets.token_urlsafe(32)
    _sessions[token] = {
        "chat_id": chat_id,
        "created_at": time.time(),
    }
    return token


def verify_session(session_token: str) -> Optional[int]:
    """Return chat_id if session is valid and not expired, else None."""
    entry = _sessions.get(session_token)
    if not entry:
        return None
    if time.time() - entry["created_at"] > SESSION_EXPIRY_SECONDS:
        del _sessions[session_token]
        return None
    return entry["chat_id"]


def destroy_session(session_token: str) -> bool:
    """Remove a session. Return True if it existed."""
    if session_token in _sessions:
        del _sessions[session_token]
        return True
    return False
