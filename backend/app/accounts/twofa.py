"""Authenticator-app (TOTP) codes. Ported from gecko-notes; the QR is SVG, not PNG, so the
suite needs no imaging library."""

from __future__ import annotations

import base64
import io

import pyotp
import qrcode
import qrcode.image.svg

ISSUER = "Gecko Suite"
# Codes from the neighbouring 30-second window are accepted, for clock drift. Notes' value.
_VALID_WINDOW = 1


def generate_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=ISSUER)


def verify_code(secret: str, code: str) -> bool:
    if not secret or not code:
        return False
    try:
        return pyotp.TOTP(secret).verify(code.strip(), valid_window=_VALID_WINDOW)
    except Exception:  # noqa: BLE001 - a malformed secret is a wrong code, not an error
        return False


def qr_data_uri(uri: str) -> str:
    buffer = io.BytesIO()
    qrcode.make(uri, image_factory=qrcode.image.svg.SvgPathImage).save(buffer)
    return "data:image/svg+xml;base64," + base64.b64encode(buffer.getvalue()).decode()
