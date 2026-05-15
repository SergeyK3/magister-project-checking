"""Read-only operator diagnostics for one registration row."""

from __future__ import annotations

from dataclasses import dataclass

from magister_checking.bot.config import BotConfig
from magister_checking.bot.models import UserForm
from magister_checking.bot.sheets_repo import (
    RecheckHistoryEntry,
    get_spreadsheet,
    load_user,
    read_last_recheck_entry,
)
from magister_checking.drive_latest_snapshot import (
    LatestSnapshotPick,
    download_drive_file_bytes,
    pick_latest_snapshot_for_row,
)
from magister_checking.project_snapshot import ProjectSnapshot, project_snapshot_from_json_str

_FINGERPRINT_PREFIX_LEN = 12


@dataclass(frozen=True)
class OpsRetrySummary:
    timestamp: str = ""
    source: str = ""
    stopped_at: str = ""
    passed: str = ""
    issues: str = ""
    pages_total: str = ""
    sources_count: str = ""
    compliance: str = ""
    fingerprint_prefix: str = ""


@dataclass(frozen=True)
class OpsStageSummary:
    stage_id: str
    status: str
    summary: str = ""
    warnings_count: int = 0


@dataclass(frozen=True)
class OpsSnapshotSummary:
    present: bool
    modified_time: str = ""
    generated_at: str = ""
    fill_status: str = ""
    stopped_at: str = ""
    fingerprint_prefix: str = ""
    stages: tuple[OpsStageSummary, ...] = ()
    read_error: str = ""


@dataclass(frozen=True)
class OpsRowDiagnostics:
    row_number: int
    fio: str = ""
    fill_status: str = ""
    last_action: str = ""
    retry: OpsRetrySummary | None = None
    snapshot: OpsSnapshotSummary = OpsSnapshotSummary(present=False)


def _fingerprint_prefix(value: str | None) -> str:
    cleaned = str(value or "").strip()
    return cleaned[:_FINGERPRINT_PREFIX_LEN] if cleaned else ""


def retry_summary_from_entry(entry: RecheckHistoryEntry | None) -> OpsRetrySummary | None:
    if entry is None:
        return None
    return OpsRetrySummary(
        timestamp=entry.timestamp,
        source=entry.source,
        stopped_at=entry.stopped_at,
        passed=entry.passed,
        issues=entry.issues,
        pages_total=entry.pages_total,
        sources_count=entry.sources_count,
        compliance=entry.compliance,
        fingerprint_prefix=_fingerprint_prefix(entry.fingerprint),
    )


def snapshot_summary_from_pick(
    pick: LatestSnapshotPick | None,
    snapshot: ProjectSnapshot | None = None,
    *,
    read_error: str = "",
) -> OpsSnapshotSummary:
    if pick is None:
        return OpsSnapshotSummary(present=False)
    stages: tuple[OpsStageSummary, ...] = ()
    generated_at = ""
    fill_status = ""
    stopped_at = ""
    fingerprint_prefix = ""
    if snapshot is not None:
        generated_at = snapshot.generated_at
        fill_status = snapshot.fill_status
        stopped_at = snapshot.stopped_at or ""
        fingerprint_prefix = _fingerprint_prefix(snapshot.provenance.source_fingerprint)
        stages = tuple(
            OpsStageSummary(
                stage_id=phase.id,
                status=phase.status,
                summary=phase.summary,
                warnings_count=len(phase.warnings),
            )
            for phase in snapshot.phases
        )
    return OpsSnapshotSummary(
        present=True,
        modified_time=pick.modified_time,
        generated_at=generated_at,
        fill_status=fill_status,
        stopped_at=stopped_at,
        fingerprint_prefix=fingerprint_prefix,
        stages=stages,
        read_error=read_error,
    )


def build_ops_row_diagnostics(
    *,
    row_number: int,
    user: UserForm,
    retry_entry: RecheckHistoryEntry | None,
    latest_snapshot: LatestSnapshotPick | None,
    snapshot: ProjectSnapshot | None = None,
    snapshot_read_error: str = "",
) -> OpsRowDiagnostics:
    return OpsRowDiagnostics(
        row_number=row_number,
        fio=(user.fio or "").strip(),
        fill_status=(user.fill_status or "").strip(),
        last_action=(user.last_action or "").strip(),
        retry=retry_summary_from_entry(retry_entry),
        snapshot=snapshot_summary_from_pick(
            latest_snapshot,
            snapshot,
            read_error=snapshot_read_error,
        ),
    )


def collect_ops_row_diagnostics(config: BotConfig, row_number: int) -> OpsRowDiagnostics:
    """Collect a small, sanitized-ready read-only diagnostic view for one row."""

    spreadsheet = get_spreadsheet(config)
    worksheet = spreadsheet.worksheet(config.worksheet_name)
    user = load_user(worksheet, row_number)
    retry_entry = read_last_recheck_entry(spreadsheet, row_number)
    latest_snapshot = pick_latest_snapshot_for_row(config, row_number)

    snapshot: ProjectSnapshot | None = None
    snapshot_read_error = ""
    if latest_snapshot is not None:
        try:
            raw = download_drive_file_bytes(config, latest_snapshot.file_id)
            snapshot = project_snapshot_from_json_str(raw.decode("utf-8-sig"))
        except Exception as exc:  # noqa: BLE001
            snapshot_read_error = type(exc).__name__

    return build_ops_row_diagnostics(
        row_number=row_number,
        user=user,
        retry_entry=retry_entry,
        latest_snapshot=latest_snapshot,
        snapshot=snapshot,
        snapshot_read_error=snapshot_read_error,
    )
