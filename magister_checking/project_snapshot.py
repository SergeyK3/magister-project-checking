"""Каноническая модель «снимка проекта» (docs/contract_project_snapshot.md)."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, timezone
from typing import Any

from magister_checking.bot.models import UserForm
from magister_checking.bot.row_pipeline import (
    RowCheckReport,
    Stage3CellUpdate,
    Stage4Result,
    StageResult,
    compliance_to_text,
)

PROJECT_SNAPSHOT_SCHEMA_VERSION = 1

# Стабильные id этапов для JSON / рендеров
PHASE_STAGE1 = "stage1"
PHASE_STAGE2 = "stage2"
PHASE_STAGE3 = "stage3"
PHASE_STAGE4 = "stage4"

PhaseStatus = str  # passed | failed | skipped | pending | not_run


@dataclass(frozen=True)
class SnapshotIdentity:
    fio: str
    group: str = ""
    workplace: str = ""
    position: str = ""
    phone: str = ""
    supervisor: str = ""


@dataclass(frozen=True)
class SnapshotLinks:
    report_url: str = ""
    project_folder_url: str = ""
    lkb_url: str = ""
    dissertation_url: str = ""
    publication_url: str = ""
    report_url_valid: str = ""
    report_url_accessible: str = ""
    dissertation_title: str = ""
    dissertation_language: str = ""


@dataclass(frozen=True)
class SnapshotPhase:
    id: str
    status: PhaseStatus
    summary: str = ""
    details: str = ""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SnapshotStage3Cell:
    column_key: str
    value: str
    strikethrough: bool = False

    @classmethod
    def from_update(cls, u: Stage3CellUpdate) -> SnapshotStage3Cell:
        return cls(
            column_key=u.column_key, value=u.value, strikethrough=u.strikethrough
        )


@dataclass(frozen=True)
class DissertationMetricsSnapshot:
    pages_total: int | None
    sources_count: int | None
    compliance: bool | None
    compliance_label: str
    """Короткая подпись (``compliance_to_text``) для отображения."""


@dataclass(frozen=True)
class SnapshotProvenance:
    trigger: str
    source_fingerprint: str | None = None


@dataclass(frozen=True)
class ProjectSnapshot:
    schema_version: int
    generated_at: str
    row_number: int | None
    identity: SnapshotIdentity
    links: SnapshotLinks
    fill_status: str
    phases: tuple[SnapshotPhase, ...]
    metrics: DissertationMetricsSnapshot | None
    """Заполняется, если известны метрики Stage 4 (в т.ч. skipped)."""
    stage3_extracted: tuple[SnapshotStage3Cell, ...]
    stage4_skipped_reason: str | None
    unchanged: bool
    stopped_at: str | None
    """Совпадает с ``RowCheckReport.stopped_at`` (где остановился пайплайн)."""
    sheet_enrichment_metrics: tuple[str, str, str] | None
    """(pages, sources, compliance) из листа, если нет ``metrics`` (Stage 4 не гоняли)."""
    provenance: SnapshotProvenance


def _stage_status(sr: StageResult) -> PhaseStatus:
    if not sr.executed:
        return "not_run"
    return "passed" if sr.passed else "failed"


def _stage_summary(sr: StageResult) -> str:
    if not sr.executed:
        return "Этап не выполнялся"
    if not sr.issues:
        return "Нарушений не найдено" if sr.passed else "Требуется внимание"
    return ""


def _stage_warnings(sr: StageResult) -> tuple[str, ...]:
    return tuple(sr.issues)


def _phase_stage4(s4: Stage4Result) -> tuple[SnapshotPhase, DissertationMetricsSnapshot | None, str | None]:
    if not s4.executed:
        ph = SnapshotPhase(
            id=PHASE_STAGE4,
            status="skipped",
            summary=s4.skipped_reason or "Stage 4 не выполнялся",
            details="",
            warnings=(),
        )
        return ph, None, s4.skipped_reason
    m = DissertationMetricsSnapshot(
        pages_total=s4.pages_total,
        sources_count=s4.sources_count,
        compliance=s4.compliance,
        compliance_label=compliance_to_text(s4.compliance),
    )
    ph = SnapshotPhase(
        id=PHASE_STAGE4,
        status="passed" if s4.passed else "failed",
        summary="Содержательный разбор выполнен" if s4.passed else "Замечания по разбору",
        details="",
        warnings=tuple(s4.issues),
    )
    return ph, m, None


def build_project_snapshot(
    *,
    user: UserForm,
    report: RowCheckReport,
    extra_values: dict[str, str] | None = None,
    fill_status: str | None = None,
    trigger: str = "row_check",
    source_fingerprint: str | None = None,
    generated_at: datetime | None = None,
) -> ProjectSnapshot:
    """Собирает канонический снимок из анкеты, отчёта проверки и обогащения листа."""

    ex = extra_values or {}
    fs = fill_status if fill_status is not None else (user.fill_status or "")
    gen = generated_at or datetime.now(timezone.utc)
    if gen.tzinfo is None:
        gen = gen.replace(tzinfo=timezone.utc)
    iso = gen.isoformat()
    fp = source_fingerprint if source_fingerprint is not None else report.source_fingerprint

    identity = SnapshotIdentity(
        fio=(user.fio or report.fio or "").strip(),
        group=(user.group_name or "").strip(),
        workplace=(user.workplace or "").strip(),
        position=(user.position or "").strip(),
        phone=(user.phone or "").strip(),
        supervisor=(user.supervisor or "").strip(),
    )
    links = SnapshotLinks(
        report_url=(user.report_url or "").strip(),
        project_folder_url=str(ex.get("project_folder_url", "") or ""),
        lkb_url=str(ex.get("lkb_url", "") or ""),
        dissertation_url=str(ex.get("dissertation_url", "") or ""),
        publication_url=str(ex.get("publication_url", "") or ""),
        report_url_valid=(user.report_url_valid or "").strip(),
        report_url_accessible=(user.report_url_accessible or "").strip(),
        dissertation_title=str(ex.get("dissertation_title", "") or ""),
        dissertation_language=str(ex.get("dissertation_language", "") or ""),
    )

    p1 = SnapshotPhase(
        id=PHASE_STAGE1,
        status=_stage_status(report.stage1),
        summary=_stage_summary(report.stage1),
        details="",
        warnings=_stage_warnings(report.stage1),
    )
    p2 = SnapshotPhase(
        id=PHASE_STAGE2,
        status=_stage_status(report.stage2),
        summary=_stage_summary(report.stage2),
        details="",
        warnings=_stage_warnings(report.stage2),
    )
    p3 = SnapshotPhase(
        id=PHASE_STAGE3,
        status=_stage_status(report.stage3),
        summary=_stage_summary(report.stage3),
        details="",
        warnings=_stage_warnings(report.stage3),
    )
    p4, metrics, s4_skip = _phase_stage4(report.stage4)
    phases = (p1, p2, p3, p4)
    s3t = tuple(SnapshotStage3Cell.from_update(u) for u in report.stage3_cells)

    sem: tuple[str, str, str] | None = None
    if metrics is None:
        sem = (
            str(ex.get("pages_total", "") or ""),
            str(ex.get("sources_count", "") or ""),
            str(ex.get("compliance", "") or ""),
        )
        if not any(sem):
            sem = None

    return ProjectSnapshot(
        schema_version=PROJECT_SNAPSHOT_SCHEMA_VERSION,
        generated_at=iso,
        row_number=report.row_number,
        identity=identity,
        links=links,
        fill_status=fs,
        phases=phases,
        metrics=metrics,
        stage3_extracted=s3t,
        stage4_skipped_reason=s4_skip,
        unchanged=report.unchanged,
        stopped_at=report.stopped_at,
        sheet_enrichment_metrics=sem,
        provenance=SnapshotProvenance(
            trigger=trigger,
            source_fingerprint=fp,
        ),
    )


def _to_jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, dict):
        return {k: _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def project_snapshot_to_json(snapshot: ProjectSnapshot) -> str:
    """JSON для хранения, логов и тестов (UTF-8, ensure_ascii=False)."""

    return json.dumps(
        _to_jsonable(snapshot),
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def _require_keys(d: dict[str, Any], *keys: str) -> None:
    missing = [k for k in keys if k not in d]
    if missing:
        raise ValueError(f"В снимке не хватает полей: {', '.join(missing)}")


def _int_or_none(v: Any) -> int | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return int(v.strip())
        except ValueError:
            return None
    return None


def _bool_or_none(v: Any) -> bool | None:
    if v is None or isinstance(v, bool):
        return v
    return None


def _metrics_from_dict(m: Any) -> DissertationMetricsSnapshot | None:
    if m is None:
        return None
    if not isinstance(m, dict):
        raise TypeError("metrics: ожидается объект или null")
    return DissertationMetricsSnapshot(
        pages_total=_int_or_none(m.get("pages_total")),
        sources_count=_int_or_none(m.get("sources_count")),
        compliance=_bool_or_none(m.get("compliance")),
        compliance_label=str(m.get("compliance_label", "")),
    )


def _identity_from_dict(d: Any) -> SnapshotIdentity:
    if not isinstance(d, dict):
        raise TypeError("identity: ожидается объект")
    return SnapshotIdentity(
        fio=str(d.get("fio", "")),
        group=str(d.get("group", "")),
        workplace=str(d.get("workplace", "")),
        position=str(d.get("position", "")),
        phone=str(d.get("phone", "")),
        supervisor=str(d.get("supervisor", "")),
    )


def _links_from_dict(d: Any) -> SnapshotLinks:
    if not isinstance(d, dict):
        raise TypeError("links: ожидается объект")
    return SnapshotLinks(
        report_url=str(d.get("report_url", "")),
        project_folder_url=str(d.get("project_folder_url", "")),
        lkb_url=str(d.get("lkb_url", "")),
        dissertation_url=str(d.get("dissertation_url", "")),
        publication_url=str(d.get("publication_url", "")),
        report_url_valid=str(d.get("report_url_valid", "")),
        report_url_accessible=str(d.get("report_url_accessible", "")),
        dissertation_title=str(d.get("dissertation_title", "")),
        dissertation_language=str(d.get("dissertation_language", "")),
    )


def _provenance_from_dict(d: Any) -> SnapshotProvenance:
    if not isinstance(d, dict):
        raise TypeError("provenance: ожидается объект")
    fp = d.get("source_fingerprint")
    return SnapshotProvenance(
        trigger=str(d.get("trigger", "")),
        source_fingerprint=str(fp) if fp is not None else None,
    )


def _phase_from_dict(p: Any) -> SnapshotPhase:
    if not isinstance(p, dict):
        raise TypeError("элемент phases: ожидается объект")
    w = p.get("warnings", [])
    if w is None:
        w = []
    if not isinstance(w, (list, tuple)):
        raise TypeError("warnings: ожидается список")
    return SnapshotPhase(
        id=str(p.get("id", "")),
        status=str(p.get("status", "not_run")),
        summary=str(p.get("summary", "")),
        details=str(p.get("details", "")),
        warnings=tuple(str(x) for x in w),
    )


def _s3_cell_from_dict(c: Any) -> SnapshotStage3Cell:
    if not isinstance(c, dict):
        raise TypeError("элемент stage3_extracted: ожидается объект")
    return SnapshotStage3Cell(
        column_key=str(c.get("column_key", "")),
        value=str(c.get("value", "")),
        strikethrough=bool(c.get("strikethrough", False)),
    )


def project_snapshot_from_dict(d: Any) -> ProjectSnapshot:
    """Восстанавливает :class:`ProjectSnapshot` из JSON-объекта (как у ``project_snapshot_to_json``)."""

    if not isinstance(d, dict):
        raise TypeError("Ожидается JSON-объект (dict)")
    _require_keys(
        d,
        "schema_version",
        "generated_at",
        "identity",
        "links",
        "fill_status",
        "phases",
        "provenance",
    )
    schema_version = d.get("schema_version")
    if schema_version != PROJECT_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError(
            f"Неподдерживаемая schema_version: {schema_version!r} "
            f"(ожидается {PROJECT_SNAPSHOT_SCHEMA_VERSION})"
        )
    row_number = d.get("row_number")
    rn: int | None
    if row_number is None:
        rn = None
    elif isinstance(row_number, int) and not isinstance(row_number, bool):
        rn = row_number
    else:
        s = str(row_number)
        rn = int(s) if s.isdigit() else None

    phases_raw = d.get("phases", [])
    if not isinstance(phases_raw, (list, tuple)):
        raise TypeError("phases: ожидается список")
    phases: tuple[SnapshotPhase, ...] = tuple(
        _phase_from_dict(p) for p in phases_raw
    )

    s3_raw = d.get("stage3_extracted", [])
    if s3_raw is None:
        s3_raw = []
    if not isinstance(s3_raw, (list, tuple)):
        raise TypeError("stage3_extracted: ожидается список")
    s3: tuple[SnapshotStage3Cell, ...] = tuple(
        _s3_cell_from_dict(c) for c in s3_raw
    )

    sem = d.get("sheet_enrichment_metrics")
    sheet_m: tuple[str, str, str] | None
    if sem is None:
        sheet_m = None
    elif isinstance(sem, (list, tuple)) and len(sem) == 3:
        sheet_m = (str(sem[0] or ""), str(sem[1] or ""), str(sem[2] or ""))
    else:
        raise TypeError("sheet_enrichment_metrics: ожидается кортеж из 3 строк или null")

    stopped = d.get("stopped_at")
    s4sr = d.get("stage4_skipped_reason")

    return ProjectSnapshot(
        schema_version=PROJECT_SNAPSHOT_SCHEMA_VERSION,
        generated_at=str(d.get("generated_at", "")),
        row_number=rn,
        identity=_identity_from_dict(d.get("identity")),
        links=_links_from_dict(d.get("links")),
        fill_status=str(d.get("fill_status", "")),
        phases=phases,
        metrics=_metrics_from_dict(d.get("metrics")),
        stage3_extracted=s3,
        stage4_skipped_reason=str(s4sr) if s4sr is not None else None,
        unchanged=bool(d.get("unchanged", False)),
        stopped_at=str(stopped) if stopped is not None else None,
        sheet_enrichment_metrics=sheet_m,
        provenance=_provenance_from_dict(d.get("provenance", {})),
    )


def project_snapshot_from_json_str(raw: str) -> ProjectSnapshot:
    """Парсит UTF-8 JSON (файл снимка с Drive) в :class:`ProjectSnapshot`."""

    data = json.loads(raw)
    return project_snapshot_from_dict(data)
