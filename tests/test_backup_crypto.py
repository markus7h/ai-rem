"""Tests für die Backup-Verschlüsselung (lib/backup_crypto)."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lib import backup_crypto as bc  # noqa: E402

PASS = b"correct horse battery staple"
PLAIN = b'{"version": 2, "entities": [], "relations": []}'


def test_roundtrip_returns_original():
    blob = bc.encrypt(PLAIN, PASS)
    assert blob != PLAIN
    assert bc.decrypt(blob, PASS) == PLAIN


def test_same_input_yields_different_blobs():
    """Salt + Nonce pro Datei neu → identische Daten ergeben verschiedene Blobs."""
    assert bc.encrypt(PLAIN, PASS) != bc.encrypt(PLAIN, PASS)


def test_wrong_passphrase_raises():
    blob = bc.encrypt(PLAIN, PASS)
    with pytest.raises(Exception):
        bc.decrypt(blob, b"wrong passphrase")


def test_tampered_blob_raises():
    blob = bytearray(bc.encrypt(PLAIN, PASS))
    blob[-1] ^= 0xFF  # letztes Ciphertext/Tag-Byte kippen
    with pytest.raises(Exception):
        bc.decrypt(bytes(blob), PASS)


def test_is_encrypted_detects_blob():
    blob = bc.encrypt(PLAIN, PASS)
    assert bc.is_encrypted(blob) is True


def test_is_encrypted_rejects_plaintext_json():
    assert bc.is_encrypted(PLAIN) is False


def test_is_encrypted_handles_short_input():
    assert bc.is_encrypted(b"") is False
    assert bc.is_encrypted(b"AI") is False
