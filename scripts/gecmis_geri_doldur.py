#!/usr/bin/env python3
"""Rapor geçmişini ARŞİVDEN geri doldurur (yalnızca EKSİK günler).

NEDEN VAR: REPORT_HISTORY_DAYS 7'den 30'a çıkarıldı, ama `rapor_gecmis.json`
eski ayarla budandığı için 7 günden eski kayıtlar zaten SİLİNMİŞTİ. Yani
koruma kendiliğinden ancak 30 günde tam etkili olurdu.

ÖLÇÜLEN VAKA: Mustang Panda / CoolClient + çekirdek rootkit haberi 14
Ağustos'ta yayımlandı (arşivde HoneyMyte takma adıyla), 24 Ağustos'ta MANŞET
oldu. Yeni tanım ikisini eşleştiriyor ('codename:coolclient+topic=0.45') ama
14 Ağustos kaydı geçmişte olmadığı için karşılaştırma hiç yapılamıyordu.

Arşiv (data/haberler_arsiv.txt) 30 günden uzun geçmiş tutuyor ve her günün
yayımlanmış haberlerini başlık + paragraf olarak içeriyor — çapraz-gün
karşılaştırması için yeterli. Bu betik yalnızca EKSİK günleri ekler; mevcut
günlerin (daha zengin: title/full_text de taşıyan) kayıtlarına DOKUNMAZ.

Kullanım: python3 scripts/gecmis_geri_doldur.py [--uygula]
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.config import REPORT_HISTORY_DAYS, REPORT_HISTORY_FILE  # noqa: E402

ARSIV = 'data/haberler_arsiv.txt'
_AY = {'JANUARY': 1, 'FEBRUARY': 2, 'MARCH': 3, 'APRIL': 4, 'MAY': 5,
       'JUNE': 6, 'JULY': 7, 'AUGUST': 8, 'SEPTEMBER': 9, 'OCTOBER': 10,
       'NOVEMBER': 11, 'DECEMBER': 12}


def arsivi_ayristir(yol=ARSIV):
    """{'YYYY-MM-DD': [{'tr_title','paragraph','title','full_text'}, ...]}"""
    gunler, gun, baslik, para = {}, None, None, []

    def _yaz():
        if gun and baslik:
            gunler.setdefault(gun, []).append(
                {'tr_title': baslik, 'paragraph': ' '.join(para),
                 'title': '', 'full_text': ''})

    with open(yol, encoding='utf-8') as f:
        for satir in f:
            m = re.match(r'📅 (\d+) ([A-ZÇĞİÖŞÜ]+) (\d{4})', satir)
            if m:
                _yaz()
                baslik, para = None, []
                gun = datetime.date(int(m.group(3)),
                                    _AY.get(m.group(2), 1),
                                    int(m.group(1))).strftime('%Y-%m-%d')
                continue
            m = re.match(r'\[\s*\d+\]\s+(.+)', satir)
            if m:
                _yaz()
                baslik, para = m.group(1).strip(), []
                continue
            if baslik and satir.strip():
                para.append(satir.strip())
    _yaz()
    return gunler


def main(uygula=False):
    arsiv = arsivi_ayristir()
    try:
        with open(REPORT_HISTORY_FILE, encoding='utf-8') as f:
            mevcut = json.load(f)
    except (OSError, ValueError):
        mevcut = []
    var = {r['date'] for r in mevcut if isinstance(r, dict) and r.get('date')}
    bugun = datetime.date.today()
    esik = (bugun - datetime.timedelta(days=REPORT_HISTORY_DAYS)).strftime('%Y-%m-%d')
    eklenecek = [(g, vs) for g, vs in sorted(arsiv.items())
                 if g not in var and esik <= g < bugun.strftime('%Y-%m-%d')]
    print(f"arşiv: {len(arsiv)} gün | geçmişte var: {len(var)} gün | "
          f"pencere: {esik} → bugün")
    for g, vs in eklenecek:
        print(f"  + {g}: {len(vs)} haber")
    if not eklenecek:
        print("eklenecek gün yok.")
        return 0
    if not uygula:
        print("\n(kuru koşu — yazmak için --uygula)")
        return 0
    mevcut.extend({'date': g, 'views': vs} for g, vs in eklenecek)
    mevcut.sort(key=lambda r: r.get('date', ''))
    with open(REPORT_HISTORY_FILE, 'w', encoding='utf-8') as f:
        json.dump(mevcut, f, ensure_ascii=False)
    print(f"\n✅ {len(eklenecek)} gün eklendi → {REPORT_HISTORY_FILE}")
    return 0


if __name__ == '__main__':
    sys.exit(main('--uygula' in sys.argv))
