"""Правила оформления магистерских проектов (Stage 4 compliance).

Один источник истины для:
- допустимого шрифта/кегля/межстрочного интервала;
- ширины полей страницы (см) и допуска по ним;
- положения номера страницы.

Используется в ``magister_checking.bot.row_pipeline`` (формирование текста
issue и значения колонки «Соответствие оформлению») и в ``run_stage4``
(вычисление расширенного ``compliance``).

Значения читаются из переменных окружения ``FORMATTING_*`` (.env).
Любую переменную можно опустить — берётся дефолт из этого модуля.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from magister_checking.dissertation_metrics import DissertationMetrics


_LINE_SPACING_LITERALS: tuple[str, ...] = ("single", "1.0", "1,0")
_NUMBERING_POSITIONS: tuple[str, ...] = (
    "bottom-right",
    "bottom-center",
    "bottom-left",
    "top-right",
    "top-center",
    "top-left",
)


@dataclass(frozen=True)
class FormattingRules:
    """Нормативные требования к оформлению.

    Поля ``margin_*_cm`` хранятся в сантиметрах с точностью до сотых:
    в DOCX поля ``<w:pgMar>`` записываются в твипсах (1 см ≈ 567 твипсов),
    в Google Doc — в ``pt`` (1 см ≈ 28.346 pt). Конверсия делается
    в ``magister_checking.dissertation_metrics``; здесь — только эталон.

    ``ratio_threshold`` = 0.95 значит «не менее 95% символов / абзацев
    с правильным шрифтом, кеглем, интервалом». Совпадает со значением,
    исторически захардкоженным в ``_formatting_compliance``.

    ``margin_tolerance_cm`` = 0.2 — типичный допуск при экспорте DOCX
    из Google Docs (поля округляются до твипсов и обратно с потерями).
    """

    font_family: str = "Times New Roman"
    font_size_pt: float = 14.0
    line_spacing: str = "single"
    margin_top_cm: float = 2.0
    margin_bottom_cm: float = 1.0
    margin_left_cm: float = 3.0
    margin_right_cm: float = 1.0
    page_numbering_position: str = "bottom-right"
    ratio_threshold: float = 0.95
    margin_tolerance_cm: float = 0.2

    @property
    def margins_cm(self) -> dict[str, float]:
        return {
            "top": self.margin_top_cm,
            "bottom": self.margin_bottom_cm,
            "left": self.margin_left_cm,
            "right": self.margin_right_cm,
        }


def _read(name: str, default: str) -> str:
    raw = os.environ.get(name)
    if raw is None:
        return default
    stripped = raw.strip()
    return stripped if stripped else default


def _read_float(name: str, default: float) -> float:
    raw = _read(name, "")
    if not raw:
        return default
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return default


def _read_position(name: str, default: str) -> str:
    raw = _read(name, default).lower().replace("_", "-")
    return raw if raw in _NUMBERING_POSITIONS else default


def load_formatting_rules() -> FormattingRules:
    """Собирает ``FormattingRules`` из env, подставляя дефолты на отсутствующие.

    Не валится при опечатках: некорректное значение → берётся дефолт.
    Это сознательно: оформление — не критичный к жизни параметр, и
    лучше дать понятный отчёт магистранту по дефолтным правилам, чем
    уронить весь ``check-row`` из-за лишней буквы в FORMATTING_*.
    """

    return FormattingRules(
        font_family=_read("FORMATTING_FONT_FAMILY", "Times New Roman"),
        font_size_pt=_read_float("FORMATTING_FONT_SIZE_PT", 14.0),
        line_spacing=(_read("FORMATTING_LINE_SPACING", "single").lower()),
        margin_top_cm=_read_float("FORMATTING_MARGIN_TOP_CM", 2.0),
        margin_bottom_cm=_read_float("FORMATTING_MARGIN_BOTTOM_CM", 1.0),
        margin_left_cm=_read_float("FORMATTING_MARGIN_LEFT_CM", 3.0),
        margin_right_cm=_read_float("FORMATTING_MARGIN_RIGHT_CM", 1.0),
        page_numbering_position=_read_position(
            "FORMATTING_PAGE_NUMBERING_POSITION", "bottom-right"
        ),
        ratio_threshold=_read_float("FORMATTING_RATIO_THRESHOLD", 0.95),
        margin_tolerance_cm=_read_float("FORMATTING_MARGIN_TOLERANCE_CM", 0.2),
    )


def is_single_spacing_literal(value: str) -> bool:
    """True, если строка из конфига означает «одинарный межстрочный интервал»."""

    return (value or "").strip().lower().replace(",", ".") in _LINE_SPACING_LITERALS


def position_human_ru(position: str) -> str:
    """Человекочитаемое описание положения номера страницы для отчёта на русском."""

    mapping = {
        "bottom-right": "внизу справа",
        "bottom-center": "внизу по центру",
        "bottom-left": "внизу слева",
        "top-right": "вверху справа",
        "top-center": "вверху по центру",
        "top-left": "вверху слева",
    }
    return mapping.get(position, position)


def _format_cm(value: float) -> str:
    """1.83 → '1,83', 2.0 → '2,0' (русская локаль для cell/Telegram)."""

    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",") or "0"


def _format_margins_cm(margins: dict[str, float]) -> str:
    return (
        f"верхнее {_format_cm(margins['top'])} / "
        f"нижнее {_format_cm(margins['bottom'])} / "
        f"левое {_format_cm(margins['left'])} / "
        f"правое {_format_cm(margins['right'])} см"
    )


def _format_pct(ratio: float | None) -> str:
    if ratio is None:
        return "—"
    return f"{round(ratio * 100)}%"


@dataclass(frozen=True)
class ComplianceReport:
    """Итог расширенной проверки оформления.

    ``compliance`` — итоговый бинарный вердикт (None, если данных мало).
    ``text`` — подробное сообщение «Найдено / Нужно» для cell листа и
    Telegram (handoff §formatting v2 — full_in_same_cell). Один и тот
    же текст идёт и в колонку «Соответствие оформлению», и магистранту.
    ``warnings`` — диагностика, которая НЕ блокирует compliance (например,
    coverage нумерации < 50% секций для DOCX). Пишется внутри ``text``.
    """

    compliance: bool | None
    text: str
    warnings: tuple[str, ...] = ()


def evaluate_formatting_compliance(
    metrics: "DissertationMetrics",
    rules: FormattingRules,
) -> ComplianceReport:
    """Сверяет фактические метрики оформления с эталоном из ``rules``.

    Блокирует ``compliance=False``, если хоть одно из условий нарушено:
    - доля Times New Roman / 14 pt / single-spacing < ``ratio_threshold``;
    - любое из полей ``page_margins_cm`` отличается от эталона больше
      чем на ``margin_tolerance_cm``;
    - ``page_numbering_present is False`` (PAGE-поле не найдено вовсе);
    - ``page_numbering_position`` ≠ требуемой (warning, но всё равно
      блокирует — это явное несоблюдение методички, а не рендерер-баг).

    Не блокирует, но отмечает в ``warnings`` (и в ``text``):
    - DOCX: coverage ``sections_with_footer / sections_total < 0.5``
      («нумерация задана только для N из M секций — в Google Docs может
      не отображаться»). Это подсказка магистранту, не вердикт.

    Возвращает ``compliance=None``, если ни одна метрика недоступна
    (например, .pdf без структуры — но мы такие до Stage 4 не доводим).
    """

    found_lines: list[str] = []
    needed_lines: list[str] = []
    issues: list[str] = []
    warnings: list[str] = []

    has_any_metric = False

    # Шрифт
    if metrics.times_new_roman_ratio is not None:
        has_any_metric = True
        actual_pct = round(metrics.times_new_roman_ratio * 100)
        found_lines.append(f"шрифт «{rules.font_family}»: {actual_pct}% символов")
        needed_lines.append(f"шрифт «{rules.font_family}»: 100% символов")
        if metrics.times_new_roman_ratio < rules.ratio_threshold:
            issues.append("font_family")

    # Кегль
    if metrics.font_size_14_ratio is not None:
        has_any_metric = True
        actual_pct = round(metrics.font_size_14_ratio * 100)
        size_label = (
            f"{int(rules.font_size_pt)}"
            if rules.font_size_pt.is_integer()
            else f"{rules.font_size_pt}"
        )
        found_lines.append(f"кегль {size_label} pt: {actual_pct}% символов")
        needed_lines.append(f"кегль {size_label} pt: 100% символов")
        if metrics.font_size_14_ratio < rules.ratio_threshold:
            issues.append("font_size")

    # Межстрочный интервал
    if metrics.single_spacing_ratio is not None:
        has_any_metric = True
        actual_pct = round(metrics.single_spacing_ratio * 100)
        found_lines.append(f"межстрочный интервал — одинарный: {actual_pct}% абзацев")
        needed_lines.append("межстрочный интервал — одинарный: 100% абзацев")
        if metrics.single_spacing_ratio < rules.ratio_threshold:
            issues.append("line_spacing")

    # Поля
    if metrics.page_margins_cm:
        has_any_metric = True
        actual_str = _format_margins_cm(metrics.page_margins_cm)
        expected_str = _format_margins_cm(rules.margins_cm)
        found_lines.append(f"поля страницы: {actual_str}")
        needed_lines.append(f"поля страницы: {expected_str}")
        for key in ("top", "bottom", "left", "right"):
            actual = metrics.page_margins_cm.get(key)
            expected = rules.margins_cm[key]
            if actual is None or abs(actual - expected) > rules.margin_tolerance_cm:
                issues.append(f"margin_{key}")
        if metrics.page_margins_secondary_cm:
            n_other = len(metrics.page_margins_secondary_cm)
            warnings.append(
                f"в документе ещё {n_other} комбинаци"
                + ("я" if n_other == 1 else "и")
                + " полей у других секций (титул/приложения)"
            )

    # Нумерация страниц
    if metrics.page_numbering_present is not None:
        has_any_metric = True
        if metrics.page_numbering_present is False:
            found_lines.append("нумерация страниц: отсутствует")
            issues.append("page_numbering_missing")
        else:
            actual_position = metrics.page_numbering_position or "не определено"
            actual_human = (
                position_human_ru(actual_position)
                if actual_position != "не определено"
                else "не определено"
            )
            coverage_note = ""
            with_footer = metrics.page_numbering_sections_with_footer
            total = metrics.page_numbering_sections_total
            if (
                with_footer is not None
                and total is not None
                and total > 0
                and with_footer * 2 < total
            ):
                coverage_note = (
                    f" (задана только для {with_footer} из {total} секций — "
                    "в Google Docs может не отображаться на большинстве страниц)"
                )
                warnings.append(
                    f"нумерация задана только для {with_footer} из {total} секций"
                )
            found_lines.append(f"нумерация страниц: {actual_human}{coverage_note}")
            if (
                metrics.page_numbering_position is not None
                and metrics.page_numbering_position != rules.page_numbering_position
            ):
                issues.append("page_numbering_position")
        needed_lines.append(
            f"нумерация страниц: {position_human_ru(rules.page_numbering_position)}"
        )

    if not has_any_metric:
        return ComplianceReport(compliance=None, text="—", warnings=())

    compliance = not issues
    if compliance:
        # OK кейс — короткий текст в cell. Подробности «найдено: …» в
        # данном случае не нужны: магистрант видит «соответствует» и
        # идёт дальше. Длинный «Найдено / Нужно» только при проблеме.
        text = "соответствует"
    else:
        text = (
            "не соответствует. "
            "Найдено: " + "; ".join(found_lines) + ". "
            "Нужно: " + "; ".join(needed_lines) + "."
        )

    return ComplianceReport(
        compliance=compliance, text=text, warnings=tuple(warnings)
    )
