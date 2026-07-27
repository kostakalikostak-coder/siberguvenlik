#!/usr/bin/env python3
"""CONTENT_SELECTORS keşfi — makale sayfası kazımasını runner IP'sinde (üretim) ölçer.

feed-özet fallback'in kurtaramadığı kaynaklar (feed'de özet zayıf, tam metin makale
sayfasında) için doğru içerik seçicisini TAHMİNLE değil ÖLÇEREK bulur.

Her hedef kaynak için:
  1. Feed'den en yeni makale linkini al.
  2. Makale sayfasını main.py fetch_full_article ile AYNI header/stream/timeout ile çek.
  3. Şunların kelime sayısını raporla:
     - MEVCUT zincir (main.py: selectors→<article>→<main>→content-<div>)  [baseline]
     - kaynağa özel ADAY seçiciler
  4. >100 kelime veren ilk seçici = önerilen CONTENT_SELECTORS girişi.

Çalıştır: .github/workflows/feed_test.yml (aynı workflow, script'i değiştir) veya
ayrı bir dispatch. Salt teşhis — hiçbir şey commit'lemez.
"""
import sys
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
CONTENT_NS = '{http://purl.org/rss/1.0/modules/content/}'
ATOM = '{http://www.w3.org/2005/Atom}'

# Hedef kaynak → (feed_url, [aday seçiciler]). Seçiciler main.py CONTENT_SELECTORS
# formatında: {'class': 'x'} veya {'name': 'article'} → soup.find(**sel).
TARGETS = {
    'CrowdStrike': ('https://www.crowdstrike.com/en-us/blog/feed/', [
        {'class': 'blog-content'}, {'class': 'article__body'}, {'class': 'cmp-text'},
        {'class': 'aem-Grid'}, {'name': 'article'},
    ]),
    'ANSSI (CERT-FR)': ('https://www.cert.ssi.gouv.fr/feed/', [
        {'class': 'article-content'}, {'class': 'texte'}, {'class': 'content'},
        {'name': 'article'}, {'name': 'main'},
    ]),
    'CERT-EU': ('https://cert.europa.eu/publications/security-advisories-rss', [
        {'class': 'field--name-body'}, {'class': 'node__content'},
        {'class': 'content'}, {'name': 'article'}, {'name': 'main'},
    ]),
    'NIST': ('https://www.nist.gov/news-events/news/rss.xml', [
        {'class': 'text-content'}, {'class': 'nist-page__content'},
        {'class': 'field--name-body'}, {'name': 'article'}, {'name': 'main'},
    ]),
    'IranWire': ('https://iranwire.com/en/feed/', [
        {'class': 'article-body'}, {'class': 'post-content'},
        {'class': 'entry-content'}, {'name': 'article'}, {'name': 'main'},
    ]),
    'The DFIR Report': ('https://thedfirreport.com/feed/', [
        {'class': 'entry-content'}, {'class': 'post-content'}, {'name': 'article'},
    ]),
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


def newest_link(feed_bytes):
    root = ET.fromstring(feed_bytes)
    if root.tag.endswith('feed'):
        e = root.find(f'.//{ATOM}entry')
        if e is None:
            return None
        l = e.find(f'{ATOM}link')
        return l.get('href') if l is not None else None
    item = root.find('.//item')
    if item is None:
        return None
    l = item.find('link')
    return (l.text or '').strip() if l is not None else None


def fetch_page(url):
    """main.py fetch_full_article ile aynı: stream + 500KB kap + (3,5) timeout."""
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
    print('=' * 80)
    for src, (feed_url, candidates) in TARGETS.items():
        print(f'\n■ {src}')
        try:
            fr = requests.get(feed_url, headers=HEADERS, timeout=(5, 10))
            link = newest_link(fr.content)
        except Exception as e:
            print(f'   feed HATA: {e}')
            continue
        if not link:
            print('   makale linki bulunamadı')
            continue
        print(f'   makale: {link}')
        try:
            code, raw = fetch_page(link)
        except Exception as e:
            print(f'   sayfa HATA: {e}')
            continue
        if code != 200:
            print(f'   ⛔ sayfa HTTP {code} (makale sayfası engelli)')
            continue
        soup = BeautifulSoup(raw, 'html.parser')
        base = baseline_wc(soup)
        base_flag = 'OK' if base > 100 else 'YETERSİZ'
        print(f'   baseline (mevcut zincir): {base} kelime [{base_flag}]')
        best = None
        for sel in candidates:
            try:
                el = soup.find(**sel)
            except Exception as e:
                print(f'     {sel}  → HATA {e}')
                continue
            wc = len(_extract(el).split()) if el else 0
            mark = ' ✅' if wc > 100 else ''
            print(f'     {str(sel):32} → {wc} kelime{mark}')
            if wc > 100 and best is None:
                best = sel
        if best:
            print(f'   → ÖNERİLEN: {src!r}: [{best}]')
        elif base > 100:
            print(f'   → mevcut fallback zaten yeterli (seçici gerekmez)')
        else:
            print(f'   → seçici bulunamadı; feed-özet fallback veya kaynak dışı bırakma')
    print('\n' + '=' * 80)


if __name__ == '__main__':
    sys.exit(main())
