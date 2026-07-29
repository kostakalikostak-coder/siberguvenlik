"""
Vercel serverless fonksiyonu — "Raporu Sıfırla & Yeniden Üret" uç noktası
(/api/reset_regenerate).

Anasayfadaki pop-up buraya POST eder:
    { "password": "...", "action": "reset_regenerate" }

Yaptığı TEK iş: şifreyi doğrular ve GitHub Actions "Günlük Rapor" workflow'unu
`workflow_dispatch` ile, `reset_today=true` input'uyla TETİKLER. Gerçek reset +
taze üretim işini main.py (_reset_today_state) Actions içinde yapar — bu uç
nokta hiçbir dosya yazmaz, LLM çağırmaz; sadece tetikleyicidir (hızlı döner).

Gerekli ortam değişkenleri (Vercel — manual_add ile AYNI değişkenler yeterli):
  MANUAL_ADD_PASSWORD  — pop-up'ta girilecek şifre (manual_add ile ortak).
  GH_TOKEN             — bu repoda GitHub Actions'ı tetikleyebilen token.
                         ⚠️ workflow_dispatch için token'ın Actions: read/write
                         (fine-grained PAT) VEYA klasik token'da `workflow`
                         kapsamı OLMALIDIR. (manual_add'ın kullandığı contents:
                         write TEK BAŞINA yetmez — token'a Actions yetkisi ekle.)
"""
import os
import json
import hmac
import time
from http.server import BaseHTTPRequestHandler

import requests

REPO = "siberguvenlikhaberler/siberguvenlik"
BRANCH = "main"
WORKFLOW_FILE = "daily.yml"
GH_API = "https://api.github.com"

# Tarayıcı (github.io) → fonksiyon (vercel.app) çapraz-köken istekleri için CORS.
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


# ── Kaba-kuvvet yavaşlatma ────────────────────────────────────────────────
# Uç nokta şifreyle korunuyor ama deneme sınırı YOKTU: CORS "*" olduğu için
# şifre sınırsız ve paralel denenebiliyordu. Başarılı olan saldırgan yayımlanan
# rapora içerik yazabilir veya sınırsız workflow tetikleyip LLM maliyeti
# üretebilirdi.
#
# DÜRÜST SINIR: Vercel serverless durumsuzdur; bu sayaç yalnızca SICAK örneğin
# belleğinde yaşar ve örnekler arasında paylaşılmaz. Yani kesin bir kilit değil,
# maliyeti yükselten bir yavaşlatmadır. Asıl koruma, yanlış şifrede uygulanan
# sabit gecikmedir: dağıtık denemede bile her deneme en az _FAIL_DELAY_SEC
# sürer, bu da saniyede binlerce deneme yapılmasını engeller.
_ATTEMPTS = {}
_WINDOW_SEC = 300        # 5 dakikalık pencere
_MAX_ATTEMPTS = 8        # pencere başına en fazla başarısız deneme
_FAIL_DELAY_SEC = 1.0    # yanlış şifrede sabit gecikme


def client_ip(headers):
    """Vercel proxy arkasındaki gerçek istemci IP'si."""
    fwd = (headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return fwd or (headers.get("x-real-ip") or "bilinmeyen").strip()


def too_many_attempts(ip):
    now = time.time()
    hist = [t for t in _ATTEMPTS.get(ip, []) if now - t < _WINDOW_SEC]
    _ATTEMPTS[ip] = hist
    return len(hist) >= _MAX_ATTEMPTS


def record_failure(ip):
    _ATTEMPTS.setdefault(ip, []).append(time.time())


def password_ok(given, expected):
    """Sabit-zamanlı şifre karşılaştırması (ASCII-dışı ve tip-güvenli).

    hmac.compare_digest str girdilerde YALNIZCA ASCII kabul eder: şifrede
    Türkçe karakter varsa TypeError atar, dıştaki except onu yakalar ve uç nokta
    "Beklenmeyen hata" ile 500 döner — yani doğru şifreyle bile giriş yapılamaz
    ve sebebi görünmez. Ayrıca JSON'dan gelen değer str olmayabilir (sayı/liste).
    Her iki tarafı UTF-8 bayta çevirerek bu iki sorunu da kapat.
    """
    if not isinstance(given, str) or not isinstance(expected, str):
        return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def process(payload, ip="bilinmeyen"):
    """POST gövdesini işler → (http_code, result_dict) döndürür."""
    if too_many_attempts(ip):
        return 429, {"ok": False, "error": "Çok fazla hatalı deneme. "
                     "Lütfen birkaç dakika sonra tekrar deneyin."}

    raw_pw = payload.get("password")
    password = raw_pw.strip() if isinstance(raw_pw, str) else ""
    if not password:
        return 400, {"ok": False, "error": "Şifre giriniz."}

    expected = os.getenv("MANUAL_ADD_PASSWORD", "")
    if not expected:
        return 500, {"ok": False, "error": "Sunucuda MANUAL_ADD_PASSWORD tanımlı değil."}
    if not password_ok(password, expected):
        record_failure(ip)
        time.sleep(_FAIL_DELAY_SEC)   # kaba kuvvet hızını düşür
        return 403, {"ok": False, "error": "Şifre yanlış."}

    token = os.getenv("GH_TOKEN", "")
    if not token:
        return 500, {"ok": False, "error": "Sunucuda GH_TOKEN tanımlı değil."}

    url = f"{GH_API}/repos/{REPO}/actions/workflows/{WORKFLOW_FILE}/dispatches"
    try:
        r = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "siberguvenlik-reset",
            },
            # reset_today input'u main.py'de RESET_TODAY env'ine geçer → bugünün
            # ham/rapor/cron-işareti silinir, linkler+arşivde bugün cerrahi
            # çıkarılır, pipeline SIFIRDAN taze fetch + rapor üretir.
            json={"ref": BRANCH, "inputs": {"reset_today": "true"}},
            timeout=20,
        )
    except requests.RequestException as e:
        return 502, {"ok": False, "error": f"GitHub'a bağlanılamadı: {str(e)[:160]}"}

    if r.status_code == 204:
        return 200, {
            "ok": True,
            "message": ("Reset + taze üretim tetiklendi. Rapor GitHub Actions'ta "
                        "sıfırdan üretiliyor; ~10 dakika içinde güncellenecek. "
                        "Sayfayı biraz sonra sert yenileyin (Ctrl+F5)."),
        }
    # 401/403 → token yetkisiz (Actions yetkisi yok); 404 → workflow/branch yok
    detail = ""
    try:
        detail = (r.json() or {}).get("message", "")
    except Exception:
        detail = r.text[:160]
    hint = ""
    if r.status_code in (401, 403):
        hint = (" (GH_TOKEN'da Actions: read/write yetkisi yok — fine-grained "
                "PAT'te Actions'ı yaz'a açın veya klasik token'a `workflow` "
                "kapsamı ekleyin.)")
    return 502, {"ok": False,
                 "error": f"GitHub dispatch başarısız (HTTP {r.status_code}): {detail}{hint}"}


class handler(BaseHTTPRequestHandler):
    def _send(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        for k, v in CORS_HEADERS.items():
            self.send_header(k, v)
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            self._send(400, {"ok": False, "error": "Geçersiz istek gövdesi."})
            return
        try:
            code, result = process(payload, client_ip(self.headers))
        except Exception as e:
            code, result = 500, {"ok": False, "error": f"Beklenmeyen hata: {str(e)[:200]}"}
        self._send(code, result)
