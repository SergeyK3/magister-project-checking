"""Подтверждение номера телефона одноразовым PIN-кодом (MVP ТЗ 30.04.2026).

Память процесса: одна активная попытка на ``telegram_id`` чата. Хеш PIN — SHA-256.
Срок действия и лимит попыток настраиваются константами ниже.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
import time
from dataclasses import dataclass

logger = logging.getLogger("magistrcheckbot")

_PIN_LOCK = asyncio.Lock()

PIN_TTL_SEC = 5 * 60
PIN_MAX_ATTEMPTS = 3
PIN_LEN_MIN = 4
PIN_LEN_MAX = 6


@dataclass
class _PinChallenge:
    pin_hash: bytes
    expires_at: float
    attempts: int
    phone_normalized: str


_CHALLENGES: dict[str, _PinChallenge] = {}


def _hash_pin(plaintext: str) -> bytes:
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


def _purge_expired_unlocked() -> None:
    now = time.time()
    dead = [k for k, v in _CHALLENGES.items() if v.expires_at <= now]
    for k in dead:
        del _CHALLENGES[k]


async def clear_challenge(telegram_user_id: str) -> None:
    """Удаляет активную попытку (отмена диалога, успех, блокировка)."""

    key = str(telegram_user_id or "").strip()
    if not key:
        return
    async with _PIN_LOCK:
        _CHALLENGES.pop(key, None)


async def issue_pin_challenge(
    telegram_user_id: str, phone_normalized: str
) -> tuple[str, int] | None:
    """Создаёт новый PIN. Возвращает ``(plaintext, length)`` или ``None``, если телефона нет."""

    key = str(telegram_user_id or "").strip()
    phone = (phone_normalized or "").strip()
    if not key or not phone:
        return None

    length = secrets.randbelow(PIN_LEN_MAX - PIN_LEN_MIN + 1) + PIN_LEN_MIN
    plain = "".join(secrets.choice("0123456789") for _ in range(length))

    async with _PIN_LOCK:
        _purge_expired_unlocked()
        _CHALLENGES[key] = _PinChallenge(
            pin_hash=_hash_pin(plain),
            expires_at=time.time() + PIN_TTL_SEC,
            attempts=0,
            phone_normalized=phone,
        )

    logger.info(
        "%s",
        json.dumps(
            {
                "event": "pin_issued",
                "telegram_id": key,
                "phone_normalized": phone,
                "pin_plaintext": plain,
                "ttl_sec": PIN_TTL_SEC,
            },
            ensure_ascii=False,
        ),
    )
    return plain, length


class PinVerifyResult:
    OK = "ok"
    WRONG = "wrong"
    EXPIRED = "expired"
    LOCKED = "locked"
    NO_CHALLENGE = "none"


async def verify_pin_challenge(telegram_user_id: str, entered: str) -> str:
    """Проверяет введённый PIN. Возвращает одно из ``PinVerifyResult.*``."""

    key = str(telegram_user_id or "").strip()
    entered_clean = "".join(c for c in (entered or "") if c.isdigit())

    async with _PIN_LOCK:
        ch = _CHALLENGES.get(key)
        if ch is None:
            return PinVerifyResult.NO_CHALLENGE

        now = time.time()
        if now > ch.expires_at:
            _CHALLENGES.pop(key, None)
            logger.info(
                "%s",
                json.dumps(
                    {"event": "pin_verify", "telegram_id": key, "status": "expired"},
                    ensure_ascii=False,
                ),
            )
            return PinVerifyResult.EXPIRED

        if ch.attempts >= PIN_MAX_ATTEMPTS:
            _CHALLENGES.pop(key, None)
            logger.info(
                "%s",
                json.dumps(
                    {"event": "pin_verify", "telegram_id": key, "status": "locked"},
                    ensure_ascii=False,
                ),
            )
            return PinVerifyResult.LOCKED

        if len(entered_clean) < PIN_LEN_MIN or len(entered_clean) > PIN_LEN_MAX:
            ch.attempts += 1
            status = "wrong"
            if ch.attempts >= PIN_MAX_ATTEMPTS:
                _CHALLENGES.pop(key, None)
                status = "locked"
            logger.info(
                "%s",
                json.dumps(
                    {
                        "event": "pin_verify",
                        "telegram_id": key,
                        "status": status,
                        "attempts": ch.attempts,
                    },
                    ensure_ascii=False,
                ),
            )
            return PinVerifyResult.LOCKED if status == "locked" else PinVerifyResult.WRONG

        if _hash_pin(entered_clean) != ch.pin_hash:
            ch.attempts += 1
            status = "wrong"
            if ch.attempts >= PIN_MAX_ATTEMPTS:
                _CHALLENGES.pop(key, None)
                status = "locked"
            logger.info(
                "%s",
                json.dumps(
                    {
                        "event": "pin_verify",
                        "telegram_id": key,
                        "status": status,
                        "attempts": ch.attempts,
                    },
                    ensure_ascii=False,
                ),
            )
            return PinVerifyResult.LOCKED if status == "locked" else PinVerifyResult.WRONG

        _CHALLENGES.pop(key, None)
        logger.info(
            "%s",
            json.dumps(
                {
                    "event": "pin_verify",
                    "telegram_id": key,
                    "status": "ok",
                    "phone_normalized": ch.phone_normalized,
                },
                ensure_ascii=False,
            ),
        )
        return PinVerifyResult.OK
