"""Многоэтапный конвейер проверки строки магистранта (п.7 ТЗ).

Модуль чистый: не делает HTTP/Docs API-вызовов. Вызывающая сторона
получает документы и пробы HTTP сама, затем передаёт их в оркестратор.
Это позволяет тестировать этапы без сети и переиспользовать пайплайн
как в CLI, так и в боте.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from magister_checking.bot.models import UserForm
from magister_checking.bot.stage_checks import run_stage1_checks
from magister_checking.drive_urls import DriveUrlKind, classify_drive_url
from magister_checking.report_parser import ParsedReport


LINK_MISSING_VALUE = "нет"

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@dataclass(frozen=True)
class _FieldPolicy:
    """Описание ожиданий по типу ссылки в одной колонке.

    ``accepted_kinds`` — допустимые DriveUrlKind. Если фактический
    kind не входит в этот набор, Stage 3 пишет в справку warning и
    помечает ячейку зачёркиванием.

    ``required_mime_for_drive_file`` — если фактический kind ==
    ``drive_file`` и mime известен, требуется совпадение с этим
    значением; иначе warning + зачёркивание. Для google_doc этот
    параметр игнорируется (тип сам по себе достаточен).

    ``fail_stage_on_mismatch`` — если True, mismatch (тип или mime)
    делает Stage 3 не passed (``stopped_at='stage3'``). Используется
    только для диссертации, без которой Stage 4 (содержательный
    разбор) невозможен. Для остальных полей mismatch — мягкий warning.

    ``human_expected`` — формулировка ожидания для текста справки
    («должна вести на …»).
    """

    column_key: str
    label: str
    accepted_kinds: tuple[DriveUrlKind, ...]
    required_mime_for_drive_file: str | None
    fail_stage_on_mismatch: bool
    human_expected: str


_FIELD_POLICIES: tuple[_FieldPolicy, ...] = (
    _FieldPolicy(
        column_key="project_folder_url",
        label="магистерский проект",
        accepted_kinds=("drive_folder",),
        required_mime_for_drive_file=None,
        fail_stage_on_mismatch=False,
        human_expected="на папку Google Drive",
    ),
    _FieldPolicy(
        column_key="lkb_url",
        label="заключение ЛКБ",
        accepted_kinds=("drive_file",),
        required_mime_for_drive_file=PDF_MIME,
        fail_stage_on_mismatch=False,
        human_expected="на PDF-файл в Google Drive",
    ),
    _FieldPolicy(
        column_key="dissertation_url",
        label="диссертацию",
        accepted_kinds=("google_doc", "drive_file"),
        required_mime_for_drive_file=DOCX_MIME,
        fail_stage_on_mismatch=True,
        human_expected="на Google-документ или .docx-файл в Google Drive",
    ),
    _FieldPolicy(
        column_key="publication_url",
        label="публикацию",
        accepted_kinds=("drive_file",),
        required_mime_for_drive_file=PDF_MIME,
        fail_stage_on_mismatch=False,
        human_expected="на PDF-файл в Google Drive",
    ),
)


_KIND_HUMAN: dict[DriveUrlKind, str] = {
    "google_doc": "Google-документ",
    "google_sheet": "Google-таблица",
    "drive_folder": "папка Google Drive",
    "drive_file": "файл Google Drive",
    "other": "ссылка нераспознанного формата",
}


_LINK_FIELDS: tuple[tuple[str, str], ...] = tuple(
    (p.column_key, p.label) for p in _FIELD_POLICIES
)


@dataclass
class StageResult:
    """Универсальный результат одного этапа."""

    name: str
    issues: list[str] = field(default_factory=list)
    passed: bool = False
    executed: bool = False


@dataclass
class Stage3CellUpdate:
    """Значение для колонки L/M/N/O + флаг зачёркивания."""

    column_key: str
    value: str
    strikethrough: bool = False


@dataclass
class RowCheckReport:
    """Итог прогонки одной строки по этапам 1-3."""

    fio: str
    row_number: int | None = None
    stage1: StageResult = field(default_factory=lambda: StageResult("stage1"))
    stage2: StageResult = field(default_factory=lambda: StageResult("stage2"))
    stage3: StageResult = field(default_factory=lambda: StageResult("stage3"))
    stage3_cells: list[Stage3CellUpdate] = field(default_factory=list)
    stopped_at: str | None = None

    def all_issues(self) -> list[str]:
        return [*self.stage1.issues, *self.stage2.issues, *self.stage3.issues]

    def spravka_lines(self) -> list[str]:
        """Человеко-читаемая сводка для «справки» магистранту."""

        lines: list[str] = []
        if self.fio:
            lines.append(f"Магистрант: {self.fio}")
        if self.row_number is not None:
            lines.append(f"Строка в листе «Регистрация»: {self.row_number}")
        issues = self.all_issues()
        if not issues:
            lines.append("Нарушений не найдено.")
        else:
            lines.append("Найдены отклонения:")
            for issue in issues:
                lines.append(f"- {issue}")
        if self.stopped_at:
            lines.append(f"Проверка остановлена на этапе: {self.stopped_at}")
        return lines


def run_stage2(
    *,
    report_url: str,
    url_probe: tuple[str, str],
) -> StageResult:
    """Второй этап: формат URL и открываемость ссылки на отчёт.

    ``url_probe`` — результат ``check_report_url`` из validation.py:
    кортеж ``(valid, accessible)`` со значениями ``"yes"/"no"/""``.
    """

    result = StageResult("stage2", executed=True)
    if not report_url:
        result.issues.append("Ссылка на промежуточный отчёт отсутствует")
        return result
    valid, accessible = url_probe
    if valid != "yes" or accessible != "yes":
        result.issues.append("Ссылка не открыта")
        return result
    result.passed = True
    return result


def _pick_publication_url(parsed: ParsedReport) -> str:
    """Приоритет: явный ``publication_url`` (заполняется парсером по
    заголовкам «Публикации:» / «PDF публикации:»), затем legacy-fallback
    на ``results_article_url`` / ``review_article_url`` — для табличных
    шаблонов, где «Статья по результатам» указана отдельным полем."""
    return (
        parsed.publication_url
        or parsed.results_article_url
        or parsed.review_article_url
        or ""
    ).strip()


def _extract_link(parsed: ParsedReport, key: str) -> str:
    if key == "publication_url":
        return _pick_publication_url(parsed)
    value = getattr(parsed, key, None)
    return (value or "").strip()


def _check_url_kind_and_mime(
    *,
    url: str,
    policy: _FieldPolicy,
    link_mime_types: dict[str, str] | None,
) -> tuple[bool, list[str]]:
    """Возвращает ``(ok, warnings)`` для одной ссылки.

    ``ok`` — соответствует ли ссылка ожидаемому типу (и MIME для file).
    ``warnings`` — человеко-читаемые формулировки для справки магистранта.
    Если ``ok=False`` — caller помечает ячейку зачёркиванием.

    Mime-проверка применяется только если фактический kind == drive_file
    и policy требует MIME. Если mime неизвестен (caller не сделал prefetch
    или drive.files().get упал) — это warning «не удалось проверить MIME»,
    ячейка зачёркивается. Иначе магистрант мог бы залить, например, .jpeg
    под видом PDF, и проверка молча пропустила бы.
    """

    warnings: list[str] = []
    kind = classify_drive_url(url)
    if kind not in policy.accepted_kinds:
        warnings.append(
            f"Ссылка на {policy.label} должна вести {policy.human_expected}, "
            f"а указана {_KIND_HUMAN[kind]}. "
            "Поле в листе помечено зачёркиванием — обновите ссылку в отчёте."
        )
        return False, warnings

    if kind == "drive_file" and policy.required_mime_for_drive_file:
        expected_mime = policy.required_mime_for_drive_file
        if link_mime_types is None:
            return True, warnings
        actual_mime = link_mime_types.get(url, "")
        if not actual_mime:
            warnings.append(
                f"Не удалось определить формат файла по ссылке на {policy.label}. "
                "Поле в листе помечено зачёркиванием — проверьте доступ или укажите "
                f"корректную ссылку (ожидается формат {expected_mime})."
            )
            return False, warnings
        if actual_mime != expected_mime:
            warnings.append(
                f"Ссылка на {policy.label} ведёт на файл формата {actual_mime}, "
                f"а ожидается {expected_mime}. Поле в листе помечено зачёркиванием — "
                "обновите ссылку в отчёте."
            )
            return False, warnings

    return True, warnings


def run_stage3(
    *,
    parsed: ParsedReport,
    accessibility: dict[str, bool] | None = None,
    link_mime_types: dict[str, str] | None = None,
) -> tuple[StageResult, list[Stage3CellUpdate]]:
    """Третий этап: извлечение ссылок из отчёта и их валидация.

    Проверяет три уровня:
      1) ссылка указана (не пустая) — иначе issue «отсутствует»;
      2) тип ссылки соответствует ожиданию (folder / Doc / file) — см.
         ``_FIELD_POLICIES`` и ``classify_drive_url``;
      3) для drive_file — MIME совпадает с требуемым (PDF / DOCX) —
         использует ``link_mime_types`` (caller заполняет через
         ``drive.files().get(fields='mimeType')``);
      4) URL открывается по HTTP (через ``accessibility``).

    Любое нарушение по пунктам 2–4 пишет ссылку в лист с
    зачёркиванием и добавляет warning в справку. ``fail_stage_on_mismatch``
    из политики (только для диссертации) делает Stage 3 не passed —
    дальнейшие этапы пайплайна не запускаются, пока магистрант не
    исправит отчёт. Для остальных полей пайплайн продолжает работу.

    ``accessibility`` / ``link_mime_types``: ``None`` — соответствующая
    проверка отключена (используется в тестах без сети).
    """

    result = StageResult("stage3", executed=True)
    cells: list[Stage3CellUpdate] = []
    diss_mismatch = False

    for policy in _FIELD_POLICIES:
        url = _extract_link(parsed, policy.column_key)
        if not url:
            cells.append(
                Stage3CellUpdate(
                    column_key=policy.column_key,
                    value=LINK_MISSING_VALUE,
                    strikethrough=False,
                )
            )
            result.issues.append(f"Ссылка на {policy.label} отсутствует")
            continue

        type_ok, type_warnings = _check_url_kind_and_mime(
            url=url, policy=policy, link_mime_types=link_mime_types
        )
        result.issues.extend(type_warnings)

        reachable = True
        if accessibility is not None:
            reachable = bool(accessibility.get(url, True))
        if not reachable:
            result.issues.append(f"Ссылка на {policy.label} не открывается")

        cell_strike = (not type_ok) or (not reachable)
        cells.append(
            Stage3CellUpdate(
                column_key=policy.column_key,
                value=url,
                strikethrough=cell_strike,
            )
        )

        if policy.fail_stage_on_mismatch and not type_ok:
            diss_mismatch = True

    diss_cell = next(
        (c for c in cells if c.column_key == "dissertation_url"), None
    )
    result.passed = bool(
        diss_cell
        and diss_cell.value != LINK_MISSING_VALUE
        and not diss_cell.strikethrough
        and not diss_mismatch
    )
    return result, cells


def run_row_pipeline(
    user_form: UserForm,
    *,
    report_document: Any = None,
    url_probe: tuple[str, str] | None = None,
    parsed_report: ParsedReport | None = None,
    link_accessibility: dict[str, bool] | None = None,
    link_mime_types: dict[str, str] | None = None,
    row_number: int | None = None,
) -> RowCheckReport:
    """Пропускает строку через этапы 1-3.

    Остановки:
    - Stage 1: если передан ``report_document`` и в нём нет маркера
      «Промежуточный отчёт», дальнейшие этапы не запускаются.
    - Stage 2: если ссылка не открыта, Stage 3 не запускается.
    - Stage 3: пайплайн завершается в любом случае; ``stage3.passed``
      сигнализирует, имеет ли смысл Stage 4 (не реализован в этой фазе).

    Все IO выполняет caller. Пайплайн принимает уже готовые артефакты.
    """

    report = RowCheckReport(fio=user_form.fio or "", row_number=row_number)

    s1 = run_stage1_checks(user_form, report_document=report_document)
    report.stage1.executed = True
    report.stage1.issues = list(s1.issues)
    report.stage1.passed = not s1.issues and (not s1.report_checked or s1.report_link_ok)

    if s1.report_checked and not s1.report_link_ok:
        report.stopped_at = "stage1"
        return report

    if url_probe is None:
        return report

    report.stage2 = run_stage2(report_url=user_form.report_url or "", url_probe=url_probe)
    if not report.stage2.passed:
        report.stopped_at = "stage2"
        return report

    if parsed_report is None:
        return report

    stage3_result, cells = run_stage3(
        parsed=parsed_report,
        accessibility=link_accessibility,
        link_mime_types=link_mime_types,
    )
    report.stage3 = stage3_result
    report.stage3_cells = cells
    if not report.stage3.passed:
        report.stopped_at = "stage3"
    return report
