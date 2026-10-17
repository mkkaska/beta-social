"""Instagram API with Instagram Login istemcisi (graph.instagram.com).

Gerekli izinler:
    instagram_business_basic            profil ve gönderi listesi
    instagram_business_manage_insights  gönderi ve hesap istatistikleri
    instagram_business_content_publish  yayın (Faz 3)

Kaynak: https://developers.facebook.com/docs/instagram-platform
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

import requests

from ..http import ApiError, request_json
from ..tokens import TokenRecord

BASE = "https://graph.instagram.com"
PLATFORM = "instagram"

# Medya türüne göre geçerli istatistik metrikleri (Meta belgesine göre).
FEED_METRICS = (
    "reach", "views", "likes", "comments", "saved", "shares",
    "total_interactions", "profile_visits", "follows",
)
REELS_METRICS = (
    "reach", "views", "likes", "comments", "saved", "shares", "total_interactions",
    "ig_reels_avg_watch_time", "ig_reels_video_view_total_time", "reels_skip_rate",
)
STORY_METRICS = ("reach", "views", "replies", "shares", "total_interactions", "navigation")
ACCOUNT_METRICS = ("reach", "views", "accounts_engaged", "total_interactions", "profile_links_taps")

MEDIA_FIELDS = "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count"
PROFILE_FIELDS = "user_id,username,name,account_type,followers_count,follows_count,media_count"


@dataclass
class Media:
    id: str
    media_type: str | None
    product_type: str | None
    caption: str | None
    permalink: str | None
    timestamp: str | None
    like_count: int | None
    comments_count: int | None

    @property
    def kind(self) -> str:
        if self.product_type == "REELS":
            return "REELS"
        if self.product_type == "STORY":
            return "STORY"
        return "FEED"


def metrics_for(kind: str) -> tuple[str, ...]:
    return {"REELS": REELS_METRICS, "STORY": STORY_METRICS}.get(kind, FEED_METRICS)


def parse_insights(payload: dict) -> dict[str, float | None]:
    """Insights yanıtını {metric: değer} sözlüğüne çevirir.

    Meta iki biçim döndürebilir: `values[0].value` veya `total_value.value`.
    Sayı olmayan değerler (ör. navigation kırılımı) atlanır.
    """
    out: dict[str, float | None] = {}
    for item in payload.get("data", []):
        name = item.get("name")
        value: Any = None
        if "total_value" in item and isinstance(item["total_value"], dict):
            value = item["total_value"].get("value")
        elif item.get("values"):
            value = item["values"][0].get("value")
        if isinstance(value, (int, float)):
            out[name] = float(value)
    return out


class InstagramClient:
    def __init__(self, token: TokenRecord, api_version: str = "v25.0", session: requests.Session | None = None):
        self.token = token
        self.version = api_version
        self.session = session or requests.Session()

    def _get(self, path: str, **params: Any) -> dict:
        params["access_token"] = self.token.access_token
        return request_json(PLATFORM, "GET", f"{BASE}/{self.version}/{path.lstrip('/')}",
                            session=self.session, params=params)

    # --- profil ---
    def profile(self) -> dict:
        return self._get("me", fields=PROFILE_FIELDS)

    # --- gönderiler ---
    def iter_media(self, user_id: str, limit: int = 50) -> Iterator[Media]:
        """En yeni gönderilerden başlayarak en fazla `limit` gönderi döndürür."""
        data = self._get(f"{user_id}/media", fields=MEDIA_FIELDS, limit=min(limit, 50))
        count = 0
        while True:
            for m in data.get("data", []):
                yield Media(
                    id=m["id"],
                    media_type=m.get("media_type"),
                    product_type=m.get("media_product_type"),
                    caption=m.get("caption"),
                    permalink=m.get("permalink"),
                    timestamp=m.get("timestamp"),
                    like_count=m.get("like_count"),
                    comments_count=m.get("comments_count"),
                )
                count += 1
                if count >= limit:
                    return
            next_url = data.get("paging", {}).get("next")
            if not next_url:
                return
            data = request_json(PLATFORM, "GET", next_url, session=self.session)

    def media_insights(self, media: Media) -> dict[str, float | None]:
        """Gönderi istatistiklerini çeker.

        Bir metrik o gönderi için geçersizse Meta tüm isteği reddeder; bu
        durumda metrikler tek tek istenir ve geçersiz olanlar atlanır.
        """
        wanted = metrics_for(media.kind)
        try:
            return parse_insights(self._get(f"{media.id}/insights", metric=",".join(wanted)))
        except ApiError as exc:
            if exc.status != 400:
                raise
        out: dict[str, float | None] = {}
        for metric in wanted:
            try:
                out.update(parse_insights(self._get(f"{media.id}/insights", metric=metric)))
            except ApiError as exc:
                if exc.status != 400:
                    raise
        return out

    def account_insights(self, user_id: str, day: datetime | None = None) -> dict[str, float | None]:
        """Bir günün hesap toplamlarını döndürür (varsayılan: dün, UTC)."""
        day = day or (datetime.now(timezone.utc) - timedelta(days=1))
        since = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        until = since + timedelta(days=1)
        out: dict[str, float | None] = {}
        for metric in ACCOUNT_METRICS:
            try:
                out.update(parse_insights(self._get(
                    f"{user_id}/insights", metric=metric, period="day", metric_type="total_value",
                    since=int(since.timestamp()), until=int(until.timestamp()),
                )))
            except ApiError as exc:
                if exc.status != 400:
                    raise
        return out


# --- token işlemleri ---
def _record_from(resp: dict) -> TokenRecord:
    expires_in = resp.get("expires_in")
    expires_at = None
    if expires_in:
        expires_at = (datetime.now(timezone.utc) + timedelta(seconds=int(expires_in))).replace(microsecond=0).isoformat()
    return TokenRecord(access_token=resp["access_token"], expires_at=expires_at)


def exchange_for_long_lived(short_token: str, app_secret: str) -> TokenRecord:
    """Kısa ömürlü token'ı yaklaşık 60 günlük token'a çevirir."""
    resp = request_json(PLATFORM, "GET", f"{BASE}/access_token", params={
        "grant_type": "ig_exchange_token", "client_secret": app_secret, "access_token": short_token,
    })
    return _record_from(resp)


def refresh_long_lived(token: TokenRecord) -> TokenRecord:
    """Uzun ömürlü token'ı yeniler (token en az 24 saatlik ve süresi dolmamış olmalı)."""
    resp = request_json(PLATFORM, "GET", f"{BASE}/refresh_access_token", params={
        "grant_type": "ig_refresh_token", "access_token": token.access_token,
    })
    return _record_from(resp)
