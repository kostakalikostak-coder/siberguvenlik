"""
AKIŞ BAYAT (stale mirror) telafisi testleri.

Senaryo: feed 200 döner ve madde verir, ama en yenisi bile normal penceredan
(96s) eski kalır — ayna bayat snapshot servis ediyordur. 2026-08-14→17'de
The Hacker News'te bu yaşandı: pencere dışı madde sayısı 8→16→33→40 tırmandı,
17'sinde en verimli kaynak (14'ünde 32 haber) hiç üretmedi.

Beklenen davranış: aynı koşuda telafi penceresine (168s) geçilir, haberler
kurtarılır ve rss_errors'a "AKIŞ BAYAT" kaydı düşer ki sonraki koşular da
geniş pencerede kalsın.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

import main as main_mod


def _sistem():
    s = main_mod.HaberSistemi.__new__(main_mod.HaberSistemi)
    s.rss_errors = []
    s._failed_sources_cache = set()
    s._gap_days_cache = 0
    return s


def _haber(saat_once):
    """Verilen saat kadar önce yayınlanmış tek haber."""
    dt = datetime.now(timezone.utc) - timedelta(hours=saat_once)
    return {
        'title': f'Haber {saat_once}s',
        'link': f'https://example.com/{saat_once}',
        'date': dt.strftime('%a, %d %b %Y %H:%M:%S +0000'),
    }


class TestBayatAkisTespiti:

    def test_hepsi_pencere_disiysa_telafi_penceresi_kurtarir(self):
        """96s dışı ama 168s içi haberler kurtarılır + kayıt düşer."""
        s = _sistem()
        # 100s ve 150s: normal pencere (96s) dışı, telafi penceresi (168s) içi
        articles = [_haber(100), _haber(150)]

        with patch.object(main_mod.HaberSistemi, 'fetch_rss', return_value=articles), \
             patch.object(main_mod.HaberSistemi, '_crawl_newsletter_links', return_value=[]), \
             patch.object(main_mod, 'time'):
            s.sources = {'Test Kaynak': 'https://example.com/feed'}
            kurtarilan = self._topla_tek_kaynak(s)

        assert len(kurtarilan) == 2, "168s içindeki haberler kurtarılmalı"
        assert any('AKIŞ BAYAT - Test Kaynak' in e for e in s.rss_errors)
        # Sonraki adımlar/koşular da geniş pencere görsün
        assert 'Test Kaynak' in s._failed_sources_cache

    def test_telafi_penceresi_de_yetmezse_bos_kalir(self):
        """168s'den de eski haberler kurtarılmaz; kayıt yine düşer."""
        s = _sistem()
        articles = [_haber(400), _haber(500)]   # ~17-21 gün

        with patch.object(main_mod.HaberSistemi, 'fetch_rss', return_value=articles), \
             patch.object(main_mod.HaberSistemi, '_crawl_newsletter_links', return_value=[]), \
             patch.object(main_mod, 'time'):
            s.sources = {'Test Kaynak': 'https://example.com/feed'}
            kurtarilan = self._topla_tek_kaynak(s)

        assert kurtarilan == []
        assert any('AKIŞ BAYAT' in e for e in s.rss_errors)

    def test_pencere_ici_haber_varsa_tetiklenmez(self):
        """Sağlıklı kaynakta bayat-akış yolu HİÇ çalışmaz (eski davranış)."""
        s = _sistem()
        articles = [_haber(10), _haber(200)]    # biri pencere içi

        with patch.object(main_mod.HaberSistemi, 'fetch_rss', return_value=articles), \
             patch.object(main_mod.HaberSistemi, '_crawl_newsletter_links', return_value=[]), \
             patch.object(main_mod, 'time'):
            s.sources = {'Test Kaynak': 'https://example.com/feed'}
            kurtarilan = self._topla_tek_kaynak(s)

        assert len(kurtarilan) == 1, "yalnızca pencere içindeki haber kalmalı"
        assert s.rss_errors == []

    @staticmethod
    def _topla_tek_kaynak(s):
        """Gerçek üretim yolunu çağırır (topla() bu metodu kullanır)."""
        articles = s.fetch_rss('https://example.com/feed', 'Test Kaynak')
        return s._pencere_filtresi('Test Kaynak', articles)


class TestKayitTanima:

    def test_akis_bayat_kaydi_telafi_penceresi_actirir(self, tmp_path):
        """rss_errors.txt'teki AKIŞ BAYAT satırı sonraki koşuda tanınmalı."""
        s = main_mod.HaberSistemi.__new__(main_mod.HaberSistemi)
        s._failed_sources_cache = None
        bugun = main_mod._now_tr().strftime('%Y-%m-%d')
        p = tmp_path / 'rss_errors.txt'
        p.write_text(
            f"{bugun} 12:05 | AKIŞ BAYAT - The Hacker News: 40 madde geldi ama "
            f"hepsi pencere dışı (>96s)\n", encoding='utf-8')
        s.rss_errors_file = str(p)

        assert 'The Hacker News' in s._recently_failed_sources()

    def test_eski_kayit_sayilmaz(self, tmp_path):
        """7 günden eski AKIŞ BAYAT kaydı pencereyi açmaz."""
        s = main_mod.HaberSistemi.__new__(main_mod.HaberSistemi)
        s._failed_sources_cache = None
        eski = (main_mod._now_tr() - timedelta(days=30)).strftime('%Y-%m-%d')
        p = tmp_path / 'rss_errors.txt'
        p.write_text(f"{eski} 12:05 | AKIŞ BAYAT - The Hacker News: 40 madde\n",
                     encoding='utf-8')
        s.rss_errors_file = str(p)

        assert s._recently_failed_sources() == set()
