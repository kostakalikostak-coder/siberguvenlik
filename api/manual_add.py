"""
Vercel serverless fonksiyonu — Manuel Haber Ekle uç noktası (/api/manual-add).

TAMAMEN SUNUCU TARAFINDA çalışır. Anasayfadaki pop-up buraya POST eder:
    { "password": "...", "url": "https://...", "remove_index": 0|1|2 }

Yapılan iş:
  1. Şifre (MANUAL_ADD_PASSWORD env) sunucuda doğrulanır.
  2. URL çekilir, makale metni çıkarılır.
  3. BİZİM sistemin promptu (src.config.get_deep_analysis_prompt) + LLM istemcisi
     (src.llm_client) ile, raporun geri kalanıyla BİREBİR aynı yöntem/format/üslupta
     Türkçe başlık + paragraf üretilir.
  4. main.py'deki kritik-kart kalıbının AYNISIYLA bir <div class="top3-card"> üretilir.
  5. docs/index.html VE o günün arşiv raporunda (docs/raporlar/YYYY-MM-DD.html)
     YALNIZCA işaretlenen kritik kart değiştirilir — başka hiçbir şey değişmez.
  6. Her iki dosya GitHub API ile main'e commit edilir.
  7. Üretilen kart HTML'i tarayıcıya döner; pop-up sayfadaki kartı anında değiştirir.

Gerekli ortam değişkenleri (Vercel):
  OPENROUTER_API_KEY   — LLM çağrısı (Actions'taki ile aynı)
  LLM_PROVIDER         — "openrouter"
  MANUAL_ADD_PASSWORD  — pop-up'ta girilecek şifre
  GH_TOKEN             — bu repo'ya contents:write yetkili GitHub token (commit için)
"""
import os
import re
import sys
import json
import base64
import hmac
import socket
import ipaddress
from datetime import datetime
from html import escape as _html_escape
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlparse

# Repo kökünü import yoluna ekle (src/ paketine erişim için)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
from bs4 import BeautifulSoup

from src.config import HEADERS, get_deep_analysis_prompt, get_executive_summary_prompt
from src import llm_client
from src.http_utils import requests_get_with_retry

REPO = "siberguvenlikhaberler/siberguvenlik"
BRANCH = "main"
GH_API = "https://api.github.com"
INDEX_PATH = "docs/index.html"

# Tarayıcı (github.io) → fonksiyon (vercel.app) çapraz-köken istekleri için CORS.
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
}


# ─────────────────────────────────────────────────────────────────────────────
# Makale metni çekme (main.py: HaberSistemi.fetch_full_article mantığının
# tek-iş parçacıklı, genel-seçicili sadeleştirilmiş kopyası)
# ─────────────────────────────────────────────────────────────────────────────
def assert_public_url(url):
    """SSRF koruması: URL şemasını ve çözümlenen TÜM IP'leri doğrular.

    Uç nokta şifre korumalı olsa bile, kullanıcı verdiği URL sunucu tarafında
    çekildiğinden iç ağ / bulut metadata uçları (169.254.169.254 vb.) hedef
    alınabilir. Bu yüzden yalnızca http/https'e ve PUBLIC (özel/loopback/
    link-local/reserved olmayan) IP'lere izin verilir. Sorun varsa ValueError.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("Yalnızca http/https URL'lerine izin verilir.")
    host = parsed.hostname
    if not host:
        raise ValueError("URL host bilgisi yok.")
    try:
        infos = socket.getaddrinfo(host, parsed.port or 0, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise ValueError(f"Host çözümlenemedi: {e}")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            raise ValueError("Özel/iç ağ adreslerine istek yapılamaz.")


def fetch_article(url):
    # Redirect'ler ELLE izlenir: requests'in otomatik takibi her hop'u
    # doğrulamadan çeker; public bir URL 302 ile iç ağa / bulut metadata'ya
    # (169.254.169.254) yönlendirilerek assert_public_url atlatılabilirdi.
    # Her hop'ta şema + çözümlenen IP'ler yeniden doğrulanır.
    for _ in range(5):
        assert_public_url(url)
        r = requests_get_with_retry(url, headers=HEADERS, timeout=(5, 12),
                                    stream=True, allow_redirects=False)
        if r.is_redirect or r.is_permanent_redirect:
            location = r.headers.get("Location", "")
            r.close()
            if not location:
                raise ValueError("Yönlendirme hedefi (Location) boş.")
            url = requests.compat.urljoin(url, location)
            continue
        break
    else:
        raise ValueError("Çok fazla yönlendirme (5+).")
    chunks, total = [], 0
    for chunk in r.iter_content(chunk_size=8192):
        chunks.append(chunk)
        total += len(chunk)
        if total > 800_000:
            break
    r.close()
    raw = b"".join(chunks).decode(r.encoding or "utf-8", errors="replace")
    soup = BeautifulSoup(raw, "html.parser")
    domain = urlparse(url).netloc.replace("www.", "")

    # Gövde metnini kirleten yapısal öğeleri en baştan ayıkla (nav/menü/altbilgi
    # vb.). Bunlar kalırsa "20+ karakter" filtresini geçen menü/footer bağlantıları
    # metne karışıp hem gürültü yapar hem de yanlış öğenin seçilmesine yol açar.
    for junk in soup.find_all(["script", "style", "noscript", "nav", "header",
                               "footer", "aside", "form"]):
        junk.decompose()

    _SKIP = ("cookie", "subscribe", "newsletter", "abone ol", "çerez",
             "gizlilik politika", "tüm hakları", "all rights reserved",
             "advertisement", "reklam")

    def _extract(element):
        if not element:
            return ""
        parts, seen = [], set()
        for p in element.find_all(["p", "h1", "h2", "h3", "h4", "li", "blockquote"]):
            t = " ".join(p.get_text().split())  # iç boşlukları normalize et
            if len(t) <= 20 or any(x in t.lower() for x in _SKIP):
                continue
            if t in seen:  # tekrar eden öğeleri (ör. iki kez basılmış başlık) atla
                continue
            seen.add(t)
            parts.append(t)
        return "\n\n".join(parts)

    # 1) Anlamsal ana içerik etiketleri
    text = ""
    for tag in ["article", "main"]:
        el = soup.find(tag)
        if el:
            text = _extract(el)
            if len(text.split()) >= 50:
                break

    # 2) Sık kullanılan içerik kapsayıcıları: class VEYA id'de tipik kalıplar,
    #    div dışında section/figure gibi etiketleri de kapsar. Birden çok aday
    #    bulunursa EN UZUN metni veren seçilir (gövde genelde en zengin bloktur).
    if len(text.split()) < 50:
        _PAT = ("content", "article", "article-body", "story", "entry",
                "post", "haber", "icerik", "metin", "detay", "main-text",
                "news-body", "body-text", "text-body")

        def _looks_like_content(val):
            return val and any(x in str(val).lower() for x in _PAT)

        candidates = soup.find_all(
            ["div", "section"],
            attrs={"class": _looks_like_content},
        ) + soup.find_all(
            ["div", "section"],
            attrs={"id": _looks_like_content},
        )
        best = text
        for el in candidates:
            cand = _extract(el)
            if len(cand.split()) > len(best.split()):
                best = cand
        text = best

    # 3) Son çare: tüm sayfadaki <p>'ler (yapısı kalıba uymayan siteler için)
    if len(text.split()) < 50:
        fallback = _extract(soup.body or soup)
        if len(fallback.split()) > len(text.split()):
            text = fallback

    # 4) Hâlâ kısaysa meta açıklama/og:description ile destekle — JS ile render
    #    edilen sayfalarda en azından özet metni yakalamayı sağlar.
    if len(text.split()) < 50:
        metas = []
        for attr, key in (("name", "description"), ("property", "og:description"),
                          ("name", "twitter:description")):
            m = soup.find("meta", attrs={attr: key})
            if m and m.get("content"):
                metas.append(" ".join(m["content"].split()))
        if metas:
            text = (text + "\n\n" + "\n\n".join(metas)).strip()

    text = text.replace("\t", " ").replace("\r", "")
    return text, domain


# ─────────────────────────────────────────────────────────────────────────────
# LLM ile başlık + paragraf üretimi — raporun geri kalanıyla AYNI prompt/format
# ─────────────────────────────────────────────────────────────────────────────
def generate_content(url, full_text, report_date):
    block = (
        f"=== HABER ID: 1 ===\n"
        f"Kaynak: Manuel\n"
        f"Başlık: \n"
        f"Tarih: {report_date}\n"
        f"Link: {url}\n\n"
        f"TAM METİN:\n{full_text}\n"
    )
    model = os.getenv("OPENROUTER_MODEL", "") or "google/gemini-3-flash-preview (varsayılan)"
    data = llm_client.generate_json(
        get_deep_analysis_prompt(block),
        max_output_tokens=4096,
        label="ManuelEkle",
    )
    if not data:
        return None, None, (
            f"LLM yanıt döndürmedi. OPENROUTER_API_KEY geçerli mi ve "
            f"OPENROUTER_MODEL ('{model}') hesabında erişilebilir mi? "
            f"(Vercel env + redeploy gerekir.)"
        )

    # LLM çıktısı farklı biçimlerde gelebilir; hepsini özyinelemeli tara:
    #   {tr_title, paragraph}
    #   {"1": {tr_title, paragraph}}
    #   [{tr_title, paragraph}]
    #   [{"1": {tr_title, paragraph}}]   ← gözlemlenen biçim
    # İlk geçerli (tr_title/paragraph içeren) sözlük kabul edilir.
    def _find_entry(node, depth=0):
        if depth > 6:
            return None
        if isinstance(node, dict):
            if "tr_title" in node or "paragraph" in node:
                return node
            for v in node.values():
                found = _find_entry(v, depth + 1)
                if found:
                    return found
        elif isinstance(node, list):
            for v in node:
                found = _find_entry(v, depth + 1)
                if found:
                    return found
        return None

    entry = _find_entry(data)
    if not entry:
        preview = list(data)[:5] if isinstance(data, (dict, list)) else str(data)[:160]
        return None, None, f"LLM beklenmeyen biçim döndürdü: {preview}"
    return (entry.get("tr_title") or "").strip(), (entry.get("paragraph") or "").strip(), None


def _safe_href(link):
    """href attribute'una girecek bağlantıyı güvenli hale getirir: yalnızca
    http/https şemasına izin verir (javascript: vb. reddedilir), tırnak dahil
    escape eder. main.py'deki _safe_content korumasının manuel araçtaki dengi."""
    link = (link or "").strip()
    if not re.match(r"^https?://", link, re.IGNORECASE):
        return "#"
    return _html_escape(link, quote=True)


# ─────────────────────────────────────────────────────────────────────────────
# Kritik kart HTML'i — main.py:1275 kalıbının BİREBİR aynısı (NATO rozeti yok)
# paragraph_is_html: rapordan çıkarılan (decode_contents ile alınmış, zaten
# escape'li) paragraf yeniden escape edilirse çift-escape olur; o yol True geçer.
# ─────────────────────────────────────────────────────────────────────────────
def build_card_html(tr_title, paragraph, link, domain, art_date,
                    paragraph_is_html=False):
    tr_title = _html_escape(tr_title or "")
    if not paragraph_is_html:
        paragraph = _html_escape(paragraph or "")
    link = _safe_href(link)
    domain = _html_escape(domain or "")
    art_date = _html_escape(art_date or "")
    return (
        '                <div class="top3-card">\n'
        '                    <div class="top3-card-title">'
        f'<a href="{link}" target="_blank" style="color:inherit;text-decoration:none;">'
        f"{tr_title}</a></div>\n"
        f'                    <p class="top3-card-paragraph">{paragraph}</p>\n'
        '                    <p class="source"><b>(XXXXXXX, AÇIK - '
        f'<a href="{link}" target="_blank">{domain}</a>, {art_date})</b></p>\n'
        "                </div>\n"
    )


# NOT: main.py:create_html kart şablonu 16 boşluk girintiyle üretir; ancak
# girinti ileride değişirse regex SESSİZCE kırılmasın diye baştaki/kapanıştaki
# boşluk esnek bırakıldı ([ \t]*). Kapanış </div> kendi satırında olmalıdır.
_CARD_RE = re.compile(
    r'[ \t]*<div class="top3-card">.*?\n[ \t]*</div>\n',
    re.DOTALL,
)


# Rapordaki "diğer haber" (.news-item) bloğu — _CARD_RE ile aynı mantık: dış
# <div> kapanışı KENDİ satırında (indentasyonlu) olduğundan, news-title'ın
# satır-içi </div>'i değil, dış kapanış yakalanır. id'ye göre tekil eşleşir.
def _news_item_re(news_id):
    return re.compile(
        r'[ \t]*<div class="news-item[^"]*" id="' + re.escape(news_id) + r'">'
        r'.*?\n[ \t]*</div>\n',
        re.DOTALL,
    )


def extract_news_item(html, news_id):
    """Verilen news-item bloğundan (tr_title, paragraph, link, domain, art_date) çıkarır.

    Yalnızca eşleşen FRAGMAN BeautifulSoup ile parse edilir; tüm döküman yeniden
    serialize EDİLMEZ (mevcut format/diff korunur).
    """
    m = _news_item_re(news_id).search(html)
    if not m:
        # Haber gövdesi raporda yok — neredeyse her zaman bu haber DAHA ÖNCE
        # kritik bölüme taşınmış olduğu içindir (sayfanız bayat kalmış olabilir).
        raise ValueError("bu haber raporda yok; muhtemelen daha önce taşındı. "
                         "Lütfen sayfayı yenileyip tekrar deneyin")
    frag = BeautifulSoup(m.group(0), "html.parser")
    title_el = frag.find(class_="news-title")
    content_el = frag.find(class_="news-content")
    source_el = frag.find(class_="source")

    tr_title = title_el.get_text(" ", strip=True) if title_el else ""
    paragraph = content_el.decode_contents().strip() if content_el else ""
    link = domain = art_date = ""
    if source_el:
        a = source_el.find("a")
        if a:
            link = a.get("href", "")
            domain = a.get_text(strip=True)
        dm = re.search(r"\d{2}\.\d{2}\.\d{4}", source_el.get_text())
        if dm:
            art_date = dm.group(0)
    if not tr_title or not paragraph:
        raise ValueError("başlık/metin çıkarılamadı")
    return tr_title, paragraph, link, domain, art_date


def remove_news_item(html, news_id):
    """news-item bloğunu ve yönetici tablosundaki ilgili HÜCREYİ kaldırır.

    Yönetici tablosu İKİ SÜTUNLUDUR (<tr><td>A</td><td>B</td></tr>). Bu yüzden
    tüm <tr>'yi silmek YANLIŞ olur — aynı satırdaki komşu haberi de götürürdü.
    Önceki sürüm tek-sütun (<tr><td>...</td></tr>) varsayıp eşleşemiyor ve
    sessizce geçiyordu; sonuçta taşınan habere ait ÖLÜ bir bağlantı (#haber-N)
    tabloda kalıyordu. Artık yalnızca o haberin <td> hücresi BOŞ <td></td> ile
    değiştirilir: ölü bağlantı gider, sütun hizası ve komşu haber korunur
    (tek-haberlik satırlardaki boş hücre kalıbıyla tutarlı).
    """
    new_html = _news_item_re(news_id).sub("", html, count=1)
    cell_re = re.compile(
        r'([ \t]*)<td><a href="#' + re.escape(news_id) + r'">.*?</a></td>\n',
        re.DOTALL,
    )
    new_html = cell_re.sub(lambda m: m.group(1) + "<td></td>\n", new_html, count=1)
    return new_html


def extract_top3_card(html, index):
    """index'inci kritik kartın içeriğini (tr_title, paragraph, link, domain,
    art_date) çıkarır. Kart gövdeye TAŞINIRKEN (silinmek yerine) kullanılır.
    Kart yoksa/eksikse None döner."""
    matches = list(_CARD_RE.finditer(html))
    if index < 0 or index >= len(matches):
        return None
    frag = BeautifulSoup(matches[index].group(0), "html.parser")
    title_el = frag.find(class_="top3-card-title")
    para_el = frag.find(class_="top3-card-paragraph")
    source_el = frag.find(class_="source")
    tr_title = title_el.get_text(" ", strip=True) if title_el else ""
    paragraph = para_el.decode_contents().strip() if para_el else ""
    link = domain = art_date = ""
    if source_el:
        a = source_el.find("a")
        if a:
            link = a.get("href", "")
            domain = a.get_text(strip=True)
        dm = re.search(r"\d{2}\.\d{2}\.\d{4}", source_el.get_text())
        if dm:
            art_date = dm.group(0)
    if not tr_title or not paragraph:
        return None
    return tr_title, paragraph, link, domain, art_date


def build_news_item_html(tr_title, paragraph, link, domain, art_date, item_id="haber-90000",
                         paragraph_is_html=False):
    """main.py:_render_item kalıbının aynısıyla bir gövde haber bloğu üretir.
    item_id geçici verilir; insert sonrası renumber_and_reflow yeniden numaralar.
    paragraph_is_html: build_card_html'deki ile aynı çift-escape koruması."""
    tr_title = _html_escape(tr_title or "")
    if not paragraph_is_html:
        paragraph = _html_escape(paragraph or "")
    link = _safe_href(link)
    domain = _html_escape(domain or "")
    art_date = _html_escape(art_date or "")
    return (
        f'            <div class="news-item" id="{item_id}">\n'
        f'                <div class="news-title"><b>{tr_title}</b></div>\n'
        f'                <p class="news-content">{paragraph}</p>\n'
        '                <p class="source"><b>(XXXXXXX, AÇIK - '
        f'<a href="{link}" target="_blank">{domain}</a>, {art_date})</b></p>\n'
        "            </div>\n"
    )


def insert_body_news_item(html, item_html):
    """Bir gövde haber bloğunu 'diğer haberler' listesinin SONUNA (regular
    haberlerin bittiği yere) ekler. Sıra: sosyal kutudan / zafiyet başlığından
    hemen ÖNCE; hiçbiri yoksa son regular haberin ardına. Böylece çıkarılan
    kritik haber silinmez, gövdeye taşınır (renumber_and_reflow sonra numaralar)."""
    for anchor in ('<div class="social-signals">',
                   '<!-- SOCIAL_SIGNALS_HERE -->',
                   '<div class="vuln-section-heading"'):
        idx = html.find(anchor)
        if idx != -1:
            line_start = html.rfind("\n", 0, idx) + 1
            return html[:line_start] + item_html + html[line_start:]
    # Son çare: son REGULAR haber bloğunun ardına (vuln-item'ları hariç tutar).
    reg = list(re.finditer(
        r'[ \t]*<div class="news-item" id="haber-\d+">.*?\n[ \t]*</div>\n',
        html, re.DOTALL))
    if reg:
        m = reg[-1]
        return html[: m.end()] + item_html + html[m.end():]
    return html + item_html


def _esc_html_text(s):
    """Tablo hücresine düz metin yazmak için minimal HTML kaçışı."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_EXEC_TABLE_RE = re.compile(
    r'(<table class="executive-table">\n).*?(\n[ \t]*</table>)', re.DOTALL
)


def renumber_and_reflow(html):
    """Haber numaralarını GÖVDE SIRASINA göre 1..N (regular) / N+1..M (vuln)
    olarak yeniden verir ve indeks tablosunu (executive-table) boşluksuz, temiz
    2 sütun olarak yeniden kurar.

    Neden: manuel taşıma bir haberi listeden çıkarınca (remove_news_item) kalan
    haberler yeniden numaralanmıyordu; tablo boşluklu (#3'ten başlıyor, boş
    <td></td>) kalıyordu. main.py:_build_html'deki numaralandırma şemasıyla
    (regular 1..N, vuln N+1..M; tabloda yalnızca regular) aynıdır.

    Yazma cerrahidir: yalnızca gövde id'leri ve executive-table bloğu değişir;
    dosyanın geri kalanı (biçim/diff) korunur. Gövde/tablo yoksa HTML aynen döner.
    """
    soup = BeautifulSoup(html, "html.parser")
    regular, vuln = [], []
    for el in soup.select(".news-item[id]"):
        nid = el.get("id", "")
        if not re.match(r"^haber-\d+$", nid):
            continue
        title_el = el.find(class_="news-title")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        if "vuln-item" in (el.get("class") or []):
            vuln.append((nid, title))
        else:
            regular.append((nid, title))
    if not regular and not vuln:
        return html

    # Belge sırasına göre yeni numara: regular 1..N, sonra vuln N+1..M.
    mapping = {}      # eski_id -> yeni_num
    regular_new = []  # tablo için (yeni_num, başlık)
    n = 0
    for nid, title in regular:
        n += 1
        mapping[nid] = n
        regular_new.append((n, title))
    for nid, _title in vuln:
        n += 1
        mapping[nid] = n

    # 1) Gövde id'lerini iki fazlı (çakışmasız) güncelle. Tek geçişli global
    #    replace, eski/yeni aralıklar çakıştığında cascade'e yol açar.
    new_html = html
    for nid in mapping:
        new_html = new_html.replace('id="%s"' % nid, 'id="__MA_ID_%s__"' % nid)
    for nid, num in mapping.items():
        new_html = new_html.replace('id="__MA_ID_%s__"' % nid, 'id="haber-%d"' % num)

    # 2) İndeks tablosunu yalnızca regular haberlerden yeniden kur (vuln tabloda
    #    yer almaz — main.py ile aynı). Tek-haberlik satırda boş hücre kalıbı.
    row_indent, cell_indent = " " * 16, " " * 20
    rows = []
    for i in range(0, len(regular_new), 2):
        pair = regular_new[i:i + 2]
        cells = ""
        for num, title in pair:
            cells += ('%s<td><a href="#haber-%d">%d. %s</a></td>\n'
                      % (cell_indent, num, num, _esc_html_text(title)))
        if len(pair) == 1:
            cells += "%s<td></td>\n" % cell_indent
        rows.append("%s<tr>\n%s%s</tr>\n" % (row_indent, cells, row_indent))
    table_inner = "".join(rows).rstrip("\n")

    if regular_new and _EXEC_TABLE_RE.search(new_html):
        new_html = _EXEC_TABLE_RE.sub(
            lambda m: m.group(1) + table_inner + m.group(2), new_html, count=1
        )
    return new_html


def replace_top3_card(html, index, new_card_html):
    matches = list(_CARD_RE.finditer(html))
    if len(matches) < 1:
        raise ValueError(f"Kritik kart bulunamadı (bulunan: {len(matches)}).")
    if index < 0 or index >= len(matches):
        raise ValueError("Geçersiz kart indeksi.")
    m = matches[index]
    return html[: m.start()] + new_card_html + html[m.end():]


def add_top3_card(html, new_card_html):
    """Yeni kritik kartı mevcut kartların SONUNA ekler (mevcut hiçbirini silmez).
    Kartlar top3-section içinde; son kartın hemen ardına yerleştirilir.

    Kart HİÇ yoksa (az-haber guard top3'ü boşaltmış — dejenere rapor): yeni bir
    top3-section kurar ve Önemli Gelişmeler kutusundaki alt aksiyon çubuğundan
    (block-actions-bottom) ÖNCE yerleştirir. Böylece kartsız raporda bile ekleme
    yapılabilir (P2)."""
    matches = list(_CARD_RE.finditer(html))
    if matches:
        last = matches[-1]
        return html[: last.end()] + new_card_html + html[last.end():]
    # Hiç kart yok → bölümü sıfırdan kur.
    section = ('            <div class="top3-section">\n' + new_card_html
               + '            </div>\n')
    idx = html.find('<div class="block-actions-bottom">')
    if idx != -1:
        line_start = html.rfind("\n", 0, idx) + 1
        return html[:line_start] + section + html[line_start:]
    # Son çare: Önemli Gelişmeler blok başlığının hemen ardına.
    m = re.search(r'id="onemli-gelismeler-block">[ \t]*\n', html)
    if m:
        return html[: m.end()] + section + html[m.end():]
    raise ValueError("Kritik kart eklenecek konum bulunamadı.")


def delete_top3_card(html, index):
    """index'inci kritik kartı siler (yerine bir şey koymaz). Kalan kart sayısı
    azalır; günlük otomatik üretim ertesi gün yeniden 3 kart kurar."""
    matches = list(_CARD_RE.finditer(html))
    if index < 0 or index >= len(matches):
        raise ValueError("Geçersiz kart indeksi.")
    m = matches[index]
    return html[: m.start()] + html[m.end():]


def report_date_from_html(html):
    """index.html <title>'ından rapor tarihini çıkarır → (dd.mm.yyyy, arşiv yolu)."""
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", html)
    if not m:
        raise ValueError("Rapor tarihi index.html içinde bulunamadı.")
    dd, mm, yyyy = m.group(1), m.group(2), m.group(3)
    return f"{dd}.{mm}.{yyyy}", f"docs/raporlar/{yyyy}-{mm}-{dd}.html"


# ─────────────────────────────────────────────────────────────────────────────
# Yönetici Özeti senkronizasyonu
#
# Sorun: bir kritik kart takas edildiğinde (URL ile yeni haber VEYA rapordan
# taşıma) #yonetici-ozeti-block içindeki özet paragrafı dokunulmadan kaldığı
# için çıkarılan habere değinmeye devam eder, yeni haberden hiç söz etmez.
#
# Çözüm: takas HTML'e uygulandıktan SONRA, güncel sayfadan main.py:Pass6 ile
# AYNI kaynak kümesini (3 kritik kart + belge sırasındaki ilk 6 normal haber)
# toplayıp get_executive_summary_prompt ile paragrafı bütün olarak yeniden üret;
# yalnızca .exec-brief-paragraph içeriğini değiştir. LLM başarısız olursa
# main.py'deki deterministik başlık-tabanlı yedeğin aynısıyla doldur — böylece
# bayat/yanlış metin asla kalmaz.
# ─────────────────────────────────────────────────────────────────────────────
_EXEC_BRIEF_PARA_RE = re.compile(
    r'(<p class="exec-brief-paragraph">).*?(</p>)', re.DOTALL
)


def _collect_exec_sources(html):
    """Güncel HTML'den özet kaynak kümesini (en fazla 9 kalem) çıkarır.

    main.py:Pass6 mantığı: 3 kritik kart (top3) + belge sırasındaki ilk 6 normal
    haber (.news-item). Her kalem (tr_title, snippet) olarak döner.
    """
    soup = BeautifulSoup(html, "html.parser")
    items = []

    for card in soup.select(".top3-card"):
        title_el = card.find(class_="top3-card-title")
        para_el = card.find(class_="top3-card-paragraph")
        tr_title = title_el.get_text(" ", strip=True) if title_el else ""
        paragraph = para_el.get_text(" ", strip=True) if para_el else ""
        if tr_title:
            items.append((tr_title, paragraph))

    regular = 0
    for ni in soup.select(".news-item"):
        if regular >= 6:
            break
        title_el = ni.find(class_="news-title")
        para_el = ni.find(class_="news-content")
        tr_title = title_el.get_text(" ", strip=True) if title_el else ""
        paragraph = para_el.get_text(" ", strip=True) if para_el else ""
        if tr_title:
            items.append((tr_title, paragraph))
            regular += 1

    return items


def _summary_warning(mode):
    """Yönetici Özeti LLM ile üretilemediyse kullanıcıya gösterilecek uyarı.

    'llm'/'yok' durumunda uyarı yok (None). Aksi halde özet basit başlık-listesi
    yedeğiyle dolduruldu demektir; kullanıcı bunu bilmeli ki Vercel ortam
    değişkenini (OPENROUTER_API_KEY) düzeltebilsin.
    """
    if mode == "atlandi":
        return ("Yönetici Özeti akıcı biçimde yeniden üretilemedi: sunucuda "
                "OPENROUTER_API_KEY tanımlı değil; geçici olarak başlık-listesi "
                "özeti kullanıldı.")
    if mode == "deterministik":
        return ("Yönetici Özeti LLM ile üretilemedi (geçici hata); geçici olarak "
                "başlık-listesi özeti kullanıldı. Tekrar denenebilir.")
    return None


def _deterministic_exec_summary(items):
    """LLM başarısızsa main.py:Pass6 yedeğinin aynısı — başlıklardan tek paragraf."""
    titles = [t.strip().rstrip(".") for t, _ in items if t and t.strip()]
    if not titles:
        return ""
    lead = ("Son 48 saatin siber güvenlik gündeminde öne çıkan "
            "başlıca gelişmeler şunlardır: ")
    return lead + "; ".join(titles[:8]) + "."


def regenerate_exec_summary(html):
    """Güncel HTML'e göre Yönetici Özeti paragrafını yeniden üretir.

    (yeni_html, mod) döner. mod ∈ {"llm", "deterministik", "yok", "atlandi"}:
      - "llm"           : LLM akıcı özet üretti (istenen durum).
      - "deterministik" : LLM başarısız → başlık-tabanlı yedek kullanıldı.
      - "yok"           : Bu raporda Yönetici Özeti kutusu/kaynak yok → dokunulmadı.
      - "atlandi"       : OPENROUTER_API_KEY tanımsız → LLM hiç denenmedi.
    Özet bloğu yoksa veya kaynak çıkmıyorsa HTML değiştirilmeden döner.
    """
    if not _EXEC_BRIEF_PARA_RE.search(html):
        return html, "yok"  # Bu raporda Yönetici Özeti kutusu yok → dokunma.

    items = _collect_exec_sources(html)
    if not items:
        return html, "yok"

    es_lines = []
    for i, (tr_title, paragraph) in enumerate(items, 1):
        snippet = " ".join(paragraph.split()[:90])
        es_lines.append(
            f"=== HABER {i} ===\n"
            f"Başlık: {tr_title}\n"
            f"Özet: {snippet}\n"
        )

    # main.py:Pass6 ile aynı: tek geçici hatada özet kaybolmasın diye 3 deneme.
    # max_output_tokens 1024 DEĞİL 4096: Gemini 3 Flash bir "thinking" modeli
    # ve OpenRouter'da reasoning token'ları da BU bütçeden harcanır. 1024 ile
    # reasoning bütçeyi tüketip JSON çıktısını yarım bırakıyor → parse başarısız
    # → her seferinde deterministik yedeğe düşülüyordu. İçerik üretimiyle (4096)
    # aynı geniş bütçe, akıcı özetin güvenle dönmesini sağlar.
    exec_summary = ""
    key_present = bool(os.getenv("OPENROUTER_API_KEY", ""))
    if key_present:
        for attempt in range(3):
            try:
                data = llm_client.generate_json(
                    get_executive_summary_prompt("\n".join(es_lines)),
                    max_output_tokens=4096,
                    label=f"ManuelEkle-YoneticiOzeti(d{attempt + 1})",
                )
            except Exception:
                data = None
            if data and isinstance(data.get("ozet"), str) and data["ozet"].strip():
                exec_summary = data["ozet"].strip()
                break

    mode = "llm" if exec_summary else ("atlandi" if not key_present else "deterministik")

    # Deterministik yedek — LLM yok/başarısız olsa bile bayat metin kalmaz.
    if not exec_summary:
        exec_summary = _deterministic_exec_summary(items)
    if not exec_summary:
        return html, "yok"

    # HTML kaçışı: özet metni HTML gövdesine düz metin olarak girer; <, &
    # karakterleri kaçırılmazsa sayfayı bozabilir.
    safe = exec_summary.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    new_html = _EXEC_BRIEF_PARA_RE.sub(
        lambda m: m.group(1) + safe + m.group(2), html, count=1
    )
    return new_html, mode


# ─────────────────────────────────────────────────────────────────────────────
# GitHub Contents API yardımcıları
# ─────────────────────────────────────────────────────────────────────────────
def _gh_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def gh_get_file(path, token):
    r = requests.get(
        f"{GH_API}/repos/{REPO}/contents/{path}",
        headers=_gh_headers(token),
        params={"ref": BRANCH},
        timeout=30,
    )
    r.raise_for_status()
    j = r.json()
    content = base64.b64decode(j["content"]).decode("utf-8")
    return content, j["sha"]


def gh_put_file(path, new_content, sha, token, message):
    r = requests.put(
        f"{GH_API}/repos/{REPO}/contents/{path}",
        headers=_gh_headers(token),
        json={
            "message": message,
            "content": base64.b64encode(new_content.encode("utf-8")).decode("ascii"),
            "sha": sha,
            "branch": BRANCH,
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def gh_commit_files(files, token, message):
    """Birden çok dosyayı TEK commit'te ATOMİK yazar (Git Data API).

    files: [(path, content_str), ...]. Contents API ile dosya-dosya commit
    yapıldığında index güncellenip arşiv güncellenmeden hata olursa tutarsız
    durum doğardı. Tek tree + tek commit ile ya hepsi ya hiçbiri uygulanır.
    """
    h = _gh_headers(token)
    base = f"{GH_API}/repos/{REPO}/git"

    r = requests.get(f"{base}/ref/heads/{BRANCH}", headers=h, timeout=30)
    r.raise_for_status()
    base_commit_sha = r.json()["object"]["sha"]

    r = requests.get(f"{base}/commits/{base_commit_sha}", headers=h, timeout=30)
    r.raise_for_status()
    base_tree_sha = r.json()["tree"]["sha"]

    tree = []
    for path, content in files:
        r = requests.post(
            f"{base}/blobs", headers=h, timeout=30,
            json={"content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
                  "encoding": "base64"},
        )
        r.raise_for_status()
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": r.json()["sha"]})

    r = requests.post(
        f"{base}/trees", headers=h, timeout=30,
        json={"base_tree": base_tree_sha, "tree": tree},
    )
    r.raise_for_status()
    new_tree_sha = r.json()["sha"]

    r = requests.post(
        f"{base}/commits", headers=h, timeout=30,
        json={"message": message, "tree": new_tree_sha, "parents": [base_commit_sha]},
    )
    r.raise_for_status()
    new_commit_sha = r.json()["sha"]

    r = requests.patch(
        f"{base}/refs/heads/{BRANCH}", headers=h, timeout=30,
        json={"sha": new_commit_sha, "force": False},
    )
    r.raise_for_status()
    return r.json()


# ─────────────────────────────────────────────────────────────────────────────
# Çekirdek iş akışı
# ─────────────────────────────────────────────────────────────────────────────
def process(payload):
    """Ortak doğrulama + İŞLEME göre dallanma.

    action = "replace" → bir kritik kartı çıkar + yerine ekle (URL/rapor kaynağı).
    action = "add"     → mevcut hiçbirini SİLMEDEN yeni kritik kart ekle (4'e çıkar).
    action = "delete"  → bir haberi SİL (kritik kart VEYA alt liste haberi).
    Geriye dönük uyum: action yoksa "replace" kabul edilir.
    """
    # hmac.compare_digest str girdilerde YALNIZCA ASCII kabul eder → şifrede
    # Türkçe karakter varsa TypeError atar ve uç nokta doğru şifreyle bile 500
    # döner. JSON'dan gelen değer str de olmayabilir. İkisini de UTF-8 bayta
    # çevirerek karşılaştır (sabit-zamanlı kalır).
    password = payload.get("password")
    expected = os.getenv("MANUAL_ADD_PASSWORD", "")
    if not expected:
        return 500, {"error": "Sunucuda MANUAL_ADD_PASSWORD tanımlı değil."}
    if not isinstance(password, str) or not hmac.compare_digest(
            password.encode("utf-8"), expected.encode("utf-8")):
        return 401, {"error": "Şifre hatalı."}

    token = os.getenv("GH_TOKEN", "")
    if not token:
        return 500, {"error": "Sunucuda GH_TOKEN tanımlı değil."}

    action = (payload.get("action") or "replace").strip().lower()
    if action == "delete":
        return process_delete(payload, token)
    if action == "add":
        return process_add(payload, token)
    if action == "replace":
        return process_replace(payload, token)
    return 400, {"error": "Geçersiz işlem."}


def _read_index(token):
    """index.html'i oku; (html, rapor_tarihi, arşiv_yolu) döner."""
    index_html, _ = gh_get_file(INDEX_PATH, token)
    report_date, archive_path = report_date_from_html(index_html)
    return index_html, report_date, archive_path


def _commit_transform(index_html, archive_path, transform, token, msg, extra):
    """`transform(html) -> (yeni_html, summary_mode)` fonksiyonunu index'e ve
    (varsa) arşive uygular, TEK atomik commit'te yazar. Ortak yazma yolu:
    replace/add/delete işlemlerinin hepsi bunu kullanır."""
    try:
        new_index, summary_mode = transform(index_html)
    except Exception as e:
        return 500, {"error": f"index.html güncellenemedi: {str(e)[:160]}"}

    files = [(INDEX_PATH, new_index)]
    try:
        archive_html, _ = gh_get_file(archive_path, token)
        new_archive, _ = transform(archive_html)
        files.append((archive_path, new_archive))
    except Exception:
        # Arşiv dosyası yoksa/okunamıyorsa yalnızca index güncellenir.
        pass

    try:
        gh_commit_files(files, token, msg)
    except Exception as e:
        return 502, {"error": f"Commit başarısız: {str(e)[:160]}"}

    resp = {"ok": True, "summary_mode": summary_mode,
            "summary_warning": _summary_warning(summary_mode)}
    resp.update(extra or {})
    return 200, resp


def _card_from_source(payload, index_html, report_date, token):
    """Kaynağa (mode) göre kritik kart HTML'i üretir.

    mode = "report" → rapordaki bir haberi karta dönüştür (alt listeden TAŞINIR).
    mode = "url"    → URL'yi çek + LLM ile içerik üret.
    Dönüş: (card_html, tasinan_news_id | None, hata | None). Hata varsa
    (code, body) tuple'ıdır; çağıran doğrudan döndürür."""
    mode = (payload.get("mode") or "").strip().lower()
    if not mode:
        mode = "url" if (payload.get("url") or "").strip() else ""

    if mode == "report":
        news_id = (payload.get("news_id") or "").strip()
        if not re.match(r"^haber-\d+$", news_id):
            return None, None, (400, {"error": "Geçersiz haber kimliği."})
        try:
            tr_title, paragraph, link, domain, art_date = extract_news_item(index_html, news_id)
        except Exception as e:
            return None, None, (422, {"error": f"Seçilen haber çıkarılamadı: {str(e)[:160]}"})
        # paragraph rapordan decode_contents ile alındı — zaten escape'li HTML.
        card_html = build_card_html(tr_title, paragraph, link, domain,
                                    art_date or report_date, paragraph_is_html=True)
        return card_html, news_id, None

    if mode == "url":
        url = (payload.get("url") or "").strip()
        if not re.match(r"^https?://", url, re.IGNORECASE):
            return None, None, (400, {"error": "Geçerli bir URL giriniz."})
        if not os.getenv("OPENROUTER_API_KEY", ""):
            return None, None, (500, {"error": "Sunucuda OPENROUTER_API_KEY tanımlı değil."})
        try:
            full_text, domain = fetch_article(url)
        except Exception as e:
            return None, None, (502, {"error": f"URL çekilemedi: {str(e)[:160]}"})
        if not full_text or len(full_text.split()) < 50:
            return None, None, (422, {"error": "Makale metni çıkarılamadı veya çok kısa."})
        try:
            tr_title, paragraph, gen_err = generate_content(url, full_text, report_date)
        except Exception as e:
            return None, None, (502, {"error": f"İçerik üretilemedi: {str(e)[:200]}"})
        if not tr_title or not paragraph:
            return None, None, (502, {"error": gen_err or "LLM geçerli başlık/paragraf döndürmedi."})
        card_html = build_card_html(tr_title, paragraph, url, domain, report_date)
        return card_html, None, None

    return None, None, (400, {"error": "Geçersiz kaynak modu."})


def process_replace(payload, token):
    """Bir kritik kartı çıkar + yerine (URL/rapor) yeni kart koy. (Mevcut akış.)"""
    try:
        remove_index = int(payload.get("remove_index"))
    except (TypeError, ValueError):
        return 400, {"error": "Geçersiz remove_index."}
    if remove_index < 0:
        return 400, {"error": "Geçersiz remove_index."}

    try:
        index_html, report_date, archive_path = _read_index(token)
    except Exception as e:
        return 502, {"error": f"index.html okunamadı: {str(e)[:160]}"}

    card_html, news_id, err = _card_from_source(payload, index_html, report_date, token)
    if err:
        return err

    def _t(html):
        # Çıkarılan kritik haber SİLİNMEZ: içeriğini al, kart yenisiyle değişince
        # gövdedeki 'diğer haberler' listesine taşı.
        demoted = extract_top3_card(html, remove_index)
        html = replace_top3_card(html, remove_index, card_html)
        if news_id:
            html = remove_news_item(html, news_id)
        if demoted:
            # demoted paragrafı karttan decode_contents ile geldi — zaten escape'li.
            html = insert_body_news_item(
                html, build_news_item_html(*demoted, paragraph_is_html=True))
        html = renumber_and_reflow(html)
        return regenerate_exec_summary(html)

    return _commit_transform(
        index_html, archive_path, _t, token,
        f"manuel: kritik haber güncellendi, çıkarılan haber gövdeye taşındı ({report_date})",
        {"card_html": card_html, "removed_news_id": news_id, "demoted_to_body": True},
    )


def _parse_demote_indices(payload, index_html):
    """payload['demote_indices'] (opsiyonel, ÇOKLU liste) doğrular ve azalan
    sırada döndürür. Seçilen kritik kartlar SİLİNMEZ — add_top3_card/insert
    öncesi gövdeye indirilir (bkz. process_add). Sıra azalan olmalı ki
    delete_top3_card art arda çağrıldığında erken indeksler kaymasın.
    Geçersiz/aralık dışı indekste (code, body) tuple'ı döner; aksi halde
    (None, sorted_desc_list)."""
    raw = payload.get("demote_indices")
    if not isinstance(raw, list) or not raw:
        return None, []
    existing_count = len(list(_CARD_RE.finditer(index_html)))
    seen, ordered = set(), []
    for item in raw:
        try:
            idx = int(item)
        except (TypeError, ValueError):
            return (400, {"error": "Geçersiz çıkarılacak haber indeksi."}), []
        if idx < 0 or idx >= existing_count:
            return (400, {"error": f"Geçersiz kritik kart indeksi: {idx}"}), []
        if idx not in seen:
            seen.add(idx)
            ordered.append(idx)
    return None, sorted(ordered, reverse=True)


def _demote_cards(html, demote_desc):
    """demote_desc'teki (azalan sıralı) kritik kart indekslerini SİLMEDEN
    gövdeye indirir: içeriğini alır, karttan kaldırır, gövde haberi olarak
    ekler. Azalan sırada işlendiği için erken indeksler kaymaz."""
    for idx in demote_desc:
        demoted = extract_top3_card(html, idx)
        html = delete_top3_card(html, idx)
        if demoted:
            html = insert_body_news_item(
                html, build_news_item_html(*demoted, paragraph_is_html=True))
    return html


def process_add(payload, token):
    """Yeni bir veya BİRDEN ÇOK kritik kart ekler. Kaynak URL ise LLM ile
    üretilir (tek); rapor ise seçilen haber(ler) alt listeden TAŞINIR (yeni
    karta dönüşür, gövdeden kaldırılır) — payload['news_ids'] listesiyle tekli
    de çoklu da AYNI yoldan işlenir.

    demote_indices (opsiyonel, ÇOKLU): eklenen yeni kart(lar) için yer açmak
    üzere seçilen MEVCUT kritik kartlar SİLİNMEZ, gövdeye indirilir. Demote
    işlemi eklemeden ÖNCE uygulanır (indeksler orijinal kart listesine göre).

    Tek commit, tek Yönetici Özeti yeniden üretimi. Geriye dönük uyum: eski
    tekli 'news_id' alanı da (liste değilse) kabul edilir."""
    try:
        index_html, report_date, archive_path = _read_index(token)
    except Exception as e:
        return 502, {"error": f"index.html okunamadı: {str(e)[:160]}"}

    demote_err, demote_desc = _parse_demote_indices(payload, index_html)
    if demote_err:
        return demote_err

    # 'news_ids' yoksa eski tekli 'news_id' alanını listeye çevir (geriye dönük uyum).
    news_ids = payload.get("news_ids")
    if not (isinstance(news_ids, list) and news_ids) and payload.get("news_id"):
        news_ids = [payload.get("news_id")]

    if isinstance(news_ids, list) and news_ids:
        seen, ordered = set(), []
        for raw in news_ids:
            nid = (str(raw) or "").strip()
            if not re.match(r"^haber-\d+$", nid):
                return 400, {"error": f"Geçersiz haber kimliği: {nid[:40]}"}
            if nid not in seen:
                seen.add(nid)
                ordered.append(nid)
        # Yanıt için kartları ORİJİNAL index'ten önden üret (hepsi hâlâ mevcut;
        # id'ler bağımsız olduğundan sırayla çıkarmak güvenli). Bu aynı zamanda
        # transform çalışmadan önce her id'nin gerçekten var olduğunu doğrular.
        pre_cards = []
        for nid in ordered:
            try:
                tr_title, paragraph, link, domain, art_date = extract_news_item(index_html, nid)
            except Exception as e:
                return 422, {"error": f"Seçilen haber çıkarılamadı ({nid}): {str(e)[:120]}"}
            pre_cards.append(build_card_html(
                tr_title, paragraph, link, domain, art_date or report_date,
                paragraph_is_html=True))

        def _t_multi(html):
            html = _demote_cards(html, demote_desc)
            for nid in ordered:
                tr_title, paragraph, link, domain, art_date = extract_news_item(html, nid)
                card = build_card_html(tr_title, paragraph, link, domain,
                                       art_date or report_date, paragraph_is_html=True)
                html = add_top3_card(html, card)
                html = remove_news_item(html, nid)
            html = renumber_and_reflow(html)
            return regenerate_exec_summary(html)

        msg = f"manuel: {len(ordered)} haber gövdeden kritik bölüme taşındı"
        if demote_desc:
            msg += f", {len(demote_desc)} haber gövdeye indirildi"
        return _commit_transform(
            index_html, archive_path, _t_multi, token, f"{msg} ({report_date})",
            {"added_cards": pre_cards, "removed_news_ids": ordered,
             "demoted_indices": demote_desc, "added": True},
        )

    # ── URL modu (tek haber, LLM ile üretim) ─────────────────────────────────
    card_html, _news_id, err = _card_from_source(payload, index_html, report_date, token)
    if err:
        return err

    def _t(html):
        html = _demote_cards(html, demote_desc)
        html = add_top3_card(html, card_html)
        html = renumber_and_reflow(html)
        return regenerate_exec_summary(html)

    msg = "manuel: kritik habere ekleme yapıldı"
    if demote_desc:
        msg += f", {len(demote_desc)} haber gövdeye indirildi"
    return _commit_transform(
        index_html, archive_path, _t, token, f"{msg} ({report_date})",
        {"added_cards": [card_html], "removed_news_ids": [],
         "demoted_indices": demote_desc, "added": True},
    )


def process_delete(payload, token):
    """Bir veya BİRDEN ÇOK haberi SİL (tamamen; yerine bir şey konmaz).

    ÇOKLU: payload['targets'] listesi — her öğe {"t":"critical","i":N} veya
    {"t":"body","id":"haber-N"}. Kritik kartlar İNDEKS AZALAN sırada silinir
    (silme sonrası kalan indeksler kaymasın); gövde haberleri id ile silinir
    (sıra bağımsız). Tek commit, tek Yönetici Özeti yeniden üretimi.
    TEKLİ: eski delete_target/remove_index/news_id yolu korunur.

    Not: bir kritik haberi silmeden gövdeye indirmek istiyorsan Ekle işleminde
    'Çıkarılacak Haber' seçeneğini kullan — o haber gövdeye iner."""
    try:
        index_html, report_date, archive_path = _read_index(token)
    except Exception as e:
        return 502, {"error": f"index.html okunamadı: {str(e)[:160]}"}

    # ── ÇOKLU silme (targets listesi) ─────────────────────────────────────────
    targets = payload.get("targets")
    if isinstance(targets, list) and targets:
        crit_indices, body_ids = set(), []
        for t in targets:
            if not isinstance(t, dict):
                return 400, {"error": "Geçersiz silme hedefi (öğe biçimi)."}
            tt = (t.get("t") or t.get("delete_target") or "").strip().lower()
            if tt == "critical":
                try:
                    idx = int(t.get("i", t.get("remove_index")))
                except (TypeError, ValueError):
                    return 400, {"error": "Geçersiz kritik kart indeksi."}
                if idx < 0:
                    return 400, {"error": "Geçersiz kritik kart indeksi."}
                crit_indices.add(idx)
            elif tt == "body":
                nid = (str(t.get("id") or t.get("news_id") or "")).strip()
                if not re.match(r"^haber-\d+$", nid):
                    return 400, {"error": f"Geçersiz haber kimliği: {nid[:40]}"}
                if nid not in body_ids:
                    body_ids.append(nid)
            else:
                return 400, {"error": "Geçersiz silme hedefi."}

        crit_desc = sorted(crit_indices, reverse=True)

        def _t_multi(html):
            # Kritik kartlar AZALAN indeksle silinir → erken indeksler geçerli kalır.
            for idx in crit_desc:
                html = delete_top3_card(html, idx)
            for nid in body_ids:
                html = remove_news_item(html, nid)
            html = renumber_and_reflow(html)
            return regenerate_exec_summary(html)

        return _commit_transform(
            index_html, archive_path, _t_multi, token,
            f"manuel: {len(crit_desc) + len(body_ids)} haber silindi ({report_date})",
            {"deleted_indices": crit_desc, "removed_news_ids": body_ids},
        )

    # ── TEKLİ silme — eski yol ────────────────────────────────────────────────
    target = (payload.get("delete_target") or "").strip().lower()

    if target == "critical":
        try:
            idx = int(payload.get("remove_index"))
        except (TypeError, ValueError):
            return 400, {"error": "Geçersiz remove_index."}
        if idx < 0:
            return 400, {"error": "Geçersiz remove_index."}

        def _t(html):
            html = delete_top3_card(html, idx)
            html = renumber_and_reflow(html)
            return regenerate_exec_summary(html)

        return _commit_transform(
            index_html, archive_path, _t, token,
            f"manuel: kritik haber silindi ({report_date})",
            {"deleted_index": idx},
        )

    if target == "body":
        news_id = (payload.get("news_id") or "").strip()
        if not re.match(r"^haber-\d+$", news_id):
            return 400, {"error": "Geçersiz haber kimliği."}

        def _t(html):
            html = remove_news_item(html, news_id)
            html = renumber_and_reflow(html)
            return regenerate_exec_summary(html)

        return _commit_transform(
            index_html, archive_path, _t, token,
            f"manuel: haber silindi ({report_date})",
            {"removed_news_id": news_id},
        )

    return 400, {"error": "Geçersiz silme hedefi."}


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
            self._send(400, {"error": "Geçersiz istek gövdesi."})
            return
        try:
            code, result = process(payload)
        except Exception as e:
            code, result = 500, {"error": f"Beklenmeyen hata: {str(e)[:200]}"}
        self._send(code, result)
