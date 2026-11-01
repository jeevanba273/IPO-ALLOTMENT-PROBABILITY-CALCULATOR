"""The dashboard password is stored only as a salted scrypt hash, never as itself.

Format: ``scrypt:<n>:<r>:<p>:<salt>:<key>`` with the salt and key in URL-safe
base64. It holds no ``$``, so .env files, shells and Railway variables keep it
as typed. Make one with ``ipo-intel hash-password``.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os

SCHEME = "scrypt"
# About 50 ms and 16 MiB per check: slow to brute-force, fine for one sign-in.
N, R, P = 2**14, 8, 1
_SALT_BYTES = 16
_KEY_BYTES = 32


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int, dklen: int) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=r, p=p, dklen=dklen, maxmem=64 * 1024 * 1024)


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("the password cannot be empty")
    salt = os.urandom(_SALT_BYTES)
    key = _scrypt(password, salt, N, R, P, _KEY_BYTES)
    return f"{SCHEME}:{N}:{R}:{P}:{_b64(salt)}:{_b64(key)}"


def _parse(stored: str) -> tuple[int, int, int, bytes, bytes]:
    parts = stored.split(":")
    if len(parts) != 6 or parts[0] != SCHEME:
        raise ValueError(f"not a {SCHEME} hash; make one with `ipo-intel hash-password`")
    try:
        n, r, p = (int(part) for part in parts[1:4])
        salt, key = _unb64(parts[4]), _unb64(parts[5])
    except (ValueError, binascii.Error) as exc:
        raise ValueError(f"malformed {SCHEME} hash; make a new one with `ipo-intel hash-password`") from exc
    if n < 2 or n & (n - 1) or r < 1 or p < 1 or not salt or len(key) < 16:
        raise ValueError(f"malformed {SCHEME} hash; make a new one with `ipo-intel hash-password`")
    return n, r, p, salt, key


def check_hash(stored: str) -> None:
    """Raise ValueError unless ``stored`` is a hash verify_password can use."""
    _parse(stored)


def verify_password(password: str, stored: str) -> bool:
    try:
        n, r, p, salt, key = _parse(stored)
    except ValueError:
        return False
    return hmac.compare_digest(_scrypt(password, salt, n, r, p, len(key)), key)
