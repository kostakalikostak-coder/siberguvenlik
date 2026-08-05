"""Critique kurtarma kapsamı — kategori yüzünden sıfırlanmış haberler denetime girer.

2026-08-05 vakası: AISI'nin yayımladığı olay raporu (bir yapay zekâ ajanının
gerçek bir açık kaynak projesine arka kapı sokmaya çalışması, kanıt silmesi,
ikinci bir hesapla kendini doğrulaması) ÜÇ ayrı kaynaktan da `urun_icerik`
etiketlendi. `_record_total` bu kategoride toplamı koşulsuz sıfırlar; critique
kapsamı ise yalnızca 'toplam'a göre top-20 seçtiği için (o gün eşik 80'di)
sıfırlanan haberler yapısal olarak DENETİM DIŞI kalıyordu. Yani yanlış
sıfırlama tek ajanın tek kararıyla kesinleşiyordu ve critique prompt'undaki
"olay raporu analiz değildir" istisnası hiçbir zaman tetiklenemiyordu.

Bu testler kurtarma kapsamının o boşluğu kapattığını doğrular.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import main


def _rec(kat, s, e, a, k, siber=1):
    r = {'kat': kat, 'siber': siber, 'mukerrer': 0, 's': s, 'e': e, 'a': a, 'k': k}
    r['toplam'] = main.HaberSistemi._record_total(r)
    return r


def _scope(records, top_k=20, rescue_k=main.HaberSistemi.CRITIQUE_RESCUE_K):
    """_critique_scores içindeki kapsam seçiminin saf kopyası."""
    ranked = sorted(records, key=lambda aid: records[aid]['toplam'], reverse=True)
    scope = list(ranked[:top_k])
    for aid, rec in records.items():
        if rec['kat'] == 'zafiyet_aktif_apt' and aid not in scope:
            scope.append(aid)
    zeroed = [aid for aid, rec in records.items()
              if aid not in scope and rec.get('siber')
              and rec['kat'] in ('urun_icerik', 'siber_disi')]
    zeroed.sort(key=lambda aid: (records[aid]['s'] + records[aid]['e']
                                 + records[aid]['a'] + records[aid]['k']),
                reverse=True)
    scope.extend(zeroed[:rescue_k])
    return scope


def test_urun_icerik_kategorisi_toplami_sifirlar():
    """Kaybın mekanizması: rubrik 45 olsa bile kategori toplamı sıfırlar."""
    assert _rec('urun_icerik', 10, 10, 10, 15)['toplam'] == 0
    assert _rec('tedarik_zinciri', 10, 10, 10, 15)['toplam'] == 45


def test_sifirlanmis_olay_raporu_kapsama_girer():
    """Mythos vakası: 20 dolu haber + sıfırlanmış olay raporu."""
    records = {i: _rec('veri_ihlali', 20, 15, 15, 12) for i in range(20)}
    records[99] = _rec('urun_icerik', 10, 10, 10, 15)   # AISI olay raporu
    scope = _scope(records)
    assert 99 in scope, "sıfırlanmış olay raporu critique denetimine girmeli"


def test_eski_davranista_kapsam_disindaydi():
    """Regresyon koruması: kurtarma kapsamı olmadan haber görünmezdi."""
    records = {i: _rec('veri_ihlali', 20, 15, 15, 12) for i in range(20)}
    records[99] = _rec('urun_icerik', 10, 10, 10, 15)
    assert 99 not in _scope(records, rescue_k=0)


def test_yuksek_rubrikli_aday_pazarlama_icerigini_geride_birakir():
    """Ham rubrik ön elemesi: olay (45) ürün duyurusundan (27) önce gelir."""
    records = {i: _rec('veri_ihlali', 20, 15, 15, 12) for i in range(20)}
    records[50] = _rec('urun_icerik', 10, 10, 10, 15)   # olay, ham 45
    for j in range(51, 71):                              # 20 pazarlama haberi
        records[j] = _rec('urun_icerik', 5, 5, 5, 12)    # ham 27
    scope = _scope(records)
    assert 50 in scope


def test_kurtarma_kapsami_sinirli():
    """Token bütçesi: en fazla CRITIQUE_RESCUE_K haber eklenir."""
    records = {i: _rec('veri_ihlali', 20, 15, 15, 12) for i in range(20)}
    for j in range(100, 140):
        records[j] = _rec('urun_icerik', 10, 10, 10, 15)
    scope = _scope(records)
    assert len(scope) == 20 + main.HaberSistemi.CRITIQUE_RESCUE_K


def test_siber_disi_olmayan_gundem_disi_haber_kurtarilmaz():
    """siber=0 olan haber kurtarma adayı değildir (gerçekten gündem dışı)."""
    records = {i: _rec('veri_ihlali', 20, 15, 15, 12) for i in range(20)}
    records[99] = _rec('siber_disi', 10, 10, 10, 15, siber=0)
    assert 99 not in _scope(records)


def test_critique_kategoriyi_yukseltince_puan_geri_gelir():
    """Kurtarma ancak puan yeniden hesaplanırsa işe yarar (_normalize_record)."""
    s = main.HaberSistemi.__new__(main.HaberSistemi)
    duzeltilmis = s._normalize_record(
        {'kat': 'tedarik_zinciri', 'siber': 1, 'mukerrer': 0,
         's': 30, 'e': 22, 'a': 18, 'k': 15})
    assert duzeltilmis['toplam'] == 85
