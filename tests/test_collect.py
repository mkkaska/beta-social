"""Toplayıcıyı gerçek ağ olmadan, HTTP katmanını taklit ederek dener."""

from beta_social import collect, db
from beta_social.cli import build_parser
from beta_social.config import Settings
from beta_social.http import ApiError
from beta_social.platforms import instagram as ig_mod
from beta_social.platforms import tiktok as tt_mod
from beta_social.tokens import TokenRecord, TokenStore


def fake_instagram(platform, method, url, session=None, **kw):
    params = kw.get("params", {})
    if url.endswith("/me"):
        return {"user_id": "1784", "username": "betahardware_", "followers_count": 4, "media_count": 2}
    if url.endswith("/1784/media"):
        return {"data": [
            {"id": "m2", "media_product_type": "REELS", "media_type": "VIDEO", "caption": "Kablolama",
             "timestamp": "2026-10-16T07:55:42+0000", "like_count": 2, "comments_count": 0},
            {"id": "m1", "media_product_type": "FEED", "media_type": "IMAGE", "caption": "Tanıtım",
             "timestamp": "2026-10-07T16:01:47+0000", "like_count": 2, "comments_count": 0},
        ]}
    if url.endswith("/1784/insights"):
        return {"data": [{"name": params["metric"], "total_value": {"value": 317}}]}
    if url.endswith("/m2/insights"):
        return {"data": [{"name": "views", "values": [{"value": 435}]},
                         {"name": "reels_skip_rate", "values": [{"value": 0.476}]}]}
    if url.endswith("/m1/insights"):
        # Toplu istekte geçersiz metrik → 400; tek tek istekte yalnızca reach geçerli
        if "," in params["metric"] or params["metric"] != "reach":
            raise ApiError("instagram", 400, "invalid metric")
        return {"data": [{"name": "reach", "values": [{"value": 9}]}]}
    raise AssertionError(url)


def fake_tiktok(platform, method, url, session=None, **kw):
    if url.endswith("/user/info/"):
        return {"data": {"user": {"open_id": "oid", "display_name": "betahardware",
                                  "follower_count": 1, "likes_count": 10, "video_count": 3}},
                "error": {"code": "ok"}}
    if url.endswith("/video/list/"):
        return {"data": {"videos": [{"id": 711, "title": "NAS nedir", "create_time": 1792000000,
                                     "view_count": 12100, "like_count": 10, "comment_count": 0,
                                     "share_count": 1}], "has_more": False},
                "error": {"code": "ok"}}
    raise AssertionError(url)


def test_collect_all(tmp_path, monkeypatch):
    monkeypatch.setattr(ig_mod, "request_json", fake_instagram)
    monkeypatch.setattr(tt_mod, "request_json", fake_tiktok)
    store = TokenStore(backend="file", fallback_file=tmp_path / ".tokens.json")
    store.save("instagram", TokenRecord(access_token="ig"))
    store.save("tiktok", TokenRecord(access_token="tt", extra={"open_id": "oid"}))
    conn = db.connect(tmp_path / "t.sqlite")
    db.init_db(conn)

    results = collect.collect_all(conn, Settings(), store)
    assert [(r.platform, r.ok, r.posts) for r in results] == [("instagram", True, 2), ("tiktok", True, 1)]

    latest = {(r["platform_id"], r["metric"]): r["value"] for r in db.latest_post_metrics(conn)}
    assert latest[("m2", "views")] == 435
    assert latest[("m1", "reach")] == 9          # tek tek yeniden deneme çalıştı
    assert ("m1", "views") not in latest         # geçersiz metrik atlandı
    assert latest[("711", "views")] == 12100
    snap = {(r["platform"], r["metric"]): r["value"] for r in db.latest_account_snapshot(conn)}
    assert snap[("instagram", "day_reach")] == 317 and snap[("tiktok", "likes_total")] == 10


def test_missing_token_is_reported(tmp_path):
    store = TokenStore(backend="file", fallback_file=tmp_path / ".tokens.json")
    conn = db.connect(tmp_path / "t.sqlite")
    db.init_db(conn)
    results = collect.collect_all(conn, Settings(), store)
    assert all(not r.ok and "Token yok" in r.message for r in results)


def test_cli_parser():
    p = build_parser()
    assert p.parse_args(["token", "set", "instagram", "--short-lived"]).short_lived
    assert p.parse_args(["collect", "--platform", "tiktok"]).platform == ["tiktok"]
