"""GOLDEN SET KAPISI — dedup/manşet değişiklikleri regresyon üretemesin.

NEDEN VAR:
2026 Temmuz-Ağustos'ta dedup eşikleri vaka-vaka elle ayarlandı; her düzeltme
başka bir sınıfı bozdu (08-06 güvenlik tabanı yanlış tetiklemesi, 08-07'de
81 adet ≥85 puanlı haberin mükerrer bayrağıyla kaybı, 08-12'de günün 2. ve 3.
en yüksek puanlı haberinin manşetten düşmesi). Ortak sebep: DEĞİŞİKLİĞİN
ETKİSİNİ ÖLÇEN SABİT BİR REFERANS YOKTU.

Bu dosya o referansı bir KAPIYA çevirir. data/dedup_golden.json'daki elle
etiketli gerçek üretim çiftlerine karşı iki şeyi güvence altına alır:

  1) YANLIŞ BİRLEŞTİRME OLMAZ (en zararlı hata sınıfı) — ILISKISIZ veya
     AYNI_AKTOR_FARKLI_OLAY etiketli hiçbir çift "aynı olay" sayılamaz.
     İki farklı haberi birleştirmek, bir mükerreri kaçırmaktan daha kötüdür
     (bkz. src/dedup.py Kural 5 yorumu) — bu yüzden burası KATI eşitliktir.

  2) GERİLEME OLMAZ — doğru karar sayısı kayıtlı taban çizgisinin ALTINA
     düşemez. Taban çizgisi bilinçli olarak düşüktür: same_event tek başına
     bazı mükerrerleri yakalayamaz (mimari sınır, bkz. TABAN yorumu).
     Kapının işi taban çizgisini yükseltmek değil, DÜŞMESİNİ engellemektir.

Golden set'i yenilemek/genişletmek için: python scripts/golden_set_kur.py
Ayrıntılı rapor için:                     python scripts/dedup_olc.py
"""
import json
import os

import pytest

from src import dedup

GOLDEN = os.path.join(os.path.dirname(__file__), '..', 'data', 'dedup_golden.json')

# same_event'in "aynı olay" DEMEMESİ gereken etiketler.
BIRLESTIRILEMEZ = {'ILISKISIZ', 'AYNI_AKTOR_FARKLI_OLAY'}
# same_event'in "aynı olay" DEMESİ gereken etiketler.
BIRLESMELI = {'AYNI_GELISME'}

# TABAN ÇİZGİSİ — 2026-08-12'de ölçülen doğru karar sayısı.
#
# 18 puanlanan çiftin 12'si doğru. Kaçan 6 vaka (Patch Tuesday↔ESU, Delta,
# kötü amaçlı SIM, Mozilla GPG, Gunra, CEVA) same_event'in YAPISAL sınırıdır:
# hepsi ortak kod adı/aktör/CVE taşımaz ve konu örtüşmesi eşiğin altındadır.
# Bunları eşik indirerek yakalamak yanlış-birleştirme üretir (ölçüldü:
# _TOPIC_WITH_ENTITY 0.22→0.18 gövdede 10 yanlış-pozitif). Çözümleri eşik
# ayarı değil, YENİ SİNYAL + dört değerli sınıflandırıcıdır (src/olay_iliski).
#
# Bu sayı DÜŞERSE bir regresyon vardır. YÜKSELİRSE burayı güncelle — kapı
# yeni seviyeyi korusun.
TABAN_DOGRU = 12


def _ciftler():
    if not os.path.exists(GOLDEN):
        pytest.skip('data/dedup_golden.json yok — scripts/golden_set_kur.py')
    with open(GOLDEN, encoding='utf-8') as f:
        return json.load(f)['ciftler']


def _karar(cift):
    return dedup.same_event(cift['a'], cift['b'],
                            explain=True, cross_day=not cift['ayni_gun'])


def test_golden_set_yapisi():
    """Golden set okunabilir ve her çift eksiksiz olmalı."""
    ciftler = _ciftler()
    assert len(ciftler) >= 20, 'Golden set beklenenden küçük — bozulmuş olabilir'
    gecerli = {'AYNI_GELISME', 'YENI_GELISME',
               'AYNI_AKTOR_FARKLI_OLAY', 'ILISKISIZ'}
    for c in ciftler:
        assert c['iliski'] in gecerli, f'{c["ad"]}: bilinmeyen etiket'
        assert isinstance(c['ayni_gun'], bool), f'{c["ad"]}: ayni_gun eksik'
        for yan in ('a', 'b'):
            metin = (c[yan].get('title', '') + c[yan].get('full_text', '')
                     + c[yan].get('tr_title', '') + c[yan].get('paragraph', ''))
            assert metin.strip(), f'{c["ad"]}: {yan} tarafı boş'


@pytest.mark.parametrize('cift', [
    pytest.param(c, id=c['ad']) for c in _ciftler()
    if c['iliski'] in BIRLESTIRILEMEZ
])
def test_yanlis_birlestirme_olmaz(cift):
    """FARKLI iki haber ASLA 'aynı olay' sayılmamalı (en zararlı hata sınıfı)."""
    karar, neden = _karar(cift)
    assert karar is False, (
        f'{cift["ad"]}: farklı iki haber birleştirildi ({neden}).\n'
        f'   {cift["not"]}')


def test_gerileme_olmaz():
    """Doğru karar sayısı taban çizgisinin altına düşemez."""
    dogru, hatalar = 0, []
    for c in _ciftler():
        if c['iliski'] not in (BIRLESMELI | BIRLESTIRILEMEZ):
            continue          # YENI_GELISME: politika sorusu, sinyal sorusu değil
        beklenen = c['iliski'] in BIRLESMELI
        karar, neden = _karar(c)
        if karar == beklenen:
            dogru += 1
        else:
            hatalar.append(f'{c["ad"]} (beklenen={beklenen}, gerçek={karar})')
    assert dogru >= TABAN_DOGRU, (
        f'REGRESYON: {dogru} doğru < taban {TABAN_DOGRU}.\n'
        + '\n'.join('   • ' + h for h in hatalar))


def test_redaksiyon_isareti_kod_adi_sayilmaz():
    """Arşiv kaynak maskesi (XXXXXXX) olay parmak izi değildir."""
    assert 'xxxxxxx' not in dedup.extract_codenames(
        'Bir haber metni (XXXXXXX, AÇIK - example.com, 12.08.2026)')


def test_system_kod_adi_sayilmaz():
    """'SYSTEM-level privileges' kalıbı olay parmak izi değildir."""
    assert 'system' not in dedup.extract_codenames(
        'execute code with SYSTEM-level privileges on the host')


def test_yama_dongusu_kurali_promptta_duruyor():
    """Yama döngüsü kuralı ve satıcı koruması sessizce düşmemeli.

    2026-08-12 üretim koşusunda Microsoft'un Yama Salısı toplamı ile Windows 10
    ESU bülteni raporda AYNI ANDA yer aldı — deterministik katman ikisini
    bağlayamıyor (ortak kimlik yok, topic=0.15), bu ayrım dünya bilgisi ister.
    Kural LLM denetim promptuna eklendi. İkinci assert eşit derecede önemli:
    kuralı satıcı sınırı olmadan bırakmak, ölçülmüş yanlış-birleştirme sınıfını
    (SAP↔Adobe, Ivanti↔SonicWall, ICS↔Microsoft) geri getirirdi."""
    from src.config import get_dedup_review_prompt
    p = get_dedup_review_prompt('=== HABER ID: 1 ===\nBaşlık: x\nÖzet: y\n')
    assert 'AYNI SATICININ AYNI YAMA DÖNGÜSÜ' in p
    assert 'FARKLI SATICILARIN' in p
