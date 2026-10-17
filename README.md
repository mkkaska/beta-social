# beta-social

Instagram ve TikTok hesaplarını **resmi API'lerle** takip eden, günlük rapor üreten ve (sonraki fazlarda) onaylanmış içeriği yayınlayan, Windows üzerinde yerel çalışan bir otomasyon aracı.

Beta Hardware'in sosyal medya hesapları için yazıldı; ayarlar `.env` dosyasından okunduğu için başka hesaplara da uyarlanabilir.

## Ne yapar

| Faz | Durum | İçerik |
| --- | --- | --- |
| 0 · Kurulum | ✅ | Ayarlar, güvenli token saklama, SQLite şeması, `check` komutu |
| 1 · Veri ve rapor | ✅ (ilk sürüm) | Instagram + TikTok istatistikleri, günlük Markdown + CSV rapor |
| 2 · İçerik hattı | ⏳ | Kuyruk klasörü, marka bandı (ffmpeg), onay durumları |
| 3 · Instagram yayını | ⏳ | Reels / hikaye / gönderi, tekrar deneme, çift yayına karşı kilit |
| 4 · TikTok yayını + onay | ⏳ | TikTok doğrudan yayın (denetim sonrası), Telegram onay botu |

## İlkeler

- **Sadece resmi API.** Şifreyle giriş yapan gayriresmi kütüphaneler kullanılmaz.
- **Otomatik takip / beğeni / DM yok.**
- **Token'lar koda ve GitHub'a girmez.** Windows Kimlik Yöneticisi'nde (Credential Manager) saklanır.
- **Onaysız yayın yok** (Faz 2'den itibaren).

## Kurulum (Windows)

1. **Python 3.12+** — <https://www.python.org/downloads/> (kurulumda *Add python.exe to PATH* işaretli olsun)
2. **Git** ve isteğe bağlı **ffmpeg** (Faz 2 için):
   ```powershell
   winget install Git.Git
   winget install Gyan.FFmpeg
   ```
3. Proje klasöründe sanal ortam ve paket kurulumu:
   ```powershell
   cd $HOME\Documents\GitHub\beta-social
   python -m venv .venv
   .venv\Scripts\activate
   pip install -e ".[dev]"
   ```
4. Ayar dosyası:
   ```powershell
   copy .env.example .env
   notepad .env
   ```
5. Veritabanı:
   ```powershell
   beta-social init
   ```

## Hesapları bağlama

### Instagram

Meta uygulamasında **Instagram API with Instagram Login** ürünü ekli olmalı ve şu izinler verilmiş olmalı:
`instagram_business_basic`, `instagram_business_manage_insights` (istatistikler için şart), `instagram_business_content_publish` (Faz 3).

Panelden ürettiğin token'ı kaydet (ekranda görünmez):

```powershell
beta-social token set instagram            # panelden alınan uzun ömürlü token
beta-social token set instagram --short-lived   # kısa ömürlü token'ı 60 günlüğe çevirir (APP_SECRET gerekir)
```

Token, süresinin dolmasına 10 günden az kaldığında `daily` komutu tarafından otomatik yenilenir.

### TikTok

TikTok geliştirici uygulamasında **Login Kit (Desktop)** ve **Content Posting API** ürünleri, şu kapsamlar:
`user.info.basic`, `user.info.stats`, `video.list` (ve Faz 4 için `video.publish`).
Yönlendirme adresi olarak örneğin `http://localhost:3455/callback/` kaydet ve aynısını `.env` içindeki `TIKTOK_REDIRECT_URI`'ye yaz.

```powershell
beta-social tiktok-auth
```

Tarayıcıda izin sayfası açılır; onay verince token otomatik alınır ve saklanır. Erişim token'ı 24 saat, yenileme token'ı 365 gün geçerlidir; yenileme otomatiktir.

## Kullanım

```powershell
beta-social check      # iki hesabın profilini çeker (Faz 0 kapısı)
beta-social collect    # istatistikleri veritabanına yazar
beta-social report     # reports\YYYY-MM-DD.md ve .csv
beta-social daily      # token yenile + collect + report
beta-social token status
```

### Her sabah otomatik çalıştırma

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register_daily_task.ps1 -Time 08:30
```

Windows Görev Zamanlayıcı'ya `BetaSocialDaily` görevi eklenir. Bilgisayar o saatte uykudaysa açıldığında çalışır. Kayıtlar `data\daily.log` dosyasına yazılır.

## Proje yapısı

```
src/beta_social/
  cli.py            komut satırı
  config.py         .env ayarları
  tokens.py         token deposu (Windows Kimlik Yöneticisi / keyring)
  db.py             SQLite şeması ve yardımcılar
  http.py           istekler, 429/5xx'te bekleyip tekrar deneme
  collect.py        toplayıcı
  report.py         günlük rapor
  platforms/
    instagram.py    graph.instagram.com istemcisi
    tiktok.py       Login Kit (PKCE), Display API
tests/              ağ kullanmayan testler (pytest)
scripts/            Windows zamanlayıcı kurulumu
```

## Test

```powershell
pytest
```

## Lisans

MIT
