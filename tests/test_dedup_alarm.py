"""DEDUP SIFIR alarmının eleme NEDENİNE göre ayrışması.

2026-08-05 denetiminde alarmın yararsız hâle geldiği ölçüldü: kaydın bulunduğu
7 günün TAMAMINDA Mandiant/Recorded Future/BSI/NCSC UK gibi düşük frekanslı
kaynaklar uyarı üretti. Nedeni bir filtre arızası değildi — bu kaynaklar 168
saatlik telafi penceresi kullanır, haftada bir yayın yapar, dolayısıyla aynı
yazı 7 koşu boyunca pencerede kalır: ilk gün raporlanır, kalan 6 gün Seviye 1
(URL) dedup'ı onu eler. Yani alarm SAĞLIKLI davranışta ötüyordu.

Somut kanıt (2026-08-05): NCSC UK 7/7 gün alarm verdiği hâlde o gün rapora
haber soktu; Recorded Future'ın penceredeki 2 yazısı 07-30 ve 07-31'de zaten
raporlanmıştı.

Bu testler alarmın artık YALNIZCA taze haber filtreye takıldığında (Seviye
2-5) ötmesini garantiler.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import main


def _sistem():
    return main.HaberSistemi.__new__(main.HaberSistemi)


def test_note_dedup_sinifları_ayrı_sayar():
    s = _sistem()
    s.dedup_reasons = {}
    s._note_dedup('Mandiant (Google Cloud)', 'seen')
    s._note_dedup('Mandiant (Google Cloud)', 'seen')
    s._note_dedup('ANSSI (CERT-FR)', 'filtered')

    assert s.dedup_reasons['Mandiant (Google Cloud)'] == {'seen': 2, 'filtered': 0}
    assert s.dedup_reasons['ANSSI (CERT-FR)'] == {'seen': 0, 'filtered': 1}


def _alarmlar(dedup_reasons, stats):
    """topla() içindeki alarm koşulunun saf kopyası üzerinden uyarı listesi."""
    out = []
    for src, st in stats.items():
        kept, text_ok, pool = st['kept'], st['text_ok'], st['pool']
        if kept > 0 and text_ok == 0:
            out.append(('CIKARIM', src))
        elif text_ok > 0 and pool == 0:
            r = dedup_reasons.get(src, {})
            if r.get('filtered', 0) == 0 and r.get('seen', 0) > 0:
                continue
            out.append(('DEDUP', src))
    return out


def test_hepsi_daha_once_raporlanmissa_alarm_yok():
    """Recorded Future 2026-08-05 vakası: penceredeki 2 yazı da 5-6 gün önce
    raporlanmıştı → Seviye 1 eledi → bu sağlıklıdır, alarm ötmemeli."""
    stats = {'Recorded Future': {'kept': 2, 'text_ok': 2, 'pool': 0}}
    reasons = {'Recorded Future': {'seen': 2, 'filtered': 0}}
    assert _alarmlar(reasons, stats) == []


def test_taze_haber_filtreye_takilirsa_alarm_var():
    """2026-07-29 ANSSI vakası: hiç raporlanmamış haberler benzerlik
    filtresine takıldı → asıl izlenmesi gereken kayıp, alarm ötmeli."""
    stats = {'ANSSI (CERT-FR)': {'kept': 34, 'text_ok': 34, 'pool': 0}}
    reasons = {'ANSSI (CERT-FR)': {'seen': 3, 'filtered': 31}}
    assert _alarmlar(reasons, stats) == [('DEDUP', 'ANSSI (CERT-FR)')]


def test_karisik_durumda_tek_taze_kayip_bile_alarm_uretir():
    stats = {'BSI': {'kept': 5, 'text_ok': 5, 'pool': 0}}
    reasons = {'BSI': {'seen': 4, 'filtered': 1}}
    assert _alarmlar(reasons, stats) == [('DEDUP', 'BSI')]


def test_cikarim_sifir_alarmi_etkilenmez():
    """Metin hiç çıkmadıysa neden ayrımı devreye girmez — ayrı kayıp sınıfı."""
    stats = {'Dark Reading': {'kept': 9, 'text_ok': 0, 'pool': 0}}
    assert _alarmlar({}, stats) == [('CIKARIM', 'Dark Reading')]


def test_havuza_haber_giren_kaynak_hic_incelenmez():
    """Citizen Lab 2026-08-05: 2 metin, 1'i havuza girdi → alarm konusu değil."""
    stats = {'Citizen Lab': {'kept': 2, 'text_ok': 2, 'pool': 1}}
    reasons = {'Citizen Lab': {'seen': 1, 'filtered': 0}}
    assert _alarmlar(reasons, stats) == []
