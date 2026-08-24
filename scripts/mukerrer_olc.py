#!/usr/bin/env python3
"""MÜKERRER ÖLÇÜMÜ — elle etiketli referansa karşı isabet.

NEDEN VAR: "aynı olay"ın üç ayrı tanımı vardı ve hangisinin daha doğru olduğu
ÖLÇÜLMEMİŞTİ. Bu betik data/mukerrer_golden.json'daki elle etiketli 38 çifte
karşı üç seçeneği yan yana ölçer:
  same_event      — src.dedup (bag-of-words + entity/codename)
  dört-değerli    — olay_iliski.iliski_belirle (AYNI_GELISME|YENI_GELISME)
  ayni_olay       — olay_iliski.ayni_olay (birleşik tanım)

Kullanım: GEMINI_API_KEY=x python3 scripts/mukerrer_olc.py
"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src import dedup, olay_iliski as O  # noqa: E402


def _yukle():
    def _oku(p):
        with open(p, encoding='utf-8') as f:
            return {r['date']: r.get('views', []) or [] for r in json.load(f)}
    rapor = _oku('data/rapor_gecmis.json')
    with open('data/mukerrer_golden.json', encoding='utf-8') as f:
        altin = json.load(f)['ciftler']
    indeks = {(g, (v.get('tr_title') or '')): v
              for g, vs in rapor.items() for v in vs}
    tum = [v for vs in rapor.values() for v in vs]
    return altin, indeks, O.OlaySozlugu(tum)


def olc():
    altin, indeks, sz = _yukle()
    yontemler = {
        'same_event': lambda a, b: dedup.same_event(a, b, cross_day=True),
        'dört-değerli': lambda a, b: O.iliski_belirle(a, b, sozluk=sz)
        in (O.AYNI_GELISME, O.YENI_GELISME),
        'ayni_olay': lambda a, b: O.ayni_olay(a, b, sozluk=sz),
    }
    sonuc = {}
    hatalar = {ad: [] for ad in yontemler}
    atlanan = 0
    for c in altin:
        a = indeks.get((c['gun_a'], c['baslik_a']))
        b = indeks.get((c['gun_b'], c['baslik_b']))
        if a is None or b is None:
            atlanan += 1
            continue
        for ad, fn in yontemler.items():
            tahmin = bool(fn(b, a))
            dogru = tahmin == c['mukerrer']
            sonuc.setdefault(ad, {'dogru': 0, 'kacan': 0, 'sahte': 0})
            if dogru:
                sonuc[ad]['dogru'] += 1
            elif c['mukerrer']:
                sonuc[ad]['kacan'] += 1
                hatalar[ad].append(('KAÇAN', c))
            else:
                sonuc[ad]['sahte'] += 1
                hatalar[ad].append(('SAHTE', c))
    n = len(altin) - atlanan
    print(f"referans: {n} çift"
          + (f" ({atlanan} çift geçmişten düşmüş, atlandı)" if atlanan else ""))
    for ad, s in sonuc.items():
        print(f"  {ad:14s} {s['dogru']:2d}/{n} doğru | "
              f"kaçan(mükerrer sanılmadı)={s['kacan']} | "
              f"sahte(farklı ama mükerrer sanıldı)={s['sahte']}")
    if '-v' in sys.argv:
        for ad, hs in hatalar.items():
            if not hs:
                continue
            print(f"\n── {ad} hataları ──")
            for tur, c in hs:
                print(f"  {tur} #{c['no']}\n     {c['baslik_a'][:66]}\n"
                      f"     {c['baslik_b'][:66]}")
    return sonuc


if __name__ == '__main__':
    olc()
