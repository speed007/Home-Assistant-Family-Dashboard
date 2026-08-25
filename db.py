"""
SQLite-backed persistence layer for the Family Hub Telegram bot.
Handles local storage for shopping lists, notes, meal plans, and custom appointments.
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

DB_PATH = os.getenv("DB_PATH", "/data/dashboard.db")


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS shopping_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                item TEXT NOT NULL,
                item_lower TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_shopping_item_lower
                ON shopping_items(item_lower);

            CREATE TABLE IF NOT EXISTS meal_overrides (
                day_key TEXT PRIMARY KEY,
                meal_text TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS daily_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                note_time TEXT NOT NULL,
                text TEXT NOT NULL,
                author TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                date TEXT,
                time TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bot_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """
        )
        conn.commit()

    with _connect() as conn:
        try:
            conn.execute("ALTER TABLE daily_notes ADD COLUMN author TEXT NOT NULL DEFAULT ''")
            conn.commit()
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE bot_messages ADD COLUMN pinned INTEGER NOT NULL DEFAULT 0")
            conn.commit()
        except sqlite3.OperationalError:
            pass


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds") + "Z"


# ---------------------------------------------------------------- Shopping

def add_shopping(item: str) -> bool:
    with _connect() as conn:
        try:
            conn.execute(
                "INSERT INTO shopping_items (item, item_lower, created_at) VALUES (?, ?, ?)",
                (item, item.lower(), _now()),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False


def get_shopping() -> list[str]:
    with _connect() as conn:
        rows = conn.execute("SELECT item FROM shopping_items ORDER BY id ASC").fetchall()
        return [r["item"] for r in rows]


def get_shopping_with_ids() -> list[dict]:
    with _connect() as conn:
        return conn.execute(
            "SELECT id, item FROM shopping_items ORDER BY id ASC"
        ).fetchall()


def delete_shopping_item_by_index(list_index: int) -> bool:
    items = get_shopping_with_ids()
    if list_index < 1 or list_index > len(items):
        return False
    target_id = items[list_index - 1]["id"]
    with _connect() as conn:
        conn.execute("DELETE FROM shopping_items WHERE id = ?", (target_id,))
        conn.commit()
        return True


def delete_shopping_item(item_name: str) -> bool:
    target = item_name.lower().strip()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM shopping_items WHERE item_lower = ?", (target,))
        conn.commit()
        if cursor.rowcount > 0:
            return True

    with _connect() as conn:
        row = conn.execute(
            "SELECT id FROM shopping_items WHERE item_lower LIKE ? ORDER BY id DESC LIMIT 1",
            (f"%{target}%",),
        ).fetchone()
        if not row:
            return False
        conn.execute("DELETE FROM shopping_items WHERE id = ?", (row["id"],))
        conn.commit()
        return True


def clear_shopping():
    with _connect() as conn:
        conn.execute("DELETE FROM shopping_items")
        conn.commit()


# -------------------------------------------------------------------- Meals

def set_meal(day_key: str, meal_text: str):
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO meal_overrides (day_key, meal_text, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(day_key) DO UPDATE SET
                meal_text = excluded.meal_text,
                updated_at = excluded.updated_at
            """,
            (day_key, meal_text, _now()),
        )
        conn.commit()


def get_meals() -> dict:
    with _connect() as conn:
        rows = conn.execute("SELECT day_key, meal_text FROM meal_overrides").fetchall()
        return {r["day_key"]: r["meal_text"] for r in rows}


def clear_meals():
    with _connect() as conn:
        conn.execute("DELETE FROM meal_overrides")
        conn.commit()


# --------------------------------------------------------------------- Notes

def add_daily_note(text: str, author: str = ""):
    note_time = datetime.now(timezone.utc).strftime("%H:%M")
    with _connect() as conn:
        conn.execute(
            "INSERT INTO daily_notes (note_time, text, author, created_at) VALUES (?, ?, ?, ?)",
            (note_time, text, author, _now()),
        )
        conn.commit()


def get_daily_notes() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, note_time AS time, text, author FROM daily_notes ORDER BY id ASC"
        ).fetchall()
        notes_list = []
        for index, row in enumerate(rows, start=1):
            note_dict = dict(row)
            note_dict["index"] = index
            notes_list.append(note_dict)
        return notes_list


def delete_note_by_index(index: int) -> bool:
    notes = get_daily_notes()
    match = next((n for n in notes if n["index"] == index), None)
    if not match:
        return False
    with _connect() as conn:
        conn.execute("DELETE FROM daily_notes WHERE id = ?", (match["id"],))
        conn.commit()
        return True


def delete_note_by_text(fragment: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM daily_notes WHERE LOWER(text) LIKE ?", (f"%{fragment.lower()}%",)
        )
        conn.commit()
        return cursor.rowcount > 0


def clear_daily_notes():
    with _connect() as conn:
        conn.execute("DELETE FROM daily_notes")
        conn.commit()


# ------------------------------------------------------------- Appointments

def add_appointment(title: str, date: str | None = None, time: str | None = None):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO appointments (title, date, time, created_at) VALUES (?, ?, ?, ?)",
            (title, date, time, _now()),
        )
        conn.commit()


def get_appointments() -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, title, date, time FROM appointments "
            "ORDER BY date IS NULL, date ASC, time ASC, id ASC"
        ).fetchall()
        appts_list = []
        for index, row in enumerate(rows, start=1):
            appt_dict = dict(row)
            appt_dict["index"] = index
            appts_list.append(appt_dict)
        return appts_list


def delete_appointment_by_index(index: int) -> bool:
    appts = get_appointments()
    match = next((a for a in appts if a["index"] == index), None)
    if not match:
        return False
    with _connect() as conn:
        conn.execute("DELETE FROM appointments WHERE id = ?", (match["id"],))
        conn.commit()
        return True


def delete_appointment_by_text(fragment: str) -> bool:
    with _connect() as conn:
        cursor = conn.execute(
            "DELETE FROM appointments WHERE LOWER(title) LIKE ?", (f"%{fragment.lower()}%",)
        )
        conn.commit()
        return cursor.rowcount > 0


def prune_expired_appointments():
    today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with _connect() as conn:
        conn.execute("DELETE FROM appointments WHERE date IS NOT NULL AND date < ?", (today_iso,))
        conn.commit()


# -------------------------------------------------- Bot message tracking

def add_bot_message(message_id: int, chat_id: int):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO bot_messages (message_id, chat_id, created_at) VALUES (?, ?, ?)",
            (message_id, chat_id, _now()),
        )
        conn.commit()


def mark_message_pinned(message_id: int, chat_id: int):
    with _connect() as conn:
        conn.execute(
            "UPDATE bot_messages SET pinned = 1 WHERE message_id = ? AND chat_id = ?",
            (message_id, chat_id),
        )
        conn.commit()


def get_old_bot_messages(days: int = 3) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, message_id, chat_id, pinned FROM bot_messages WHERE created_at < ?",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]


def delete_bot_message_record(row_id: int):
    with _connect() as conn:
        conn.execute("DELETE FROM bot_messages WHERE id = ?", (row_id,))
        conn.commit()


def clear_bot_messages():
    with _connect() as conn:
        conn.execute("DELETE FROM bot_messages")
        conn.commit()


def clear_appointments():
    with _connect() as conn:
        conn.execute("DELETE FROM appointments")
        conn.commit()
