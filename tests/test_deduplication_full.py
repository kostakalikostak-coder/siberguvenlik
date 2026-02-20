"""
Comprehensive Deduplication Tests
Hash-based ve URL normalizasyonu testleri
"""
import pytest
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
import tempfile

# main.py'nin bulunduğu dizini path'e ekle
sys.path.insert(0, str(Path(__file__).parent.parent))

from main import HaberSistemi, _calculate_content_hash, _normalize_url_advanced


class TestHashCalculation:
    """Content hash fonksiyonu testleri"""

    def test_hash_consistency(self):
        """Aynı içerik aynı hash üretiyor mu?"""
        title = "Critical Security Vulnerability"
        desc = "A critical vulnerability in production systems"
        hash1 = _calculate_content_hash(title, desc)
        hash2 = _calculate_content_hash(title, desc)
        assert hash1 == hash2
        assert len(hash1) == 16

    def test_hash_case_insensitivity(self):
        """Hash case-insensitive mi?"""
        hash1 = _calculate_content_hash("Test Title", "Test Description")
        hash2 = _calculate_content_hash("TEST TITLE", "TEST DESCRIPTION")
        assert hash1 == hash2

    def test_hash_different_content(self):
        """Farklı içerik farklı hash üretiyor mu?"""
        hash1 = _calculate_content_hash("Title A", "Description A")
        hash2 = _calculate_content_hash("Title B", "Description B")
        assert hash1 != hash2

    def test_hash_empty_content(self):
        """Boş içerik için hash"""
        hash_val = _calculate_content_hash("", "")
        assert len(hash_val) == 16
        assert isinstance(hash_val, str)

    def test_hash_special_characters(self):
        """Özel karakterler içeren content"""
        hash_val = _calculate_content_hash(
            "Title with $pecial @haracters",
            "Description with <html> & symbols"
        )
        assert len(hash_val) == 16


class TestURLNormalization:
    """Advanced URL normalizasyonu testleri"""

    def test_remove_utm_parameters(self):
        """UTM parametreleri kaldırılıyor mu?"""
        url = "https://example.com/article?utm_source=twitter&utm_medium=social&utm_campaign=news"
        normalized = _normalize_url_advanced(url)
        assert "utm_" not in normalized
        assert "https://example.com/article" == normalized

    def test_remove_source_medium_params(self):
        """source/medium parametreleri de kaldırılıyor mu?"""
        url = "https://example.com/article?source=rss&medium=feed"
        normalized = _normalize_url_advanced(url)
        assert "source" not in normalized
        assert "medium" not in normalized

    def test_preserve_other_params(self):
        """Diğer parametreler korunuyor mu?"""
        url = "https://example.com/article?id=123&category=security"
        normalized = _normalize_url_advanced(url)
        assert "id=123" in normalized
        assert "category=security" in normalized

    def test_trailing_slash_removal(self):
        """Trailing slash kaldırılıyor mu?"""
        url = "https://example.com/article/"
        normalized = _normalize_url_advanced(url)
        assert not normalized.endswith('/')
        assert normalized == "https://example.com/article"

    def test_protocol_https_normalization(self):
        """HTTP otomatik HTTPS'ye dönüşüyor mu?"""
        url = "http://example.com/article"
        normalized = _normalize_url_advanced(url)
        assert normalized.startswith("https://")

    def test_the_register_redirect(self):
        """The Register proxy URL'leri çöz"""
        url = "https://go.theregister.com/feed/www.theregister.com/security/article"
        normalized = _normalize_url_advanced(url)
        assert "go.theregister.com" not in normalized
        assert normalized.startswith("https://www.theregister.com")

    def test_google_feedburner_redirect(self):
        """Google FeedBurner redirect'lerini çöz"""
        url = "https://feedproxy.google.com/~r/somesite/~3/article-id/full"
        normalized = _normalize_url_advanced(url)
        assert "feedproxy.google.com" not in normalized
        assert normalized.startswith("https://")

    def test_lowercase_domain(self):
        """Domain lowercase'e dönüştürülüyor mu?"""
        url = "https://Example.COM/Article"
        normalized = _normalize_url_advanced(url)
        assert normalized.startswith("https://example.com")

    def test_parameter_sorting(self):
        """Parametreler alfabetik sıraya konuyor mu?"""
        url1 = "https://example.com/article?z=3&a=1&m=2"
        url2 = "https://example.com/article?a=1&m=2&z=3"
        norm1 = _normalize_url_advanced(url1)
        norm2 = _normalize_url_advanced(url2)
        assert norm1 == norm2

    def test_complex_url_normalization(self):
        """Karmaşık URL'nin tamamen normalize edilmesi"""
        url = "HTTP://Example.COM/article/?utm_source=twitter&id=123&utm_medium=social/"
        normalized = _normalize_url_advanced(url)
        assert normalized.startswith("https://example.com")
        assert "utm_" not in normalized
        assert not normalized.endswith('/')
        assert "id=123" in normalized


class TestHaberSistemiDeduplication:
    """HaberSistemi sınıfının deduplication metodları"""

    def test_load_used_links_empty_file(self, tmp_path):
        """Dosya yokken boş set döndürüyor mu?"""
        sistem = HaberSistemi()
        sistem.used_links_file = str(tmp_path / "nonexistent.txt")
        links, titles, hashes = sistem._load_used_links()
        assert links == set()
        assert titles == {}
        assert hashes == set()

    def test_load_used_links_old_format(self, tmp_path):
        """Eski 3-sütun format uyumluluğu"""
        links_file = tmp_path / "haberler_linkler.txt"
        links_file.write_text(
            "2026-02-20\thttps://example.com/article1\tTitle 1\n"
            "2026-02-20\thttps://example.com/article2\tTitle 2\n",
            encoding='utf-8'
        )

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)
        links, titles, hashes = sistem._load_used_links()

        assert len(links) == 2
        assert len(titles) == 2
        assert len(hashes) == 0  # Eski format'ta hash yok

    def test_load_used_links_new_format(self, tmp_path):
        """Yeni 4-sütun format uyumluluğu"""
        links_file = tmp_path / "haberler_linkler.txt"
        links_file.write_text(
            "2026-02-20\thttps://example.com/article1\tTitle 1\thash1234567890ab\n"
            "2026-02-20\thttps://example.com/article2\tTitle 2\thash2345678901bc\n",
            encoding='utf-8'
        )

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)
        links, titles, hashes = sistem._load_used_links()

        assert len(links) == 2
        assert len(titles) == 2
        assert len(hashes) == 2

    def test_load_used_links_7day_cutoff(self, tmp_path):
        """7 günden eski linkler filtreleniyor mu?"""
        links_file = tmp_path / "haberler_linkler.txt"
        today = datetime.now().strftime('%Y-%m-%d')
        old_date = (datetime.now() - timedelta(days=8)).strftime('%Y-%m-%d')

        links_file.write_text(
            f"{today}\thttps://example.com/new\tNew Article\n"
            f"{old_date}\thttps://example.com/old\tOld Article\n",
            encoding='utf-8'
        )

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)
        links, titles, hashes = sistem._load_used_links()

        assert len(links) == 1  # Sadece güncel olan
        assert "https://example.com/new" in links or any("new" in l for l in links)

    def test_save_used_links_with_hash(self, tmp_path):
        """Yeni linker hash'le beraber kaydediliyor mu?"""
        links_file = tmp_path / "haberler_linkler.txt"

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)

        articles = [
            {
                'link': 'https://example.com/article1',
                'title': 'Test Article 1',
                'description': 'Test description 1'
            }
        ]

        sistem._save_used_links(articles)

        # Dosya oluşturuldu mu?
        assert links_file.exists()

        # İçerik doğru mu?
        content = links_file.read_text(encoding='utf-8')
        lines = content.strip().split('\n')
        assert len(lines) >= 1

        # Format kontrol (4 sütun)
        parts = lines[0].split('\t')
        assert len(parts) == 4  # date, link, title, hash


class TestFilterDuplicates:
    """_filter_duplicates() metodu testleri"""

    def test_filter_duplicates_by_url(self, tmp_path):
        """URL duplikatları filtreleniyror mu?"""
        links_file = tmp_path / "haberler_linkler.txt"
        links_file.write_text(
            "2026-02-20\thttps://example.com/article\tOld Title\thash123\n",
            encoding='utf-8'
        )

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)

        all_news = {
            'Test Source': [
                {
                    'link': 'https://example.com/article',
                    'title': 'New Title',
                    'description': 'Different description'
                }
            ]
        }

        filtered = sistem._filter_duplicates(all_news)
        assert 'Test Source' not in filtered or len(filtered.get('Test Source', [])) == 0

    def test_filter_duplicates_by_hash(self, tmp_path):
        """Hash duplikatları filtreleniyror mu?"""
        links_file = tmp_path / "haberler_linkler.txt"
        title = "Critical Vulnerability"
        desc = "A critical vulnerability in systems"
        old_hash = _calculate_content_hash(title, desc)

        links_file.write_text(
            f"2026-02-20\thttps://example.com/old-url\t{title}\t{old_hash}\n",
            encoding='utf-8'
        )

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)

        all_news = {
            'Test Source': [
                {
                    'link': 'https://different-site.com/new-article',
                    'title': title,
                    'description': desc
                }
            ]
        }

        filtered = sistem._filter_duplicates(all_news)
        assert 'Test Source' not in filtered or len(filtered.get('Test Source', [])) == 0

    def test_filter_duplicates_by_similarity(self, tmp_path):
        """Benzer başlıklar filtreleniyror mu?"""
        links_file = tmp_path / "haberler_linkler.txt"
        links_file.write_text(
            "2026-02-20\thttps://example.com/article1\tMicrosoft Exchange Critical Vulnerability\thash123\n",
            encoding='utf-8'
        )

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)

        all_news = {
            'Test Source': [
                {
                    'link': 'https://example.com/article2',
                    'title': 'Microsoft Exchange Critical Security Flaw',  # 85%+ benzer
                    'description': 'Different vulnerability'
                }
            ]
        }

        filtered = sistem._filter_duplicates(all_news)
        assert 'Test Source' not in filtered or len(filtered.get('Test Source', [])) == 0

    def test_filter_duplicates_allow_new(self, tmp_path):
        """Yeni haberler geçiyor mu?"""
        links_file = tmp_path / "haberler_linkler.txt"
        links_file.write_text("", encoding='utf-8')

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)

        all_news = {
            'Test Source': [
                {
                    'link': 'https://example.com/new-article',
                    'title': 'Brand New Story',
                    'description': 'Completely new story'
                }
            ]
        }

        filtered = sistem._filter_duplicates(all_news)
        assert 'Test Source' in filtered
        assert len(filtered['Test Source']) == 1
        assert filtered['Test Source'][0]['title'] == 'Brand New Story'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
