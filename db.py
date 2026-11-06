import sqlite3
import json
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "bot.db")
ORDERS_JSON = os.path.join(os.path.dirname(__file__), "orders.json")


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables and migrate legacy data."""
    with _get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id  INTEGER PRIMARY KEY,
                language TEXT    NOT NULL DEFAULT 'en'
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                order_id         TEXT    PRIMARY KEY,
                user_id          INTEGER NOT NULL,
                username         TEXT,
                first_name       TEXT,
                plan_id          TEXT,
                plan_name        TEXT,
                price            REAL    DEFAULT 0,
                price_display    TEXT,
                order_type       TEXT    DEFAULT 'regular',
                status           TEXT    NOT NULL DEFAULT 'pending',
                email            TEXT,
                sub_link         TEXT,
                screenshot_file_id TEXT,
                admin_messages   TEXT    DEFAULT '{}',
                approved_by      INTEGER,
                rejected_by      INTEGER,
                approved_at      TEXT,
                rejected_at      TEXT,
                created_at       TEXT    NOT NULL
            )
        """)

        # One row per user who has ever received a test account
        conn.execute("""
            CREATE TABLE IF NOT EXISTS test_account_users (
                user_id    INTEGER PRIMARY KEY,
                granted_at TEXT    NOT NULL
            )
        """)

        conn.commit()

    _migrate_orders_json()


# ==================== LANGUAGE ====================

def get_user_lang(user_id: int) -> str:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT language FROM user_settings WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["language"] if row else "en"


def set_user_lang(user_id: int, lang: str):
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_settings (user_id, language)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET language = excluded.language
            """,
            (user_id, lang),
        )
        conn.commit()


def load_all_langs() -> dict[int, str]:
    """Load all stored user languages into a dict for fast in-memory access."""
    with _get_conn() as conn:
        rows = conn.execute("SELECT user_id, language FROM user_settings").fetchall()
    return {row["user_id"]: row["language"] for row in rows}


# ==================== TEST ACCOUNT ====================

def has_used_test_account(user_id: int) -> bool:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM test_account_users WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row is not None


def mark_test_account_used(user_id: int):
    from datetime import datetime
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO test_account_users (user_id, granted_at)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (user_id, datetime.now().isoformat()),
        )
        conn.commit()


# ==================== ORDERS ====================

def _migrate_orders_json():
    """One-time migration: import orders.json into SQLite if not already done."""
    if not os.path.exists(ORDERS_JSON):
        return
    try:
        with open(ORDERS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data:
            return
        with _get_conn() as conn:
            for order_id, o in data.items():
                exists = conn.execute(
                    "SELECT 1 FROM orders WHERE order_id = ?", (order_id,)
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    """
                    INSERT INTO orders (
                        order_id, user_id, username, first_name,
                        plan_id, plan_name, price, price_display,
                        order_type, status, email, sub_link,
                        screenshot_file_id, admin_messages,
                        approved_by, rejected_by, approved_at, rejected_at, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        order_id,
                        o.get("user_id"),
                        o.get("username"),
                        o.get("first_name"),
                        o.get("plan_id"),
                        o.get("plan_name"),
                        o.get("price", 0),
                        o.get("price_display"),
                        o.get("order_type", "regular"),
                        o.get("status", "pending"),
                        o.get("email"),
                        o.get("sub_link"),
                        o.get("screenshot_file_id"),
                        json.dumps(o.get("admin_messages", {})),
                        o.get("approved_by"),
                        o.get("rejected_by"),
                        o.get("approved_at"),
                        o.get("rejected_at"),
                        o.get("created_at", ""),
                    ),
                )
            conn.commit()
        os.rename(ORDERS_JSON, ORDERS_JSON + ".migrated")
        print(f"[DB] Migrated {len(data)} orders from orders.json to SQLite.")
    except Exception as e:
        print(f"[DB] orders.json migration error: {e}")


def _row_to_order(row) -> dict:
    d = dict(row)
    d["admin_messages"] = json.loads(d.get("admin_messages") or "{}")
    return d


def get_order(order_id: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
    return _row_to_order(row) if row else None


def get_all_orders() -> dict:
    with _get_conn() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    return {row["order_id"]: _row_to_order(row) for row in rows}


def get_pending_orders() -> dict:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE status = 'pending' ORDER BY created_at ASC"
        ).fetchall()
    return {row["order_id"]: _row_to_order(row) for row in rows}


def get_user_approved_orders(user_id: int) -> list[dict]:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id = ? AND status = 'approved' ORDER BY approved_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_order(r) for r in rows]


def save_order(order_id: str, order: dict):
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO orders (
                order_id, user_id, username, first_name,
                plan_id, plan_name, price, price_display,
                order_type, status, email, sub_link,
                screenshot_file_id, admin_messages,
                approved_by, rejected_by, approved_at, rejected_at, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(order_id) DO UPDATE SET
                username           = excluded.username,
                first_name         = excluded.first_name,
                plan_id            = excluded.plan_id,
                plan_name          = excluded.plan_name,
                price              = excluded.price,
                price_display      = excluded.price_display,
                order_type         = excluded.order_type,
                status             = excluded.status,
                email              = excluded.email,
                sub_link           = excluded.sub_link,
                screenshot_file_id = excluded.screenshot_file_id,
                admin_messages     = excluded.admin_messages,
                approved_by        = excluded.approved_by,
                rejected_by        = excluded.rejected_by,
                approved_at        = excluded.approved_at,
                rejected_at        = excluded.rejected_at
            """,
            (
                order_id,
                order.get("user_id"),
                order.get("username"),
                order.get("first_name"),
                order.get("plan_id"),
                order.get("plan_name"),
                order.get("price", 0),
                order.get("price_display"),
                order.get("order_type", "regular"),
                order.get("status", "pending"),
                order.get("email"),
                order.get("sub_link"),
                order.get("screenshot_file_id"),
                json.dumps(order.get("admin_messages", {})),
                order.get("approved_by"),
                order.get("rejected_by"),
                order.get("approved_at"),
                order.get("rejected_at"),
                order.get("created_at", ""),
            ),
        )
        conn.commit()


def update_order_fields(order_id: str, **fields):
    """Patch specific fields on an order row."""
    if not fields:
        return
    if "admin_messages" in fields:
        fields["admin_messages"] = json.dumps(fields["admin_messages"])
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [order_id]
    with _get_conn() as conn:
        conn.execute(
            f"UPDATE orders SET {set_clause} WHERE order_id = ?", values
        )
        conn.commit()



# ==================== LANGUAGE ====================

def get_user_lang(user_id: int) -> str:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT language FROM user_settings WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["language"] if row else "en"


def set_user_lang(user_id: int, lang: str):
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO user_settings (user_id, language)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET language = excluded.language
            """,
            (user_id, lang),
        )
        conn.commit()


def load_all_langs() -> dict[int, str]:
    """Load all stored user languages into a dict for fast in-memory access."""
    with _get_conn() as conn:
        rows = conn.execute("SELECT user_id, language FROM user_settings").fetchall()
    return {row["user_id"]: row["language"] for row in rows}


# ==================== ORDERS ====================

def _migrate_orders_json():
    """One-time migration: import orders.json into SQLite if not already done."""
    if not os.path.exists(ORDERS_JSON):
        return
    try:
        with open(ORDERS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data:
            return
        with _get_conn() as conn:
            for order_id, o in data.items():
                exists = conn.execute(
                    "SELECT 1 FROM orders WHERE order_id = ?", (order_id,)
                ).fetchone()
                if exists:
                    continue
                conn.execute(
                    """
                    INSERT INTO orders (
                        order_id, user_id, username, first_name,
                        plan_id, plan_name, price, price_display,
                        order_type, status, email, sub_link,
                        screenshot_file_id, admin_messages,
                        approved_by, rejected_by, approved_at, rejected_at, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        order_id,
                        o.get("user_id"),
                        o.get("username"),
                        o.get("first_name"),
                        o.get("plan_id"),
                        o.get("plan_name"),
                        o.get("price", 0),
                        o.get("price_display"),
                        o.get("order_type", "regular"),
                        o.get("status", "pending"),
                        o.get("email"),
                        o.get("sub_link"),
                        o.get("screenshot_file_id"),
                        json.dumps(o.get("admin_messages", {})),
                        o.get("approved_by"),
                        o.get("rejected_by"),
                        o.get("approved_at"),
                        o.get("rejected_at"),
                        o.get("created_at", ""),
                    ),
                )
            conn.commit()
        # Rename the file so migration doesn't run again
        os.rename(ORDERS_JSON, ORDERS_JSON + ".migrated")
        print(f"[DB] Migrated {len(data)} orders from orders.json to SQLite.")
    except Exception as e:
        print(f"[DB] orders.json migration error: {e}")


def _row_to_order(row) -> dict:
    d = dict(row)
    d["admin_messages"] = json.loads(d.get("admin_messages") or "{}")
    return d


def get_order(order_id: str) -> dict | None:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
    return _row_to_order(row) if row else None


def get_all_orders() -> dict:
    """Return {order_id: order_dict} — mirrors the old load_orders() shape."""
    with _get_conn() as conn:
        rows = conn.execute("SELECT * FROM orders ORDER BY created_at DESC").fetchall()
    return {row["order_id"]: _row_to_order(row) for row in rows}


def get_pending_orders() -> dict:
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE status = 'pending' ORDER BY created_at ASC"
        ).fetchall()
    return {row["order_id"]: _row_to_order(row) for row in rows}


def get_user_approved_orders(user_id: int) -> list[dict]:
    """Return approved orders for a specific user, newest first."""
    with _get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM orders WHERE user_id = ? AND status = 'approved' ORDER BY approved_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_order(r) for r in rows]


def save_order(order_id: str, order: dict):
    """Upsert a full order dict."""
    with _get_conn() as conn:
        conn.execute(
            """
            INSERT INTO orders (
                order_id, user_id, username, first_name,
                plan_id, plan_name, price, price_display,
                order_type, status, email, sub_link,
                screenshot_file_id, admin_messages,
                approved_by, rejected_by, approved_at, rejected_at, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(order_id) DO UPDATE SET
                username           = excluded.username,
                first_name         = excluded.first_name,
                plan_id            = excluded.plan_id,
                plan_name          = excluded.plan_name,
                price              = excluded.price,
                price_display      = excluded.price_display,
                order_type         = excluded.order_type,
                status             = excluded.status,
                email              = excluded.email,
                sub_link           = excluded.sub_link,
                screenshot_file_id = excluded.screenshot_file_id,
                admin_messages     = excluded.admin_messages,
                approved_by        = excluded.approved_by,
                rejected_by        = excluded.rejected_by,
                approved_at        = excluded.approved_at,
                rejected_at        = excluded.rejected_at
            """,
            (
                order_id,
                order.get("user_id"),
                order.get("username"),
                order.get("first_name"),
                order.get("plan_id"),
                order.get("plan_name"),
                order.get("price", 0),
                order.get("price_display"),
                order.get("order_type", "regular"),
                order.get("status", "pending"),
                order.get("email"),
                order.get("sub_link"),
                order.get("screenshot_file_id"),
                json.dumps(order.get("admin_messages", {})),
                order.get("approved_by"),
                order.get("rejected_by"),
                order.get("approved_at"),
                order.get("rejected_at"),
                order.get("created_at", ""),
            ),
        )
        conn.commit()


def update_order_fields(order_id: str, **fields):
    """Patch specific fields on an order row."""
    if not fields:
        return
    # admin_messages must be JSON-encoded
    if "admin_messages" in fields:
        fields["admin_messages"] = json.dumps(fields["admin_messages"])
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [order_id]
    with _get_conn() as conn:
        conn.execute(
            f"UPDATE orders SET {set_clause} WHERE order_id = ?", values
        )
        conn.commit()
