"""Capability and opaque-token primitives.

Only hashes of browser capabilities and download grants are retained.  Neither
identifier is included in artifact object-store keys or public object URLs.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from pathlib import PurePath


def new_job_id() -> str:
    # 192 bits of entropy, URL-safe and intentionally not sequential.
    return secrets.token_urlsafe(24)


def new_secret() -> str:
    # 256 bits of entropy for a bearer capability or an artifact grant.
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def secret_matches(presented: str | None, expected_hash: str) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(token_hash(presented), expected_hash)


def safe_download_filename(filename: str, fallback: str) -> str:
    """Prevent a user-supplied upload name from becoming a response-header injection."""
    leaf = PurePath(filename.replace("\\", "/")).name
    cleaned = "".join(char for char in leaf if char.isalnum() or char in ".-_ ")
    return cleaned[:180] or fallback
