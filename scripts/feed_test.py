#!/usr/bin/env python3
"""Feed erişilebilirlik teşhisi — GitHub Actions runner (üretim IP'si) üzerinde çalışır.

Amaç: "şu feed URL'i üretim ortamından çekiyor mu, çekmiyor mu?" sorusunu KESİN
yanıtlamak. Bu ortamın (Claude) egress'i tüm dış siteleri 403 ile kestiği için
doğrulama ancak gerçek runner'da yapılabilir. Workflow: .github/workflows/feed_test.yml

Her URL için: HTTP durumu, XML parse sonucu, <item>/<entry> sayısı ve en yeni
başlık yazdırılır. main.py fetch_rss ile AYNI header + parse mantığı kullanılır ki
sonuç üretimdekiyle birebir olsun.
"""
import email.utils as eu
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

WINDOW_HOURS = 168  # main.py NEWS_WINDOW_HOURS ile aynı

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


def _age_days(raw_date):
    """Yayın tarihini parse edip bugünden kaç GÜN önce olduğunu döner (yoksa None)."""
    if not raw_date:
        return None
    dt = None
    for fmt in ('%a, %d %b %Y %H:%M:%S %z', '%Y-%m-%dT%H:%M:%S%z',
                '%Y-%m-%dT%H:%M:%S.%f%z'):
        try:
            dt = datetime.strptime(raw_date, fmt); break
        except Exception:
            pass
    if dt is None and raw_date.endswith('Z'):
        for fmt in ('%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ'):
            try:
                dt = datetime.strptime(raw_date, fmt).replace(tzinfo=timezone.utc); break
            except Exception:
                pass
    if dt is None:
        try:
            dt = eu.parsedate_to_datetime(raw_date)
        except Exception:
            return None
    if dt is None:
        return None
    if not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0


def parse_items(content):
    """(madde_sayisi, en_yeni_baslik, en_yeni_tarih_ham, pencere_ici_sayi) döner."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        return -1, f'XML parse hatası: {str(e)[:60]}', '', 0
    items = []  # (title, raw_date)
    if root.tag.endswith('feed'):  # Atom
        ns = '{http://www.w3.org/2005/Atom}'
        for entry in root.findall(f'.//{ns}entry'):
            t = entry.find(f'{ns}title')
            d = entry.find(f'{ns}published') or entry.find(f'{ns}updated')
            if t is not None and (t.text or '').strip():
                items.append((t.text.strip(), d.text if d is not None else ''))
    else:  # RSS
        for item in root.findall('.//item'):
            t = item.find('title')
            p = item.find('pubDate')
            if t is not None and (t.text or '').strip():
                items.append((t.text.strip(), p.text if p is not None else ''))
    if not items:
        return 0, '(başlık yok)', '', 0
    # pencere içi (168s) madde sayısı: tarihsiz olanlar güvenli tarafta sayılır
    within = 0
    for _, rd in items:
        age = _age_days(rd)
        if age is None or age <= WINDOW_HOURS / 24.0:
            within += 1
    return len(items), items[0][0][:60], items[0][1], within


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
            n, newest, newest_date, within = parse_items(r.content)
            if n > 0:
                ok += 1
                age = _age_days(newest_date)
                age_s = f'{age:.0f}g' if age is not None else 'tarih?'
                flag = '' if within > 0 else '  ⛔ HEPSİ >7 GÜN (pencere eler → 0)'
                print(f'✅ [200] {label:28} madde={n:<3} pencere-içi={within:<2} '
                      f'en-yeni={age_s:>6} → {newest}{flag}')
            elif n == 0:
                empty += 1
                print(f'⚠️  [200] {label:28} BOŞ (0 madde) — {url}')
            else:
                empty += 1
                print(f'⚠️  [200] {label:28} {newest}')
        except Exception as e:
            blocked += 1
            print(f'❌ [ERR] {label:30} {type(e).__name__}: {str(e)[:50]}')
    print('-' * 78)
    print(f'ÖZET: {ok} çekiyor / {empty} boş-veya-parse-hatası / {blocked} erişilemez')
    print('=' * 78)


if __name__ == '__main__':
    sys.exit(main())
