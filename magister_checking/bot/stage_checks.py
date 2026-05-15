"""Поэтапная проверка данных магистранта (п. 6-8 ТЗ).

Первый этап (Stage 1) покрывает поля B:I листа «Регистрация»:
ФИО, Телефон и тип документа по ссылке на промежуточный отчёт.
Если маркер «Промежуточный отчёт» в документе не найден, дальнейшие
этапы проверки не имеют смысла — вызывающая сторона обязана остановиться.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from magister_checking.bot.models import UserForm
from magister_checking.bot.validation import (
    check_report_document_marker,
    validate_fio_shape,
    validate_phone_shape,
)


@dataclass
class Stage1Report:
    """Результат первого этапа.

    issues — сообщения для «справки» магистранту, в порядке появления.
    report_link_ok — True, только если передан загруженный документ
    и в нём найден маркер «Промежуточный отчёт». В этом случае имеет
    смысл запускать этапы 2–4.
    """

    issues: list[str] = field(default_factory=list)
    report_link_ok: bool = False
    report_checked: bool = False


def run_stage1_checks(
    user_form: UserForm,
    *,
    report_document: Any = None,
) -> Stage1Report:
    """Запускает валидаторы первого этапа.

    ``report_document`` — уже полученное через Docs API тело документа
    (dict из ``documents().get().execute()``). Если ``None``, проверка
    маркера не выполняется; caller решает, когда подгружать документ.
    """

    report = Stage1Report()

    fio_error = validate_fio_shape(user_form.fio)
    if fio_error:
        report.issues.append(fio_error)

    phone_error = validate_phone_shape(user_form.phone)
    if phone_error:
        report.issues.append(phone_error)

    if report_document is not None:
        report.report_checked = True
        marker_error = check_report_document_marker(report_document)
        if marker_error:
            report.issues.append(marker_error)
        else:
            report.report_link_ok = True

    return report
