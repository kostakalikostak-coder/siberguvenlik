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
# Genel teşhis hedefleri: bir kaynak sorunlu görününce buraya MEVCUT config URL'i +
# ADAY alternatifleri yaz, workflow'u çalıştır, çıktıdan çalışan varyantı seç.
# (Örnek geçmiş: Help Net "Exceeded 30 redirects" → /feed/ ile çözüldü.)
TARGETS = {
    # --- The Register: 2026-07-28 üretiminde HTTP 403 verdi (önceden çalışıyordu).
    #     Geçici mi kalıcı mı + çalışan varyant var mı, üretim IP'sinde ölçülüyor.
    'TheReg (mevcut cyber_crime)': 'https://www.theregister.com/security/cyber_crime/headlines.atom',
    'TheReg (security)':           'https://www.theregister.com/security/headlines.atom',
    'TheReg (kök headlines)':      'https://www.theregister.com/headlines.atom',
    'TheReg (offsite RSS)':        'https://www.theregister.com/security/cyber_crime/headlines.rss',
    'TheReg (feed yolu)':          'https://www.theregister.com/security/feed/',
    # --- Kontrol grubu (çalıştığı bilinen kaynaklar) ---
    'BleepingComputer (kontrol)':  'https://www.bleepingcomputer.com/feed/',
    'Help Net Security (kontrol)': 'https://www.helpnetsecurity.com/feed/',
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


import re as _re

DESC_FLOOR = 100  # description-fallback için önerilen kelime tabanı


def _wc(html_or_text):
    """HTML etiketlerini soyup kelime sayısı döner."""
    if not html_or_text:
        return 0
    txt = _re.sub(r'<[^>]+>', ' ', html_or_text)
    txt = _re.sub(r'&[a-z]+;', ' ', txt)
    return len(txt.split())


def parse_items(content):
    """(madde_sayisi, en_yeni_baslik, en_yeni_tarih, pencere_ici, en_yeni_desc_wc,
    fallback_kurtarilan) döner. fallback_kurtarilan = pencere içindeki maddelerden
    description'ı >= DESC_FLOOR kelime olanların sayısı."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as e:
        return -1, f'XML parse hatası: {str(e)[:60]}', '', 0, 0, 0
    ATOM = '{http://www.w3.org/2005/Atom}'
    CONTENT_NS = '{http://purl.org/rss/1.0/modules/content/}'
    items = []  # (title, raw_date, desc_wc)
    if root.tag.endswith('feed'):  # Atom
        for entry in root.findall(f'.//{ATOM}entry'):
            t = entry.find(f'{ATOM}title')
            d = entry.find(f'{ATOM}published')
            if d is None:
                d = entry.find(f'{ATOM}updated')
            body = entry.find(f'{ATOM}content')
            if body is None:
                body = entry.find(f'{ATOM}summary')
            if t is not None and (t.text or '').strip():
                items.append((t.text.strip(), d.text if d is not None else '',
                              _wc(body.text if body is not None else '')))
    else:  # RSS
        for item in root.findall('.//item'):
            t = item.find('title')
            p = item.find('pubDate')
            enc = item.find(f'{CONTENT_NS}encoded')
            desc = item.find('description')
            body = enc if enc is not None else desc
            if t is not None and (t.text or '').strip():
                items.append((t.text.strip(), p.text if p is not None else '',
                              _wc(body.text if body is not None else '')))
    if not items:
        return 0, '(başlık yok)', '', 0, 0, 0
    within = rescue = 0
    for _, rd, dwc in items:
        age = _age_days(rd)
        if age is None or age <= WINDOW_HOURS / 24.0:
            within += 1
            if dwc >= DESC_FLOOR:
                rescue += 1
    return (len(items), items[0][0][:50], items[0][1], within,
            items[0][2], rescue)


def main():
    print('=' * 78)
    print('FEED ERİŞİLEBİLİRLİK TEŞHİSİ — runner IP (üretim ile aynı)')
    print('=' * 78)
    ok = blocked = empty = 0
    for label, url in TARGETS.items():
        try:
            r = requests.get(url, headers=HEADERS, timeout=(5, 10))
            code = r.status_code
            redir = f' [{len(r.history)} redirect → {r.url}]' if r.history else ''
            if code != 200:
                blocked += 1
                print(f'❌ [{code}] {label:30} {url}{redir}')
                continue
            n, newest, newest_date, within, desc_wc, rescue = parse_items(r.content)
            if n > 0:
                ok += 1
                age = _age_days(newest_date)
                age_s = f'{age:.0f}g' if age is not None else 'tarih?'
                flag = '' if within > 0 else '  ⛔ HEPSİ >7 GÜN'
                print(f'✅ [200] {label:26} madde={n:<3} pencere-içi={within:<2} '
                      f'en-yeni={age_s:>5}{redir}  → {newest}')
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
