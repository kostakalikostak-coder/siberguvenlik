#!/usr/bin/env python3
"""Proxy keşfi — IP-engelli kaynakları temiz-IP proxy'siyle çekmeyi runner'da (üretim
IP'si) ölçer.

CISA / SANS ISC / Sophos feed'leri GitHub Actions IP'lerinden engelli (403/boş/timeout).
DFIR makale sayfası kazınamıyor. Bu probe, her engelli kaynak × her proxy adayı için
"proxy 200 + geçerli içerik veriyor mu" sorusunu KESİN yanıtlar. Salt teşhis; hiçbir
şey commit'lemez. Çalıştır: .github/workflows/proxy_probe.yml (workflow_dispatch).

Placeholder: {enc}=urlencode(url, safe=''), {raw}=url olduğu gibi.
"""
import re
import sys
import xml.etree.ElementTree as ET
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# Ham upstream gövdeyi döndüren proxy adayları (feed XML için — parse edilebilmeli)
FEED_PROXIES = {
    'direct (kontrol)':      '{raw}',
    'allorigins/raw':        'https://api.allorigins.win/raw?url={enc}',
    'codetabs':              'https://api.codetabs.com/v1/proxy/?quest={enc}',
    'corsproxy.io':          'https://corsproxy.io/?url={enc}',
    'thingproxy':            'https://thingproxy.freeboard.io/fetch/{raw}',
}

BLOCKED_FEEDS = {
    'CISA':      'https://www.cisa.gov/cybersecurity-advisories/all.xml',
    'SANS ISC':  'https://isc.sans.edu/rssfeed.xml',
    'Sophos':    'https://news.sophos.com/en-us/feed/',
}

# DFIR makale gövdesi için (feed'i doğrudan çalışıyor, sorun makale sayfası)
DFIR_FEED = 'https://thedfirreport.com/feed/'
ARTICLE_PROXIES = {
    'direct (kontrol)': '{raw}',
    'jina reader':      'https://r.jina.ai/{raw}',
    'allorigins/raw':   'https://api.allorigins.win/raw?url={enc}',
}


def build(tpl, url):
    return tpl.replace('{enc}', quote(url, safe='')).replace('{raw}', url)


def count_items(content):
    try:
        root = ET.fromstring(content)
    except Exception:
        # XML değilse (HTML/challenge) ham içinde <item> ara — yine de fikir verir
        n = len(re.findall(rb'<item[ >]|<entry[ >]', content))
        return (n, '(XML değil)') if n else (-1, 'XML parse hatası / içerik yok')
    titles = []
    if root.tag.endswith('feed'):
        ns = '{http://www.w3.org/2005/Atom}'
        for e in root.findall(f'.//{ns}entry'):
            t = e.find(f'{ns}title')
            if t is not None and (t.text or '').strip():
                titles.append(t.text.strip())
    else:
        for it in root.findall('.//item'):
            t = it.find('title')
            if t is not None and (t.text or '').strip():
                titles.append(t.text.strip())
    return len(titles), (titles[0][:55] if titles else '(başlık yok)')


def _extract_wc(html):
    soup = BeautifulSoup(html, 'html.parser')
    for tag in ['article', 'main']:
        el = soup.find(tag)
        if el:
            parts = [p.get_text().strip() for p in el.find_all(['p', 'h1', 'h2', 'h3', 'li'])
                     if len(p.get_text().strip()) > 20]
            if parts:
                return len('\n'.join(parts).split())
    return 0


def main():
    print('=' * 82)
    print('PROXY KEŞFİ — IP-engelli kaynaklar (runner IP = üretim)')
    print('=' * 82)

    for src, url in BLOCKED_FEEDS.items():
        print(f'\n■ {src}  ({url})')
        for name, tpl in FEED_PROXIES.items():
            purl = build(tpl, url)
            try:
                r = requests.get(purl, headers=HEADERS, timeout=(6, 15))
                if r.status_code != 200:
                    print(f'   {name:18} ❌ HTTP {r.status_code}')
                    continue
                n, newest = count_items(r.content)
                if n > 0:
                    print(f'   {name:18} ✅ madde={n:<3} → {newest}')
                else:
                    print(f'   {name:18} ⚠️  {newest}')
            except Exception as e:
                print(f'   {name:18} ❌ {type(e).__name__}: {str(e)[:45]}')

    # DFIR makale gövdesi
    print(f'\n■ The DFIR Report — makale gövdesi')
    try:
        fr = requests.get(DFIR_FEED, headers=HEADERS, timeout=(6, 15))
        root = ET.fromstring(fr.content)
        item = root.find('.//item')
        link = item.find('link').text.strip()
        print(f'   makale: {link}')
        for name, tpl in ARTICLE_PROXIES.items():
            purl = build(tpl, link)
            try:
                r = requests.get(purl, headers=HEADERS, timeout=(8, 25))
                if r.status_code != 200:
                    print(f'   {name:18} ❌ HTTP {r.status_code}')
                    continue
                if name == 'jina reader':
                    wc = len(r.text.split())  # Jina temiz metin/markdown döndürür
                else:
                    wc = _extract_wc(r.text)
                mark = ' ✅' if wc > 100 else ''
                print(f'   {name:18} kelime={wc}{mark}')
            except Exception as e:
                print(f'   {name:18} ❌ {type(e).__name__}: {str(e)[:45]}')
    except Exception as e:
        print(f'   DFIR feed HATA: {e}')

    print('\n' + '=' * 82)


if __name__ == '__main__':
    sys.exit(main())
