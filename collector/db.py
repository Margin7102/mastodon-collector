"""Канонічна модель + SQLite-сховище з курсорами й дедуплікацією."""
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    source     TEXT NOT NULL,
    source_id  TEXT NOT NULL,
    author     TEXT NOT NULL,
    url        TEXT NOT NULL,
    date       TEXT NOT NULL,          -- ISO-8601 UTC
    text       TEXT NOT NULL,
    media      TEXT NOT NULL DEFAULT '[]',  -- JSON-список URL
    tag        TEXT,
    fetched_at TEXT NOT NULL,
    UNIQUE (source, source_id),
    UNIQUE (url)
);
CREATE INDEX IF NOT EXISTS idx_posts_date ON posts(date);

CREATE TABLE IF NOT EXISTS cursors (
    key   TEXT PRIMARY KEY,            -- напр. 'python:newest' / 'python:oldest'
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT, finished_at TEXT,
    fetched INTEGER, inserted INTEGER, errors INTEGER, rate_limited INTEGER
);
"""


@dataclass
class Post:
    source: str
    source_id: str
    author: str
    url: str
    date: str
    text: str
    media: list[str]
    tag: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.executescript(SCHEMA)

    def insert_many(self, posts: list[Post]) -> int:
        """INSERT OR IGNORE -> ідемпотентно. Повертає кількість реально нових."""
        before = self.conn.total_changes
        with self.conn:
            self.conn.executemany(
                "INSERT OR IGNORE INTO posts (source, source_id, author, url, date, text, media, tag, fetched_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [(p.source, p.source_id, p.author, p.url, p.date, p.text,
                  json.dumps(p.media, ensure_ascii=False), p.tag, now_iso()) for p in posts],
            )
        return self.conn.total_changes - before

    def get_cursor(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM cursors WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_cursor(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO cursors(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

    def log_run(self, started: str, fetched: int, inserted: int, errors: int, limited: int) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO runs(started_at, finished_at, fetched, inserted, errors, rate_limited) VALUES (?,?,?,?,?,?)",
                (started, now_iso(), fetched, inserted, errors, limited))

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM posts").fetchone()[0]

    def export_jsonl(self, out: str, limit: int | None = None) -> int:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        q = "SELECT author, url, date, text, media, tag, source, source_id FROM posts ORDER BY date DESC"
        if limit:
            q += f" LIMIT {int(limit)}"
        n = 0
        with open(out, "w", encoding="utf-8") as f:
            for a, u, d, t, m, tag, s, sid in self.conn.execute(q):
                f.write(json.dumps({"author": a, "url": u, "date": d, "text": t,
                                    "media": json.loads(m), "tag": tag, "source": s, "source_id": sid},
                                   ensure_ascii=False) + "\n")
                n += 1
        return n

    def stats(self) -> dict:
        c = self.conn
        return {
            "total": self.count(),
            "by_tag": dict(c.execute("SELECT tag, COUNT(*) FROM posts GROUP BY tag").fetchall()),
            "top_authors": c.execute("SELECT author, COUNT(*) n FROM posts GROUP BY author ORDER BY n DESC LIMIT 5").fetchall(),
            "date_range": c.execute("SELECT MIN(date), MAX(date) FROM posts").fetchone(),
            "runs": c.execute("SELECT COUNT(*), SUM(rate_limited), SUM(errors) FROM runs").fetchone(),
        }

    def dashboard_data(self) -> dict:
        """Дані для HTML-дашборда."""
        c = self.conn
        total = self.count()
        authors = c.execute("SELECT COUNT(DISTINCT author) FROM posts").fetchone()[0]
        with_media = c.execute("SELECT COUNT(*) FROM posts WHERE media != '[]'").fetchone()[0]
        days = c.execute("SELECT COUNT(DISTINCT substr(date,1,10)) FROM posts").fetchone()[0]
        n, label = (10, "днями") if days >= 4 else (13, "годинами")
        timeline = c.execute(
            f"SELECT substr(date,1,{n}) k, COUNT(*) FROM posts GROUP BY k ORDER BY k").fetchall()
        runs_n, limited, errors = c.execute(
            "SELECT COUNT(*), COALESCE(SUM(rate_limited),0), COALESCE(SUM(errors),0) FROM runs").fetchone()
        return {
            "total": total, "authors": authors,
            "media_pct": (with_media / total * 100) if total else 0.0,
            "by_tag": [(t or "(без тегу)", k) for t, k in c.execute(
                "SELECT tag, COUNT(*) k FROM posts GROUP BY tag ORDER BY k DESC").fetchall()],
            "top_authors": c.execute(
                "SELECT author, COUNT(*) k FROM posts GROUP BY author ORDER BY k DESC, author LIMIT 10").fetchall(),
            "date_range": c.execute("SELECT MIN(date), MAX(date) FROM posts").fetchone(),
            "timeline": timeline, "bucket_label": label,
            "runs_n": runs_n, "rate_limited": limited, "errors": errors,
            "runs": c.execute(
                "SELECT started_at, fetched, inserted, errors, rate_limited FROM runs ORDER BY id DESC LIMIT 10").fetchall(),
        }
