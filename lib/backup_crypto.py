"""At-Rest-Verschlüsselung für ai-rem Backups.

Format (binär): MAGIC | salt(16) | nonce(12) | ciphertext+GCM-tag
Key = scrypt(passphrase, salt) → 32 byte, AES-256-GCM (authenticated).

Isoliert von server.py, damit ohne DB/Embedding-Side-Effects testbar.
"""
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"AIREMENC1"
_SALT_LEN = 16
_NONCE_LEN = 12
_KEY_LEN = 32
# scrypt-Kostenparameter — Einmalkosten pro Backup/Restore, für Heim-Workload ok.
_N, _R, _P = 2 ** 14, 8, 1


def derive_key(passphrase: bytes, salt: bytes) -> bytes:
    """32-byte AES-Key aus Passphrase + Salt via scrypt ableiten."""
    kdf = Scrypt(salt=salt, length=_KEY_LEN, n=_N, r=_R, p=_P)
    return kdf.derive(passphrase)


def encrypt(plaintext: bytes, passphrase: bytes) -> bytes:
    """Plaintext verschlüsseln. Salt + Nonce pro Aufruf zufällig neu."""
    salt = os.urandom(_SALT_LEN)
    nonce = os.urandom(_NONCE_LEN)
    key = derive_key(passphrase, salt)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, None)
    return MAGIC + salt + nonce + ciphertext


def decrypt(blob: bytes, passphrase: bytes) -> bytes:
    """Blob entschlüsseln. Wirft bei falschem Key oder manipuliertem Blob."""
    if not is_encrypted(blob):
        raise ValueError("kein ai-rem-Backup-Blob (Magic-Header fehlt)")
    off = len(MAGIC)
    salt = blob[off:off + _SALT_LEN]
    nonce = blob[off + _SALT_LEN:off + _SALT_LEN + _NONCE_LEN]
    ciphertext = blob[off + _SALT_LEN + _NONCE_LEN:]
    key = derive_key(passphrase, salt)
    return AESGCM(key).decrypt(nonce, ciphertext, None)


def is_encrypted(data: bytes) -> bool:
    """True wenn `data` mit dem ai-rem-Verschlüsselungs-Header beginnt."""
    return data[:len(MAGIC)] == MAGIC
