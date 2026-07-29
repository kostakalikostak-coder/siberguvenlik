#!/usr/bin/env python3
"""CONTENT_SELECTORS keşfi — makale sayfası kazımasını runner IP'sinde (üretim) ölçer.

Tam metni çıkarılamayan kaynaklar için doğru içerik seçicisini TAHMİNLE değil
ÖLÇEREK bulur. Neden runner'da: siteler IP'ye göre farklı davranıyor (WAF/coğrafi
engel), geliştirme ortamından alınan sonuç üretimi temsil etmiyor.

Her hedef kaynak için:
  1. Feed'den en yeni makale linkini al.
  2. FEED gövdesinin kelime sayısını ölç (content:encoded / content / description).
     Bu, "makale kazınamıyor" ile "feed'de metin var ama FEED_SUMMARY_MIN_WORDS
     eşiğinin altında" durumlarını AYIRIR — ikisi farklı düzeltme gerektirir.
  3. Makale sayfasını main.py fetch_full_article ile AYNI header/stream/timeout ile çek.
  4. Şunların kelime sayısını raporla:
     - MEVCUT zincir (main.py: selectors→<article>→<main>→content-<div>)  [baseline]
     - kaynağa özel ADAY seçiciler
  5. >FEED_SUMMARY_MIN_WORDS kelime veren ilk seçici = önerilen CONTENT_SELECTORS girişi.

Feed URL'leri src/config.NEWS_SOURCES'tan okunur — script'e elle URL YAZILMAZ
(uydurma/eskimiş adres riski olmasın; üretimdeki adres neyse o ölçülür).

Çalıştır: .github/workflows/selector_probe.yml → Actions'ta "Run workflow".
Salt teşhis — hiçbir şey commit'lemez.
"""
import os
import sys
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.config import NEWS_SOURCES, CONTENT_SELECTORS, FEED_SUMMARY_MIN_WORDS

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
CONTENT_NS = '{http://purl.org/rss/1.0/modules/content/}'
ATOM = '{http://www.w3.org/2005/Atom}'

# Kaynak adı → aday seçiciler. Feed URL'i NEWS_SOURCES'tan gelir (yukarıdaki nota bak).
# Adaylar yaygın CMS kalıplarından türetilmiştir; ölçüm hangisinin tuttuğunu söyler.
# Kaynak adları NEWS_SOURCES anahtarlarıyla BİREBİR aynı olmalıdır (aşağıda doğrulanır).
_COMMON = [
    {'class': 'entry-content'}, {'class': 'post-content'}, {'class': 'article-content'},
    {'class': 'article-body'}, {'class': 'content'}, {'name': 'article'}, {'name': 'main'},
]

TARGETS = {
    # 2026-07-29 ölçümü: KALAN>0 ama havuza SIFIR haber giren kaynaklar
    'ANSSI (CERT-FR)': [
        {'class': 'article-content'}, {'class': 'texte'}, {'class': 'content'},
        {'name': 'article'}, {'name': 'main'},
    ],
    'Microsoft Security': [
        {'class': 'entry-content'}, {'class': 'article-body'}, {'class': 'blog-postContent'},
        {'class': 'c-paragraph'}, {'name': 'article'}, {'name': 'main'},
    ],
    'Proofpoint Threat Insight': [
        {'class': 'field--name-body'}, {'class': 'node__content'},
        {'class': 'article-body'}, {'name': 'article'}, {'name': 'main'},
    ],
    'Recorded Future': [
        {'class': 'article-body'}, {'class': 'blog-content'}, {'class': 'rich-text'},
        {'name': 'article'}, {'name': 'main'},
    ],
    'CrowdStrike': [
        {'class': 'blog-content'}, {'class': 'article__body'}, {'class': 'cmp-text'},
        {'class': 'aem-Grid'}, {'name': 'article'},
    ],
    'BSI': [
        {'class': 'c-teaser__text'}, {'class': 'content'}, {'class': 'rich-text'},
        {'name': 'article'}, {'name': 'main'},
    ],
    'Unit 42': [
        {'class': 'entry-content'}, {'class': 'post-content'},
        {'class': 'article-body'}, {'name': 'article'}, {'name': 'main'},
    ],
    'SentinelOne Labs': [
        {'class': 'entry-content'}, {'class': 'post-content'},
        {'class': 'article-body'}, {'name': 'article'}, {'name': 'main'},
    ],
    'Securelist (Kaspersky)': [
        {'class': 'entry-content'}, {'class': 'js-reading-wrapper'},
        {'class': 'content'}, {'name': 'article'}, {'name': 'main'},
    ],
    'NIST': [
        {'class': 'text-content'}, {'class': 'nist-page__content'},
        {'class': 'field--name-body'}, {'name': 'article'}, {'name': 'main'},
    ],
    'Mandiant (Google Cloud)': [
        {'class': 'article-body'}, {'class': 'devsite-article-body'},
        {'class': 'rich-text'}, {'name': 'article'}, {'name': 'main'},
    ],
    'Bellingcat': [
        {'class': 'entry-content'}, {'class': 'post-content'},
        {'class': 'article-body'}, {'name': 'article'}, {'name': 'main'},
    ],
    'Graham Cluley': [
        {'class': 'entry-content'}, {'class': 'post-content'}, {'name': 'article'},
    ],
    # Kısmi kayıp veren, selector'ı TANIMSIZ olan kaynaklar
    'Dark Reading': _COMMON,
    'SANS ISC': _COMMON,
    'The Cyber Express': _COMMON,
    'Talos Intelligence': _COMMON,
    'CERT-EU': [
        {'class': 'field--name-body'}, {'class': 'node__content'},
        {'class': 'content'}, {'name': 'article'}, {'name': 'main'},
    ],
    'NCSC UK': _COMMON,
    'The Record': _COMMON,
    'Schneier on Security': _COMMON,
    'Industrial Cyber': _COMMON,
    'IranWire': _COMMON,
}


def _extract(element):
    """main.py _extract birebir kopyası."""
    if not element:
        return ''
    parts = []
    for p in element.find_all(['p', 'h1', 'h2', 'h3', 'li']):
        t = p.get_text().strip()
        if len(t) > 20 and not any(x in t.lower() for x in ['cookie', 'subscribe', 'newsletter']):
            parts.append(t)
    return '\n\n'.join(parts)


def newest_entry(feed_bytes):
    """Feed'in en yeni maddesinden (link, feed_gövdesi_html) döndürür.

    feed gövdesi main.py fetch_rss ile AYNI önceliği kullanır: content:encoded /
    Atom content önce, description sonra — yani feed-özet fallback'in gerçekte
    göreceği metin ölçülür.
    """
    root = ET.fromstring(feed_bytes)
    if root.tag.endswith('feed'):
        e = root.find(f'.//{ATOM}entry')
        if e is None:
            return None, ''
        l = e.find(f'{ATOM}link')
        link = l.get('href') if l is not None else None
        c = e.find(f'{ATOM}content')
        s = e.find(f'{ATOM}summary')
        body = (c.text if c is not None and c.text else
                (s.text if s is not None and s.text else '')) or ''
        return link, body
    item = root.find('.//item')
    if item is None:
        return None, ''
    l = item.find('link')
    link = (l.text or '').strip() if l is not None else None
    enc = item.find(f'{CONTENT_NS}encoded')
    d = item.find('description')
    body = (enc.text if enc is not None and enc.text else
            (d.text if d is not None and d.text else '')) or ''
    return link, body


def feed_body_wc(body_html):
    """Feed gövdesinin kelime sayısı — main.py _feed_summary_fallback ile aynı yol."""
    if not body_html.strip():
        return 0
    soup = BeautifulSoup(body_html, 'html.parser')
    text = _extract(soup)
    wc = len(text.split())
    plain = soup.get_text(separator='\n').strip()
    return max(wc, len(plain.split()))


def fetch_page(url):
    """main.py fetch_full_article ile aynı: stream + 500KB kap + timeout."""
    r = requests.get(url, headers=HEADERS, timeout=(5, 10), stream=True)
    chunks, total = [], 0
    for chunk in r.iter_content(chunk_size=8192):
        chunks.append(chunk)
        total += len(chunk)
        if total > 500_000:
            break
    r.close()
    raw = b''.join(chunks).decode(r.encoding or 'utf-8', errors='replace')
    return r.status_code, raw


def baseline_wc(soup):
    """main.py'nin seçici-yoksa fallback zinciri: <article>→<main>→content-<div>."""
    for tag in ['article', 'main']:
        el = soup.find(tag)
        if el:
            t = _extract(el)
            if t:
                return len(t.split())
    el = soup.find('div', class_=lambda c: c and any(
        x in str(c).lower() for x in ['content', 'article', 'body', 'post']))
    return len(_extract(el).split()) if el else 0


def main():
    print('=' * 80)
    print('CONTENT_SELECTORS KEŞFİ — makale sayfası kazıma (runner IP = üretim)')
    print(f'eşik: {FEED_SUMMARY_MIN_WORDS} kelime')
    print('=' * 80)

    unknown = [s for s in TARGETS if s not in NEWS_SOURCES]
    if unknown:
        print(f'⚠️  NEWS_SOURCES\'ta olmayan hedef(ler), atlanacak: {unknown}')

    verdicts = {}
    for src, candidates in TARGETS.items():
        if src not in NEWS_SOURCES:
            continue
        feed_url = NEWS_SOURCES[src]
        has_sel = 'seçici TANIMLI' if src in CONTENT_SELECTORS else 'seçici yok'
        print(f'\n■ {src}  ({has_sel})')
        try:
            fr = requests.get(feed_url, headers=HEADERS, timeout=(5, 10))
            if fr.status_code != 200:
                print(f'   ⛔ feed HTTP {fr.status_code}')
                verdicts[src] = 'FEED-ENGELLİ'
                continue
            link, feed_body = newest_entry(fr.content)
        except Exception as e:
            print(f'   feed HATA: {type(e).__name__}: {e}')
            verdicts[src] = 'FEED-HATA'
            continue
        if not link:
            print('   makale linki bulunamadı')
            verdicts[src] = 'LİNK-YOK'
            continue

        fwc = feed_body_wc(feed_body)
        feed_flag = ('YETERLİ' if fwc >= FEED_SUMMARY_MIN_WORDS
                     else f'eşik altı (<{FEED_SUMMARY_MIN_WORDS})')
        print(f'   feed gövdesi: {fwc} kelime [{feed_flag}]')
        print(f'   makale: {link}')

        try:
            code, raw = fetch_page(link)
        except Exception as e:
            print(f'   sayfa HATA: {type(e).__name__}: {e}')
            verdicts[src] = f'SAYFA-HATA (feed {fwc}k)'
            continue
        if code != 200:
            print(f'   ⛔ sayfa HTTP {code} (makale sayfası engelli)')
            verdicts[src] = f'SAYFA-{code} (feed {fwc}k)'
            continue

        soup = BeautifulSoup(raw, 'html.parser')
        base = baseline_wc(soup)
        base_flag = 'OK' if base >= FEED_SUMMARY_MIN_WORDS else 'YETERSİZ'
        print(f'   baseline (mevcut zincir): {base} kelime [{base_flag}]')

        best = None
        for sel in candidates:
            try:
                el = soup.find(**sel)
            except Exception as e:
                print(f'     {str(sel):34} → HATA {e}')
                continue
            wc = len(_extract(el).split()) if el else 0
            mark = ' ✅' if wc >= FEED_SUMMARY_MIN_WORDS else ''
            print(f'     {str(sel):34} → {wc} kelime{mark}')
            if wc >= FEED_SUMMARY_MIN_WORDS and best is None:
                best = sel

        if best:
            print(f'   → ÖNERİLEN SEÇİCİ: {src!r}: [{best}]')
            verdicts[src] = f'SEÇİCİ: {best}'
        elif base >= FEED_SUMMARY_MIN_WORDS:
            print('   → mevcut fallback zaten yeterli (seçici gerekmez)')
            verdicts[src] = 'BASELINE-YETERLİ'
        elif fwc >= FEED_SUMMARY_MIN_WORDS:
            print('   → sayfa kazınamıyor AMA feed gövdesi yeterli → feed-özet yolu kurtarmalı')
            verdicts[src] = f'FEED-YETERLİ ({fwc}k)'
        else:
            print('   → ne sayfa ne feed yeterli → proxy fallback veya eşik gözden geçirme')
            verdicts[src] = f'ÇÖZÜMSÜZ (feed {fwc}k, sayfa {base}k)'

    print('\n' + '=' * 80)
    print('ÖZET')
    print('=' * 80)
    for src, v in verdicts.items():
        print(f'  {src:28} {v}')
    print('=' * 80)


if __name__ == '__main__':
    sys.exit(main())
