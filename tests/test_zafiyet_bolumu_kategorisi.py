"""Güvenlik Açıkları bölümü CANLI kategoriye bakmalı, bayat kopyaya değil.

2026-08-25: "ToxicPanda Bankacılık Truva Atının Kurumsal Tehdide Dönüşmesi"
haberi raporun "🔐 Güvenlik Açıkları" bölümünde basıldı; oysa yayın yönetmeni
onu `phishing_sosyal_muhendislik` kategorisine taşımıştı (skorlama logundaki
son kayıt da böyle diyor). Sebep: renderer'a verilen `category_by_id`
SIRALAMA anında kurulan bir anlık görüntüydü; kategori sonradan üç yerde
değişiyor (_enforce_apt_attribution x2, yayın yönetmeni düzeltmesi) ama harita
tazelenmiyordu.
"""
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import main as main_mod


def _rapor(kat):
    articles = [{'id': 1, 'title': 'ToxicPanda Banking Trojan', 'link': 'https://x/1',
                 'source': 'Dark Reading', 'date': '', 'description': ''},
                {'id': 2, 'title': 'Baska Haber', 'link': 'https://x/2',
                 'source': 'BleepingComputer', 'date': '', 'description': ''}]
    icerik = {1: {'tr_title': 'ToxicPanda Bankacılık Truva Atı',
                  'paragraph': 'Gövde metni.'},
              2: {'tr_title': 'Diger Haber', 'paragraph': 'Gövde metni.'}}
    return main_mod.HaberSistemi._build_html(
        articles=articles, top10_ids=[1, 2], remaining_ids=[],
        content_by_id=icerik, today_str='25 AĞUSTOS 2026', top3_ids=[],
        exec_summary='', category_by_id={1: kat, 2: 'nation_state_apt'})


def _zafiyet_bolumunde_mi(html):
    i = html.find('id="guvenlik-aciklari"')
    return i != -1 and 'ToxicPanda' in html[i:]


def test_zafiyet_kategorisi_zafiyet_bolumune_gider():
    assert _zafiyet_bolumunde_mi(_rapor('zafiyet_rutin')), \
        'zafiyet etiketli haber Güvenlik Açıkları bölümüne gitmedi'


def test_zafiyet_disi_kategori_zafiyet_bolumune_gitmez():
    """Yönetmenin taşıdığı kategori raporda KARŞILIĞINI bulmalı."""
    assert not _zafiyet_bolumunde_mi(_rapor('phishing_sosyal_muhendislik')), \
        'zafiyet olmayan haber Güvenlik Açıkları bölümünde basıldı'


def test_renderer_kategori_haritasi_canli_kayitlardan_tazelenir():
    """Kanca yerinde mi: `_build_html` çağrısından ÖNCE harita `score_records`
    üzerinden yeniden kurulmalı. Bu satır düşerse hata sessizce geri gelir."""
    kaynak = inspect.getsource(main_mod.HaberSistemi.create_html)
    i_taze = kaynak.find("category_by_id = {aid: rec['kat'] for aid, rec in "
                         "score_records.items()")
    i_cagri = kaynak.find('html = self._build_html(')
    assert i_taze != -1, 'kategori haritası canlı kayıtlardan tazelenmiyor'
    assert i_taze < i_cagri, 'tazeleme _build_html çağrısından SONRA yapılıyor'
