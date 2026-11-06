"""Tests for bot.py helpers: formatting, i18n, and the admin_only guard."""
import asyncio
import types

import pytest

import bot


# ---------- formatting helpers ----------

def test_format_bytes():
    assert bot.format_bytes(0) == "0.00 B"
    assert bot.format_bytes(1024) == "1.00 KB"
    assert bot.format_bytes(1024 ** 2) == "1.00 MB"
    assert bot.format_bytes(1024 ** 3) == "1.00 GB"
    assert bot.format_bytes(1024 ** 4) == "1.00 TB"
    assert bot.format_bytes(1024 ** 5) == "1.00 PB"


def test_format_ts():
    from datetime import datetime

    assert bot.format_ts(0) == "Never"
    # 2026-01-01 00:00 UTC in ms; expected in the server's local timezone
    expected = datetime.fromtimestamp(1767225600).strftime("%Y-%m-%d %H:%M")
    assert bot.format_ts(1767225600000) == expected


# ---------- i18n ----------

def test_t_translation_lookup():
    bot.USER_LANG[1001] = "en"
    assert bot.t(1001, "buy_vpn") == bot.translations["en"]["buy_vpn"]
    bot.USER_LANG[1002] = "fa"
    assert bot.t(1002, "buy_vpn") == bot.translations["fa"]["buy_vpn"]


def test_t_fallback_to_english_for_unknown_lang():
    bot.USER_LANG[1003] = "xx"
    assert bot.t(1003, "buy_vpn") == bot.translations["en"]["buy_vpn"]
    bot.USER_LANG.pop(1003, None)


def test_t_missing_key_returns_key():
    bot.USER_LANG[1004] = "en"
    assert bot.t(1004, "definitely_not_a_key") == "definitely_not_a_key"


def test_t_format_kwargs():
    bot.USER_LANG[1005] = "en"
    text = bot.t(1005, "order_approved", order_id="xyz")
    assert "xyz" in text


# ---------- plans ----------

def test_load_plans_returns_list():
    plans = bot.load_plans()
    assert isinstance(plans, list)
    assert len(plans) > 0
    assert "id" in plans[0] and "name" in plans[0] and "price_display" in plans[0]


# ---------- admin_only decorator ----------

class DummyUser:
    def __init__(self, user_id):
        self.id = user_id


class DummyMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)


class DummyUpdate:
    def __init__(self, user_id, message=None, callback_query=None):
        self._user = DummyUser(user_id) if message or callback_query else DummyUser(user_id)
        self.message = message
        self.callback_query = callback_query

    @property
    def effective_user(self):
        return self._user


def test_admin_only_blocks_non_admin():
    message = DummyMessage()
    update = DummyUpdate(12345, message=message)
    context = types.SimpleNamespace()

    @bot.admin_only
    async def handler(update, context):
        return "secret data"

    assert asyncio.run(handler(update, context)) is None
    assert message.replies and "Access denied" in message.replies[0]


def test_admin_only_allows_admin():
    original = bot.ADMIN_USER_IDS
    bot.ADMIN_USER_IDS = [1000]
    try:
        update = DummyUpdate(1000, message=DummyMessage())
        context = types.SimpleNamespace()

        @bot.admin_only
        async def handler(update, context):
            return "secret data"

        assert asyncio.run(handler(update, context)) == "secret data"
    finally:
        bot.ADMIN_USER_IDS = original


def test_admin_only_passes_extra_args():
    """Callback handlers invoke admin-only functions with extra positional args
    like inbound_id. The wrapper MUST forward them."""
    original = bot.ADMIN_USER_IDS
    bot.ADMIN_USER_IDS = [1000]
    try:
        update = DummyUpdate(1000, message=DummyMessage())
        context = types.SimpleNamespace()

        @bot.admin_only
        async def handler(update, context, inbound_id, flag=True):
            return (inbound_id, flag)

        assert asyncio.run(handler(update, context, 42, flag=False)) == (42, False)
    finally:
        bot.ADMIN_USER_IDS = original
