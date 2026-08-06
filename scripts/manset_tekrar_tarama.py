#!/usr/bin/env python3
"""MANŞET TEKRAR TARAMASI — kaçan mükerrer sınıfını erken bulmak için.

NEDEN VAR:
scripts/dedup_backtest.py bir kuralın YANLIŞ POZİTİFİNİ ölçer ("iki farklı olayı
birleştirdim mi?"). Bu script tam TERSİNİ arar: KAÇAN mükerreri ("aynı olayı
göremedim mi?"). İkisi farklı sorulardır ve farklı arızaları yakalar.

Gerekçe ölçümle sabit: 2026-08-07 taramasında geçmiş manşetlerde 8 günde 7 tekrar
çifti bulundu — su altyapısı haberi DÖRT gün üst üste (07-30…08-02), keyv/cacheable
npm solucanı iki gün (08-05, 08-06) manşet olmuştu. Yani manşet tekrarı tek bir
vaka değil, SİSTEMİK bir örüntü. Her seferinde kök neden aynı biçimdeydi: olayın
en ayırt edici kimliği hiçbir yüksek-özgüllük sinyaline girmiyordu (Minnesota →
özel ad kuralı yoktu; keyv → küçük harfli paket adı kuralı yoktu).

Bu yüzden asıl değer B BÖLÜMÜNDEDİR: konu örtüşmesi yüksek AMA hiçbir sinyal
üretmeyen çiftler. Bir sonraki kör nokta oradan çıkar.

NE YAPAR (üç bölüm):
  A) YAKALANAN  — geçmişte rapora girmiş ama BUGÜNKÜ kurallarla aynı-olay çıkan
                  çiftler. O gün kaçmış demektir; kural sonradan güçlenmişse
                  beklenen sonuçtur. Sayı ARTIYORSA yeni bir sızıntı var.
  B) ŞÜPHELİ    — same_event False, ama konu örtüşmesi eşiğin üstünde ve
                  codename/actor/entity/package kümelerinin HEPSİ boş.
                  Kör nokta adayı; gözle denetlenmeli.
  C) ÖZET       — gün başına manşet tekrar sayısı.

Ağ, LLM veya API anahtarı GEREKTİRMEZ; yalnızca repo'daki JSON geçmişini okur.
Hiçbir dosyaya YAZMAZ. Salt teşhis.

Çalıştır:
    python scripts/manset_tekrar_tarama.py                # kritik3 geçmişi
    python scripts/manset_tekrar_tarama.py --rapor        # tüm rapor geçmişi
    python scripts/manset_tekrar_tarama.py --supheli 0.18 # şüpheli eşiğini indir

Çıkış kodu: ŞÜPHELİ çift varsa 1 (CI'da uyarı olarak kullanılabilir), yoksa 0.
"""
import argparse
import itertools
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import dedup

KRITIK3_FILE = 'data/kritik3_gecmis.json'
RAPOR_FILE = 'data/rapor_gecmis.json'

# Şüpheli sayılmak için gereken asgari konu örtüşmesi. same_event'in aktör
# kuralı 0.14, özel ad kuralı 0.22 kullanır; 0.20 ikisinin arasında durur ve
# "konusu belirgin biçimde örtüşüyor ama hiçbir kimlik sinyali yok" durumunu
# yakalar — keyv vakasında ölçülen değer 0.25 idi.
VARSAYILAN_SUPHELI_ESIK = 0.20


def _blob(view):
    return ' '.join(str(view.get(k, '') or '')
                    for k in ('tr_title', 'paragraph', 'title', 'full_text'))


def _topic(a, b):
    """same_event'in kullandığı konu örtüşmesinin aynısı (en yükseği)."""
    pa, pb = a.get('paragraph', '') or '', b.get('paragraph', '') or ''
    ba, bb = _blob(a), _blob(b)
    ek, jac = dedup.event_keywords, dedup._jaccard
    return max(jac(ek(pa), ek(pb)), jac(ek(ba), ek(bb)))


def _sinyaller(a, b):
    """İki haberin PAYLAŞTIĞI yüksek-özgüllük sinyalleri."""
    ba, bb = _blob(a), _blob(b)
    return {
        'codename': dedup.extract_codenames(ba) & dedup.extract_codenames(bb),
        'actor': dedup.extract_actors(ba) & dedup.extract_actors(bb),
        'entity': dedup.shared_entities(a, b),
        'package': dedup.extract_package_names(ba) & dedup.extract_package_names(bb),
    }


def _yukle(path):
    if not os.path.exists(path):
        print(f"⚠️  {path} yok — atlanıyor.")
        return []
    with open(path, encoding='utf-8') as f:
        kayitlar = json.load(f)
    return [(k.get('date', ''), v)
            for k in kayitlar if isinstance(k, dict)
            for v in (k.get('views') or []) if isinstance(v, dict)]


def _baslik(v):
    return (v.get('tr_title') or v.get('title') or '')[:62]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--rapor', action='store_true',
                    help='kritik3 yerine TÜM rapor geçmişini tara')
    ap.add_argument('--supheli', type=float, default=VARSAYILAN_SUPHELI_ESIK,
                    help=f'şüpheli konu eşiği (varsayılan {VARSAYILAN_SUPHELI_ESIK})')
    args = ap.parse_args()

    path = RAPOR_FILE if args.rapor else KRITIK3_FILE
    kapsam = 'TÜM RAPOR' if args.rapor else 'KRİTİK 3 (manşet)'
    items = _yukle(path)
    if not items:
        return 0

    gunler = sorted({d for d, _ in items})
    print(f"📂 {path} — {len(items)} haber, {len(gunler)} gün "
          f"({gunler[0]} … {gunler[-1]})")
    print(f"🔎 Kapsam: {kapsam}\n")

    yakalanan, supheli = [], []
    for (d1, a), (d2, b) in itertools.combinations(items, 2):
        if d1 == d2:
            continue
        ok, why = dedup.same_event(a, b, explain=True, cross_day=True)
        if ok:
            yakalanan.append((d1, a, d2, b, why))
            continue
        t = _topic(a, b)
        if t < args.supheli:
            continue
        sig = _sinyaller(a, b)
        if any(sig.values()):
            continue          # sinyal var ama eşik tutmadı → kör nokta değil
        supheli.append((d1, a, d2, b, t))

    print("═" * 78)
    print(f"A) BUGÜNKÜ KURALLARLA YAKALANAN TEKRARLAR: {len(yakalanan)}")
    print("   (geçmişte rapora girmişler → o gün kaçmışlar; kural sonradan")
    print("    güçlendiyse beklenen sonuç. Sayı ARTIYORSA yeni sızıntı var.)")
    print("═" * 78)
    for d1, a, d2, b, why in yakalanan:
        print(f"  {d1}  {_baslik(a)}")
        print(f"  {d2}  {_baslik(b)}")
        print(f"     ↳ {why[:70]}\n")

    print("═" * 78)
    print(f"B) ŞÜPHELİ — KÖR NOKTA ADAYI: {len(supheli)}")
    print(f"   (konu örtüşmesi ≥{args.supheli} ama codename/actor/entity/package")
    print("    kümelerinin HEPSİ boş → aynı olay olabilir ve kimse göremez.)")
    print("═" * 78)
    if not supheli:
        print("  ✅ Yok.\n")
    for d1, a, d2, b, t in sorted(supheli, key=lambda x: -x[4]):
        print(f"  konu={t:.2f}")
        print(f"  {d1}  {_baslik(a)}")
        print(f"  {d2}  {_baslik(b)}\n")

    print("═" * 78)
    print("C) ÖZET")
    print("═" * 78)
    gun_sayaci = {}
    for d1, _, d2, _, _ in yakalanan:
        for d in (d1, d2):
            gun_sayaci[d] = gun_sayaci.get(d, 0) + 1
    if gun_sayaci:
        for d in sorted(gun_sayaci):
            print(f"  {d}: {gun_sayaci[d]} tekrar bağlantısı")
    else:
        print("  Tekrar yok.")
    print(f"\n  Yakalanan: {len(yakalanan)}   Şüpheli: {len(supheli)}")
    if supheli:
        print("\n  ⚠️  ŞÜPHELİ çiftleri gözle denetle. Gerçekten aynı olaylarsa")
        print("      src/dedup.py'ye yeni bir yüksek-özgüllük sinyali gerekiyor")
        print("      (keyv vakasında extract_package_names böyle doğdu).")
    return 1 if supheli else 0


if __name__ == '__main__':
    sys.exit(main())
