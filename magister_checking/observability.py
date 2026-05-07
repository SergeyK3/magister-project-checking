"""Small structured logging helpers for local JSON logs.

The helpers keep Phase 4A observability grep-friendly while avoiding raw PII,
tokens, callback payloads, and full Google URLs.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import secrets
from collections.abc import Mapping
from typing import Any

from googleapiclient.errors import HttpError


TRACE_ID_FIELD = "trace_id"

_trace_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "magister_trace_id", default=None
)

STRUCTURED_LOG_FIELDS = frozenset(
    {
        "api",
        "attempt",
        "category",
        "duration_ms",
        "error_class",
        "error_reason",
        "event",
        "file_id_hash",
        "file_id_tail",
        "folder_count",
        "folder_id_hash",
        "folder_id_tail",
        "google_service",
        "history_source",
        "history_write",
        "http_status",
        "last_history_found",
        "method",
        "operation",
        "quota_or_rate_limited",
        "resource_id_hash",
        "resource_kind",
        "row_number",
        "row_resolution",
        "snapshot_candidate_count",
        "snapshot_file_count",
        "snapshot_pick_result",
        "snapshot_upload_count",
        "source_fingerprint_hash_prefix",
        "status",
        "stopped_at",
        "trace_id",
        "trigger",
        "unchanged",
        "apply",
        "only_if_changed",
    }
)


def new_trace_id(prefix: str = "tr") -> str:
    """Return a short random trace id without user or token material."""

    return f"{prefix}_{secrets.token_hex(8)}"


def get_trace_id() -> str:
    trace_id = _trace_id_var.get()
    if not trace_id:
        trace_id = new_trace_id()
        _trace_id_var.set(trace_id)
    return trace_id


def set_trace_id(trace_id: str | None = None) -> contextvars.Token[str | None]:
    """Set a trace id for the current context and return the reset token."""

    return _trace_id_var.set(trace_id or new_trace_id())


def reset_trace_id(token: contextvars.Token[str | None]) -> None:
    _trace_id_var.reset(token)


def hash_value(value: object, *, length: int = 16) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def id_tail(value: object, *, length: int = 6) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    return raw[-length:]


def fingerprint_prefix(value: object, *, length: int = 12) -> str:
    return str(value or "")[:length]


def structured_extra(**fields: Any) -> dict[str, Any]:
    """Build sanitized ``extra`` payload for logging.

    Only whitelisted fields are passed through. Empty values are omitted, while
    falsey booleans and zeroes are kept.
    """

    clean: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in STRUCTURED_LOG_FIELDS:
            continue
        if value is None or value == "":
            continue
        clean[key] = value
    if TRACE_ID_FIELD not in clean:
        clean[TRACE_ID_FIELD] = get_trace_id()
    return clean


def safe_log_record_fields(record: Any) -> dict[str, Any]:
    return {
        key: getattr(record, key)
        for key in STRUCTURED_LOG_FIELDS
        if hasattr(record, key)
    }


def google_error_fields(exc: BaseException) -> dict[str, Any]:
    """Normalize Google API errors without logging request URLs or credentials."""

    fields: dict[str, Any] = {"error_class": type(exc).__name__}
    status = getattr(getattr(exc, "resp", None), "status", None)
    if status is not None:
        try:
            fields["http_status"] = int(status)
        except (TypeError, ValueError):
            fields["http_status"] = str(status)

    blob = _google_error_blob(exc)
    parsed = _parse_google_error_blob(blob)
    reason = _first_google_reason(parsed)
    service = _first_google_service(parsed, blob)
    if reason:
        fields["error_reason"] = reason
    if service:
        fields["google_service"] = service

    low = blob.lower()
    quota_or_rate_limited = (
        fields.get("http_status") in (403, 429)
        and (
            "quota" in low
            or "rate limit" in low
            or "ratelimit" in low
            or reason in {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"}
        )
    )
    if quota_or_rate_limited:
        fields["quota_or_rate_limited"] = True
    return fields


def _google_error_blob(exc: BaseException) -> str:
    parts: list[str] = []
    for attr in ("content", "error_details"):
        raw = getattr(exc, attr, None)
        if not raw:
            continue
        if isinstance(raw, (bytes, bytearray)):
            parts.append(bytes(raw).decode("utf-8", errors="replace"))
        else:
            parts.append(str(raw))
    if isinstance(exc, HttpError) and not parts:
        parts.append(str(exc))
    elif not parts:
        parts.append(str(exc))
    return " ".join(parts)


def _parse_google_error_blob(blob: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(blob)
    except Exception:  # noqa: BLE001
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _first_google_reason(parsed: Mapping[str, Any]) -> str:
    error = parsed.get("error")
    if not isinstance(error, Mapping):
        return ""
    details = error.get("details")
    if isinstance(details, list):
        for item in details:
            if isinstance(item, Mapping):
                reason = item.get("reason")
                if reason:
                    return str(reason)
    errors = error.get("errors")
    if isinstance(errors, list):
        for item in errors:
            if isinstance(item, Mapping):
                reason = item.get("reason")
                if reason:
                    return str(reason)
    status = error.get("status")
    return str(status) if status else ""


def _first_google_service(parsed: Mapping[str, Any], blob: str) -> str:
    error = parsed.get("error")
    if isinstance(error, Mapping):
        details = error.get("details")
        if isinstance(details, list):
            for item in details:
                if isinstance(item, Mapping):
                    service = item.get("service")
                    if service:
                        return str(service)
    for marker in ("docs.googleapis.com", "drive.googleapis.com", "sheets.googleapis.com"):
        if marker in blob:
            return marker
    return ""
