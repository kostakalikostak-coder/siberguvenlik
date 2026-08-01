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
        assert len(hash1) == 32  # tam MD5 hexdigest

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
        assert len(hash_val) == 32  # tam MD5 hexdigest
        assert isinstance(hash_val, str)

    def test_hash_special_characters(self):
        """Özel karakterler içeren content"""
        hash_val = _calculate_content_hash(
            "Title with $pecial @haracters",
            "Description with <html> & symbols"
        )
        assert len(hash_val) == 32  # tam MD5 hexdigest


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
        url = "https://example.com/article?source=rss&medium=feed&id=99"
        normalized = _normalize_url_advanced(url)
        assert "source=rss" not in normalized
        assert "medium=feed" not in normalized
        assert "id=99" in normalized  # diğer parametreler korunmalı

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
        """The Register ?td= proxy URL'leri çöz"""
        target = "https://www.theregister.com/2024/01/01/security_article"
        url = f"https://go.theregister.com/tg3/way/r?td={target}"
        normalized = _normalize_url_advanced(url)
        assert "go.theregister.com" not in normalized
        # www. normalize edildiğinden theregister.com olur
        assert "theregister.com" in normalized

    def test_google_feedburner_redirect(self):
        """FeedBurner URL'leri feedproxy içermeden normalize edilebiliyor mu?"""
        # Not: Gerçek FeedBurner redirect çözümü HTTP HEAD gerektirir (ağ bağımlı).
        # Bu test yalnızca non-FeedBurner URL'lerin bozulmadığını doğrular.
        url = "https://securityaffairs.com/2024/01/01/article.html"
        normalized = _normalize_url_advanced(url)
        assert normalized.startswith("https://securityaffairs.com")
        assert not normalized.endswith('/')

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
        today = datetime.now().strftime('%Y-%m-%d')
        links_file = tmp_path / "haberler_linkler.txt"
        links_file.write_text(
            f"{today}\thttps://example.com/article1\tTitle 1\n"
            f"{today}\thttps://example.com/article2\tTitle 2\n",
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
        today = datetime.now().strftime('%Y-%m-%d')
        links_file = tmp_path / "haberler_linkler.txt"
        links_file.write_text(
            f"{today}\thttps://example.com/article1\tTitle 1\thash1234567890ab\n"
            f"{today}\thttps://example.com/article2\tTitle 2\thash2345678901bc\n",
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
        today = datetime.now().strftime('%Y-%m-%d')
        links_file = tmp_path / "haberler_linkler.txt"
        links_file.write_text(
            f"{today}\thttps://example.com/article\tOld Title\thash123\n",
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
        today = datetime.now().strftime('%Y-%m-%d')
        links_file = tmp_path / "haberler_linkler.txt"
        title = "Critical Vulnerability"
        desc = "A critical vulnerability in systems"
        old_hash = _calculate_content_hash(title, desc)

        links_file.write_text(
            f"{today}\thttps://example.com/old-url\t{title}\t{old_hash}\n",
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
        """Benzer başlıklar filtreleniyror mu? (eşik: 0.85)"""
        today = datetime.now().strftime('%Y-%m-%d')
        links_file = tmp_path / "haberler_linkler.txt"
        old_title = "Microsoft Exchange Critical Vulnerability Discovered"
        links_file.write_text(
            f"{today}\thttps://example.com/article1\t{old_title}\thash123\n",
            encoding='utf-8'
        )

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)

        all_news = {
            'Test Source': [
                {
                    'link': 'https://example.com/article2',
                    'title': 'Microsoft Exchange Critical Vulnerability Found',  # >85% benzer
                    'description': 'Same vulnerability different source'
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


class TestKeywordJaccardSimilarity:
    """_keyword_jaccard_similarity() testleri — Level 4 dedup"""

    def setup_method(self):
        self.sistem = HaberSistemi()

    def test_same_event_different_wording_caught(self):
        """Aynı olay, farklı kaynak anlatımı → yakalanmalı"""
        t1 = "Rusya Bağlantılı Grupların Fortinet Güvenlik Duvarı Yönetici Şifrelerini Ele Geçirmesi"
        t2 = "Rusya Bağlantılı Saldırganların Fortinet Güvenlik Duvarlarını Kitlesel Olarak Ele Geçirmesi"
        score = self.sistem._keyword_jaccard_similarity(t1, t2)
        assert score >= 0.45, f"Beklenen >= 0.45, alınan {score:.3f}"

    def test_different_cve_not_caught(self):
        """Farklı CVE numaraları → farklı haber, yakalanmamalı"""
        t1 = "Windows Kernelde Kritik Güvenlik Açığı CVE-2024-1234"
        t2 = "Windows SMB Protokolünde Yeni Güvenlik Açığı CVE-2024-5678"
        score = self.sistem._keyword_jaccard_similarity(t1, t2)
        assert score == 0.0, f"Farklı CVE'ler 0.0 döndürmeli, alınan {score:.3f}"

    def test_same_cve_allowed_through(self):
        """Aynı CVE numarası, farklı anlatım → normal Jaccard hesabı yapılmalı (0.0 dönemez)"""
        t1 = "Kritik Açık CVE-2024-9999 Kamuoyuyla Paylaşıldı"
        t2 = "CVE-2024-9999 Yamalaması Acilen Yayınlandı"
        score = self.sistem._keyword_jaccard_similarity(t1, t2)
        assert score > 0.0, "Aynı CVE'de sıfırdan büyük olmalı"

    def test_different_version_not_caught(self):
        """Farklı sürüm numaraları → farklı haber"""
        t1 = "Apache HTTP Server 2.4.51 Güvenlik Güncellemesi"
        t2 = "Apache HTTP Server 2.4.58 Kritik Yama Yayınlandı"
        score = self.sistem._keyword_jaccard_similarity(t1, t2)
        assert score == 0.0, f"Farklı sürümler 0.0 döndürmeli, alınan {score:.3f}"

    def test_unrelated_stories_low_score(self):
        """Tamamen farklı haberler → düşük skor"""
        t1 = "Apple iPhone iOS Güncellemesi Yayınlandı"
        t2 = "Rusya Altyapı Saldırısı Enerji Sektörünü Hedef Aldı"
        score = self.sistem._keyword_jaccard_similarity(t1, t2)
        assert score < 0.30, f"Bağımsız haberler < 0.30 olmalı, alınan {score:.3f}"

    def test_filter_duplicates_level4_catches_same_event(self, tmp_path):
        """_filter_duplicates Level 4: aynı olay iki farklı kaynaktan gelince birini atar"""
        links_file = tmp_path / "haberler_linkler.txt"
        links_file.write_text("", encoding='utf-8')

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)

        all_news = {
            'securityaffairs': [
                {
                    'link': 'https://securityaffairs.com/fortinet-russia',
                    'title': 'Rusya Bağlantılı Grupların Fortinet Güvenlik Duvarı Yönetici Şifrelerini Ele Geçirmesi',
                    'description': 'Rusça konuşan tehdit grubu 75 bin cihazı ele geçirdi'
                }
            ],
            'theregister': [
                {
                    'link': 'https://theregister.com/fortinet-russia-attack',
                    'title': 'Rusya Bağlantılı Saldırganların Fortinet Güvenlik Duvarlarını Kitlesel Olarak Ele Geçirmesi',
                    'description': 'Rus siber suç grubu 194 ülkede Fortinet cihazlarına sızdı'
                }
            ]
        }

        filtered = sistem._filter_duplicates(all_news)
        total = sum(len(v) for v in filtered.values())
        assert total == 1, f"Aynı olaydan yalnızca 1 haber geçmeli, {total} geçti"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


class TestAptEvidenceGuard:
    """_has_apt_evidence — zafiyet_aktif_apt / nation_state_apt etiketinin
    deterministik doğrulaması (2026-07-31 regresyonu).

    Bu etiket kritik3'e girme hakkı verdiği için yanlış-pozitifi pahalıdır."""

    def test_explicit_state_attribution_passes(self):
        sistem = HaberSistemi()
        assert sistem._has_apt_evidence(
            'A Russia-linked threat actor targeted government mail servers.')
        assert sistem._has_apt_evidence(
            'Rusya bağlantılı tehdit aktörünün kamu kurumlarını hedeflediği '
            'bildirilmiştir.')

    def test_structural_actor_id_passes(self):
        """Aktör kimliği tek başına yeterli — 'devlet destekli' yazmasa bile."""
        sistem = HaberSistemi()
        assert sistem._has_apt_evidence('UAT-7810 expanded its ORB network.')
        assert sistem._has_apt_evidence('Laundry Bear kampanyası sürüyor.')

    def test_free_codename_alone_is_not_evidence(self):
        """Ürün/spec/kıyaslama adı + tehdit sözcüğü APT KANITI DEĞİLDİR.

        Gerçek vaka (31.07.2026): Anthropic'in kendi güvenlik testlerinde
        modelinin izole ortamdan çıkması haberi, metninde geçen 'AsyncAPI' npm
        paketi CamelCase olduğu için 'kod adı' sayıldı; 'malware' sözcüğüyle
        birleşince zafiyet_aktif_apt etiketini korudu ve 91 puanla kritik3'e
        uygun kaldı. AYNI olayın diğer üç kopyası doğru şekilde indirilmişti.
        """
        sistem = HaberSistemi()
        assert not sistem._has_apt_evidence(
            'The model uploaded a malicious package; the AsyncAPI dependency '
            'was affected and the malware exfiltrated data during tests.')
        # 2026-07-30 vakası: "ExploitGym" bir kıyaslama testi, aktör değil
        assert not sistem._has_apt_evidence(
            'The ExploitGym benchmark measured the campaign simulation.')

    def test_empty_text_is_not_evidence(self):
        sistem = HaberSistemi()
        assert not sistem._has_apt_evidence('')
        assert not sistem._has_apt_evidence('', None)


class TestOrphanedGroupRestore:
    """_restore_orphaned_groups — aynı-olay grubunun tümüyle elenmesini önler
    (2026-07-31 Analog Devices regresyonu)."""

    # Aynı olayın üç kopyası + alakasız bir haber
    _VIEWS = {
        64: {'tr_title': 'Analog Devices Şirketinin Veri İhlali Bildirmesi',
             'paragraph': 'Yarı iletken üreticisi Analog Devices, sistemlerine '
                          'yetkisiz erişim sağlandığını ve bir miktar verinin ele '
                          'geçirildiğini bildirmiştir.',
             'title': 'Analog Devices reports data breach', 'full_text': ''},
        14: {'tr_title': 'Analog Devices Veri İhlalini Açıkladı',
             'paragraph': 'Analog Devices, yetkisiz sistem erişimi sonrası bir veri '
                          'ihlali yaşandığını, operasyonlarının etkilenmediğini '
                          'açıklamıştır.',
             'title': 'Analog Devices discloses data breach', 'full_text': ''},
        20: {'tr_title': 'Analog Devices İhlali Doğruladı',
             'paragraph': 'Analog Devices, yetkisiz erişim sonrasında veri ihlali '
                          'yaşandığını doğrulamıştır.',
             'title': 'Analog Devices Discloses Data Breach', 'full_text': ''},
        16: {'tr_title': 'Televizyon Kutularında Reklam Dolandırıcılığı',
             'paragraph': 'Bir güvenlik araştırmacısı, ucuz akış cihazlarının '
                          'reklam dolandırıcılığı ağının parçası olduğunu tespit '
                          'etmiştir.',
             'title': 'Read This Before You Buy That TV Streaming Stick',
             'full_text': ''},
    }
    _SCORES = {64: {'toplam': 81}, 14: {'toplam': 80},
               20: {'toplam': 80}, 16: {'toplam': 59}}

    def _view_fn(self):
        return lambda aid: self._VIEWS[aid]

    def test_group_wiped_out_is_restored_with_highest_score(self):
        """Üç kopyanın ÜÇÜ de elenmişse en yüksek puanlı geri gelir.

        Gerçek vaka: Pass 5 kalite denetimi 14 ve 20'yi attı (çapa 64), sonra
        deterministik aynı-olay pası FARKLI çapa mantığıyla 64'ü de attı ve
        haber rapordan tamamen kayboldu."""
        sistem = HaberSistemi()
        _, restored = sistem._restore_orphaned_groups(
            candidates_before=[64, 14, 20, 16], kept_ids=[16], top3_ids=[],
            view_fn=self._view_fn(), score_records=self._SCORES)
        assert restored == [64], 'En yüksek puanlı kopya geri alınmalıydı'

    def test_only_one_member_restored_not_all(self):
        """Geri alma mükerrer YARATMAMALI — gruptan yalnızca BİR haber döner."""
        sistem = HaberSistemi()
        new_kept, restored = sistem._restore_orphaned_groups(
            candidates_before=[64, 14, 20, 16], kept_ids=[16], top3_ids=[],
            view_fn=self._view_fn(), score_records=self._SCORES)
        assert len(restored) == 1
        assert sorted(new_kept) == [16, 64]

    def test_surviving_member_means_no_restore(self):
        """Gruptan bir haber hâlâ rapordaysa geri alma YAPILMAZ."""
        sistem = HaberSistemi()
        _, restored = sistem._restore_orphaned_groups(
            candidates_before=[64, 14, 20, 16], kept_ids=[14, 16], top3_ids=[],
            view_fn=self._view_fn(), score_records=self._SCORES)
        assert restored == []

    def test_event_represented_in_top3_is_not_restored(self):
        """Olay KRİTİK 3'te temsil ediliyorsa gövdeye geri alınmaz."""
        sistem = HaberSistemi()
        _, restored = sistem._restore_orphaned_groups(
            candidates_before=[64, 14, 20, 16], kept_ids=[16], top3_ids=[20],
            view_fn=self._view_fn(), score_records=self._SCORES)
        assert restored == []

    def test_no_drops_means_no_change(self):
        sistem = HaberSistemi()
        new_kept, restored = sistem._restore_orphaned_groups(
            candidates_before=[64, 16], kept_ids=[64, 16], top3_ids=[],
            view_fn=self._view_fn(), score_records=self._SCORES)
        assert restored == []
        assert new_kept == [64, 16]

    def test_singleton_quality_removal_is_not_restored(self):
        """Kopyası OLMAYAN haber Pass 5 kalite gerekçesiyle atıldıysa geri
        GELMEMELİ — bu bir grup boşalması değildir.

        Gerçek regresyon (01.08.2026): ön şart yokken koruma, Pass 5'in kalite
        filtresini fiilen iptal etti; kopyasız 6 düşük puanlı politika/analiz
        haberi geri geldi ve gövde 12'den 17'ye şişti."""
        sistem = HaberSistemi()
        # 16 (alakasız, tekil) elendi; 64/14/20 grubundan 14 hayatta
        _, restored = sistem._restore_orphaned_groups(
            candidates_before=[64, 14, 20, 16], kept_ids=[14], top3_ids=[],
            view_fn=self._view_fn(), score_records=self._SCORES)
        assert 16 not in restored, 'Tekil kalite elemesi geri alınmamalı'
        assert restored == []

    def test_only_multi_member_groups_are_restored(self):
        """Tekil haber elenmişken grup da boşalmışsa: SADECE grup geri gelir."""
        sistem = HaberSistemi()
        _, restored = sistem._restore_orphaned_groups(
            candidates_before=[64, 14, 20, 16], kept_ids=[], top3_ids=[],
            view_fn=self._view_fn(), score_records=self._SCORES)
        assert restored == [64], f'Yalnızca grup çapası dönmeliydi, dönen: {restored}'


class TestNoveltyTiebreak:
    """_apply_novelty_tiebreak — eşit puanlı manşet adayları arasında YENİ olanı
    öne alır (2026-07-31 ölçümü).

    Eşitlik-bozucu zinciri toplam→kategori→k→a→BESLEME SIRASI şeklindeydi; `k`
    ölü bir eksen olduğu için (değerlerin %87'si 14-15) zincir pratikte doğrudan
    besleme sırasına düşüyordu. 30 günün 2'sinde günün manşetini fiilen RSS
    besleme sırası belirledi."""

    # "Tanıdık" aday: ortak parmak izi (NightLedger) yalnızca GÖVDEDE geçer ve
    # konu örtüşmesi eşiğin altındadır → same_event AYNI OLAY DEMEZ (yani
    # exclude_views onu elemez) ama nearmiss_signal bağı görür. Yenilik
    # eşitlik-bozucusunun devreye girdiği tam durum budur.
    _TANIDIK = {'tr_title': 'Bir Casusluk Grubunun Kurban Sistemlerini Ele Geçirmesi',
                'paragraph': 'Saldırganların hedef kuruluşlarda kalıcılık sağlamak '
                             'amacıyla NightLedger bileşenini kullandığı '
                             'bildirilmiştir.',
                'title': 'Espionage group turns victim systems into relays',
                'full_text': ''}
    _YENI = {'tr_title': 'İran Bağlantılı Grupların Endüstriyel Sistemleri Taraması',
             'paragraph': 'Saldırganların farklı endüstriyel denetleyici ailelerini '
                          'taradığı kaydedilmiştir.',
             'title': 'Iran-linked crews probe US industrial systems', 'full_text': ''}
    _GECMIS = [{'tr_title': 'Fidye Yazılımı Çetesinin Perakende Zincirini Hedeflemesi',
                'paragraph': 'Perakende zincirinin kasa terminalleri şifrelenmiş, '
                             'mağazalar üç gün kapalı kalmış; incelemede NightLedger '
                             'izine rastlanmıştır.',
                'title': 'Ransomware crew hits retail chain', 'full_text': ''}]

    def test_fixture_is_a_nearmiss_not_a_duplicate(self):
        """Ön koşul: _TANIDIK ile geçmiş kayıt AYNI OLAY sayılmamalı, yalnızca
        yakın-kaçış bağı taşımalı. Aksi hâlde bu sınıf yanlış şeyi test eder."""
        from src import dedup
        assert not dedup.same_event(self._TANIDIK, self._GECMIS[0], cross_day=True)
        assert dedup.nearmiss_signal(self._TANIDIK, self._GECMIS[0], cross_day=True)
        assert not dedup.nearmiss_signal(self._YENI, self._GECMIS[0], cross_day=True)

    _RECORDS = {1: {'toplam': 93, 'kat': 'nation_state_apt', 'k': 15, 'a': 19},
                2: {'toplam': 93, 'kat': 'nation_state_apt', 'k': 15, 'a': 19}}

    def _views(self):
        return {1: self._TANIDIK, 2: self._YENI}.get

    def test_new_story_wins_the_tie(self):
        """Her şey berabereyken son günlerle bağı OLMAYAN haber öne geçer."""
        sistem = HaberSistemi()
        out = sistem._apply_novelty_tiebreak([1, 2], self._RECORDS,
                                             self._views(), self._GECMIS)
        assert out == [2, 1], 'Yeni haber (2) tanıdık haberin (1) önüne geçmeliydi'

    def test_higher_score_still_wins(self):
        """Yenilik yalnızca BERABERLİĞİ bozar — puanı ASLA geçersizleştirmez."""
        sistem = HaberSistemi()
        records = {1: {'toplam': 97, 'kat': 'nation_state_apt', 'k': 15, 'a': 19},
                   2: {'toplam': 93, 'kat': 'nation_state_apt', 'k': 15, 'a': 19}}
        out = sistem._apply_novelty_tiebreak([1, 2], records,
                                             self._views(), self._GECMIS)
        assert out == [1, 2], 'Yüksek puanlı haber tanıdık olsa da önde kalmalı'

    def test_category_priority_still_wins(self):
        """Kategori önceliği de yenilikten ÖNCE gelir (zincirdeki yeri korunur)."""
        sistem = HaberSistemi()
        records = {1: {'toplam': 93, 'kat': 'nation_state_apt', 'k': 15, 'a': 19},
                   2: {'toplam': 93, 'kat': 'veri_ihlali', 'k': 15, 'a': 19}}
        out = sistem._apply_novelty_tiebreak([1, 2], records,
                                             self._views(), self._GECMIS)
        assert out == [1, 2]

    def test_no_history_means_no_change(self):
        sistem = HaberSistemi()
        assert sistem._apply_novelty_tiebreak([1, 2], self._RECORDS,
                                              self._views(), []) == [1, 2]

    def test_both_new_keeps_original_order(self):
        """İki aday da yeniyse sıra DEĞİŞMEZ (07-07 vakası)."""
        sistem = HaberSistemi()
        out = sistem._apply_novelty_tiebreak([1, 2], self._RECORDS,
                                             self._views(), [])
        assert out == [1, 2]
