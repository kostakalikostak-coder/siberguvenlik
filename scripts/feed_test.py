#!/usr/bin/env python3
"""Feed erişilebilirlik teşhisi — GitHub Actions runner (üretim IP'si) üzerinde çalışır.

Amaç: "şu feed URL'i üretim ortamından çekiyor mu, çekmiyor mu?" sorusunu KESİN
yanıtlamak. Bu ortamın (Claude) egress'i tüm dış siteleri 403 ile kestiği için
doğrulama ancak gerçek runner'da yapılabilir. Workflow: .github/workflows/feed_test.yml

Her URL için: HTTP durumu, XML parse sonucu, <item>/<entry> sayısı ve en yeni
başlık yazdırılır. main.py fetch_rss ile AYNI header + parse mantığı kullanılır ki
sonuç üretimdekiyle birebir olsun.
"""
import sys
import xml.etree.ElementTree as ET

import requests

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

# 13 günde HİÇ üretmeyen aktif kaynakların MEVCUT config URL'leri +
# WebSearch ile bulunan ADAY alternatifler. label başına test edilir.
TARGETS = {
    # --- Kronik sessiz kaynaklar: mevcut config URL'leri ---
    'CrowdStrike (mevcut)':        'https://www.crowdstrike.com/blog/feed/',
    'CrowdStrike (aday /en-us/)':  'https://www.crowdstrike.com/en-us/blog/feed/',
    'Microsoft Security (mevcut)': 'https://www.microsoft.com/en-us/security/blog/feed/',
    'NIST (mevcut)':               'https://www.nist.gov/news-events/news/rss.xml',
    'ANSSI CERT-FR (mevcut)':      'https://www.cert.ssi.gouv.fr/feed/',
    'CERT-EU (mevcut)':            'https://cert.europa.eu/publications/security-advisories-rss',
    'The Cyber Express (mevcut)':  'https://www.thecyberexpress.com/feed/',
    'Bellingcat (mevcut)':         'https://www.bellingcat.com/feed/',
    'Citizen Lab (mevcut)':        'https://citizenlab.ca/feed/',
    'The DFIR Report (mevcut)':    'https://thedfirreport.com/feed/',
    'IranWire (mevcut)':           'https://iranwire.com/en/feed/',
    # --- Kıyas için istikrarlı çalışan bir kaynak (kontrol grubu) ---
    'BleepingComputer (kontrol)':  'https://www.bleepingcomputer.com/feed/',
    'The Hacker News (kontrol)':   'https://feeds.feedburner.com/TheHackersNews',
}


def parse_items(content):
    """(item_sayisi, en_yeni_baslik) döner; parse edilemezse (-1, hata)."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        return -1, f'XML parse hatası: {str(e)[:60]}'
    titles = []
    if root.tag.endswith('feed'):  # Atom
        ns = '{http://www.w3.org/2005/Atom}'
        for entry in root.findall(f'.//{ns}entry'):
            t = entry.find(f'{ns}title')
            if t is not None and (t.text or '').strip():
                titles.append(t.text.strip())
    else:  # RSS
        for item in root.findall('.//item'):
            t = item.find('title')
            if t is not None and (t.text or '').strip():
                titles.append(t.text.strip())
    return len(titles), (titles[0][:70] if titles else '(başlık yok)')


def main():
    print('=' * 78)
    print('FEED ERİŞİLEBİLİRLİK TEŞHİSİ — runner IP (üretim ile aynı)')
    print('=' * 78)
    ok = blocked = empty = 0
    for label, url in TARGETS.items():
        try:
            r = requests.get(url, headers=HEADERS, timeout=(5, 10))
            code = r.status_code
            if code != 200:
                blocked += 1
                print(f'❌ [{code}] {label:30} {url}')
                continue
            n, newest = parse_items(r.content)
            if n > 0:
                ok += 1
                print(f'✅ [200] {label:30} madde={n:<3} → {newest}')
            elif n == 0:
                empty += 1
                print(f'⚠️  [200] {label:30} BOŞ (0 madde) — {url}')
            else:
                empty += 1
                print(f'⚠️  [200] {label:30} {newest}')
        except Exception as e:
            blocked += 1
            print(f'❌ [ERR] {label:30} {type(e).__name__}: {str(e)[:50]}')
    print('-' * 78)
    print(f'ÖZET: {ok} çekiyor / {empty} boş-veya-parse-hatası / {blocked} erişilemez')
    print('=' * 78)


if __name__ == '__main__':
    sys.exit(main())
