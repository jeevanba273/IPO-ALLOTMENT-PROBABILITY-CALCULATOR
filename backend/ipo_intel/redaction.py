"""Scrubbing secret values out of text before it is logged, stored or raised."""

from __future__ import annotations

from collections.abc import Iterable

REDACTED = "***"

# Values shorter than this are not treated as secrets; replacing them would
# mangle ordinary words without protecting anything real.
MIN_SECRET_LENGTH = 4


def redact(text: str, secrets: Iterable[str]) -> str:
    candidates = {s for s in secrets if s and len(s) >= MIN_SECRET_LENGTH}
    # Longest first, so a secret that contains another is removed whole.
    for secret in sorted(candidates, key=len, reverse=True):
        text = text.replace(secret, REDACTED)
    return text
