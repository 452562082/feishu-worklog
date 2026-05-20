from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    date        TEXT NOT NULL,         -- YYYY-MM-DD（本地时区）
    chat_id     TEXT NOT NULL,
    chat_name   TEXT NOT NULL,
    chat_type   TEXT NOT NULL,         -- 'p2p' | 'group'
    sender      TEXT NOT NULL,
    is_self     INTEGER NOT NULL,      -- 0/1
    content     TEXT NOT NULL,
    ts          INTEGER NOT NULL,      -- unix seconds (best effort)
    seq         INTEGER NOT NULL,      -- 抓取时的会话内顺序
    fetched_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_date     ON messages(date);
CREATE INDEX IF NOT EXISTS idx_messages_chat_seq ON messages(chat_id, seq);

CREATE TABLE IF NOT EXISTS daily_runs (
    date        TEXT PRIMARY KEY,
    started_at  INTEGER NOT NULL,
    finished_at INTEGER,
    msg_count   INTEGER,
    status      TEXT NOT NULL          -- 'running' | 'ok' | 'failed'
);
"""


class Storage:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init()

    def _init(self) -> None:
        with self.conn() as c:
            # journal_mode=WAL 是持久化到 DB header 的，一次就够
            c.execute("PRAGMA journal_mode=WAL")
            c.executescript(SCHEMA)

    @contextmanager
    def conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        # synchronous 是 per-connection，每条连接都得设
        c.execute("PRAGMA synchronous=NORMAL")
        try:
            yield c
            c.commit()
        finally:
            c.close()

    def insert_messages(self, rows: Iterable[dict]) -> int:
        now = int(time.time())
        params = [
            (
                r["id"], r["date"], r["chat_id"], r["chat_name"],
                r["chat_type"], r["sender"], int(r["is_self"]),
                r["content"], int(r["ts"]), int(r["seq"]), now,
            )
            for r in rows
        ]
        if not params:
            return 0
        with self.conn() as c:
            before = c.total_changes
            c.executemany(
                """INSERT OR IGNORE INTO messages
                   (id, date, chat_id, chat_name, chat_type, sender,
                    is_self, content, ts, seq, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                params,
            )
            return c.total_changes - before

    def messages_for_date(self, date: str) -> list[dict]:
        with self.conn() as c:
            rows = c.execute(
                """SELECT * FROM messages
                   WHERE date = ?
                   ORDER BY chat_id, seq""",
                (date,),
            ).fetchall()
        return [dict(r) for r in rows]

    def start_run(self, date: str) -> None:
        with self.conn() as c:
            c.execute(
                """INSERT OR REPLACE INTO daily_runs(date, started_at, status)
                   VALUES (?, ?, 'running')""",
                (date, int(time.time())),
            )

    def finish_run(self, date: str, msg_count: int, ok: bool) -> None:
        with self.conn() as c:
            c.execute(
                """UPDATE daily_runs
                   SET finished_at=?, msg_count=?, status=?
                   WHERE date=?""",
                (int(time.time()), msg_count, "ok" if ok else "failed", date),
            )

    def cleanup_old_messages(self, cutoff_date: str) -> int:
        """删 cutoff_date 之前的消息（含敏感数据，按 db_retention_days 清理）。
        cutoff_date 形如 'YYYY-MM-DD'；同时清理 daily_runs。返回删除条数。"""
        with self.conn() as c:
            before = c.total_changes
            c.execute("DELETE FROM messages WHERE date < ?", (cutoff_date,))
            c.execute("DELETE FROM daily_runs WHERE date < ?", (cutoff_date,))
            deleted = c.total_changes - before
        if deleted > 0:
            # VACUUM 不能在事务里跑，单开连接
            v = sqlite3.connect(self.db_path)
            try:
                v.execute("VACUUM")
            finally:
                v.close()
        return deleted
