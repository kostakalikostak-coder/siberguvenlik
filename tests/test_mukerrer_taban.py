"""Güvenlik tabanı — mükerrer elemesi ne zaman gevşer, KRİTİK 3 ne zaman korunur.

2026-08-06: taban "mükerrer oranı > %50" gerekçesiyle ateşledi (28/55 = %50.9)
ve mükerrer korumasını TÜM RAPOR için kapattı. Oysa o koşu günün İLK koşusuydu;
oran gerçekti (Snowflake haberi 5 kopya, su altyapısı sürüyordu), artefakt
değildi. Sonuç: dünkü KRİTİK 3 manşeti (keyv/cacheable npm solucanı) yeniden
manşet oldu.

Düzeltme iki parçalı:
  • Artefakt DOLAYLI tahmin edilmez, DOĞRUDAN ölçülür — bugünün raporu diskte
    var mı? (aynı-gün yeniden üretimin tek nedeni budur)
  • Rapor fiilen boşalacaksa taban yine gevşer, AMA yalnızca gövdede; KRİTİK 3
    mükerrer koruması sürer. Mükerrer haber gövdede tolere edilebilir,
    manşette edilemez.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import main


def _sistem():
    return main.HaberSistemi.__new__(main.HaberSistemi)


def test_ilk_kosuda_yeniden_uretim_sinyali_yok(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / 'docs' / 'raporlar').mkdir(parents=True)
    assert _sistem()._ayni_gun_yeniden_uretim() is False


def test_bugunun_raporu_varsa_yeniden_uretim(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    d = tmp_path / 'docs' / 'raporlar'
    d.mkdir(parents=True)
    (d / f"{main._now_tr().strftime('%Y-%m-%d')}.html").write_text('x')
    assert _sistem()._ayni_gun_yeniden_uretim() is True


def _taban(yeniden_uretim, cyber_n, muk_n, minimum=main.HaberSistemi.REPORT_MIN_AFTER_MUKERRER):
    """_rank_by_score taban kararının saf kopyası → (gövde, kritik3)."""
    apply_mukerrer, mukerrer_kritik3 = True, True
    kalan = cyber_n - muk_n
    if cyber_n and muk_n:
        if yeniden_uretim:
            apply_mukerrer, mukerrer_kritik3 = False, False
        elif kalan < minimum:
            apply_mukerrer = False
    return apply_mukerrer, mukerrer_kritik3


def test_0806_vakasi_taban_artik_ateslemez():
    """Gerçek sayılar: 55 siber haber, 28'i mükerrer, kalan 27."""
    govde, k3 = _taban(yeniden_uretim=False, cyber_n=55, muk_n=28)
    assert govde is True and k3 is True


def test_eski_oran_kurali_ateslerdi():
    """Regresyon tanığı: eski kural aynı veride korumayı kapatıyordu."""
    assert (28 / 55 > 0.5) is True


def test_gercek_yeniden_uretimde_ikisi_de_gevser():
    govde, k3 = _taban(yeniden_uretim=True, cyber_n=55, muk_n=28)
    assert govde is False and k3 is False


def test_havuz_gercekten_incelirse_sadece_govde_gevser():
    """Rapor boşalma riski var: gövde gevşer ama manşet korunur."""
    govde, k3 = _taban(yeniden_uretim=False, cyber_n=14, muk_n=6)   # kalan 8 < 12
    assert govde is False
    assert k3 is True, 'KRİTİK 3 mükerrer koruması gövdeyle birlikte gevşememeli'


def test_mukerrer_yoksa_taban_hic_devreye_girmez():
    govde, k3 = _taban(yeniden_uretim=False, cyber_n=40, muk_n=0)
    assert govde is True and k3 is True


def _kritik3_eligible(ranked, records, mukerrer_kritik3):
    """_derive_top3_by_score içindeki manşet mükerrer kapısının saf kopyası."""
    eligible = list(ranked)
    if mukerrer_kritik3:
        muk_disi = [a for a in eligible if not records[a].get('mukerrer')]
        if len(muk_disi) >= 3:
            eligible = muk_disi
    return eligible


def test_mukerrer_manset_adayi_dusurulur():
    """08-06 vakası: mükerrer işaretli aday manşet havuzundan çıkar."""
    records = {1: {'mukerrer': 0}, 2: {'mukerrer': 0},
               3: {'mukerrer': 1},           # keyv/cacheable — dünkü manşet
               4: {'mukerrer': 0}}
    assert _kritik3_eligible([1, 2, 3, 4], records, True) == [1, 2, 4]


def test_uc_taze_aday_yoksa_kapi_gevser():
    """KRİTİK 3 ASLA 3'ten az kalmaz — taze aday yetmezse kapı açılır."""
    records = {1: {'mukerrer': 0}, 2: {'mukerrer': 1}, 3: {'mukerrer': 1}}
    assert _kritik3_eligible([1, 2, 3], records, True) == [1, 2, 3]
