"""Sanitized Telegram rendering for operator row diagnostics."""

from __future__ import annotations

import html
import re

from magister_checking.bot.ops_diagnostics import OpsRowDiagnostics, OpsStageSummary

_URL_RE = re.compile(r"https?://\S+", re.I)
_MAX_VALUE_LEN = 120


def _clean(value: object, *, limit: int = _MAX_VALUE_LEN) -> str:
    text = str(value or "").strip()
    text = _URL_RE.sub("[url]", text)
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return html.escape(text)


def _line(label: str, value: object, *, limit: int = _MAX_VALUE_LEN) -> str:
    cleaned = _clean(value, limit=limit)
    if not cleaned:
        cleaned = "—"
    return f"{html.escape(label)}: {cleaned}"


def _render_stages(stages: tuple[OpsStageSummary, ...]) -> str:
    if not stages:
        return ""
    parts: list[str] = []
    for stage in stages:
        warn = f", warn={stage.warnings_count}" if stage.warnings_count else ""
        summary = f", {_clean(stage.summary, limit=48)}" if stage.summary else ""
        parts.append(f"{_clean(stage.stage_id, limit=16)}={_clean(stage.status, limit=16)}{warn}{summary}")
    return "\n".join(f"  {part}" for part in parts)


def render_ops_row_diagnostics(diag: OpsRowDiagnostics) -> str:
    """Compact HTML output; never renders raw row dumps, URLs, phone, PIN, or JSON."""

    lines = [
        f"<b>Ops row {diag.row_number}</b>",
        _line("ФИО", diag.fio),
        _line("fill_status", diag.fill_status),
        _line("last_action", diag.last_action),
        "",
        "<b>Retry</b>",
    ]

    if diag.retry is None:
        lines.append("последняя запись: —")
    else:
        retry_bits = [
            _line("timestamp", diag.retry.timestamp),
            _line("source", diag.retry.source),
            _line("passed", diag.retry.passed),
            _line("stopped_at", diag.retry.stopped_at),
            _line("issues", diag.retry.issues),
            _line("pages/sources/compliance", "/".join(
                v or "—"
                for v in (
                    diag.retry.pages_total,
                    diag.retry.sources_count,
                    diag.retry.compliance,
                )
            )),
            _line("fingerprint", diag.retry.fingerprint_prefix),
        ]
        lines.extend(retry_bits)

    snap = diag.snapshot
    lines.extend(["", "<b>Snapshot</b>"])
    if not snap.present:
        lines.append("latest: нет")
    else:
        lines.extend(
            [
                "latest: есть",
                _line("modifiedTime", snap.modified_time),
                _line("generated_at", snap.generated_at),
                _line("fill_status", snap.fill_status),
                _line("stopped_at", snap.stopped_at),
                _line("fingerprint", snap.fingerprint_prefix),
            ]
        )
        if snap.read_error:
            lines.append(_line("snapshot_read", snap.read_error))
        stages = _render_stages(snap.stages)
        if stages:
            lines.append("stages:")
            lines.append(stages)

    return "\n".join(lines).strip()
