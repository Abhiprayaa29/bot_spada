"""Pairing logic — delegates to root-level pairing_store.py for cross-process JSON store."""

import sys
import os
import io
import base64

# Ensure root is importable
_root = os.path.dirname(os.path.dirname(__file__))
if _root not in sys.path:
    sys.path.insert(0, _root)

from pairing_store import (
    create_pairing_token,
    is_token_valid,
    consume_token,
    get_token_status,
    cleanup_expired,
)


def generate_qr_base64(token: str) -> str:
    """Generate QR code image for the given token, return base64-encoded PNG."""
    import qrcode
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(token)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#38bdf8", back_color="#1e293b")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def generate_qr_png(token: str) -> bytes:
    """Generate QR code image for the given token, return raw PNG bytes."""
    import qrcode
    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(token)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#38bdf8", back_color="#1e293b")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
