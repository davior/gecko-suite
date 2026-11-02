"""Secrets at rest: the suite's own keys, rotation, and reading the siblings' values."""

import base64
import hashlib

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app import crypto
from app.config import Settings, settings


def test_round_trip_and_empty_means_unset():
    stored = crypto.encrypt("sk-live-123")
    assert stored.startswith("enc:") and "sk-live-123" not in stored
    assert crypto.decrypt(stored) == "sk-live-123"
    assert crypto.encrypt("") == "" and crypto.decrypt("") == ""


def test_a_plaintext_value_is_not_trusted_as_a_key():
    # The siblings accept plaintext for legacy rows. Nothing here is ever written that
    # way, so one is corruption and reads as unset.
    assert crypto.decrypt("sk-live-123") == ""


def test_the_key_does_not_follow_the_jwt_secret(monkeypatch):
    stored = crypto.encrypt("sk-live-123")
    monkeypatch.setattr(settings, "jwt_secret_key", "a-completely-different-jwt-secret")
    assert crypto.decrypt(stored) == "sk-live-123"


def test_rotation_keeps_old_values_readable_then_moves_them(monkeypatch):
    old = settings.suite_encryption_keys
    stored = crypto.encrypt("sk-live-123")

    new_key = Fernet.generate_key().decode()
    monkeypatch.setattr(settings, "suite_encryption_keys", f"{new_key},{old}")
    assert crypto.decrypt(stored) == "sk-live-123"

    moved = crypto.reencrypt(stored)
    monkeypatch.setattr(settings, "suite_encryption_keys", new_key)
    assert crypto.decrypt(moved) == "sk-live-123"
    # And the unrotated value is now unreadable, which is why rotation re-encrypts first.
    assert crypto.decrypt(stored) == ""


def test_reencrypt_refuses_a_value_it_cannot_read(monkeypatch):
    stored = crypto.encrypt("sk-live-123")
    monkeypatch.setattr(settings, "suite_encryption_keys", Fernet.generate_key().decode())
    with pytest.raises(Exception):
        crypto.reencrypt(stored)


def test_reads_a_key_stored_by_notes_gam_or_gvc():
    jwt_secret = "the-secret-all-four-apps-share"
    sibling = Fernet(base64.urlsafe_b64encode(hashlib.sha256(jwt_secret.encode()).digest()))
    stored = "enc:" + sibling.encrypt(b"sk-from-gam").decode()
    assert crypto.decrypt_legacy(stored, jwt_secret) == "sk-from-gam"
    # Their pre-encryption rows are plaintext, and they accept them; so does the importer.
    assert crypto.decrypt_legacy("sk-plain", jwt_secret) == "sk-plain"


@pytest.mark.parametrize("value", ["", "not-a-fernet-key"])
def test_startup_refuses_missing_or_malformed_keys(value):
    with pytest.raises(ValidationError) as excinfo:
        Settings(jwt_secret_key="a-strong-enough-test-secret", suite_encryption_keys=value)
    # The message names the setting and never echoes the value: a failed start writes
    # this to the container log.
    assert "SUITE_ENCRYPTION_KEYS" in str(excinfo.value)
    if value:
        assert value not in str(excinfo.value)
