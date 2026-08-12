#!/usr/bin/env python3
"""SÖKÜM HAZIRLIK DENETİMİ — hangi eski katman kaldırılabilir?

NEDEN VAR:
Boru hattında aynı soruyu soran ~10 katman birikti (bkz. src/olay_iliski.py
başlığı). Faz 1-3 bunların yerini alacak tek bir olay modeli kurdu, ama eski
katmanları HEMEN kaldırmak yeni bir tahmin olurdu — projenin bugüne kadarki
tüm arızası zaten "ölçmeden değiştir"den çıktı.

Bu script sökümü bir KARARA değil bir KRİTERE bağlar: data/kalite_denetim.jsonl
üretimde birikirken her koşuda ölçülen kaçaklar kaydedilir; burada o kayıtlar
okunup "söküm için yeterli kanıt var mı?" sorusu yanıtlanır.

KRİTERLER (hepsi sağlanmalı):
  1. En az MIN_GUN gün veri birikmiş olmalı.
  2. Çapraz-gün kaçak (rapora giren haber son 7 günde zaten raporlanmış)
     olmamalı — yeni olay modeli mükerreri kaçırmıyor demektir.
  3. Rapor-içi kaçak olmamalı.
  4. Manşet sırası tersineliği olmamalı (manşete uygun daha yüksek puanlı
     gövde haberi kalmamalı).
  5. GÖLGE KÜMELEME temiz olmalı: gruplar gözle denetlenip yanlış birleştirme
     içermediği onaylanmalı — bu script yalnızca grupları LİSTELER, doğruluk
     yargısını insan verir (yanlış birleştirme sessiz haber kaybıdır).

Kriterler sağlanınca sökülebilecekler (öncelik sırasına göre):
  • _hikaye_zinciri_filtrele — işlevi olay defterinin manset_gunleri alanı
    tarafından KAPSANIR ve defter daha genişidir (zincir ≥3 gün ararken
    defter ≥1 manşet gününde devreye girer).
  • Manşet sonrası üç LLM denetiminden ikisi (_dedup_kritik3_cross_day_llm,
    _audit_kritik3_selection) — üçü de aynı soruyu soruyor.
  • Güvenlik tabanının ORAN dalı — defter varken arz tahmini gerekmez.

Ağ/LLM/API anahtarı GEREKTİRMEZ.

Çalıştır:  python scripts/sokum_hazirlik.py
"""
import json
import os
import sys

DENETIM = 'data/kalite_denetim.jsonl'
MIN_GUN = 7


def yukle():
    if not os.path.exists(DENETIM):
        return []
    kayitlar = []
    with open(DENETIM, encoding='utf-8') as f:
        for satir in f:
            satir = satir.strip()
            if not satir:
                continue
            try:
                kayitlar.append(json.loads(satir))
            except ValueError:
                continue
    # Gün başına EN SON kayıt (aynı gün yeniden üretim olabilir).
    son = {}
    for k in kayitlar:
        son[k.get('tarih', '?')] = k
    return [son[g] for g in sorted(son)]


def main():
    kayitlar = yukle()
    if not kayitlar:
        print(f'ℹ️  {DENETIM} yok ya da boş.')
        print('   Söküm için önce üretimde veri birikmeli — her rapor koşusu '
              'bir satır yazar (bkz. main._kalite_denetimi_yaz).')
        return 1

    print(f'📊 {len(kayitlar)} günlük kalite denetimi kaydı '
          f'({kayitlar[0]["tarih"]} → {kayitlar[-1]["tarih"]})\n')

    capraz = sum(len(k.get('capraz_gun_kacak', [])) for k in kayitlar)
    ici = sum(len(k.get('rapor_ici_kacak', [])) for k in kayitlar)
    tersine = sum(len(k.get('manset_sirasi', {})
                      .get('daha_yuksek_puanli_govde', [])) for k in kayitlar)
    kume = [(k['tarih'], g) for k in kayitlar for g in k.get('kume_golge', [])]

    olcutler = [
        (f'≥{MIN_GUN} gün veri', len(kayitlar) >= MIN_GUN,
         f'{len(kayitlar)} gün'),
        ('çapraz-gün kaçak yok', capraz == 0, f'{capraz} kaçak'),
        ('rapor-içi kaçak yok', ici == 0, f'{ici} kaçak'),
        ('manşet sırası tersineliği yok', tersine == 0, f'{tersine} vaka'),
    ]
    for ad, ok, ayrinti in olcutler:
        print(f'   {"✅" if ok else "❌"} {ad:<32} {ayrinti}')

    print(f'\n   🧪 Gölge kümeleme: {len(kume)} çok üyeli grup '
          f'(doğruluk yargısı İNSANA aittir — aşağıdaki grupları denetle)')
    for tarih, grup in kume[-15:]:
        print(f'      [{tarih}] ' + ' | '.join(
            f'{x["id"]}:{x["baslik"][:34]}' for x in grup))

    hazir = all(ok for _, ok, _ in olcutler)
    print()
    if hazir:
        print('✅ Otomatik ölçütlerin HEPSİ sağlandı.')
        print('   Kalan tek şart: yukarıdaki gölge kümeleme gruplarında '
              'yanlış birleştirme OLMADIĞINI gözle doğrula.')
        print('   Doğrulanırsa söküm sırası: (1) _hikaye_zinciri_filtrele, '
              '(2) manşet LLM denetimlerinden ikisi, (3) güvenlik tabanı '
              'oran dalı. Her adımdan sonra tests/ + scripts/dedup_olc.py.')
        return 0
    print('⛔ Söküm için kanıt YETERSİZ — eski katmanlar YERİNDE KALMALI.')
    print('   Bu bir gecikme değil, tasarım: katmanları ölçmeden kaldırmak '
          'projenin bugüne kadarki arıza deseninin ta kendisi.')
    return 1


if __name__ == '__main__':
    sys.exit(main())
