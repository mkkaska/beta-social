from datetime import datetime, timedelta, timezone

import pytest

from beta_social import db
from beta_social.platforms import instagram, tiktok
from beta_social.report import write_report
from beta_social.tokens import TokenRecord, TokenStore


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "t.sqlite")
    db.init_db(c)
    return c


def test_schema_and_upserts(conn):
    pid = db.upsert_post(conn, "instagram", "m1", "REELS", "Kablolama", "https://x/1", "2026-10-16T07:55:42+0000")
    pid2 = db.upsert_post(conn, "instagram", "m1", "REELS", "Kablolama v2", "https://x/1", None)
    assert pid == pid2  # aynı gönderi tekrar eklenmez
    db.add_post_metrics(conn, pid, {"views": 379, "reach": 315, "saved": None}, "2026-10-16T12:00:00+00:00")
    db.add_post_metrics(conn, pid, {"views": 435}, "2026-10-17T06:00:00+00:00")
    conn.commit()
    latest = {r["metric"]: r["value"] for r in db.latest_post_metrics(conn)}
    assert latest == {"views": 435, "reach": 315}  # None yazılmaz, her metriğin son değeri gelir
    row = conn.execute("SELECT caption, published_at FROM posts WHERE id=?", (pid,)).fetchone()
    assert row["caption"] == "Kablolama v2" and row["published_at"].startswith("2026-10-16")


def test_queue_status_constraint(conn):
    now = db.utcnow()
    with pytest.raises(Exception):
        conn.execute("INSERT INTO queue(platform, source_path, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                     ("instagram", "a.mp4", "yayinda", now, now))


def test_run_log_records_failure(conn):
    with pytest.raises(ValueError):
        with db.run_log(conn, "collect"):
            raise ValueError("boom")
    row = conn.execute("SELECT ok, message FROM runs").fetchone()
    assert row["ok"] == 0 and "boom" in row["message"]


def test_parse_insights_both_shapes():
    payload = {"data": [
        {"name": "reach", "values": [{"value": 315}]},
        {"name": "views", "total_value": {"value": 435}},
        {"name": "navigation", "values": [{"value": {"tap_forward": 3}}]},
    ]}
    assert instagram.parse_insights(payload) == {"reach": 315.0, "views": 435.0}


def test_metrics_for_kind():
    reel = instagram.Media("1", "VIDEO", "REELS", None, None, None, None, None)
    feed = instagram.Media("2", "IMAGE", "FEED", None, None, None, None, None)
    assert "reels_skip_rate" in instagram.metrics_for(reel.kind)
    assert "profile_visits" in instagram.metrics_for(feed.kind)


def test_tiktok_pkce_is_hex_sha256():
    v = tiktok.make_code_verifier()
    assert 43 <= len(v) <= 128
    ch = tiktok.code_challenge(v)
    assert len(ch) == 64 and all(c in "0123456789abcdef" for c in ch)


def test_tiktok_callback_state_check():
    url = "http://localhost:3455/callback/?code=abc%2A123&state=s1&scopes=video.list"
    assert tiktok.parse_callback(url, "s1") == "abc*123"
    with pytest.raises(RuntimeError):
        tiktok.parse_callback(url, "baska")


def test_token_store_file_fallback(tmp_path):
    store = TokenStore(backend="file", fallback_file=tmp_path / ".tokens.json")
    exp = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    store.save("instagram", TokenRecord(access_token="secret", expires_at=exp))
    rec = store.load("instagram")
    assert rec.access_token == "secret" and 29 < rec.expires_in_days() <= 30
    store.delete("instagram")
    assert store.load("instagram") is None


def test_report_files(conn, tmp_path):
    pid = db.upsert_post(conn, "instagram", "m1", "REELS", "Kabininiz bu kadar düzenli mi?\nikinci satır",
                         "https://x/1", "2026-10-16T07:55:42+0000")
    db.add_post_metrics(conn, pid, {"views": 435, "reach": 315, "likes": 2, "saved": 1,
                                    "reels_skip_rate": 0.476, "ig_reels_avg_watch_time": 4181}, db.utcnow())
    tid = db.upsert_post(conn, "tiktok", "v1", "VIDEO", "NAS nedir", "https://t/1", "2026-10-14T07:00:00+00:00")
    db.add_post_metrics(conn, tid, {"views": 12100, "likes": 10}, db.utcnow())
    db.add_account_snapshot(conn, "instagram", "1", {"followers": 4, "media_count": 5, "day_reach": 317}, db.utcnow())
    conn.commit()
    md, csv_path = write_report(conn, tmp_path / "reports", datetime(2026, 10, 17))
    text = md.read_text(encoding="utf-8")
    assert "Takipçi 4" in text and "Dünkü erişim 317" in text
    assert "%48" in text and "4.2 sn" in text and "12.100" in text
    assert "ikinci satır" not in text  # açıklamanın yalnızca ilk satırı
    assert csv_path.read_text(encoding="utf-8-sig").splitlines()[0].startswith("platform,id,type")
