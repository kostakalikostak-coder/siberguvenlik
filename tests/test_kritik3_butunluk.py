"""Auditor (d) — manşet bütünlük denetimi: seçim + manşet-içi mükerrer.

Auditor raporun tamamına bakıyor sanılıyordu; ölçüldüğünde manşetin üç ayrı
boşluğu olduğu görüldü:
  • rapor-içi mükerrer denetimi `protected_ids=top3_ids` — iki manşet birbirinin
    aynısıysa ikisi de korunuyordu,
  • İngilizce içerik süpürmesi `top10 + remaining` üzerinden çalışıyor, manşeti
    hiç taramıyordu,
  • SEÇİMİ sorgulayan hiçbir katman yoktu — yanlış bir haber manşete çıktığında
    onu geri çevirecek mekanizma bulunmuyordu.

Üçü de aynı sözleşmeyle kapatıldı: SİLME YOK, DEĞİŞTİRME VAR. Yedek bulunamazsa
haber yerinde kalır, yani "KRİTİK 3 asla 3'ten az olamaz" garantisi — pasların
manşeti atlama gerekçesi — hiç zorlanmaz.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import main


ICERIK = {
    1: {'tr_title': 'Snowflake ihlallerinde şahsın suçunu kabul etmesi',
        'paragraph': 'Kanadalı şahıs 165 sirketi etkileyen ihlallerde suclu bulunmustur.'},
    2: {'tr_title': 'Çinli telekomların ABD ekosistemindeki varlığı',
        'paragraph': 'Salt Typhoon baglantilarina ragmen varlik surmektedir.'},
    3: {'tr_title': 'Yeni bir güvenlik ürününün pazara sunulması',
        'paragraph': 'Sirket yeni bir urun duyurmustur, olay bildirilmemistir.'},
    4: {'tr_title': 'Ransom Cartel kurucusuna 16 yıl hapis',
        'paragraph': 'Fidye yazilimi operatorune ceza verilmistir.'},
    5: {'tr_title': 'Zbtlink yönlendiricilerinde fabrika çıkışlı arka kapı',
        'paragraph': 'Yonlendiricilerde arka kapi tespit edilmistir.'},
    # 6, 1 ile AYNI olayın farklı sözcüklerle yazılmış hâli.
    6: {'tr_title': 'Snowflake veri hırsızlığı davasında sanığın itirafı',
        'paragraph': 'Kanadalı şahıs 165 sirketi etkileyen ihlallerde suclu bulunmustur.'},
}
KAYITLAR = {
    1: {'kat': 'kolluk_operasyonu', 'mukerrer': 0},
    2: {'kat': 'nation_state_apt', 'mukerrer': 0},
    3: {'kat': 'urun_icerik', 'mukerrer': 0},
    4: {'kat': 'kolluk_operasyonu', 'mukerrer': 0},
    5: {'kat': 'tedarik_zinciri', 'mukerrer': 0},
    6: {'kat': 'kolluk_operasyonu', 'mukerrer': 0},
}


def _sistem(cevap=None):
    s = main.HaberSistemi.__new__(main.HaberSistemi)
    s._gemini_call_json = lambda *a, **k: cevap
    return s


# ── Manşet-içi mükerrer (deterministik, LLM yok) ───────────────────────────

def test_manset_ici_mukerrer_degistirilir():
    """İki manşet aynı olay → ikincisi yedekle değişir."""
    out = _sistem()._dedup_kritik3_ici(
        [1, 6, 2], [4, 5], KAYITLAR, ICERIK, {}, [])
    assert len(out) == 3
    assert 6 not in out, 'mükerrer manşet yerinde kaldı'
    assert out[0] == 1 and out[2] == 2
    assert out[1] in (4, 5)


def test_manset_ici_mukerrer_yedek_yoksa_kalir():
    """KRİTİK 3 sayısı korunur — yedek yoksa haber düşürülmez."""
    out = _sistem()._dedup_kritik3_ici([1, 6, 2], [], KAYITLAR, ICERIK, {}, [])
    assert out == [1, 6, 2]


def test_manset_ici_temizse_degismez():
    assert _sistem()._dedup_kritik3_ici(
        [1, 2, 4], [5], KAYITLAR, ICERIK, {}, []) == [1, 2, 4]


# ── Seçim denetimi (LLM) ───────────────────────────────────────────────────

def _secim(cevap, top3, yedek, records=KAYITLAR):
    return _sistem(cevap)._audit_kritik3_selection(
        top3, yedek, records, ICERIK, {}, [], govde_ids=yedek)


def test_mansetlik_olmayan_haber_degistirilir():
    """ID 3 ürün duyurusu — manşetten çıkar, yerine uygun aday gelir."""
    out = _secim({'hatali': [{'id': 3, 'neden': 'ürün duyurusu, olay yok'}]},
                 [1, 2, 3], [4, 5])
    assert len(out) == 3
    assert 3 not in out
    assert out[2] in (4, 5)


def test_secim_hatasi_yoksa_degismez():
    assert _secim({'hatali': []}, [1, 2, 3], [4, 5]) == [1, 2, 3]


def test_secim_yedek_yoksa_yerinde_kalir():
    out = _secim({'hatali': [{'id': 3, 'neden': 'ürün duyurusu'}]}, [1, 2, 3], [])
    assert out == [1, 2, 3]


def test_llm_bozuk_donerse_manset_korunur():
    """Güvenli degrade — her manşet denetiminin ortak sözleşmesi."""
    assert _secim(None, [1, 2, 3], [4, 5]) == [1, 2, 3]
    assert _secim({'hatali': 'bozuk'}, [1, 2, 3], [4, 5]) == [1, 2, 3]


def test_gecersiz_id_yok_sayilir():
    """LLM manşette olmayan bir ID döndürürse dokunulmaz."""
    assert _secim({'hatali': [{'id': 99, 'neden': 'x'}]}, [1, 2, 3], [4, 5]) == [1, 2, 3]


# ── Ortak yedek seçici ─────────────────────────────────────────────────────

def test_yedek_secici_manset_disi_kategoriyi_atlar():
    s = _sistem()
    view_fn = s._dedup_view_fn(ICERIK, {})
    # 3 urun_icerik → atlanmalı, 4 seçilmeli
    assert s._kritik3_yedek_bul([3, 4], [1], KAYITLAR, view_fn, []) == 4


def test_yedek_secici_mukerreri_atlar():
    kayitlar = dict(KAYITLAR)
    kayitlar[4] = {'kat': 'kolluk_operasyonu', 'mukerrer': 1}
    s = _sistem()
    view_fn = s._dedup_view_fn(ICERIK, {})
    assert s._kritik3_yedek_bul([4, 5], [1], kayitlar, view_fn, []) == 5


def test_yedek_secici_mevcut_mansetle_ayni_olayi_atlar():
    s = _sistem()
    view_fn = s._dedup_view_fn(ICERIK, {})
    # 6, mevcut manşet 1 ile aynı olay → atlanmalı
    assert s._kritik3_yedek_bul([6, 5], [1], KAYITLAR, view_fn, []) == 5
