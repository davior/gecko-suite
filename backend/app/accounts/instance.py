"""Instance-wide sign-up switches, set by an administrator. Notes' two, under Notes' names,
so the importer can copy them across as they are."""

from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session

from app.clock import utcnow
from app.config import settings
from app.models.user import InstanceSetting

REGISTRATION_ENABLED = "registration_enabled"
EMAIL_VERIFICATION_REQUIRED = "email_verification_required"
DEFAULTS = {REGISTRATION_ENABLED: True, EMAIL_VERIFICATION_REQUIRED: True}


def get(session: Session, key: str) -> Any:
    row = session.get(InstanceSetting, key)
    if row is None:
        return DEFAULTS.get(key)
    try:
        return json.loads(row.value)
    except ValueError:
        return DEFAULTS.get(key)


def put(session: Session, key: str, value: Any) -> None:
    row = session.get(InstanceSetting, key) or InstanceSetting(key=key)
    row.value, row.updated_at = json.dumps(value), utcnow()
    session.add(row)


def verification_required(session: Session) -> bool:
    """Enforced only when switched on *and* deliverable: with no mail server a new account
    could never verify, and so never sign in. Notes' rule."""
    return settings.email_enabled and bool(get(session, EMAIL_VERIFICATION_REQUIRED))
