"""Tests for the SQLite persistence layer (db.py)."""
import json
import os
import sqlite3

import pytest

import db


@pytest.fixture()
def db_env(tmp_path, monkeypatch):
    """Point db.py at a fresh temp database for each test."""
    db_path = str(tmp_path / "test.db")
    orders_path = str(tmp_path / "orders.json")
    monkeypatch.setattr(db, "DB_PATH", db_path)
    monkeypatch.setattr(db, "ORDERS_JSON", orders_path)
    db.init_db()
    return db_path, orders_path


def test_init_db_creates_tables(db_env):
    conn = sqlite3.connect(db_env[0])
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert {"user_settings", "orders", "test_account_users"} <= tables


def test_language_set_get_roundtrip(db_env):
    assert db.get_user_lang(1) == "en"
    db.set_user_lang(1, "fa")
    assert db.get_user_lang(1) == "fa"
    assert db.load_all_langs() == {1: "fa"}


def test_test_account_usage_tracking(db_env):
    assert db.has_used_test_account(42) is False
    db.mark_test_account_used(42)
    assert db.has_used_test_account(42) is True
    # Marking twice must not duplicate / error
    db.mark_test_account_used(42)


def _sample_order(order_id="abc12345"):
    return {
        "user_id": 7,
        "username": "alice",
        "first_name": "Alice",
        "plan_id": "50gb_1m",
        "plan_name": "50GB 1 Month",
        "price": 80000,
        "price_display": "80,000 Toman",
        "order_type": "regular",
        "status": "pending",
        "email": None,
        "sub_link": None,
        "screenshot_file_id": "AgA123",
        "admin_messages": {},
        "approved_by": None,
        "rejected_by": None,
        "approved_at": None,
        "rejected_at": None,
        "created_at": "2026-11-07T10:00:00",
    }


def test_save_get_order_roundtrip(db_env):
    db.save_order("abc12345", _sample_order())
    order = db.get_order("abc12345")
    assert order is not None
    assert order["user_id"] == 7
    assert order["status"] == "pending"
    assert order["admin_messages"] == {}
    assert db.get_order("nope") is None


def test_save_order_upsert(db_env):
    db.save_order("abc12345", _sample_order())
    updated = _sample_order()
    updated["status"] = "approved"
    updated["approved_at"] = "2026-11-07T11:00:00"
    db.save_order("abc12345", updated)
    order = db.get_order("abc12345")
    assert order["status"] == "approved"
    assert order["approved_at"] == "2026-11-07T11:00:00"


def test_update_order_fields(db_env):
    db.save_order("abc12345", _sample_order())
    db.update_order_fields("abc12345", status="approved", approved_by=1)
    order = db.get_order("abc12345")
    assert order["status"] == "approved"
    assert order["approved_by"] == 1
    # admin_messages must be JSON-encoded safely
    db.update_order_fields("abc12345", admin_messages={"1": {"message_id": 5}})
    assert db.get_order("abc12345")["admin_messages"] == {"1": {"message_id": 5}}


def test_get_pending_orders_oldest_first(db_env):
    o1 = _sample_order("aaa00001")
    o1["created_at"] = "2026-11-07T09:00:00"
    o2 = _sample_order("aaa00002")
    o2["created_at"] = "2026-11-07T08:00:00"
    db.save_order("aaa00001", o1)
    db.save_order("aaa00002", o2)
    pending = db.get_pending_orders()
    assert list(pending.keys()) == ["aaa00002", "aaa00001"]
    # Approved orders are not pending
    db.update_order_fields("aaa00001", status="approved")
    assert list(db.get_pending_orders().keys()) == ["aaa00002"]


def test_get_user_approved_orders(db_env):
    for oid in ("a1", "a2", "b1"):
        o = _sample_order(oid)
        if oid == "b1":
            o["user_id"] = 99
        db.save_order(oid, o)
    db.update_order_fields("a1", status="approved", approved_at="2026-11-07T12:00:00")
    db.update_order_fields("a2", status="approved", approved_at="2026-11-07T13:00:00")
    approved = db.get_user_approved_orders(7)
    assert {o["order_id"] for o in approved} == {"a1", "a2"}
    assert approved[0]["order_id"] == "a2"  # newest first


def test_migrate_orders_json(db_env):
    db_path, orders_path = db_env
    legacy = {
        "old00001": {
            "user_id": 5,
            "plan_id": "infinity_1m",
            "plan_name": "Infinity 1 Month",
            "price": 150000,
            "status": "pending",
            "created_at": "2026-07-01T00:00:00",
        }
    }
    with open(orders_path, "w", encoding="utf-8") as f:
        json.dump(legacy, f)

    db.init_db()

    order = db.get_order("old00001")
    assert order is not None
    assert order["user_id"] == 5
    assert order["plan_name"] == "Infinity 1 Month"
    # Source file renamed after migration
    assert not os.path.exists(orders_path)
    assert os.path.exists(orders_path + ".migrated")

    # Running init again must not attempt re-import
    db.init_db()
    assert db.get_order("old00001")["user_id"] == 5
