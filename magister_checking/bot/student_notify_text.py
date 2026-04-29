"""Тексты личных напоминаний магистранту из панели администратора (plain text)."""

from __future__ import annotations


def build_standard_reminder(
    *,
    recipient_fio: str = "",
    extra_lines: list[str] | None = None,
) -> str:
    """Шаблон с призывом снова зайти в бота и при необходимости списком замечаний."""

    greeting = ""
    fn = (recipient_fio or "").strip()
    if fn:
        greeting = f"Здравствуйте, {fn}!\n\n"
    else:
        greeting = "Здравствуйте!\n\n"

    body = (
        "По результатам проверки материалов (промежуточный отчёт и/или диссертацию) есть замечания, "
        "которые нужно устранить.\n\n"
        "Откройте этого бота в личном чате и нажмите /start — обновите при необходимости анкету "
        "и снова запустите проверку командой /recheck.\n\n"
        "Сервер обычно включается после 18 часов и примерно до 22 часов. Следите за объявлениями в телеграм группе "
        "в разделе «Общение по проекту».\n\n"
        "С уважением,\nпроверка магистерских проектов"
    )

    lines = [ln.strip() for ln in (extra_lines or []) if (ln or "").strip() and (ln.strip() != "-")]
    lines = lines[:3]

    if not lines:
        return greeting + body

    remarks = "\n".join(f"• {ln}" for ln in lines)
    return greeting + body + "\n\nЗамечания:\n" + remarks
