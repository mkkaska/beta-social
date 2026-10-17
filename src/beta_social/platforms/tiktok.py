"""TikTok istemcisi: Login Kit (masaüstü, PKCE), Display API, kullanıcı istatistikleri.

Gerekli kapsamlar (scope):
    user.info.basic   profil
    user.info.stats   takipçi, toplam beğeni, video sayısı
    video.list        videolar ve izlenme / beğeni / yorum / paylaşım
    video.publish     doğrudan yayın (Faz 4, denetim sonrası)

Kaynaklar:
    https://developers.tiktok.com/doc/login-kit-desktop
    https://developers.tiktok.com/doc/oauth-user-access-token-management
    https://developers.tiktok.com/doc/tiktok-api-v2-video-object
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import urllib.parse
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Iterator

import requests

from ..http import ApiError, request_json
from ..tokens import TokenRecord

AUTHORIZE_URL = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL = "https://open.tiktokapis.com/v2/oauth/token/"
API = "https://open.tiktokapis.com/v2"
PLATFORM = "tiktok"

USER_FIELDS = "open_id,display_name,username,follower_count,following_count,likes_count,video_count"
VIDEO_FIELDS = "id,title,video_description,create_time,duration,share_url,view_count,like_count,comment_count,share_count"


# --- PKCE ---
def make_code_verifier() -> str:
    # 43-128 karakter, URL güvenli
    return secrets.token_urlsafe(64)[:96]


def code_challenge(verifier: str) -> str:
    """TikTok masaüstü: SHA256'nın HEX kodlaması (base64url DEĞİL)."""
    return hashlib.sha256(verifier.encode("ascii")).hexdigest()


def build_authorize_url(client_key: str, redirect_uri: str, scopes: tuple[str, ...], state: str, verifier: str) -> str:
    query = {
        "client_key": client_key,
        "response_type": "code",
        "scope": ",".join(scopes),
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": code_challenge(verifier),
        "code_challenge_method": "S256",
    }
    return f"{AUTHORIZE_URL}?{urllib.parse.urlencode(query)}"


def parse_callback(url: str, expected_state: str) -> str:
    """Yönlendirme adresinden `code` değerini çıkarır, `state` değerini doğrular."""
    params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    if params.get("error"):
        raise RuntimeError(f"TikTok izni reddedildi: {params.get('error_description', params['error'])[0]}")
    if params.get("state", [""])[0] != expected_state:
        raise RuntimeError("state değeri eşleşmiyor; işlem güvenlik nedeniyle durduruldu.")
    code = params.get("code", [""])[0]
    if not code:
        raise RuntimeError("Yönlendirme adresinde 'code' bulunamadı.")
    return code


def wait_for_callback(redirect_uri: str, timeout: int = 300) -> str:
    """localhost'ta kısa ömürlü bir sunucu açıp TikTok'un yönlendirmesini yakalar."""
    parsed = urllib.parse.urlparse(redirect_uri)
    if parsed.hostname not in ("localhost", "127.0.0.1") or not parsed.port:
        raise RuntimeError("Otomatik yakalama için yönlendirme adresi localhost ve sabit port içermeli.")
    captured: dict[str, str] = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            captured["url"] = f"{parsed.scheme}://{parsed.netloc}{self.path}"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("TikTok bağlantısı alındı, bu pencereyi kapatabilirsin.".encode("utf-8"))

        def log_message(self, *args: Any) -> None:
            pass

    server = HTTPServer((parsed.hostname, parsed.port), Handler)
    server.timeout = timeout
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    thread.join(timeout)
    server.server_close()
    if "url" not in captured:
        raise TimeoutError("TikTok yönlendirmesi zamanında gelmedi.")
    return captured["url"]


def _record_from(resp: dict) -> TokenRecord:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    return TokenRecord(
        access_token=resp["access_token"],
        expires_at=(now + timedelta(seconds=int(resp.get("expires_in", 86400)))).isoformat(),
        refresh_token=resp.get("refresh_token"),
        refresh_expires_at=(now + timedelta(seconds=int(resp.get("refresh_expires_in", 0)))).isoformat()
        if resp.get("refresh_expires_in") else None,
        extra={"open_id": resp.get("open_id"), "scope": resp.get("scope")},
    )


def _token_post(data: dict) -> dict:
    resp = request_json(PLATFORM, "POST", TOKEN_URL, data=data,
                        headers={"Content-Type": "application/x-www-form-urlencoded"})
    if "access_token" not in resp:
        raise ApiError(PLATFORM, 400, resp.get("error_description") or str(resp), resp)
    return resp


def exchange_code(client_key: str, client_secret: str, code: str, redirect_uri: str, verifier: str) -> TokenRecord:
    return _record_from(_token_post({
        "client_key": client_key,
        "client_secret": client_secret,
        "code": urllib.parse.unquote(code),
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code_verifier": verifier,
    }))


def refresh(client_key: str, client_secret: str, token: TokenRecord) -> TokenRecord:
    if not token.refresh_token:
        raise RuntimeError("TikTok yenileme token'ı yok; 'beta-social tiktok-auth' ile yeniden bağlan.")
    new = _record_from(_token_post({
        "client_key": client_key,
        "client_secret": client_secret,
        "grant_type": "refresh_token",
        "refresh_token": token.refresh_token,
    }))
    if not new.refresh_token:  # TikTok aynı yenileme token'ını geri dönmeyebilir
        new.refresh_token = token.refresh_token
        new.refresh_expires_at = token.refresh_expires_at
    return new


def authorize_interactive(client_key: str, client_secret: str, redirect_uri: str, scopes: tuple[str, ...],
                          open_browser: bool = True) -> TokenRecord:
    """Tarayıcıda TikTok iznini açar, dönüşü yakalar, token'ı alır."""
    state = secrets.token_urlsafe(16)
    verifier = make_code_verifier()
    url = build_authorize_url(client_key, redirect_uri, scopes, state, verifier)
    print("TikTok izin sayfası açılıyor. Açılmazsa bu adresi tarayıcına yapıştır:\n", url, sep="")
    if open_browser:
        webbrowser.open(url)
    try:
        callback = wait_for_callback(redirect_uri)
    except (RuntimeError, OSError, TimeoutError) as exc:
        print(f"Otomatik yakalama olmadı ({exc}).")
        callback = input("Tarayıcının adres çubuğundaki tam adresi buraya yapıştır: ").strip()
    code = parse_callback(callback, state)
    return exchange_code(client_key, client_secret, code, redirect_uri, verifier)


# --- API ---
@dataclass
class Video:
    id: str
    title: str | None
    description: str | None
    create_time: int | None
    duration: int | None
    share_url: str | None
    view_count: int | None
    like_count: int | None
    comment_count: int | None
    share_count: int | None

    @property
    def published_at(self) -> str | None:
        if not self.create_time:
            return None
        return datetime.fromtimestamp(self.create_time, tz=timezone.utc).isoformat()

    def metrics(self) -> dict[str, float | None]:
        return {
            "views": self.view_count,
            "likes": self.like_count,
            "comments": self.comment_count,
            "shares": self.share_count,
        }


def _check(resp: dict) -> dict:
    err = resp.get("error") or {}
    if err.get("code") not in (None, "ok"):
        raise ApiError(PLATFORM, 400, err.get("message") or err.get("code"), resp)
    return resp.get("data") or {}


class TikTokClient:
    def __init__(self, token: TokenRecord, session: requests.Session | None = None):
        self.token = token
        self.session = session or requests.Session()

    @property
    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.token.access_token}"}

    def user_info(self) -> dict:
        resp = request_json(PLATFORM, "GET", f"{API}/user/info/", session=self.session,
                            headers=self._headers, params={"fields": USER_FIELDS})
        return _check(resp).get("user", {})

    def iter_videos(self, limit: int = 50) -> Iterator[Video]:
        cursor: int | None = None
        count = 0
        while True:
            body: dict[str, Any] = {"max_count": 20}
            if cursor is not None:
                body["cursor"] = cursor
            resp = request_json(PLATFORM, "POST", f"{API}/video/list/", session=self.session,
                                headers={**self._headers, "Content-Type": "application/json"},
                                params={"fields": VIDEO_FIELDS}, json=body)
            data = _check(resp)
            for v in data.get("videos", []):
                yield Video(
                    id=str(v["id"]),
                    title=v.get("title"),
                    description=v.get("video_description"),
                    create_time=v.get("create_time"),
                    duration=v.get("duration"),
                    share_url=v.get("share_url"),
                    view_count=v.get("view_count"),
                    like_count=v.get("like_count"),
                    comment_count=v.get("comment_count"),
                    share_count=v.get("share_count"),
                )
                count += 1
                if count >= limit:
                    return
            if not data.get("has_more"):
                return
            cursor = data.get("cursor")
