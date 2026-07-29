#!/usr/bin/env python3
"""DEDUP teşhisi — haberleri hangi filtre seviyesinin elediğini ÜRETİM koduyla ölçer.

2026-07-29 denetim zinciri:
  1. kaynak_saglik.txt: ANSSI 30 KALAN → havuzda 0 haber, durum "OK".
  2. selector_probe: ANSSI'nin seçicisi ZATEN doğru, sayfa kazınabiliyor.
  3. fetch_probe: üretim koşullarında ANSSI 6/8, CrowdStrike/NIST/Securelist 8/8,
     SIFIR timeout → çekim suçlu DEĞİL.
  4. Kod okuması: _filter_duplicates + _filter_old_articles, tam metin çekiminden
     SONRA çalışıyor → kayıp orada.
  5. Yerel ölçüm: bu kaynakların 7 günlük link geçmişinde yalnızca 1-3 kaydı var,
     yani Seviye-1 (link eşleşmesi) 30 haberi eleyemez.

Geriye kalan soru: Seviye 2 (hash) / 3 (benzerlik) / 4 (anahtar kelime) /
5 (kod adı) / pencere — hangisi eliyor? Bu script onu TAHMİN ETMEZ, ölçer:
kaynak başına üretimin kendi fetch_rss + fetch_full_article + _filter_duplicates
+ _filter_old_articles zincirini çalıştırır ve _filter_duplicates'in bastığı
seviye-bazlı sayaçları yakalar.

Kaynak başına İZOLE çalıştırılır (tek kaynaklı all_news) ki elenme o kaynağa
atfedilebilsin; üretimde kaynaklar arası dedup da vardır, bu yüzden buradaki
sayılar ALT SINIRDIR (üretimde en az bu kadar elenir).

Çalıştır: .github/workflows/selector_probe.yml. Salt teşhis — commit'lemez.
NOT: repo checkout'undaki GERÇEK data/haberler_linkler.txt geçmişini kullanır.
"""
import io
import os
import sys
import contextlib

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import main as M
from src.config import NEWS_SOURCES

# Havuza hiç haber veremeyen kaynaklardan örneklem + KONTROL.
# 'The Register' kontrol: üretimde bol haber veriyor; burada da yüksek HAVUZ
# vermezse ölçüm kurgusu hatalıdır (izolasyon fazla eliyor demektir).
TARGETS = [
    'ANSSI (CERT-FR)',
    'CrowdStrike',
    'NIST',
    'Securelist (Kaspersky)',
    'BSI',
    'Recorded Future',
    'The Register',           # KONTROL
]


def main():
    print('=' * 84)
    print('DEDUP TEŞHİSİ — hangi filtre seviyesi eliyor? (üretim kodu, izole kaynak)')
    print('=' * 84)

    sis = M.HaberSistemi()
    summary = {}

    for src in TARGETS:
        if src not in NEWS_SOURCES:
            print(f'\n■ {src} — NEWS_SOURCES\'ta yok, atlanıyor')
            continue
        print(f'\n{"=" * 84}\n■ {src}\n{"=" * 84}')

        articles = sis.fetch_rss(NEWS_SOURCES[src], src)
        n_raw = len(articles)
        if not articles:
            print('   feed boş / hata')
            summary[src] = f'HAM=0'
            continue

        # Üretimdeki satır-içi pencere filtresi (kaynak-farkında)
        cutoff = sis._news_cutoff_dt(src)
        articles = [a for a in articles if sis._article_within_window(a, cutoff)]
        n_win = len(articles)

        # Üretimdeki tam metin zinciri: makale → feed özeti → proxy
        for a in articles:
            if a.get('link') and not a.get('success'):
                a.update(sis.fetch_full_article(a['link'], src))
                if not a.get('success'):
                    sis._feed_summary_fallback(a)
        n_text = sum(1 for a in articles
                     if a.get('success') and a.get('word_count', 0) > 0)

        # Üretim dedup zinciri — seviye sayaçlarını yakalamak için stdout'u al
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            out = sis._filter_duplicates({src: articles})
            out = sis._filter_old_articles(out)
        dedup_log = buf.getvalue()
        n_pool = sum(1 for arts in out.values() for a in arts
                     if a.get('success') and a.get('word_count', 0) > 0)

        print(f'\n   HAM={n_raw}  PENCERE_SONRASI={n_win}  TAM_METİN={n_text}  HAVUZ={n_pool}')
        print('   ── dedup çıktısı (seviye sayaçları) ──')
        for line in dedup_log.splitlines():
            if line.strip():
                print(f'   {line}')

        kayip_dedup = n_text - n_pool
        summary[src] = (f'HAM={n_raw} PENCERE={n_win} METİN={n_text} HAVUZ={n_pool} '
                        f'→ dedup kaybı={kayip_dedup}')

    print('\n' + '=' * 84)
    print('ÖZET')
    print('=' * 84)
    for src, v in summary.items():
        print(f'  {src:26} {v}')
    print('=' * 84)
    print('Kontrol: The Register yüksek HAVUZ vermezse ölçüm kurgusu hatalıdır.')


if __name__ == '__main__':
    sys.exit(main())
