"""Многоэтапный конвейер проверки строки магистранта (п.7 ТЗ).

Модуль чистый: не делает HTTP/Docs API-вызовов. Вызывающая сторона
получает документы и пробы HTTP сама, затем передаёт их в оркестратор.
Это позволяет тестировать этапы без сети и переиспользовать пайплайн
как в CLI, так и в боте.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from magister_checking.bot.models import FillStatus, UserForm, compute_fill_status
from magister_checking.bot.stage_checks import run_stage1_checks
from magister_checking.bot.validation import (
    REPORT_URL_HTTP_INACCESSIBLE_MESSAGE,
)
from magister_checking.dissertation_metrics import DissertationMetrics
from magister_checking.drive_urls import (
    DriveUrlKind,
    classify_drive_url,
    is_google_drive_folder_url,
)
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
    """Описание ожиданий по типу ссылки в одной колонке."""

    column_key: str
    """Ключ столбца в листе (``report_url``, ``project_folder_url``, …)."""
    label: str
    """Внутренний короткий идентификатор поля (сообщения)."""
    issue_title: str
    """Заголовок в сообщении магистранту («…» перед двоеточием)."""
    accepted_kinds: tuple[DriveUrlKind, ...]
    required_mime_for_drive_file: str | tuple[str, ...] | None
    human_expected: str
    """Подпись типа («папка Google Drive», …) — для текста ошибки типа/формата."""


_FIELD_POLICIES: tuple[_FieldPolicy, ...] = (
    _FieldPolicy(
        column_key="report_url",
        label="промежуточный отчёт",
        issue_title="Промежуточный отчёт",
        accepted_kinds=("google_doc", "drive_file"),
        required_mime_for_drive_file=(DOCX_MIME, PDF_MIME),
        human_expected=(
            "документ Google (ссылка на Google Doc) или файл .docx / PDF на Google Drive"
        ),
    ),
    _FieldPolicy(
        column_key="project_folder_url",
        label="папку магистерского проекта",
        issue_title='Папка «Магистерский проект»',
        accepted_kinds=("drive_folder",),
        required_mime_for_drive_file=None,
        human_expected="папка Google Drive",
    ),
    _FieldPolicy(
        column_key="lkb_url",
        label="заключение ЛКБ",
        issue_title="Заключение ЛКБ",
        accepted_kinds=("google_doc", "drive_file"),
        required_mime_for_drive_file=PDF_MIME,
        human_expected=(
            "Google Doc или файл PDF на Google Drive"
        ),
    ),
    _FieldPolicy(
        column_key="dissertation_url",
        label="диссертацию",
        issue_title="Диссертация",
        accepted_kinds=("google_doc", "drive_file"),
        required_mime_for_drive_file=DOCX_MIME,
        human_expected="Google Doc или файл .docx на Google Drive",
    ),
    _FieldPolicy(
        column_key="publication_url",
        label="публикацию",
        issue_title="Публикация",
        accepted_kinds=("google_doc", "drive_file"),
        required_mime_for_drive_file=PDF_MIME,
        human_expected="Google Doc или файл PDF на Google Drive",
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
    source_fingerprint: str | None = None
    """Снимок входов re-check (``row_check_cli``), для provenance в project snapshot."""
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
        result.issues.append(REPORT_URL_HTTP_INACCESSIBLE_MESSAGE)
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


def _extract_link(
    parsed: ParsedReport,
    key: str,
    *,
    registration_report_url: str = "",
) -> str:
    if key == "report_url":
        return (registration_report_url or "").strip()
    if key == "publication_url":
        return _pick_publication_url(parsed)
    value = getattr(parsed, key, None)
    return (value or "").strip()


def _kind_mismatch_human(kind: DriveUrlKind) -> str:
    """Пояснение «что оказалось по ссылке» для ошибки типа."""
    kh = _KIND_HUMAN[kind]
    if kind == "drive_folder":
        return f"{kh} (ссылка на папку; частая ошибка — вместо документа указана папка или наоборот)"
    if kind == "google_doc":
        return f"{kh}"
    if kind == "drive_file":
        return f"{kh} (общая ссылка на файл без открытия в Docs)"
    if kind == "google_sheet":
        return f"{kh}; для этого поля обычно нужен документ, а не таблица"
    return kh


def _folder_word_in_google_url(url: str) -> bool:
    """Подстрока «folder» в URL Google — типичный признак ссылки на каталог Drive."""

    if "folder" not in url.casefold():
        return False
    netloc = (urlparse(url.strip()).netloc or "").lower()
    return netloc.endswith("google.com") or netloc.endswith("googleusercontent.com")


def _extra_kind_mismatch_hints(
    policy: _FieldPolicy,
    *,
    kind: DriveUrlKind,
    url: str,
) -> str:
    """Дополнительные пояснения к ошибке типа ссылки (политика полей Stage 3)."""

    parts: list[str] = []
    pk = policy.column_key

    if pk == "lkb_url" and kind == "drive_folder":
        parts.append(
            " для ЛКБ нужно указывать ссылку не на папку, "
            "а на конкретный pdf файл."
        )

    if pk == "report_url":
        if kind == "drive_folder" or _folder_word_in_google_url(url):
            parts.append(
                " Слово folder в адресе допустимо только для поля "
                "«Папка «Магистерский проект»»; для промежуточного отчёта "
                "укажите ссылку на документ Google или файл .docx/PDF на Drive, а не на папку."
            )

    if pk == "project_folder_url":
        if kind in ("google_doc", "drive_file"):
            parts.append(
                " Для этого поля нужна ссылка именно на папку Google Drive "
                "(в адресе есть …/folders/…), а не на файл или документ."
            )
        elif not is_google_drive_folder_url(url):
            parts.append(
                " Обычно ссылка на папку содержит фрагмент …/folders/… в адресе; если его "
                "нет, возможно, указан файл или страница не того типа — замените ссылку "
                "на каталог магистерского проекта."
            )

    return "".join(parts)


def _check_url_kind_and_mime(
    *,
    url: str,
    policy: _FieldPolicy,
    link_mime_types: dict[str, str] | None,
) -> tuple[bool, list[str]]:
    """Возвращает ``(ok, warnings)`` для одной ссылки."""

    warnings: list[str] = []
    ht = policy.issue_title
    kind = classify_drive_url(url)
    if kind not in policy.accepted_kinds:
        warnings.append(
            f"«{ht}»: неверный тип ссылки — ожидалось {policy.human_expected}; "
            f"фактически: {_kind_mismatch_human(kind)}. "
            "Поле в листе помечено зачёркиванием — поправьте ссылку в промежуточном отчёте."
            f"{_extra_kind_mismatch_hints(policy, kind=kind, url=url)}"
        )
        return False, warnings

    if kind == "drive_file" and policy.required_mime_for_drive_file is not None:
        spec = policy.required_mime_for_drive_file
        allowed: tuple[str, ...] = (spec,) if isinstance(spec, str) else tuple(spec)
        if link_mime_types is None:
            return True, warnings
        actual_mime = link_mime_types.get(url, "")
        if not actual_mime:
            warnings.append(
                f"«{ht}»: не удалось определить MIME файла для проверки формата (нет ответа "
                "Drive API — часто из-за доступа только для владельца). "
                f"Ожидалось содержимое как у {policy.human_expected}. "
                "Поле в листе зачёркнуто."
            )
            return False, warnings
        if actual_mime not in allowed:
            warnings.append(
                f"«{ht}»: неверный формат хранящегося файла (MIME «{actual_mime}»; "
                f"ожидался тип документа как у {policy.human_expected}). "
                "Частая причина — загружен не тот файл. Поле в листе зачёркнуто."
            )
            return False, warnings

    return True, warnings


def _issue_unreachable(*, policy: _FieldPolicy) -> str:
    """Сеть/HTTP не открывает ссылку (частая причина — закрыт доступ только владельцу)."""
    return (
        f"«{policy.issue_title}»: ссылку не удалось проверить (не открывается по HTTPS с "
        "сервера — как «страница недоступна»). Откройте доступ на чтение для всех по ссылке: "
        "«Настроить доступ» → «Все, у кого есть ссылка» → роль «Читатель»."
    )


def run_stage3(
    *,
    parsed: ParsedReport,
    registration_report_url: str = "",
    accessibility: dict[str, bool] | None = None,
    link_mime_types: dict[str, str] | None = None,
) -> tuple[StageResult, list[Stage3CellUpdate]]:
    """Третий этап: пять контролируемых ссылок (промежуточный отчёт + блок из Прил. 1).

    По каждому полю сообщения описывают: отсутствие ссылки, неверный тип (папка вместо
    документа и наоборот), недоступность по HTTPS (доступ для бота/проверки), ошибку
    формата файла для ``drive_file`` (MIME через Drive API при наличии).

    ``registration_report_url`` — ссылка на промежуточный отчёт из строки регистрации
    (тот же URL, что Stage 2 проверил по формату/HTTP для доступа как к странице).
    Колонку ``report_url`` в листе бот затем записывает с результатом этого шага.

    ``accessibility`` / ``link_mime_types``: ``None`` — проверка отключена (тесты без сети).
    """

    result = StageResult("stage3", executed=True)
    cells: list[Stage3CellUpdate] = []

    for policy in _FIELD_POLICIES:
        url = _extract_link(
            parsed,
            policy.column_key,
            registration_report_url=registration_report_url,
        )
        if not url:
            cells.append(
                Stage3CellUpdate(
                    column_key=policy.column_key,
                    value=LINK_MISSING_VALUE,
                    strikethrough=False,
                )
            )
            result.issues.append(
                f"«{policy.issue_title}»: в промежуточном отчёте не указана ссылка "
                "(поле из списка пяти обязательных ссылок пустое или не найдено парсером)."
            )
            continue

        type_ok, type_warnings = _check_url_kind_and_mime(
            url=url, policy=policy, link_mime_types=link_mime_types
        )
        result.issues.extend(type_warnings)

        reachable = True
        if accessibility is not None:
            reachable = bool(accessibility.get(url, True))
        if not reachable:
            result.issues.append(_issue_unreachable(policy=policy))

        cell_strike = (not type_ok) or (not reachable)
        cells.append(
            Stage3CellUpdate(
                column_key=policy.column_key,
                value=url,
                strikethrough=cell_strike,
            )
        )

    diss_cell = next(
        (c for c in cells if c.column_key == "dissertation_url"), None
    )
    rep_cell = next((c for c in cells if c.column_key == "report_url"), None)
    result.passed = bool(
        diss_cell
        and diss_cell.value != LINK_MISSING_VALUE
        and not diss_cell.strikethrough
        and rep_cell
        and rep_cell.value != LINK_MISSING_VALUE
        and not rep_cell.strikethrough
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
        registration_report_url=user_form.report_url or "",
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
