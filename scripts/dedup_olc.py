#!/usr/bin/env python3
"""DEDUP ÖLÇÜM — data/dedup_golden.json'a karşı mevcut davranışı ölçer.

NEDEN VAR:
Dedup/manşet mantığındaki her değişiklik bir öncekini bozuyordu çünkü etkisi
ölçülmüyordu (bkz. scripts/golden_set_kur.py başlığı). Bu script değişikliğin
etkisini TEK KOMUTLA gösterir: hangi vaka düzeldi, hangisi bozuldu.

İKİ ÖLÇÜM YAPAR:
1) src.dedup.same_event — deterministik "aynı olay mı" kararı (ikili).
   Beklenti: AYNI_GELISME → True ; AYNI_AKTOR_FARKLI_OLAY, ILISKISIZ → False.
   YENI_GELISME BİLGİ AMAÇLIDIR: same_event'in "aynı olay" demesi doğrudur
   (olay aynı), ama POLİTİKA elemek olmamalıdır — bu ayrımı same_event değil
   src.olay_iliski yapar. Bu yüzden puanlamaya girmez, ayrıca raporlanır.

2) src.olay_iliski.iliski_belirle — dört değerli ilişki sınıflandırıcı
   (varsa). Beklenti: etiketin AYNISI. Sınıflandırıcı yoksa bu bölüm atlanır.

Ağ/LLM/API anahtarı GEREKTİRMEZ.

Çalıştır:  python scripts/dedup_olc.py          (rapor)
           python scripts/dedup_olc.py --kapi   (regresyonda çıkış kodu 1)
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src import dedup

GOLDEN = 'data/dedup_golden.json'

# same_event'in "True" demesi BEKLENEN etiketler (gerçek mükerrer).
SAME_EVENT_TRUE = {'AYNI_GELISME'}
# same_event'in "False" demesi BEKLENEN etiketler (birleştirilmemeli).
SAME_EVENT_FALSE = {'AYNI_AKTOR_FARKLI_OLAY', 'ILISKISIZ'}
# Puanlamaya girmeyen, bilgi amaçlı etiket (politika sorusu, sinyal sorusu değil).
SAME_EVENT_BILGI = {'YENI_GELISME'}


def yukle():
    if not os.path.exists(GOLDEN):
        print(f'❌ {GOLDEN} yok — önce scripts/golden_set_kur.py çalıştır.')
        sys.exit(2)
    with open(GOLDEN, encoding='utf-8') as f:
        return json.load(f)['ciftler']


def olc_same_event(ciftler):
    """same_event ikili kararını ölçer. Dönüş: (kayıtlar, hata_sayısı)."""
    kayitlar = []
    for c in ciftler:
        karar, neden = dedup.same_event(
            c['a'], c['b'], explain=True, cross_day=not c['ayni_gun'])
        beklenen = (True if c['iliski'] in SAME_EVENT_TRUE else
                    False if c['iliski'] in SAME_EVENT_FALSE else None)
        kayitlar.append({
            'ad': c['ad'], 'iliski': c['iliski'], 'ayni_gun': c['ayni_gun'],
            'karar': karar, 'neden': neden, 'beklenen': beklenen,
            'gecti': beklenen is None or karar == beklenen,
            'not': c['not'],
        })
    hata = sum(1 for k in kayitlar if not k['gecti'])
    return kayitlar, hata


def olc_iliski(ciftler):
    """Dört değerli sınıflandırıcıyı ölçer; yoksa None döner."""
    try:
        from src import olay_iliski
    except ImportError:
        return None, None
    kayitlar = []
    for c in ciftler:
        tahmin, neden = olay_iliski.iliski_belirle(
            c['a'], c['b'], ayni_gun=c['ayni_gun'], explain=True)
        kayitlar.append({
            'ad': c['ad'], 'beklenen': c['iliski'], 'tahmin': tahmin,
            'neden': neden, 'gecti': tahmin == c['iliski'], 'not': c['not'],
        })
    hata = sum(1 for k in kayitlar if not k['gecti'])
    return kayitlar, hata


def _yaz(baslik, kayitlar, alan_beklenen, alan_tahmin):
    print(f'\n{"=" * 78}\n{baslik}\n{"=" * 78}')
    for k in kayitlar:
        if k['gecti']:
            im = '✅'
        else:
            im = '❌'
        b, t = k[alan_beklenen], k[alan_tahmin]
        if b is None:
            im = 'ℹ️ '
        gun = 'gün-içi' if k.get('ayni_gun') else 'çapraz-gün'
        print(f'{im} [{gun}] {k["ad"]}')
        print(f'      beklenen={b}  gerçek={t}'
              + (f'  ({k["neden"]})' if k.get('neden') else ''))
        if not k['gecti']:
            print(f'      ↳ {k["not"]}')


def main():
    kapi = '--kapi' in sys.argv
    ciftler = yukle()
    print(f'📊 Golden set: {len(ciftler)} çift')

    se_kayit, se_hata = olc_same_event(ciftler)
    _yaz('1) src.dedup.same_event — deterministik ikili karar',
         se_kayit, 'beklenen', 'karar')
    puanlanan = [k for k in se_kayit if k['beklenen'] is not None]
    print(f'\n   SONUÇ: {len(puanlanan) - se_hata}/{len(puanlanan)} doğru, '
          f'{se_hata} hata '
          f'({len(se_kayit) - len(puanlanan)} bilgi amaçlı vaka puanlanmadı)')

    il_kayit, il_hata = olc_iliski(ciftler)
    if il_kayit is None:
        print('\nℹ️  src/olay_iliski.py yok — dört değerli ölçüm atlandı.')
    else:
        _yaz('2) src.olay_iliski.iliski_belirle — dört değerli sınıflandırıcı',
             il_kayit, 'beklenen', 'tahmin')
        print(f'\n   SONUÇ: {len(il_kayit) - il_hata}/{len(il_kayit)} doğru, '
              f'{il_hata} hata')

    if kapi:
        # Kapı YALNIZCA sınıflandırıcıya bakar: same_event tek başına
        # YENI_GELISME'yi ayıramaz (mimari sınır), sınıflandırıcı ayırabilir.
        if il_kayit is None:
            print('\n⚠️  Kapı: sınıflandırıcı yok — same_event hatalarına bakılıyor.')
            return 1 if se_hata else 0
        return 1 if il_hata else 0
    return 0


if __name__ == '__main__':
    sys.exit(main())
