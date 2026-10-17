"""SQLite veritabanı: şema ve yardımcı fonksiyonlar.

Tasarım ilkesi: modüller birbirini çağırmaz, durumu buradan okur ve buraya
yazar. Metrikler "uzun" biçimde (metric, value) tutulur; böylece platformlar
yeni metrik eklediğinde şema değişmez.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS accounts (
    platform    TEXT NOT NULL,              -- 'instagram' | 'tiktok'
    account_id  TEXT NOT NULL,
    username    TEXT,
    updated_at  TEXT NOT NULL,
    PRIMARY KEY (platform, account_id)
);

CREATE TABLE IF NOT EXISTS account_snapshots (
    platform     TEXT NOT NULL,
    account_id   TEXT NOT NULL,
    captured_at  TEXT NOT NULL,             -- ISO-8601 UTC
    metric       TEXT NOT NULL,             -- followers, media_count, likes_total ...
    value        REAL,
    PRIMARY KEY (platform, account_id, captured_at, metric)
);

CREATE TABLE IF NOT EXISTS posts (
    id              INTEGER PRIMARY KEY,
    platform        TEXT NOT NULL,
    platform_id     TEXT NOT NULL,          -- Instagram media id / TikTok video id
    media_type      TEXT,
    caption         TEXT,
    permalink       TEXT,
    published_at    TEXT,
    first_seen_at   TEXT NOT NULL,
    UNIQUE (platform, platform_id)
);

CREATE TABLE IF NOT EXISTS post_metrics (
    post_id      INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    captured_at  TEXT NOT NULL,
    metric       TEXT NOT NULL,             -- views, reach, likes, comments, shares, saves ...
    value        REAL,
    PRIMARY KEY (post_id, captured_at, metric)
);

CREATE TABLE IF NOT EXISTS queue (
    id                 INTEGER PRIMARY KEY,
    platform           TEXT NOT NULL,
    kind               TEXT NOT NULL DEFAULT 'reel',  -- reel | story | image | carousel | video
    source_path        TEXT NOT NULL,
    caption            TEXT,
    status             TEXT NOT NULL DEFAULT 'draft'
                       CHECK (status IN ('draft','approved','publishing','published','failed','rejected')),
    scheduled_at       TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    published_id       TEXT,
    error              TEXT
);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY,
    job          TEXT NOT NULL,             -- collect | publish | report | refresh
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    ok           INTEGER,
    message      TEXT
);

CREATE INDEX IF NOT EXISTS idx_post_metrics_metric ON post_metrics(metric, captured_at);
CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status, scheduled_at);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(SCHEMA_VERSION),),
    )
    conn.commit()


@contextmanager
def run_log(conn: sqlite3.Connection, job: str) -> Iterator[dict]:
    """Bir işin başlangıç/bitiş kaydını tutar. Hata olursa kaydedip yeniden fırlatır."""
    cur = conn.execute("INSERT INTO runs(job, started_at) VALUES (?, ?)", (job, utcnow()))
    run_id = cur.lastrowid
    conn.commit()
    state = {"message": ""}
    try:
        yield state
    except Exception as exc:
        conn.execute(
            "UPDATE runs SET finished_at=?, ok=0, message=? WHERE id=?",
            (utcnow(), f"{type(exc).__name__}: {exc}", run_id),
        )
        conn.commit()
        raise
    conn.execute(
        "UPDATE runs SET finished_at=?, ok=1, message=? WHERE id=?",
        (utcnow(), state["message"], run_id),
    )
    conn.commit()


def upsert_account(conn: sqlite3.Connection, platform: str, account_id: str, username: str | None) -> None:
    conn.execute(
        "INSERT INTO accounts(platform, account_id, username, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(platform, account_id) DO UPDATE SET username=excluded.username, updated_at=excluded.updated_at",
        (platform, account_id, username, utcnow()),
    )


def add_account_snapshot(
    conn: sqlite3.Connection, platform: str, account_id: str, metrics: dict[str, float | None], captured_at: str
) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO account_snapshots(platform, account_id, captured_at, metric, value) VALUES (?,?,?,?,?)",
        [(platform, account_id, captured_at, k, v) for k, v in metrics.items() if v is not None],
    )


def upsert_post(
    conn: sqlite3.Connection,
    platform: str,
    platform_id: str,
    media_type: str | None,
    caption: str | None,
    permalink: str | None,
    published_at: str | None,
) -> int:
    conn.execute(
        "INSERT INTO posts(platform, platform_id, media_type, caption, permalink, published_at, first_seen_at) "
        "VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(platform, platform_id) DO UPDATE SET "
        "media_type=excluded.media_type, caption=excluded.caption, permalink=excluded.permalink, "
        "published_at=COALESCE(excluded.published_at, posts.published_at)",
        (platform, platform_id, media_type, caption, permalink, published_at, utcnow()),
    )
    row = conn.execute(
        "SELECT id FROM posts WHERE platform=? AND platform_id=?", (platform, platform_id)
    ).fetchone()
    return int(row["id"])


def add_post_metrics(
    conn: sqlite3.Connection, post_id: int, metrics: dict[str, float | None], captured_at: str
) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO post_metrics(post_id, captured_at, metric, value) VALUES (?,?,?,?)",
        [(post_id, captured_at, k, v) for k, v in metrics.items() if v is not None],
    )


def latest_post_metrics(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Her gönderinin her metriği için en son ölçülen değeri döndürür."""
    return conn.execute(
        """
        SELECT p.platform, p.platform_id, p.media_type, p.caption, p.permalink, p.published_at,
               m.metric, m.value, m.captured_at
        FROM posts p
        JOIN post_metrics m ON m.post_id = p.id
        WHERE m.captured_at = (
            SELECT MAX(m2.captured_at) FROM post_metrics m2
            WHERE m2.post_id = m.post_id AND m2.metric = m.metric
        )
        ORDER BY p.published_at DESC, p.platform, m.metric
        """
    ).fetchall()


def latest_account_snapshot(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT a.platform, a.account_id, a.metric, a.value, a.captured_at
        FROM account_snapshots a
        WHERE a.captured_at = (
            SELECT MAX(b.captured_at) FROM account_snapshots b
            WHERE b.platform = a.platform AND b.account_id = a.account_id
        )
        ORDER BY a.platform, a.metric
        """
    ).fetchall()
