"""Manşet çapraz-gün semantik denetimi — eleme değil DEĞİŞTİRME.

2026-08-06'da keyv/cacheable npm solucanı üst üste ikinci gün manşet oldu.
Sebep: hiçbir katman "bugünün manşeti dünün manşeti mi?" diye sormuyordu.
  • Auditor mükerrer denetimi  → protected_ids=top3_ids (manşete dokunamaz)
  • Deterministik çapraz-gün   → _dedup_body_cross_day (yalnızca gövde)
  • LLM çapraz-gün             → docstring: "KRİTİK 3 buraya hiç gelmez",
                                  üstelik ENABLE_LLM_CROSS_DAY_DEDUP=False
Manşet, üç eleme pasının da dışındaydı; gerekçe "KRİTİK 3 asla 3'ten az
olamaz"dı.

_dedup_kritik3_cross_day_llm o kısıtı hiç zorlamaz: işaretlenen haber SİLİNMEZ,
sıradaki uygun adayla DEĞİŞTİRİLİR. Yedek yoksa yerinde kalır. Bu testler sayı
garantisinin her yolda korunduğunu doğrular.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import main


def _sistem(flagged):
    """LLM'i taklit eden sistem: _gemini_call_json sabit cevap döndürür."""
    s = main.HaberSistemi.__new__(main.HaberSistemi)
    s._gemini_call_json = lambda *a, **k: {'duplicates': list(flagged)}
    return s


ICERIK = {
    1: {'tr_title': 'Snowflake ihlallerinden sorumlu şahsın suçunu kabul etmesi',
        'paragraph': 'Kanadalı şahıs 165 şirketi etkileyen ihlallerde suçunu kabul etmiştir.'},
    2: {'tr_title': 'Çinli telekomların ABD ekosistemindeki varlığı',
        'paragraph': 'Salt Typhoon bağlantılarına ragmen varlık sürmektedir.'},
    3: {'tr_title': 'npm keyv ve cacheable paketlerine yönelik solucan saldırısı',
        'paragraph': 'keyv ve cacheable npm paketleri ele gecirilmistir.'},
    4: {'tr_title': 'Ransom Cartel kurucusuna 16 yıl hapis',
        'paragraph': 'Fidye yazilimi operatorune ceza verilmistir.'},
    5: {'tr_title': 'Zbtlink yönlendiricilerinde fabrika çıkışlı arka kapı',
        'paragraph': 'Yonlendiricilerde arka kapi tespit edilmistir.'},
}
KAYITLAR = {
    1: {'kat': 'kolluk_operasyonu', 'mukerrer': 0},
    2: {'kat': 'nation_state_apt', 'mukerrer': 0},
    3: {'kat': 'tedarik_zinciri', 'mukerrer': 0},
    4: {'kat': 'kolluk_operasyonu', 'mukerrer': 0},
    5: {'kat': 'tedarik_zinciri', 'mukerrer': 0},
}
# Dünkü manşet — bugünün 3 numaralı haberiyle aynı olay.
GECMIS = [{'tr_title': 'Keyv bağlantılı npm solucanının yüzlerce paketi zehirlemesi',
           'paragraph': 'Keyv ve cacheable isim alanlarindaki paketler zehirlenmistir.',
           'title': '', 'full_text': ''}]


def _cagir(sistem, top3, yedek):
    return sistem._dedup_kritik3_cross_day_llm(
        top3, yedek, KAYITLAR, ICERIK, {}, GECMIS)


def test_tekrar_eden_manset_degistirilir():
    """08-06 vakası: ID 3 dünkü manşetin tekrarı → yedekle değişir."""
    out = _cagir(_sistem([3]), [1, 2, 3], [1, 2, 3, 4, 5])
    assert len(out) == 3
    assert 3 not in out, 'tekrar eden manşet yerinde kaldı'
    assert out[:2] == [1, 2], 'temiz manşetler korunmalı'
    assert out[2] in (4, 5)


def test_yedek_yoksa_yerinde_birakilir():
    """KRİTİK 3 ASLA 3'ten az kalmaz — yedek yoksa haber düşürülmez."""
    out = _cagir(_sistem([3]), [1, 2, 3], [1, 2, 3])
    assert out == [1, 2, 3]


def test_isaret_yoksa_liste_degismez():
    assert _cagir(_sistem([]), [1, 2, 3], [1, 2, 3, 4, 5]) == [1, 2, 3]


def test_gecmis_bossa_hic_calismaz():
    s = _sistem([3])
    assert s._dedup_kritik3_cross_day_llm(
        [1, 2, 3], [1, 2, 3, 4, 5], KAYITLAR, ICERIK, {}, []) == [1, 2, 3]


def test_mukerrer_isaretli_aday_yedek_olamaz():
    kayitlar = dict(KAYITLAR)
    kayitlar[4] = {'kat': 'kolluk_operasyonu', 'mukerrer': 1}
    s = _sistem([3])
    out = s._dedup_kritik3_cross_day_llm(
        [1, 2, 3], [1, 2, 3, 4, 5], kayitlar, ICERIK, {}, GECMIS)
    assert out[2] == 5, 'mükerrer işaretli aday manşete yedek olmamalı'


def test_manset_disi_kategori_yedek_olamaz():
    kayitlar = dict(KAYITLAR)
    kayitlar[4] = {'kat': 'urun_icerik', 'mukerrer': 0}
    s = _sistem([3])
    out = s._dedup_kritik3_cross_day_llm(
        [1, 2, 3], [1, 2, 3, 4, 5], kayitlar, ICERIK, {}, GECMIS)
    assert out[2] == 5


def test_llm_basarisiz_olursa_liste_korunur():
    """Güvenli degrade: LLM boş/bozuk dönerse manşet değişmez."""
    s = main.HaberSistemi.__new__(main.HaberSistemi)
    s._gemini_call_json = lambda *a, **k: None
    assert s._dedup_kritik3_cross_day_llm(
        [1, 2, 3], [1, 2, 3, 4, 5], KAYITLAR, ICERIK, {}, GECMIS) == [1, 2, 3]
