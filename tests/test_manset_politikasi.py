"""MANŞET POLİTİKASI — 2026-08-12 regresyonunun kapısı.

O gün ne oldu: günün 2. (96 puan, İran'ın ABD su altyapısına saldırısı) ve
3. (92 puan, Sandworm/UAC-0145) en yüksek puanlı haberleri KRİTİK 3'e
giremedi; yerlerine 90 puanlı iki haber girdi.

İki ayrı sebep vardı ve ikisi de aynı kök nedene bağlıydı — 'mukerrer' tek
bitinin üç durumu karıştırması:
  • İran haberi mukerrer=1 yedi (aslında YENİ gelişme: yeni eyaletler) ve
    manşet kapısı bayrağa baktığı için elendi.
  • Sandworm haberi mukerrer=0'dı; onu bir LLM manşet katmanı düşürdü
    (dünkü Polonya sabotajıyla ortak olan tek şey AKTÖRDÜ).

Bu dosya politikanın deterministik yarısını kapıya bağlar: aynı girdilerle
manşet havuzunun doğru üç haberi üretmesi gerekir. Fikstür o günün GERÇEK
adaylarını, skorlarını ve önceki 7 günün geçmişini taşır.
"""
import json
import os

import pytest

from src import dedup as _dedup
from src import olay_iliski as oi

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures',
                       'manset_2026_08_12.json')

# main.HaberSistemi.MANSET_TEKRAR_SINIRI ile aynı olmalı.
MANSET_TEKRAR_SINIRI = 1


@pytest.fixture(scope='module')
def fx():
    with open(FIXTURE, encoding='utf-8') as f:
        return json.load(f)


@pytest.fixture(scope='module')
def baglam(fx):
    """(sozluk, defter, gecmis_views) — üretimdeki kurulumun aynısı."""
    tum = [v for g in fx['gecmis'] for v in g['views']]
    sozluk = oi.OlaySozlugu(tum)
    defter = oi.defter_kur(
        [(g['gun'], g['views'], g['manset']) for g in fx['gecmis']],
        sozluk=sozluk)
    gecmis = [v for g in fx['gecmis'] for v in g['views']]
    return sozluk, defter, gecmis


def _gercek_mukerrer(view, gecmis, sozluk):
    return any(oi.iliski_belirle(view, ev, sozluk=sozluk) == oi.AYNI_GELISME
               for ev in gecmis)


def _manset_havuzu(fx, baglam):
    """Politikanın uyguladığı manşet havuzu (puan sırasında)."""
    sozluk, defter, gecmis = baglam
    havuz = []
    for a in sorted(fx['adaylar'], key=lambda x: -x['puan']):
        # (a) gerçek mükerrer mi? — yalnızca skorlayıcı bayrak koyduysa bakılır
        if a['mukerrer'] and _gercek_mukerrer(a['view'], gecmis, sozluk):
            continue
        # (b) bu olay son günlerde zaten manşet oldu mu?
        if defter.manset_gunu_sayisi(a['view']) >= MANSET_TEKRAR_SINIRI:
            continue
        havuz.append(a)
    return havuz


def test_manset_en_yuksek_puanli_uc_ayrik_olayi_secer(fx, baglam):
    """Beklenen sonuç: 97, 96, 92 — üretimde 97, 90, 90 çıkmıştı."""
    sozluk, defter, gecmis = baglam
    havuz = _manset_havuzu(fx, baglam)
    by_id = {a['id']: a for a in fx['adaylar']}
    manset_gecmisi = [v for g in fx['gecmis'] for v in g['manset']]
    secilen = _dedup.pick_distinct(
        [a['id'] for a in havuz], lambda i: by_id[i]['view'],
        n=3, exclude_views=manset_gecmisi)
    puanlar = [by_id[i]['puan'] for i in secilen]
    assert puanlar == [97, 96, 92], (
        f'manşet puanları {puanlar}; seçilenler: '
        + ' | '.join(by_id[i]['baslik'][:55] for i in secilen))


def test_iran_su_altyapisi_manşete_cikabilir(fx, baglam):
    """YENİ gelişme (yeni eyaletler) manşet yasağı YEMEMELİ."""
    sozluk, defter, gecmis = baglam
    iran = next(a for a in fx['adaylar'] if 'Iran-Linked' in a['baslik'])
    assert iran['mukerrer'] == 1, 'fikstür bozulmuş: bayrak beklenen gibi değil'
    assert not _gercek_mukerrer(iran['view'], gecmis, sozluk), (
        'İran su altyapısı haberi gerçek mükerrer sayıldı — YENİ eyaletler '
        'bildiriyor')
    assert iran['id'] in [a['id'] for a in _manset_havuzu(fx, baglam)]


def test_sandworm_ayni_aktor_yuzunden_dusmez(fx, baglam):
    """Dünkü manşetle ortak olan tek şey AKTÖRSE manşet geçmişi devralınmaz."""
    sozluk, defter, gecmis = baglam
    sw = next(a for a in fx['adaylar'] if 'Sandworm-Linked UAC' in a['baslik'])
    assert defter.manset_gunu_sayisi(sw['view']) == 0, (
        'Sandworm/UAC-0145, dünkü Sandworm/Polonya manşetinin geçmişini '
        'devraldı — aktör adı olay kimliği sayılıyor')
    assert sw['id'] in [a['id'] for a in _manset_havuzu(fx, baglam)]


def test_gercek_mukerrerler_hala_elenir(fx, baglam):
    """Politika gevşemesi gerçek mükerreri kaçırmamalı (Gunra, Head Mare)."""
    sozluk, defter, gecmis = baglam
    havuz_ids = {a['id'] for a in _manset_havuzu(fx, baglam)}
    for parca in ('Gunra Ransomware Exploits', 'Head Mare APT'):
        aday = next(a for a in fx['adaylar'] if parca in a['baslik'])
        assert aday['id'] not in havuz_ids, (
            f'{parca}: gerçek mükerrer manşet havuzuna girdi')


def test_uretimdeki_sonuc_gercekten_hataliydi(fx):
    """Fikstürün taşıdığı üretim sonucu, düzeltilen hatayı gösteriyor olmalı."""
    assert sorted(fx['uretimdeki_kritik3']) == [90, 90, 97]
