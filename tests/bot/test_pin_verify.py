"""Тесты одноразового PIN (memory store, SHA-256)."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from magister_checking.bot.pin_verify import (
    PIN_TTL_SEC,
    PinVerifyResult,
    clear_challenge,
    issue_pin_challenge,
    verify_pin_challenge,
)


class PinVerifyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self) -> None:  # noqa: N802
        await asyncio.gather(clear_challenge("1"), clear_challenge("2"))

    async def test_issue_and_verify_ok(self) -> None:
        await clear_challenge("1")
        issued = await issue_pin_challenge("1", "+79001234567")
        self.assertIsNotNone(issued)
        assert issued is not None
        plain, length = issued
        self.assertEqual(len(plain), length)
        self.assertGreaterEqual(length, 4)
        self.assertLessEqual(length, 6)

        bad = "".join("1" if c == "0" else "0" for c in plain)
        self.assertEqual(await verify_pin_challenge("1", bad), PinVerifyResult.WRONG)

        self.assertEqual(await verify_pin_challenge("1", plain), PinVerifyResult.OK)
        self.assertEqual(
            await verify_pin_challenge("1", plain), PinVerifyResult.NO_CHALLENGE
        )

    async def test_lockout_three_wrong(self) -> None:
        await clear_challenge("2")
        issued = await issue_pin_challenge("2", "+79007654321")
        self.assertIsNotNone(issued)
        assert issued is not None
        plain, _length = issued
        bad = "".join("1" if c == "0" else "0" for c in plain)

        self.assertEqual(await verify_pin_challenge("2", bad), PinVerifyResult.WRONG)
        self.assertEqual(await verify_pin_challenge("2", bad), PinVerifyResult.WRONG)
        self.assertEqual(await verify_pin_challenge("2", bad), PinVerifyResult.LOCKED)
        self.assertEqual(
            await verify_pin_challenge("2", plain), PinVerifyResult.NO_CHALLENGE
        )

    async def test_expired(self) -> None:
        await clear_challenge("9")
        t_start = 1_700_000_000.0
        t_late = t_start + PIN_TTL_SEC + 1.0
        seq = [t_start, t_start, t_late]

        with patch(
            "magister_checking.bot.pin_verify.time.time",
            side_effect=seq,
        ):
            issued = await issue_pin_challenge("9", "+79001112233")
            self.assertIsNotNone(issued)
            assert issued is not None
            plain, _ = issued
            status = await verify_pin_challenge("9", plain)

        self.assertEqual(status, PinVerifyResult.EXPIRED)

    async def test_issue_requires_phone_and_telegram(self) -> None:
        await clear_challenge("3")
        self.assertIsNone(await issue_pin_challenge("", "+79005553535"))
        self.assertIsNone(await issue_pin_challenge("3", ""))
