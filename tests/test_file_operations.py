"""
File Operations Tests
Dosya I/O, encoding, error handling testleri
"""
import pytest
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent))

from main import HaberSistemi, _calculate_content_hash


class TestFileOperations:
    """Dosya işlemleri testleri"""

    def test_create_data_directory(self, tmp_path):
        """data/ klasörü oluşturuluyor mu?"""
        original_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            sistema = HaberSistemi()
            articles = [{'link': 'https://example.com', 'title': 'Test', 'description': 'Test'}]
            sistema._save_used_links(articles)
            assert (tmp_path / "data").exists()
        finally:
            os.chdir(original_cwd)

    def test_save_and_load_consistency(self, tmp_path):
        """Kaydedilen veriler doğru şekilde yüklenebiliyor mu?"""
        links_file = tmp_path / "haberler_linkler.txt"

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)

        # Veri kaydet
        articles = [
            {'link': 'https://example1.com', 'title': 'Article 1', 'description': 'Desc 1'},
            {'link': 'https://example2.com', 'title': 'Article 2', 'description': 'Desc 2'},
            {'link': 'https://example3.com', 'title': 'Article 3', 'description': 'Desc 3'},
        ]
        sistem._save_used_links(articles)

        # Veri yükle
        links, titles, hashes = sistem._load_used_links()

        # Kontrol et
        assert len(links) == 3
        assert len(titles) == 3
        assert len(hashes) == 3

    def test_utf8_encoding(self, tmp_path):
        """UTF-8 encoding doğru çalışıyor mu?"""
        links_file = tmp_path / "haberler_linkler.txt"

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)

        # Türkçe karakterler
        articles = [
            {
                'link': 'https://example.com/türkçe',
                'title': 'Türkçe Başlık - Siber Güvenlik Haberi',
                'description': 'Açıklama: Kritik güvenlik açığı bulundu'
            }
        ]
        sistem._save_used_links(articles)

        # Yükle ve kontrol et
        links, titles, hashes = sistem._load_used_links()
        assert len(links) == 1
        assert 'Türkçe' in list(titles.values())[0]

    def test_malformed_lines_skipped(self, tmp_path):
        """Hatalı satırlar atlanıyor mu?"""
        today = datetime.now().strftime('%Y-%m-%d')
        links_file = tmp_path / "haberler_linkler.txt"
        content = (
            f"{today}\thttps://example.com/1\tTitle 1\thash1\n"
            "INVALID_LINE_WITHOUT_TABS\n"
            f"{today}\thttps://example.com/2\tTitle 2\thash2\n"
            "\n"
            f"{today}\thttps://example.com/3\tTitle 3\n"
        )
        links_file.write_text(content, encoding='utf-8')

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)
        links, titles, hashes = sistem._load_used_links()

        # 3 geçerli satır okumalı
        assert len(links) >= 2  # En az 2 (3-sütunlu da sayılır)

    def test_empty_file_handling(self, tmp_path):
        """Boş dosya doğru handlelanıyor mu?"""
        links_file = tmp_path / "haberler_linkler.txt"
        links_file.write_text("", encoding='utf-8')

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)
        links, titles, hashes = sistem._load_used_links()

        assert links == set()
        assert titles == {}
        assert hashes == set()

    def test_file_permission_error_handling(self, tmp_path):
        """Dosya permisyon hatası gracefully handle ediliyor mu?"""
        links_file = tmp_path / "haberler_linkler.txt"
        links_file.write_text("test", encoding='utf-8')

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)

        # Dosya izinlerini kaldır (sadece read)
        os.chmod(str(links_file), 0o444)

        # Read işlemi başarılı olmalı
        links, titles, hashes = sistem._load_used_links()
        assert isinstance(links, set)

        # İzinleri geri yükle
        os.chmod(str(links_file), 0o644)


class TestDateHandling:
    """Tarih işlemleri testleri"""

    def test_old_articles_filtered_correctly(self, tmp_path):
        """7 günden eski makaleler filtreleniyor mu?"""
        links_file = tmp_path / "haberler_linkler.txt"

        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        six_days_ago = (datetime.now() - timedelta(days=6)).strftime('%Y-%m-%d')
        two_weeks_ago = (datetime.now() - timedelta(days=14)).strftime('%Y-%m-%d')

        links_file.write_text(
            f"{today}\thttps://example.com/today\tToday\thash1\n"
            f"{yesterday}\thttps://example.com/yesterday\tYesterday\thash2\n"
            f"{six_days_ago}\thttps://example.com/recent\tRecent\thash3\n"
            f"{two_weeks_ago}\thttps://example.com/old\tOld\thash4\n",
            encoding='utf-8'
        )

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)
        links, titles, hashes = sistem._load_used_links()

        # 3 geçerli (bugün + dün + 6 gün önce); 14 gün öncesi filtrelendi
        assert len(links) == 3
        assert not any('old' in str(link).lower() for link in links)

    def test_same_day_multiple_entries(self, tmp_path):
        """Aynı gün içinde birden fazla entry olabilir mi?"""
        links_file = tmp_path / "haberler_linkler.txt"
        today = datetime.now().strftime('%Y-%m-%d')

        links_file.write_text(
            f"{today}\thttps://example.com/1\tTitle 1\thash1\n"
            f"{today}\thttps://example.com/2\tTitle 2\thash2\n"
            f"{today}\thttps://example.com/3\tTitle 3\thash3\n",
            encoding='utf-8'
        )

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)
        links, titles, hashes = sistem._load_used_links()

        assert len(links) == 3


class TestErrorRecovery:
    """Hata kurtarma testleri"""

    def test_corrupted_hash_fallback(self, tmp_path):
        """Bozuk hash'le de çalışıyor mu?"""
        today = datetime.now().strftime('%Y-%m-%d')
        links_file = tmp_path / "haberler_linkler.txt"
        links_file.write_text(
            f"{today}\thttps://example.com/1\tTitle 1\tinvalidhash\n"  # Kısa hash
            f"{today}\thttps://example.com/2\tTitle 2\t1234567890abcdef\n",  # Geçerli
            encoding='utf-8'
        )

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)
        links, titles, hashes = sistem._load_used_links()

        # Her ikisi de yüklenmeli
        assert len(links) == 2

    def test_missing_required_fields(self, tmp_path):
        """Eksik alanlarla nasıl davranılıyor?"""
        links_file = tmp_path / "haberler_linkler.txt"
        links_file.write_text(
            "2026-02-20\t\tEmpty Link\thash1\n"  # Boş link
            "\thttps://example.com/2\tNo Date\thash2\n"  # Boş date
            "2026-02-20\thttps://example.com/3\t\thash3\n",  # Boş title
            encoding='utf-8'
        )

        sistem = HaberSistemi()
        sistem.used_links_file = str(links_file)

        # Hata vermeden çalışmalı
        try:
            links, titles, hashes = sistem._load_used_links()
            # En azından geçerli olanları yükledi
            assert isinstance(links, set)
        except Exception as e:
            pytest.fail(f"Exception raised: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ── Bekleyen linkler: "görüldü" işaretleme rapor başarısına bağlı ──────────
# Eskiden linkler save_txt içinde, LLM adımından ÖNCE işaretleniyordu; gün boyu
# süren bir LLM arızasında o günün TÜM haberleri 7 gün için yakılıyordu.

class TestPendingLinks:
    def _sistem(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        s = HaberSistemi()
        s.used_links_file = "data/linkler.txt"
        return s

    _ARTS = [{'link': 'https://y/1', 'title': 'Haber 1', 'description': 'd1'},
             {'link': 'https://y/2', 'title': 'Haber 2', 'description': 'd2'}]

    def _links(self, s):
        import os
        if not os.path.exists(s.used_links_file):
            return []
        with open(s.used_links_file, encoding='utf-8') as f:
            return [l.split('\t')[1] for l in f if l.strip()]

    def test_write_pending_does_not_mark_used(self, tmp_path, monkeypatch):
        """Haber çekildiğinde link HENÜZ 'görüldü' olmamalı."""
        s = self._sistem(tmp_path, monkeypatch)
        s._write_pending_links(self._ARTS)
        assert self._links(s) == []
        assert (tmp_path / s.PENDING_LINKS_FILE).exists()

    def test_commit_marks_used_and_clears(self, tmp_path, monkeypatch):
        """Rapor başarılıysa linkler işaretlenir ve bekleyen dosya silinir."""
        s = self._sistem(tmp_path, monkeypatch)
        s._write_pending_links(self._ARTS)
        s._commit_pending_links()
        assert self._links(s) == ['https://y/1', 'https://y/2']
        assert not (tmp_path / s.PENDING_LINKS_FILE).exists()

    def test_commit_works_when_save_txt_was_skipped(self, tmp_path, monkeypatch):
        """KRİTİK: ham yeniden kullanıldığında save_txt çağrılmaz.

        Bekleyen liste bellekte tutulsaydı o slotta boş olur, rapor başarılı
        olsa bile linkler işaretlenmez ve haberler ERTESİ GÜN MÜKERRER çıkardı.
        Bu yüzden liste DİSKE yazılır; farklı bir sistem nesnesi de okuyabilmeli.
        """
        s1 = self._sistem(tmp_path, monkeypatch)
        s1._write_pending_links(self._ARTS)          # 1. koşu: çekti, rapor düştü
        s2 = HaberSistemi()                          # 2. koşu: taze nesne
        s2.used_links_file = s1.used_links_file
        s2._commit_pending_links()
        assert self._links(s2) == ['https://y/1', 'https://y/2']

    def test_stale_pending_is_not_marked(self, tmp_path, monkeypatch):
        """Eski tarihli bekleyen kayıt işaretlenmemeli — haber yeniden aday olmalı.

        Bu, düzeltmenin ASIL amacı: gün boyu başarısız kalan bir koşunun
        haberleri ertesi gün yeniden değerlendirilebilsin.
        """
        import json
        s = self._sistem(tmp_path, monkeypatch)
        (tmp_path / s.PENDING_LINKS_FILE).write_text(
            json.dumps({'date': '2020-01-01', 'articles': self._ARTS}),
            encoding='utf-8')
        s._commit_pending_links()
        assert self._links(s) == []
        assert not (tmp_path / s.PENDING_LINKS_FILE).exists()

    def test_content_hash_survives_the_detour(self, tmp_path, monkeypatch):
        """Bekleyen dosyadan geçen link, eski yolla AYNI içerik hash'ini üretmeli.

        description saklanmazsa Seviye-2 (hash) dedup sessizce bozulurdu.
        """
        s = self._sistem(tmp_path, monkeypatch)
        art = {'link': 'https://x/1', 'title': 'Başlık ÖĞÜ', 'description': 'açıklama'}
        beklenen = _calculate_content_hash(art['title'], art['description'])
        s._write_pending_links([art])
        s._commit_pending_links()
        with open(s.used_links_file, encoding='utf-8') as f:
            assert f.read().strip().split('\t')[3] == beklenen


# ── DEFTER ↔ PARMAK İZİ SİMETRİSİ ──────────────────────────────────────────
# 2026-08-02/03 vakası: link defteri kapısı _rapor_basarili (TABAN DAHİL) idi,
# ama parmak izi depoları create_html içinde tabandan BAĞIMSIZ yazılıyordu.
# Taban altı bir raporda ikisi ayrıştı: olay "son 7 günde raporlandı" sayıldı
# ama linki "görülmedi" kaldı. Ölçülen sonuç — 08-02'nin 12 haberinin 12'si de
# 08-03'te BİREBİR yeniden çekildi, havuzun %32'sini doldurdu, 8'i "zaten
# raporlandı" diye elendi, rapor yine taban altı kaldı: kendini besleyen döngü.
#
# Değişmez kural: YAYINLANDIYSA ikisi de yazılır, FALLBACK ise ikisi de yazılmaz.

class TestRaporDurumKapilari:
    _DOLU = '<div class="top3-card">x</div>' * 3 + '<div class="news-item">y</div>' * 12
    _INCE = '<div class="top3-card">x</div>' * 3 + '<div class="news-item">y</div>' * 6

    def test_ince_rapor_yayinlanmis_sayilir(self):
        """Taban altı ama GERÇEK rapor → defter kapısı AÇIK (asıl düzeltme)."""
        from main import _rapor_yayinlandi
        assert _rapor_yayinlandi(self._INCE) is True

    def test_ince_rapor_basarili_sayilmaz(self):
        """Aynı rapor 'yeniden dene' kapısında BAŞARISIZ kalmalı — taban korunur."""
        from main import _rapor_basarili
        assert _rapor_basarili(self._INCE) is False
        assert _rapor_basarili(self._DOLU) is True

    @pytest.mark.parametrize('isaret', [
        '<!-- RAPOR_DURUM: FALLBACK -->',
        '[FALLBACK]',
        'Gemini API yanıt vermedi — içerik çevrilmedi',
    ])
    def test_fallback_her_iki_kapida_kapali(self, isaret):
        """Fallback'te linkler İŞARETLENMEMELİ — haberler yarına sağlam kalsın."""
        from main import _rapor_yayinlandi, _rapor_basarili
        icerik = self._DOLU + isaret
        assert _rapor_yayinlandi(icerik) is False
        assert _rapor_basarili(icerik) is False

    def test_haber_adedi_kritik3_kartlarini_sayar(self):
        """Sayaç üretimdeki toplamla (gövde + kritik3) aynı olmalı."""
        from main import _rapor_haber_adedi
        assert _rapor_haber_adedi(self._INCE) == 9
        assert _rapor_haber_adedi('<div class="news-item vuln-item">v</div>') == 1


# ── HAVUZA GÖRELİ TABAN ────────────────────────────────────────────────────
# Sabit 10 eşiği günün ARZINDAN bağımsızdı: hafta sonu havuzu daralınca
# ulaşılamaz oluyor ve gün sonsuz yeniden denemeye kilitleniyordu. 08-03'te
# beş tam LLM koşusu yapıldı (6/7/9/7/7 haber) ve en iyi sürüm iki kez ezildi.

class TestGoreliTaban:
    def _rapor(self, adet, taze=None):
        h = ('<div class="top3-card">x</div>' * min(adet, 3)
             + '<div class="news-item">y</div>' * max(0, adet - 3))
        return h + (f'<!-- RAPOR_TAZE_HAVUZ: {taze} -->' if taze else '')

    def test_normal_gunde_davranis_degismez(self):
        """Taze arz bolsa taban eskisi gibi 10 — regresyon koruması.
        Gerçek ölçüm: 07-28..08-01 taze havuz 26-39."""
        from main import _hesapla_taban, REPORT_FLOOR
        for taze in (26, 28, 29, 32, 39):
            assert _hesapla_taban(taze) == REPORT_FLOOR

    def test_ince_gunde_taban_iner(self):
        """Gerçek ölçüm: 08-03 taze 9 → 4, 07-27 taze 12 → 5."""
        from main import _hesapla_taban
        assert _hesapla_taban(9) == 4
        assert _hesapla_taban(12) == 5

    def test_mutlak_alt_sinir(self):
        """Arz ne kadar küçük olursa olsun taban FLOOR_MIN'in altına inmez."""
        from main import _hesapla_taban, REPORT_FLOOR_MIN
        assert _hesapla_taban(1) == REPORT_FLOOR_MIN
        assert _hesapla_taban(3) == REPORT_FLOOR_MIN   # 08-02: taze 3

    def test_havuz_bilinmiyorsa_eski_davranis(self):
        """İşaretsiz (eski) rapor sabit REPORT_FLOOR ile değerlendirilir."""
        from main import _hesapla_taban, _rapor_havuzu, REPORT_FLOOR
        assert _rapor_havuzu('<div>hiç işaret yok</div>') == 0
        assert _hesapla_taban(0) == REPORT_FLOOR

    def test_isaret_okunur_ve_tabani_belirler(self):
        """08-03 senaryosu: 7 haber + taze arz 9 → başarılı, döngü kırılır."""
        from main import _rapor_basarili, _rapor_havuzu
        rapor = self._rapor(7, taze=9)
        assert _rapor_havuzu(rapor) == 9
        assert _rapor_basarili(rapor) is True

    def test_isaretli_ama_gercekten_bos_rapor_gecmez(self):
        """Arz bolken ince rapor GERÇEK arızadır — taban 10'da kalır."""
        from main import _rapor_basarili
        assert _rapor_basarili(self._rapor(5, taze=39)) is False

    def test_ham_havuz_kullanilsaydi_dongu_kirilmazdi(self):
        """Payda seçiminin gerekçesini çivileyen test.

        08-03'te HAM siber havuz 21, TAZE havuz 9'du (12'si son 7 günde zaten
        raporlanmış olaylar). Ham havuz kullanılsaydı taban 9 çıkar, günün
        gerçek arzı 9 tazeyken 9 haber istenir ve gün yeniden denemeye
        kilitlenmeye devam ederdi."""
        from main import _hesapla_taban
        assert _hesapla_taban(21) == 9      # ham havuz → ulaşılamaz eşik
        assert _hesapla_taban(9) == 4       # taze havuz → gerçekçi eşik


# ── GERİLEME KORUMASI ──────────────────────────────────────────────────────
# 08-03 ölçümü: aynı gün beş koşu 6/7/9/7/7 haber üretti; en iyi sürüm (9)
# iki kez daha kötüsüyle EZİLDİ. Yeniden üretim aynı ham havuzu kullandığı
# için fark yalnızca LLM'in seçim gürültüsüdür.

class TestGerilemeKorumasi:
    def _rapor(self, adet, fallback=False):
        h = '<div class="news-item">y</div>' * adet
        return h + ('<!-- RAPOR_DURUM: FALLBACK -->' if fallback else '')

    def test_daha_az_haberli_surum_yazilmaz(self):
        from main import _gerileme_var_mi
        assert _gerileme_var_mi(self._rapor(9), 7) is True

    def test_daha_cok_haberli_surum_yazilir(self):
        from main import _gerileme_var_mi
        assert _gerileme_var_mi(self._rapor(6), 9) is False

    def test_esitlik_gerileme_sayilmaz(self):
        """İçerik düzeltmeleri aynı haber sayısıyla gelir — uygulanabilmeli."""
        from main import _gerileme_var_mi
        assert _gerileme_var_mi(self._rapor(9), 9) is False

    def test_fallback_uzerine_her_zaman_yazilir(self):
        """Diskteki sürüm fallback ise her gerçek rapor ondan iyidir."""
        from main import _gerileme_var_mi
        assert _gerileme_var_mi(self._rapor(20, fallback=True), 3) is False


# ── Devlet/APT kanıtı: atıf İFADESİ veya AKTÖR KİMLİĞİ ─────────────────────
# 2026-07-30: OpenAI'nin kendi modelinin test sırasında korumalı alandan kaçması
# haberi nation_state_apt etiketlendi ve YALNIZCA kategori önceliği sayesinde
# kritik3'e girdi (88 puanda beraberliği bozdu).

class TestAptEvidence:
    def _s(self):
        return HaberSistemi()

    def test_turkish_attribution_phrases(self):
        """Raporlar Türkçe üretiliyor; en sık kalıp tanınmalı."""
        s = self._s()
        for t in ("Rusya bağlantılı tehdit aktörü Zimbra açığını istismar etti",
                  "İran destekli saldırganlar su tesislerini hedefledi",
                  "Çin bağlantılı casusluk grubu",
                  "Kuzey Kore bağlantılı grup npm paketlerine sızdı",
                  "Çinli tehdit aktörleri"):
            assert s._has_state_attribution(t), t

    def test_country_noun_link_phrases(self):
        """Ülke SIFATI değil İSMİ ile yazılan atıflar da tanınmalı."""
        s = self._s()
        for t in ("Russia-linked actors targeted the ministry",
                  "Belarus-Linked Codebase, Analysis Finds",
                  "China-nexus espionage group"):
            assert s._has_state_attribution(t), t

    def test_icin_is_not_china(self):
        """KELİME SINIRI: Türkçe 'için', 'çin' ile eşleşmemeli.

        Sınırsız desen neredeyse HER Türkçe metni 'devlet atıflı' sayar ve
        denetimi tamamen işlevsiz bırakırdı.
        """
        s = self._s()
        for t in ("Bu haber için önemli bir gelişmedir",
                  "Kullanıcılar için güvenlik güncellemesi yayımlandı"):
            assert not s._has_state_attribution(t), t

    def test_bare_country_is_not_attribution(self):
        """Yalın ülke adı atıf değildir (ürünün nerede üretildiği vb.)."""
        s = self._s()
        for t in ("Chinese enterprise software library is widely deployed",
                  "Çin menşeli Unitree firmasının insansı robotları",
                  "Rusya'da faaliyet gösteren bir e-ticaret sitesi kapandı"):
            assert not s._has_state_attribution(t), t

    def test_actor_identity_alone_is_evidence(self):
        """Atıf CÜMLESİ olmadan da aktör adı geçen haber APT olabilir.

        Bu yol olmadan gerçek APT haberleri haksız yere cezalandırılırdı;
        geçmiş ölçümünde kayıtların dörtte biri SADECE bu yoldan geçiyordu.
        """
        s = self._s()
        for t in ("TAG-195 Upgrades MaaS Ecosystem with Modular Tools",
                  "APT29 deployed a new backdoor",
                  "Laundry Bear targeted Western organizations"):
            assert s._has_apt_evidence(t), t

    def test_product_codename_without_threat_context_is_not_evidence(self):
        """Ürün/kıyaslama adı kod adı sanılmamalı.

        'ExploitGym' bir kıyaslama testi adıdır; CamelCase olduğu için kod adı
        sayılıyordu ve OpenAI haberini APT kanıtlı gösteriyordu.
        """
        s = self._s()
        t = ("OpenAI, güvenlik testleri sırasında kontrolden çıkan modellerin "
             "ExploitGym kıyaslama testi sırasında korumalı alanı aştığını açıkladı.")
        assert not s._has_apt_evidence(t)

    def test_unverified_nation_state_loses_priority_edge(self):
        """Doğrulanmamış nation_state_apt öncelik avantajını kaybetmeli."""
        s = self._s()
        recs = {1: {'kat': 'nation_state_apt', 'toplam': 88,
                    's': 35, 'e': 20, 'a': 18, 'k': 15},
                2: {'kat': 'politika_hukuk', 'toplam': 88,
                    's': 35, 'e': 20, 'a': 18, 'k': 15}}
        arts = {1: {'full_text': 'OpenAI modeli test sırasında korumalı alandan çıktı.',
                    'title': 'Rogue AI agent breached second company'},
                2: {'full_text': 'FSB, Telegram kurucusunu teröre yardımla suçladı.',
                    'title': 'Russia charges Telegram founder'}}
        s._enforce_apt_attribution(recs, arts)
        assert recs[1].get('apt_dogrulanmadi') is True
        assert s._kat_oncelik(recs[1]) < s._kat_oncelik(recs[2])
        # Kategori DEĞİŞMEZ — günlük dürüst kalmalı
        assert recs[1]['kat'] == 'nation_state_apt'

    def test_verified_nation_state_keeps_priority(self):
        """Gerçek APT haberi önceliğini korumalı (haksız eleme olmamalı)."""
        s = self._s()
        recs = {1: {'kat': 'nation_state_apt', 'toplam': 88,
                    's': 35, 'e': 20, 'a': 18, 'k': 15}}
        arts = {1: {'full_text': 'Rusya bağlantılı tehdit aktörü bakanlığı hedef aldı.',
                    'title': 'Russia-linked actors hit ministry'}}
        s._enforce_apt_attribution(recs, arts)
        assert not recs[1].get('apt_dogrulanmadi')
        assert s._kat_oncelik(recs[1]) == 8


# ── KRİTİK3 paragraf uzunluğu: 110 kelime hedefinin deterministik denetimi ──
# 2026-07-30 ölçümü: 3 kritik3 paragrafından 2'si (106, 108 kelime) hedefin
# altında kaldı. Prompt "110'un altına düşme" diyor ama LLM kelime saymakta
# güvenilir değil.

class TestKritik3ParagraphLength:
    def _sistem(self):
        return HaberSistemi()

    def _run(self, s, wc_paragraf, new_para, full_text_words=200):
        content = {1: {'tr_title': 'Test', 'paragraph': ' '.join(['kelime'] * wc_paragraf)}}
        articles = {1: {'full_text': ' '.join(['x'] * full_text_words)}}
        calls = []

        def fake_call(prompt, max_output_tokens=None, label=None):
            calls.append(label)
            return {'paragraph': new_para} if new_para is not None else None

        with mock.patch.object(s, '_gemini_call_json', side_effect=fake_call):
            s._enforce_kritik3_paragraph_length([1], content, articles)
        return content[1]['paragraph'], len(calls)

    def test_sufficient_paragraph_untouched(self):
        s = self._sistem()
        para, calls = self._run(s, 115, None)
        assert calls == 0
        assert len(para.split()) == 115

    def test_short_paragraph_gets_regenerated(self):
        s = self._sistem()
        new_para = ' '.join(['yeni'] * 118)
        para, calls = self._run(s, 60, new_para)
        assert calls == 1
        assert para == new_para

    def test_short_source_text_skips_retry(self):
        """Kaynak (full_text) da kısaysa uzatma DENENMEZ — halüsinasyon riski."""
        s = self._sistem()
        new_para = ' '.join(['yeni'] * 118)
        para, calls = self._run(s, 60, new_para, full_text_words=40)
        assert calls == 0
        assert len(para.split()) == 60

    def test_shorter_retry_result_rejected(self):
        """Yeniden deneme ESKİSİNDEN kısaysa reddedilir (regresyon olurdu)."""
        s = self._sistem()
        shorter = ' '.join(['yeni'] * 50)
        para, calls = self._run(s, 60, shorter)
        assert calls == 1
        assert len(para.split()) == 60  # orijinal korunmuş

    def test_partial_improvement_accepted_without_fabrication(self):
        """Hedefi tutturamasa da UZAYAN sonuç kabul edilir; uydurma zorlanmaz."""
        s = self._sistem()
        longer_but_short = ' '.join(['yeni'] * 90)
        para, calls = self._run(s, 60, longer_but_short)
        assert calls == 1
        assert len(para.split()) == 90


class TestArchiveIncludesKritik3:
    """save_summary_to_archive — KRİTİK 3 manşetleri de arşivlenmeli
    (2026-07-31 regresyonu)."""

    HTML = """
    <html><body>
      <div class="top3-section">
        <div class="top3-card">
          <div class="top3-card-title"><a href="#">Manşet Bir</a></div>
          <p class="top3-card-paragraph">Manşet birinci paragraf metni.</p>
          <p class="source"><b>(XXXXXXX, AÇIK - a.com, 31.07.2026)</b></p>
        </div>
        <div class="top3-card">
          <div class="top3-card-title"><a href="#">Manşet İki</a></div>
          <p class="top3-card-paragraph">Manşet ikinci paragraf metni.</p>
          <p class="source"><b>(XXXXXXX, AÇIK - b.com, 31.07.2026)</b></p>
        </div>
      </div>
      <div class="news-item" id="haber-1">
        <div class="news-title"><b>Gövde Haberi</b></div>
        <p class="news-content">Gövde paragraf metni.</p>
        <p class="source"><b>(XXXXXXX, AÇIK - c.com, 31.07.2026)</b></p>
      </div>
    </body></html>
    """

    def _archive_to_temp(self, monkeypatch, tmp_path):
        import main as M
        monkeypatch.setattr(M, 'ARCHIVE_FILE', str(tmp_path / 'arsiv.txt'))
        sistem = M.HaberSistemi()
        sistem.save_summary_to_archive(self.HTML)
        return (tmp_path / 'arsiv.txt').read_text(encoding='utf-8')

    def test_kritik3_cards_are_archived(self, monkeypatch, tmp_path):
        """Gerçek vaka: arşiv yalnızca 'news-item' arıyordu; manşetler
        'top3-card' olarak render edildiği için GÜNÜN EN ÖNEMLİ ÜÇ HABERİ
        arşive hiç girmiyordu."""
        out = self._archive_to_temp(monkeypatch, tmp_path)
        assert 'Manşet Bir' in out
        assert 'Manşet İki' in out
        assert 'Gövde Haberi' in out
        assert 'Manşet birinci paragraf metni.' in out

    def test_kritik3_comes_first_and_numbering_is_continuous(self, monkeypatch,
                                                             tmp_path):
        out = self._archive_to_temp(monkeypatch, tmp_path)
        assert out.index('Manşet Bir') < out.index('Gövde Haberi')
        assert '[ 1] Manşet Bir' in out
        assert '[ 2] Manşet İki' in out
        assert '[ 3] Gövde Haberi' in out

    def test_same_day_second_run_does_not_duplicate_block(self, monkeypatch,
                                                          tmp_path):
        """İdempotency: aynı gün ikinci koşuda blok tekrar eklenmemeli."""
        import main as M
        out1 = self._archive_to_temp(monkeypatch, tmp_path)
        sistem = M.HaberSistemi()
        sistem.save_summary_to_archive(self.HTML)
        out2 = (tmp_path / 'arsiv.txt').read_text(encoding='utf-8')
        assert out1 == out2


# ── LLM YANIT ŞEKLİ (Pass 2/3 içerik haritası) ─────────────────────────────
# 2026-08-03 maliyet incelemesi: Pass 2'nin ilk çağrısı — günün EN PAHALI
# isteği, 10 haberin tam metni — baktığımız her koşuda "✅ başarılı" loglanıp
# SIFIR içerik üretiyor, iş 5-7 parçalı split-retry ile YENİDEN yapılıyordu.
# Sebep: normalize edici tek-anahtarlı sarmalı yalnızca değeri LİSTE ise
# açıyordu; değeri SÖZLÜK olan sarmal ({"summaries": {"3": {...}}}) dışarıya
# olduğu gibi dönüyor, çağıran taraf int("summaries") denemesinde sessizce
# vazgeçiyordu. Arıza yalnızca faturada görünüyordu.

class TestIcerikHaritasiNormalize:
    _C = {'tr_title': 'Başlık', 'paragraph': 'Paragraf'}

    def _n(self, data):
        from main import _normalize_id_content
        return set(_normalize_id_content(data))

    def test_duz_id_anahtarli_sozluk(self):
        """Prompt'un istediği biçim — bozulmadan geçmeli."""
        assert self._n({'3': dict(self._C), '7': dict(self._C)}) == {'3', '7'}

    def test_tek_haberlik_yanit_sarmal_sanilmaz(self):
        """REGRESYON: tek anahtarlı ama GERÇEK içerik haritası açılmamalı."""
        assert self._n({'42': dict(self._C)}) == {'42'}

    def test_liste_bicimi(self):
        assert self._n([{'id': 3, **self._C}, {'id': 7, **self._C}]) == {3, 7}

    def test_sarmal_liste(self):
        assert self._n({'articles': [{'id': 3, **self._C}]}) == {3}

    def test_sarmal_sozluk(self):
        """ASIL DÜZELTME: değeri sözlük olan sarmal da açılmalı."""
        assert self._n({'summaries': {'3': dict(self._C), '7': dict(self._C)}}) == {'3', '7'}

    def test_ic_ice_sarmal(self):
        assert self._n({'data': {'items': [{'id': 9, **self._C}]}}) == {9}

    def test_bozuk_girdi_cokmez(self):
        assert self._n(None) == set()
        assert self._n('metin') == set()
        assert self._n([]) == set()

    def test_sekil_uyusmazligi_loglanir(self, capsys):
        """Sıfır içerik çıkarsa şekil loglanmalı — arıza sessiz kalmasın."""
        from main import _log_sekil_uyusmazligi
        _log_sekil_uyusmazligi('Pass2', {'beklenmeyen': {'x': 1}}, 0)
        cikti = capsys.readouterr().out
        assert 'HİÇBİR içerik eşleşmedi' in cikti and 'beklenmeyen' in cikti

    def test_basarili_durumda_log_basilmaz(self, capsys):
        from main import _log_sekil_uyusmazligi
        _log_sekil_uyusmazligi('Pass2', {'3': dict(self._C)}, 1)
        assert capsys.readouterr().out == ''


# ── ÜST DÜZEY DİZİ ŞEKİLLERİ (2026-08-04 üretim koşusundan birebir) ────────
# O koşuda 63 LLM çağrısının 24'ü yanıt döndürdüğü hâlde SIFIR içerik üretti;
# hepsinin sebebi aynıydı: model üst düzeyde DİZİ döndürüyor, normalize edici
# ise dizide yalnızca {"id": N, ...} biçimini tanıyordu. Aşağıdaki A/B/C
# şekilleri log'dan okunan gerçek şekillerdir (9 + 14 + 1 kez görüldü).
# Her boşa giden çağrı, aynı tam metni bir kez daha ödeyen split-retry doğurdu.

class TestUstDuzeyDiziSekilleri:
    _C = {'tr_title': 'Başlık', 'paragraph': 'Paragraf'}

    def _n(self, data, expected_ids=None):
        from main import _normalize_id_content
        return _normalize_id_content(data, expected_ids=expected_ids)

    def test_A_tam_harita_tek_ogeli_listeye_sarili(self):
        """[{'72': {...}, '29': {...}}] — 9 kez görüldü (Pass2 dahil)."""
        assert set(self._n([{'72': dict(self._C), '29': dict(self._C)}])) == {'72', '29'}

    def test_B_her_oge_tek_anahtarli_id_sarmali(self):
        """[{'8': {...}}, {'13': {...}}] — 14 kez görüldü (en yaygın)."""
        veri = [{'8': dict(self._C)}, {'13': dict(self._C)}]
        assert set(self._n(veri)) == {'8', '13'}

    def test_B_icerik_gercekten_acilir_sarmal_kalmaz(self):
        """Sarmal açılmalı: değer içerik nesnesinin KENDİSİ olmalı."""
        cikti = self._n([{'8': dict(self._C)}])
        assert cikti['8']['tr_title'] == 'Başlık'

    def test_C_idsiz_liste_sira_ile_eslesir(self):
        """[{tr_title, paragraph}, ...] — ID yok; sayı tutuyorsa sıra ile eşleşir."""
        veri = [dict(self._C), dict(self._C)]
        assert set(self._n(veri, expected_ids=[72, 29])) == {72, 29}

    def test_C_sayi_tutmazsa_eslestirme_yapilmaz(self):
        """GÜVENLİK: hizalama kayarsa yanlış habere yanlış özet iliştirilir."""
        veri = [dict(self._C), dict(self._C)]
        assert self._n(veri, expected_ids=[72, 29, 75]) == {}

    def test_C_expected_ids_yoksa_eslestirme_yapilmaz(self):
        assert self._n([dict(self._C)]) == {}

    def test_karisik_dizi_id_alanli_ve_sarmal_birlikte(self):
        veri = [{'id': 3, **self._C}, {'8': dict(self._C)}]
        assert set(self._n(veri)) == {3, '8'}

    def test_dizi_icinde_sarmal_sozluk(self):
        """[{'summaries': {...}}] — özyinelemeli açılmalı."""
        assert set(self._n([{'summaries': {'3': dict(self._C)}}])) == {'3'}

    def test_id_alanli_liste_bozulmadan_calisir(self):
        """REGRESYON: baştan desteklenen E şekli kırılmamalı."""
        assert set(self._n([{'id': 3, **self._C}, {'id': 7, **self._C}])) == {3, 7}

    def test_tek_haberlik_dizi_sarmal_sanilmaz(self):
        """[{'42': {...}}] tek öğeli — yine de ID korunmalı."""
        assert set(self._n([{'42': dict(self._C)}])) == {'42'}

    def test_baslik_siniri_dizi_yolunda_da_uygulanir(self):
        """_apply_title_limit dizi yolunda da geçerli olmalı (dolgu atılır)."""
        uzun = {'tr_title': ('Söz konusu yeni bir CISA Uyarısının Kurumlara '
                             'Yayımlanmış Olması Hakkında'),
                'paragraph': 'P'}
        cikti = self._n([{'8': uzun}])
        assert 'söz konusu' not in cikti['8']['tr_title'].lower()
        assert len(cikti['8']['tr_title'].split()) <= 8

    def test_bozuk_dizi_ogeleri_atlanir(self):
        veri = ['metin', None, 42, {'8': dict(self._C)}]
        assert set(self._n(veri)) == {'8'}
