#!/usr/bin/env python3
"""ÇAPRAZ-GÜN DEDUP geriye-dönük ölçümü — eşik/denylist ayarı TAHMİNLE değil
ÖLÇÜMLE yapılsın diye.

NEDEN VAR:
2026-07-31 denetiminde "Minnesota su sistemleri saldırısı" 29-30-31 Temmuz'da ÜÇ
GÜN ÜST ÜSTE KRİTİK 3 manşeti oldu. Sebep bir eşik hatası değil, bir SİNYAL
EKSİKLİĞİYDİ: olayın öznesi olan özel ad (Minnesota) hiçbir kuralda ağırlık
taşımıyordu. Düzeltme (src.dedup Kural 2d, ortak özel ad + konu örtüşmesi) bir
YANLIŞ-POZİTİF riski taşır: iki FARKLI olayı birleştirmek, bir mükerreri
kaçırmaktan daha zararlıdır. Bu script o riski gerçek geçmiş üzerinde ölçer.

NE YAPAR:
data/kritik3_gecmis.json (KRİTİK 3 manşetleri) ve data/rapor_gecmis.json (rapora
giren TÜM haberler) içindeki her GÜN-ÇİFTİ için tüm haber çiftlerini tarar ve
same_event(cross_day=True) kararını verir. Kural 2d'nin TEK BAŞINA getirdiği YENİ
eşleşmeleri (yani diğer kuralların yakalamadığı, yalnız özel ad sayesinde
yakalananları) başlıklarıyla listeler — böylece her biri gözle doğru/yanlış diye
denetlenebilir.

Ağ, LLM veya API anahtarı GEREKTİRMEZ; yalnızca repo'daki JSON geçmişini okur.
Hiçbir dosyaya YAZMAZ, commit'lemez. Salt teşhis.

Çalıştır:  python scripts/dedup_backtest.py
"""
import itertools
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src import dedup

CORPORA = [
    ('KRİTİK 3 manşetleri', 'data/kritik3_gecmis.json'),
    ('Rapora giren tüm haberler', 'data/rapor_gecmis.json'),
]


def _load(path):
    """[(tarih, [görünüm, ...]), ...] döndürür; dosya yoksa boş liste."""
    if not os.path.exists(path):
        print(f'   ⚠️  {path} yok — atlanıyor.')
        return []
    with open(path, encoding='utf-8') as f:
        records = json.load(f)
    days = []
    for rec in records:
        if isinstance(rec, dict) and rec.get('views'):
            days.append((rec.get('date', '?'), rec['views']))
    return sorted(days, key=lambda x: x[0])


def _title(view):
    return (view.get('tr_title') or view.get('title') or '')[:64]


def _entity_only_match(a, b):
    """Kural 2d'nin TEK BAŞINA sağladığı eşleşme mi?

    Yani: same_event ŞU AN True diyor ama gerekçesi 'entity:...'. Diğer kurallar
    (kod adı / aktör / yüksek konu / başlık) zaten yakalıyorsa bu kural bir şey
    EKLEMİYOR demektir ve ölçüme girmez."""
    ok, why = dedup.same_event(a, b, explain=True, cross_day=True)
    if not ok or not why.startswith('entity:'):
        return None
    return why


def main():
    print('=' * 84)
    print('ÇAPRAZ-GÜN DEDUP GERİYE-DÖNÜK ÖLÇÜM — Kural 2d (ortak özel ad)')
    print(f'eşik: _TOPIC_WITH_ENTITY = {dedup._TOPIC_WITH_ENTITY}')
    print('=' * 84)

    grand_total = 0
    for label, path in CORPORA:
        print(f'\n{"=" * 84}\n■ {label}  ({path})\n{"=" * 84}')
        days = _load(path)
        if len(days) < 2:
            print('   karşılaştırılacak en az 2 gün yok.')
            continue

        pairs = 0
        hits = []
        for (d1, v1), (d2, v2) in itertools.combinations(days, 2):
            for a in v1:
                for b in v2:
                    pairs += 1
                    why = _entity_only_match(a, b)
                    if why:
                        hits.append((d1, d2, why, _title(a), _title(b)))

        print(f'   {len(days)} gün, {pairs} çift karşılaştırıldı.')
        print(f'   Kural 2d SAYESİNDE yakalanan YENİ eşleşme: {len(hits)}')
        for d1, d2, why, t1, t2 in hits:
            print(f'\n   ● {d1} ~ {d2}   [{why}]')
            print(f'      A: {t1}')
            print(f'      B: {t2}')
        grand_total += len(hits)

    print(f'\n{"=" * 84}')
    print(f'TOPLAM yeni eşleşme: {grand_total}')
    print('Her satır ELLE denetlenmeli: aynı olay mı (DOĞRU) yoksa iki farklı')
    print('olay mı (YANLIŞ)? Yanlış varsa _ENTITY_DENYLIST\'e ekle ya da')
    print('_TOPIC_WITH_ENTITY eşiğini yükselt ve bu ölçümü TEKRAR çalıştır.')
    print('=' * 84)


if __name__ == '__main__':
    main()
