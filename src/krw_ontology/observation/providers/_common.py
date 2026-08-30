"""Shared internal plumbing for observation provider adapters.

Not a port and not public API: bounded-transport helpers reused by the FMP,
FRED, and Polygon adapters so every adapter keeps the collection doctrine —
bounded payload, bounded timeout, redirects rejected, secrets never hashed
into provenance.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, build_opener

# Collection-time bounds (the interactive snapshot router uses 1.25 s / 128 KB;
# scheduled observation collection allows 10 s / 4 MB per the task brief).
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 30.0
_MAX_OBSERVATION_ABS = 1.0e18
_SECRET_PARAM_KEYS = frozenset({"apikey", "api_key", "apiKey"})


class RejectRedirects(HTTPRedirectHandler):
    """Keep the API key on the fixed HTTPS origin even if upstream redirects."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        return None


def open_without_redirects(request: Any, *, timeout: float) -> Any:
    """Open one request through a redirect-rejecting opener (snapshot doctrine)."""

    return build_opener(RejectRedirects()).open(request, timeout=timeout)


def read_bounded(response: Any, *, max_bytes: int = MAX_RESPONSE_BYTES) -> bytes:
    """Read at most ``max_bytes``; anything larger is a protocol violation."""

    body = response.read(max_bytes + 1)
    if not isinstance(body, bytes) or len(body) > max_bytes:
        raise ValueError("provider response exceeds the bounded observation payload")
    return body


def params_hash(params: Mapping[str, str]) -> str:
    """Stable 12-hex sha256 prefix over the public (secret-free) parameters."""

    public = {key: value for key, value in sorted(params.items()) if key not in _SECRET_PARAM_KEYS}
    return hashlib.sha256(urlencode(public).encode("utf-8")).hexdigest()[:12]


def safe_observation_value(value: Any) -> float | None:
    """Coerce one provider value to a bounded finite float, else None."""

    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or abs(number) > _MAX_OBSERVATION_ABS:
        return None
    return number


def iso_utc_now(utc_now: Callable[[], datetime] | None = None) -> str:
    """ISO 8601 UTC placeholder timestamp for provenance ``fetched_at``."""

    clock = utc_now or (lambda: datetime.now(UTC))
    return clock().astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
