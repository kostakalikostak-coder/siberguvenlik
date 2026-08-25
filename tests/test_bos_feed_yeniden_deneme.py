"""200 OK ama işe yaramaz feed yanıtında yeniden deneme.

2026-08-25 12:06 koşusunda iki kaynak aynı anda böyle düştü:
  • SANS ISC   → 200, gövde XML değil ("syntax error: line 1, column 0")
  • The Register → 200, geçerli XML ama 0 madde ("SESSİZ BOŞ")
Aynı gün feed_test AYNI URL'leri üretim IP'sinden sorunsuz çekti (SANS 10,
Register 50 madde) — yani arıza URL'de değil, geçici. Bu ikisi 08-21'de
havuza 9 haber koymuştu; kayıp doğrudan raporun inceliğine yazıldı.

HTTP durumu 200 olduğu için `retry_statuses` bu vakaları hiç görmüyordu.
"""
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import main as main_mod

DOLU = (b'<?xml version="1.0"?><rss><channel>'
        b'<item><title>Gercek Haber</title><link>https://x/1</link>'
        b'<description>govde</description></channel></rss>')
BOS = b'<?xml version="1.0"?><rss><channel></channel></rss>'
XML_DEGIL = b'<html><body>Just a moment... checking your browser</body></html>'


class _Yanit:
    def __init__(self, content, status_code=200):
        self.content = content
        self.status_code = status_code


def _sistem():
    s = main_mod.HaberSistemi.__new__(main_mod.HaberSistemi)
    s.rss_errors = []
    s.source_stats = {}
    s.headers = {}
    return s


def _cek(yanitlar):
    """Sıralı yanıtlarla fetch_rss çalıştırır; (haberler, istek_sayısı) döner."""
    s = _sistem()
    sayac = {'n': 0}

    def _get(url, **kw):
        y = yanitlar[min(sayac['n'], len(yanitlar) - 1)]
        sayac['n'] += 1
        return y

    with patch.object(main_mod, '_requests_get_with_retry', side_effect=_get):
        haberler = s.fetch_rss('https://example.com/feed', 'Test Kaynak')
    return s, haberler, sayac['n']


def test_bos_yanit_yeniden_denenir_ve_kurtarilir():
    """200 + 0 madde → ikinci deneme yapılır, gelen haber kurtarılır."""
    s, haberler, n = _cek([_Yanit(BOS), _Yanit(DOLU)])
    assert n == 2, 'boş yanıt yeniden denenmedi'
    assert len(haberler) == 1, 'ikinci denemedeki haber alınmadı'
    assert s.rss_errors == [], 'kurtarılan kaynağa hata kaydı düştü'
    assert s.source_stats['Test Kaynak']['status'] == 'OK'


def test_xml_olmayan_yanit_yeniden_denenir_ve_kurtarilir():
    """SANS ISC vakası: anti-bot HTML sayfası → parse hatası, sonra XML gelir."""
    s, haberler, n = _cek([_Yanit(XML_DEGIL), _Yanit(DOLU)])
    assert n == 2, 'parse hatası veren yanıt yeniden denenmedi'
    assert len(haberler) == 1
    assert s.rss_errors == [], 'kurtarılan kaynağa hata kaydı düştü'


def test_ikinci_deneme_de_bosca_sessiz_bos_kaydi_dusar():
    """Yeniden deneme kaydı GİZLEMEZ — kalıcı arıza yine iz bırakmalı."""
    s, haberler, n = _cek([_Yanit(BOS), _Yanit(BOS)])
    assert n == 2
    assert haberler == []
    assert any('SESSİZ BOŞ' in e for e in s.rss_errors)
    assert s.source_stats['Test Kaynak']['status'] == 'BOŞ (200/0 madde)'


def test_ilk_deneme_basariliysa_ikinci_istek_atilmaz():
    """Maliyet: sağlıklı kaynakta ek istek YOK."""
    s, haberler, n = _cek([_Yanit(DOLU)])
    assert n == 1, 'sağlıklı kaynağa gereksiz ikinci istek atıldı'
    assert len(haberler) == 1


def test_yavas_ilk_deneme_yeniden_denenmez():
    """Bütçe koruması: ilk deneme HIZLI_ESIK'i aştıysa ikinci tur başlamaz,
    yoksa join(20s) retry'yi ortasında keser ve kaynak TIMEOUT damgası yer."""
    s = _sistem()
    sayac = {'n': 0}
    saat = {'t': 0.0}

    def _get(url, **kw):
        sayac['n'] += 1
        saat['t'] += 9.0          # ilk deneme 9s sürdü (> HIZLI_ESIK=8)
        return _Yanit(BOS)

    with patch.object(main_mod, '_requests_get_with_retry', side_effect=_get), \
         patch('time.monotonic', side_effect=lambda: saat['t']):
        s.fetch_rss('https://example.com/feed', 'Test Kaynak')

    assert sayac['n'] == 1, 'yavaş ilk denemeden sonra ikinci tur başlatıldı'
