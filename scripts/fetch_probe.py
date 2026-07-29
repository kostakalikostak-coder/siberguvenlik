#!/usr/bin/env python3
"""Tam metin ÇEKİM teşhisi — üretim koşullarını birebir taklit eder.

selector_probe.py "doğru seçici hangisi?" sorusunu cevaplar ve TEK makale çeker.
Ama 2026-07-29 denetiminde şu çelişki çıktı: ANSSI/CrowdStrike/NIST/Securelist/
Bellingcat gibi kaynakların seçicileri ZATEN doğru (probe onları buldu), yine de
üretimde haberlerinin %100'ü düşüyor. Demek ki sorun seçici değil, ÇEKİM.

Hipotez: üretimdeki fetch_full_article
  - timeout=(3,5) kullanıyor (probe: (5,10)),
  - _requests_get_with_retry ile 4 denemeye kadar çıkıyor (backoff 1+2+4=7s),
  - ama tüm bunları 10 SANİYELİK bir thread sınırıyla kesiyor,
  - ve kaynak başına ~30 makaleyi arada yalnızca 0.5s ile art arda çekiyor.
Bir kaynak throttle'lamaya başlarsa (429/503) retry zinciri 10s'yi aşar ve
o kaynağın TÜM makaleleri sessizce başarısız olur.

Bu script hipotezi ÖLÇER: kaynak başına ilk N makaleyi üretimin AYNI kod yoluyla
(aynı timeout, aynı retry, aynı 10s sınır, aynı seçici zinciri, aynı 0.5s bekleme)
çeker ve her makale için sonuç + süre + HTTP durumu raporlar.

Çalıştır: .github/workflows/selector_probe.yml (ikinci adım). Salt teşhis.
"""
import os
import sys
import time
import threading
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from src.config import NEWS_SOURCES, CONTENT_SELECTORS
from src.http_utils import requests_get_with_retry

HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
CONTENT_NS = '{http://purl.org/rss/1.0/modules/content/}'
ATOM = '{http://www.w3.org/2005/Atom}'

# Üretimde tamamı kaybolan kaynaklardan seçilmiş örneklem + kontrol grubu.
# 'The Register' KONTROL: üretimde 26/29 başarılı → ölçüm doğruysa burada da
# yüksek başarı görmeliyiz. Görmezsek hipotez değil, ölçüm hatalıdır.
TARGETS = [
    'ANSSI (CERT-FR)',        # 30 kalan → 0 havuz
    'CrowdStrike',            # seçici doğru, yine 0
    'NIST',                   # seçici doğru, yine 0
    'Securelist (Kaspersky)', # seçici doğru, yine 0
    'Microsoft Security',     # seçici yok
    'The Register',           # KONTROL — üretimde çalışıyor
]
PER_SOURCE = 8          # kaynak başına denenecek makale sayısı
PROD_TIMEOUT = (3, 5)   # main.fetch_full_article ile AYNI
PROD_THREAD_CAP = 10    # main.fetch_full_article thread join timeout


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


def feed_links(feed_bytes, limit):
    """Feed'den ilk `limit` makale linkini döndürür."""
    root = ET.fromstring(feed_bytes)
    out = []
    if root.tag.endswith('feed'):
        for e in root.findall(f'.//{ATOM}entry')[:limit]:
            l = e.find(f'{ATOM}link')
            if l is not None and l.get('href'):
                out.append(l.get('href'))
    else:
        for item in root.findall('.//item')[:limit]:
            l = item.find('link')
            if l is not None and (l.text or '').strip():
                out.append(l.text.strip())
    return out


def prod_fetch(url, source_name):
    """main.fetch_full_article'ın ÖLÇÜM kopyası: aynı timeout/retry/sınır/zincir.

    Döner: (sonuç, kelime, saniye, detay)
    """
    res = {'wc': 0, 'ok': False, 'detail': ''}
    holder = [None]

    def _fetch():
        try:
            r = requests_get_with_retry(url, headers=HEADERS,
                                        timeout=PROD_TIMEOUT, stream=True)
            holder[0] = r
            res['detail'] = f'HTTP {r.status_code}'
            if r.status_code != 200:
                return
            chunks, total = [], 0
            for chunk in r.iter_content(chunk_size=8192):
                chunks.append(chunk)
                total += len(chunk)
                if total > 500_000:
                    break
            r.close()
            raw = b''.join(chunks).decode(r.encoding or 'utf-8', errors='replace')
            soup = BeautifulSoup(raw, 'html.parser')
            text = ''
            # main.py ile AYNI zincir (ilk EŞLEŞEN seçicide break dahil)
            if source_name in CONTENT_SELECTORS:
                for sel in CONTENT_SELECTORS[source_name]:
                    el = soup.find(**sel)
                    if el:
                        text = _extract(el)
                        break
            if not text:
                for tag in ['article', 'main']:
                    el = soup.find(tag)
                    if el:
                        text = _extract(el)
                        if text:
                            break
            if not text:
                el = soup.find('div', class_=lambda c: c and any(
                    x in str(c).lower() for x in ['content', 'article', 'body', 'post']))
                if el:
                    text = _extract(el)
            wc = len(text.split()) if text else 0
            res['wc'] = wc
            res['ok'] = wc > 100          # main.py eşiği
            if not res['ok'] and r.status_code == 200:
                res['detail'] = f'HTTP 200 ama {wc} kelime'
        except Exception as e:
            res['detail'] = f'{type(e).__name__}: {str(e)[:60]}'

    t0 = time.time()
    th = threading.Thread(target=_fetch, daemon=True)
    th.start()
    th.join(timeout=PROD_THREAD_CAP)
    elapsed = time.time() - t0
    if th.is_alive():
        try:
            if holder[0] is not None:
                holder[0].close()
        except Exception:
            pass
        return 'TIMEOUT', 0, elapsed, f'{PROD_THREAD_CAP}s sınırı aşıldı'
    return ('OK' if res['ok'] else 'BAŞARISIZ'), res['wc'], elapsed, res['detail']


def main():
    print('=' * 84)
    print('TAM METİN ÇEKİM TEŞHİSİ — üretim koşulları (timeout=(3,5), 10s sınır, retry açık)')
    print(f'kaynak başına {PER_SOURCE} makale, arada 0.5s (üretimle aynı)')
    print('=' * 84)

    summary = {}
    for src in TARGETS:
        if src not in NEWS_SOURCES:
            print(f'\n■ {src} — NEWS_SOURCES\'ta yok, atlanıyor')
            continue
        print(f'\n■ {src}')
        try:
            fr = requests.get(NEWS_SOURCES[src], headers=HEADERS, timeout=(5, 10))
            links = feed_links(fr.content, PER_SOURCE)
        except Exception as e:
            print(f'   feed HATA: {type(e).__name__}: {e}')
            summary[src] = 'FEED-HATA'
            continue
        if not links:
            print('   makale linki yok')
            summary[src] = 'LİNK-YOK'
            continue

        ok = timeout = fail = 0
        for i, link in enumerate(links, 1):
            verdict, wc, elapsed, detail = prod_fetch(link, src)
            if verdict == 'OK':
                ok += 1
            elif verdict == 'TIMEOUT':
                timeout += 1
            else:
                fail += 1
            print(f'   [{i}/{len(links)}] {verdict:9} {wc:5}k {elapsed:6.1f}s  {detail}')
            time.sleep(0.5)          # üretimle aynı bekleme

        summary[src] = f'OK={ok} TIMEOUT={timeout} BAŞARISIZ={fail} (/{len(links)})'

    print('\n' + '=' * 84)
    print('ÖZET')
    print('=' * 84)
    for src, v in summary.items():
        print(f'  {src:26} {v}')
    print('=' * 84)
    print('Yorum: TIMEOUT baskınsa → 10s sınırı + retry zinciri suçlu (timeout/retry ayarı).')
    print('       BAŞARISIZ baskınsa → çekim oluyor ama içerik çıkmıyor (seçici/eşik).')
    print('       Kontrol kaynağı (The Register) yüksek OK vermezse ölçüm hatalıdır.')


if __name__ == '__main__':
    sys.exit(main())
