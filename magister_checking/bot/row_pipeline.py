"""Многоэтапный конвейер проверки строки магистранта (п.7 ТЗ).

Модуль чистый: не делает HTTP/Docs API-вызовов. Вызывающая сторона
получает документы и пробы HTTP сама, затем передаёт их в оркестратор.
Это позволяет тестировать этапы без сети и переиспользовать пайплайн
как в CLI, так и в боте.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from magister_checking.bot.models import FillStatus, UserForm, compute_fill_status
from magister_checking.bot.stage_checks import run_stage1_checks
from magister_checking.dissertation_metrics import DissertationMetrics
from magister_checking.drive_urls import DriveUrlKind, classify_drive_url
from magister_checking.formatting_rules import (
    FormattingRules,
    evaluate_formatting_compliance,
)
from magister_checking.report_parser import ParsedReport


LINK_MISSING_VALUE = "нет"

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Тексты для колонки «Соответствие оформлению» (см. handoff §8.4: ru_full).
COMPLIANCE_TEXT_YES = "соответствует"
COMPLIANCE_TEXT_NO = "не соответствует"
COMPLIANCE_TEXT_UNKNOWN = "—"


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
class Stage4CellUpdate:
    """Значение для колонок Stage 4 (pages_total / sources_count / compliance).

    В отличие от Stage3CellUpdate флага strikethrough нет: согласовано с
    пользователем (handoff §8.3 — warning only, без зачёркивания).
    Конкретная буква столбца (O/P/Q/...) определяется заголовком листа
    через ``_HEADER_ALIASES`` в ``sheets_repo``.
    """

    column_key: str
    value: str


@dataclass
class Stage4Result:
    """Содержательный разбор диссертации (п.9.4 ТЗ).

    Контракт согласован в handoff §8 (новый чат):

    - Порогов на ``pages_total`` и ``sources_count`` нет — числа просто
      пишутся в лист, в ``issues`` не попадают (§8.2).
    - При ``compliance is False`` — добавляется одно сообщение в
      ``issues`` с короткой фразой и деталями в скобках; ``passed``
      всё равно остаётся ``True`` (warning-модель, §8.3 / §8.5).
    - При ``executed=False`` (Stage 3 не пройдена / метрики не получены)
      — ``skipped_reason`` объясняет причину, в лист по Stage 4 ничего
      не пишется.
    - ``compliance is None`` означает «оформление оценить не удалось»
      (например, не хватило стилей) — в лист пишем «—», без issue.
    """

    name: str = "stage4"
    executed: bool = False
    passed: bool = False
    issues: list[str] = field(default_factory=list)
    pages_total: int | None = None
    sources_count: int | None = None
    compliance: bool | None = None
    skipped_reason: str | None = None
    compliance_text: str | None = None
    """Подробный текст «Найдено / Нужно» для cell листа и Telegram
    (handoff §formatting v2 — full_in_same_cell).

    Заполняется, только если в ``run_stage4`` передан ``formatting_rules``.
    Если ``None`` — caller использует короткое «соответствует / не
    соответствует / —» через ``compliance_to_text(compliance)``.
    Это сделано для обратной совместимости со старыми тестами и
    code-paths без правил.
    """


@dataclass
class RowCheckReport:
    """Итог прогонки одной строки по этапам 1-4."""

    fio: str
    row_number: int | None = None
    stage1: StageResult = field(default_factory=lambda: StageResult("stage1"))
    stage2: StageResult = field(default_factory=lambda: StageResult("stage2"))
    stage3: StageResult = field(default_factory=lambda: StageResult("stage3"))
    stage3_cells: list[Stage3CellUpdate] = field(default_factory=list)
    stage4: Stage4Result = field(default_factory=Stage4Result)
    stage4_cells: list[Stage4CellUpdate] = field(default_factory=list)
    stopped_at: str | None = None
    unchanged: bool = False
    """True, если ``run_row_check(only_if_changed=True)`` обнаружил, что
    с прошлого прогона ничего не поменялось (fingerprint совпал) и
    короткое замыкание сработало: пайплайн не выполнялся, в лист и
    в историю проверок ничего не пишется (handoff §8 — diff_detection
    force_flag). Для остальных кейсов всегда False."""

    def all_issues(self) -> list[str]:
        return [
            *self.stage1.issues,
            *self.stage2.issues,
            *self.stage3.issues,
            *self.stage4.issues,
        ]

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


def compliance_to_text(value: bool | None) -> str:
    """Текст для колонки «Соответствие оформлению» (handoff §8.4 — ru_full)."""

    if value is True:
        return COMPLIANCE_TEXT_YES
    if value is False:
        return COMPLIANCE_TEXT_NO
    return COMPLIANCE_TEXT_UNKNOWN


def _format_compliance_issue(metrics: DissertationMetrics) -> str:
    """Сообщение для issues при ``formatting_compliance is False``.

    Форма «короткая фраза + детали в скобках» — handoff §8.5 (option both).
    """

    parts: list[str] = []
    if metrics.times_new_roman_ratio is not None:
        parts.append(f"TNR {round(metrics.times_new_roman_ratio * 100)}%")
    if metrics.font_size_14_ratio is not None:
        parts.append(f"14pt {round(metrics.font_size_14_ratio * 100)}%")
    if metrics.single_spacing_ratio is not None:
        parts.append(f"single {round(metrics.single_spacing_ratio * 100)}%")
    suffix = f" ({', '.join(parts)})" if parts else ""
    return f"оформление не соответствует требованиям{suffix}"


def _resolve_pages_total(metrics: DissertationMetrics) -> int | None:
    """Главный показатель страниц для листа.

    Приоритет:
      1) ``pdf_pages`` — наиболее точный счётчик (Drive export → PDF
         /Type /Page heuristic). Может быть ``None``: либо файл не
         Google Doc и export не вызывался, либо вызвавшая сторона его
         вообще не считала.
      2) ``approx_pages`` — для .docx это число из ``docProps/app.xml``,
         для Google Doc — оценка по символам (``len(plain)//2200``).
         У .docx часто это и есть «настоящее» число страниц.
      3) ``None`` если оба None.
    """

    if metrics.pdf_pages and metrics.pdf_pages > 0:
        return metrics.pdf_pages
    if metrics.approx_pages and metrics.approx_pages > 0:
        return metrics.approx_pages
    return None


def run_stage4(
    *,
    dissertation_metrics: DissertationMetrics | None,
    formatting_rules: FormattingRules | None = None,
) -> Stage4Result:
    """Содержательный разбор диссертации (п.9.4 ТЗ, handoff §3-§4).

    Чистая функция: caller передаёт уже посчитанные метрики (Google Doc
    через ``analyze_dissertation``, .docx через ``analyze_docx_bytes``).

    Поведение по handoff §8:
      - ``dissertation_metrics is None`` → ``executed=False``,
        ``passed=False``, ``skipped_reason`` объясняет причину; ничего
        не пишем в лист.
      - метрики получены → ``executed=True``, ``passed=True`` (warning-
        модель), значения ``pages_total/sources_count/compliance``
        переносятся в результат.

    Compliance:
      - Если ``formatting_rules`` передан (handoff §formatting v2): итог
        вычисляется ``evaluate_formatting_compliance(metrics, rules)``,
        в ``compliance_text`` сохраняется подробный «Найдено / Нужно»
        для cell и Telegram (full_in_same_cell). При несоответствии в
        ``issues`` идёт ровно тот же текст.
      - Без ``formatting_rules`` — fallback на ``metrics.formatting_compliance``
        (только три ratio TNR/14pt/single, без полей и нумерации); в
        ``issues`` уходит короткое «оформление не соответствует …».
        Этот режим оставлен для обратной совместимости с тестами,
        которые передают metrics-ручки без полей/нумерации.

    Caller обязан вызывать этот этап только после успешной Stage 3
    (диссертация — Google Doc или .docx и доступна). Пайплайн делает
    это автоматически в ``run_row_pipeline``.
    """

    result = Stage4Result()
    if dissertation_metrics is None:
        result.executed = False
        result.passed = False
        result.skipped_reason = "не удалось получить метрики диссертации"
        return result

    result.executed = True
    result.pages_total = _resolve_pages_total(dissertation_metrics)
    result.sources_count = dissertation_metrics.sources_count

    if formatting_rules is not None:
        report = evaluate_formatting_compliance(dissertation_metrics, formatting_rules)
        result.compliance = report.compliance
        result.compliance_text = report.text
        if report.compliance is False:
            result.issues.append(report.text)
    else:
        result.compliance = dissertation_metrics.formatting_compliance
        if dissertation_metrics.formatting_compliance is False:
            result.issues.append(_format_compliance_issue(dissertation_metrics))

    # Warning-модель (handoff §8.3): сам факт получения метрик == passed.
    # Несоответствие оформлению — это warning, а не блокер.
    result.passed = True
    return result


def build_stage4_cells(stage4: Stage4Result) -> list[Stage4CellUpdate]:
    """Готовит значения для записи в pages_total / sources_count / compliance.

    Если Stage 4 не выполнялся (skip из-за неуспешной Stage 3 или из-за
    отсутствия метрик), список пустой — лист по Stage 4 не трогаем.
    """

    if not stage4.executed:
        return []
    pages_value = (
        str(stage4.pages_total) if stage4.pages_total is not None else ""
    )
    sources_value = (
        str(stage4.sources_count) if stage4.sources_count is not None else ""
    )
    compliance_value = (
        stage4.compliance_text
        if stage4.compliance_text is not None
        else compliance_to_text(stage4.compliance)
    )
    return [
        Stage4CellUpdate(column_key="pages_total", value=pages_value),
        Stage4CellUpdate(column_key="sources_count", value=sources_value),
        Stage4CellUpdate(column_key="compliance", value=compliance_value),
    ]


def run_row_pipeline(
    user_form: UserForm,
    *,
    report_document: Any = None,
    url_probe: tuple[str, str] | None = None,
    parsed_report: ParsedReport | None = None,
    link_accessibility: dict[str, bool] | None = None,
    link_mime_types: dict[str, str] | None = None,
    dissertation_metrics: DissertationMetrics | None = None,
    formatting_rules: FormattingRules | None = None,
    row_number: int | None = None,
) -> RowCheckReport:
    """Пропускает строку через этапы 1-4.

    Остановки:
    - Stage 1: если передан ``report_document`` и в нём нет маркера
      «Промежуточный отчёт», дальнейшие этапы не запускаются.
    - Stage 2: если ссылка не открыта, Stage 3 не запускается.
    - Stage 3: если ``stage3.passed=False`` (диссертация не Doc/.docx
      или недоступна), пайплайн помечает ``stopped_at='stage3'`` и
      Stage 4 не запускается. Иначе пайплайн продолжает.
    - Stage 4: warning-модель, ``stopped_at`` не меняет — несоответствие
      оформлению или отсутствие метрик не блокируют последующие этапы
      (b/c в плане handoff §2).

    Все IO выполняет caller. Пайплайн принимает уже готовые артефакты,
    включая ``dissertation_metrics`` (см. ``analyze_dissertation`` /
    ``analyze_docx_bytes`` в ``magister_checking.dissertation_metrics``).
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

    report.stage4 = run_stage4(
        dissertation_metrics=dissertation_metrics,
        formatting_rules=formatting_rules,
    )
    report.stage4_cells = build_stage4_cells(report.stage4)
    return report


def resolve_fill_status_after_row_check(
    user_form: UserForm,
    report: RowCheckReport,
) -> str | None:
    """Строка для колонки ``fill_status`` после прогона проверки (п.12 ТЗ).

    ``None`` — не менять ячейку (короткое замыкание ``unchanged``).
    Для неполной анкеты синхронизируем NEW/PARTIAL; при полной —
    ``OK`` или ``NEED_FIX`` по результату этапов.
    """

    if report.unchanged:
        return None

    base = compute_fill_status(user_form)
    if base != FillStatus.REGISTERED:
        return base.value

    if report.all_issues() or report.stopped_at is not None:
        return FillStatus.NEED_FIX.value
    return FillStatus.OK.value
