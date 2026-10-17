"""Toplayıcı: iki platformdan profil ve gönderi istatistiklerini çekip SQLite'a yazar."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from . import db
from .config import Settings
from .platforms import instagram, tiktok
from .tokens import TokenStore


@dataclass
class CollectResult:
    platform: str
    ok: bool
    posts: int = 0
    message: str = ""
    profile: dict = field(default_factory=dict)


def ensure_fresh_tokens(settings: Settings, store: TokenStore) -> list[str]:
    """Süresi yaklaşan token'ları yeniler. Yapılanları açıklayan satırlar döndürür."""
    notes: list[str] = []
    ig = store.load("instagram")
    if ig:
        days = ig.expires_in_days()
        if days is not None and days < 10:
            try:
                store.save("instagram", instagram.refresh_long_lived(ig))
                notes.append("Instagram token'ı yenilendi.")
            except Exception as exc:  # yenileme başarısızsa toplama yine denenir
                notes.append(f"Instagram token yenilemesi başarısız: {exc}")
    tt = store.load("tiktok")
    if tt and settings.tiktok_client_key:
        days = tt.expires_in_days()
        if days is None or days < 0.1:  # erişim token'ı 24 saat geçerli
            try:
                store.save("tiktok", tiktok.refresh(settings.tiktok_client_key, settings.tiktok_client_secret, tt))
                notes.append("TikTok token'ı yenilendi.")
            except Exception as exc:
                notes.append(f"TikTok token yenilemesi başarısız: {exc}")
    return notes


def collect_instagram(conn: sqlite3.Connection, settings: Settings, store: TokenStore, limit: int = 50) -> CollectResult:
    token = store.load("instagram")
    if not token:
        return CollectResult("instagram", False, message="Token yok: 'beta-social token set instagram'")
    client = instagram.InstagramClient(token, settings.instagram_api_version)
    now = db.utcnow()
    profile = client.profile()
    user_id = str(profile.get("user_id") or profile.get("id"))
    db.upsert_account(conn, "instagram", user_id, profile.get("username"))
    db.add_account_snapshot(conn, "instagram", user_id, {
        "followers": profile.get("followers_count"),
        "follows": profile.get("follows_count"),
        "media_count": profile.get("media_count"),
        **{f"day_{k}": v for k, v in client.account_insights(user_id).items()},
    }, now)
    n = 0
    for media in client.iter_media(user_id, limit=limit):
        post_id = db.upsert_post(conn, "instagram", media.id, media.product_type or media.media_type,
                                 media.caption, media.permalink, media.timestamp)
        metrics = {"likes": media.like_count, "comments": media.comments_count}
        metrics.update(client.media_insights(media))
        db.add_post_metrics(conn, post_id, metrics, now)
        n += 1
    conn.commit()
    return CollectResult("instagram", True, posts=n, profile=profile)


def collect_tiktok(conn: sqlite3.Connection, settings: Settings, store: TokenStore, limit: int = 50) -> CollectResult:
    token = store.load("tiktok")
    if not token:
        return CollectResult("tiktok", False, message="Token yok: 'beta-social tiktok-auth'")
    client = tiktok.TikTokClient(token)
    now = db.utcnow()
    user = client.user_info()
    open_id = str(user.get("open_id") or (token.extra or {}).get("open_id"))
    db.upsert_account(conn, "tiktok", open_id, user.get("username") or user.get("display_name"))
    db.add_account_snapshot(conn, "tiktok", open_id, {
        "followers": user.get("follower_count"),
        "follows": user.get("following_count"),
        "likes_total": user.get("likes_count"),
        "media_count": user.get("video_count"),
    }, now)
    n = 0
    for video in client.iter_videos(limit=limit):
        post_id = db.upsert_post(conn, "tiktok", video.id, "VIDEO", video.title or video.description,
                                 video.share_url, video.published_at)
        db.add_post_metrics(conn, post_id, video.metrics(), now)
        n += 1
    conn.commit()
    return CollectResult("tiktok", True, posts=n, profile=user)


def collect_all(conn: sqlite3.Connection, settings: Settings, store: TokenStore,
                platforms: tuple[str, ...] = ("instagram", "tiktok")) -> list[CollectResult]:
    results = []
    funcs = {"instagram": collect_instagram, "tiktok": collect_tiktok}
    for name in platforms:
        try:
            results.append(funcs[name](conn, settings, store))
        except Exception as exc:
            conn.rollback()
            results.append(CollectResult(name, False, message=f"{type(exc).__name__}: {exc}"))
    return results
