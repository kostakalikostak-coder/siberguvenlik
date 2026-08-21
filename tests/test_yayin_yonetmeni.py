"""GENEL YAYIN YÖNETMENİ — bitmiş raporun tamamına bakan son geçiş.

Boru hattındaki diğer LLM denetimleri PARÇA görür (yalnızca paragraflar,
yalnızca 3 manşet, yalnızca mükerrerler). Editoryal hataların çoğu ancak
bütünde görülür — 2026-08-19'da yamalanmış bir Apple açığı manşetteyken
gövdede iki bakanlığın devlet ağından çıkarılması ve aktif istismar edilen
bir Windows açığı duruyordu.

Bu dosya katmanın SÖZLEŞMESİNİ kilitler: haber silemez, olgu değiştiremez,
takas sayısı sınırlıdır.
"""
import main


def _sistem(yanit):
    s = main.HaberSistemi.__new__(main.HaberSistemi)
    s._gemini_call_json = lambda *a, **k: yanit
    s._manset_izi = []
    return s


def _veri():
    records = {1: {'kat': 'casus_yazilim', 'toplam': 94, 's': 38, 'e': 22,
                   'a': 19, 'k': 15, 'siber': 1, 'mukerrer': 0},
               2: {'kat': 'nation_state_apt', 'toplam': 96, 's': 38, 'e': 24,
                   'a': 19, 'k': 15, 'siber': 1, 'mukerrer': 0},
               3: {'kat': 'kolluk_operasyonu', 'toplam': 95, 's': 39, 'e': 22,
                   'a': 19, 'k': 15, 'siber': 1, 'mukerrer': 0},
               # NOT: gövde adayı (4) manşettekilerden YÜKSEK puanlı seçildi.
               # Aksi halde her takas "hem puanı hem kategorisi zayıf" korumasına
               # takılırdı ve testler takas mekaniğini değil o korumayı ölçerdi
               # (bkz. test_puan_ve_kategori_birlikte_dusen_takas_reddedilir).
               4: {'kat': 'stratejik_kurum_saldirisi', 'toplam': 97, 's': 38,
                   'e': 20, 'a': 19, 'k': 15, 'siber': 1, 'mukerrer': 0}}
    content = {i: {'tr_title': f'Baslik {i}',
                   'paragraph': f'Paragraf {i} — CVE-2026-6534{i} açığı 2026 '
                                f'yılında 500 kurumu etkiledi.'}
               for i in records}
    arts = {i: {'id': i, 'title': f'T{i}', 'full_text': 'x'} for i in records}
    return records, content, arts


def test_takas_haber_silmez_yer_degistirir():
    """Manşetten inen GÖVDEDE kalır, gövdeden çıkan manşete gider."""
    records, content, arts = _veri()
    s = _sistem({'takaslar': [{'inen': 1, 'cikan': 4, 'neden': 'test'}]})
    top3, govde = s._yayin_yonetmeni([1, 2, 3], [4], records, content, arts)
    assert set(top3) == {4, 2, 3}, 'gövde haberi manşete çıkmadı'
    assert 1 in govde, 'manşetten inen haber KAYBOLDU'
    assert sorted(top3 + govde) == [1, 2, 3, 4], 'haber sayısı değişti'


def test_takas_sayisi_sinirli():
    records, content, arts = _veri()
    records[5] = dict(records[4]); records[6] = dict(records[4])
    content[5] = dict(content[4]); content[6] = dict(content[4])
    arts[5] = dict(arts[4]); arts[6] = dict(arts[4])
    s = _sistem({'takaslar': [{'inen': 1, 'cikan': 4}, {'inen': 2, 'cikan': 5},
                              {'inen': 3, 'cikan': 6}]})
    top3, govde = s._yayin_yonetmeni([1, 2, 3], [4, 5, 6], records, content, arts)
    degisen = len({1, 2, 3} - set(top3))
    assert degisen <= main.HaberSistemi.YAYIN_YONETMENI_MAX_TAKAS


def test_olgu_degistiren_metin_duzeltmesi_reddedilir():
    """Dil düzeltme yetkisi var, olgu değiştirme yetkisi YOK."""
    records, content, arts = _veri()
    eski = content[1]['paragraph']
    s = _sistem({'paragraflar': [
        {'id': 1, 'yeni': 'Paragraf 1 — CVE-2026-99999 açığı 2024 yılında '
                          '900 kurumu etkilemiştir.'}]})
    s._yayin_yonetmeni([1, 2, 3], [4], records, content, arts)
    assert content[1]['paragraph'] == eski, 'olgu değiştiren düzeltme uygulandı'


def test_dil_duzeltmesi_kabul_edilir():
    records, content, arts = _veri()
    s = _sistem({'paragraflar': [
        {'id': 1, 'yeni': 'Paragraf 1 — CVE-2026-65341 kodlu açık, 2026 '
                          'yılında 500 kurumu etkilemiştir.'}]})
    s._yayin_yonetmeni([1, 2, 3], [4], records, content, arts)
    assert 'etkilemiştir' in content[1]['paragraph']


def test_gecersiz_kategori_uygulanmaz():
    records, content, arts = _veri()
    s = _sistem({'kategoriler': [{'id': 1, 'yeni': 'uydurma_kategori'}]})
    s._yayin_yonetmeni([1, 2, 3], [4], records, content, arts)
    assert records[1]['kat'] == 'casus_yazilim'


def test_gecerli_kategori_duzeltmesi_uygulanir():
    """08-19 vakası: yamalanmış zafiyet casus_yazilim sanılmıştı."""
    records, content, arts = _veri()
    s = _sistem({'kategoriler': [{'id': 1, 'yeni': 'zafiyet_rutin',
                                  'neden': 'kurban/kampanya yok'}]})
    s._yayin_yonetmeni([1, 2, 3], [4], records, content, arts)
    assert records[1]['kat'] == 'zafiyet_rutin'


def test_bozuk_yanit_raporu_degistirmez():
    records, content, arts = _veri()
    s = _sistem(None)
    top3, govde = s._yayin_yonetmeni([1, 2, 3], [4], records, content, arts)
    assert top3 == [1, 2, 3] and govde == [4]


def test_govde_argumani_manseti_icermez():
    """Çağıran taraf sözleşmesi: `govde_ids` manşet id'lerini İÇERMEMELİ.

    2026-08-19 koşusunda main.py takas sonrası gövde listesini top10+remaining
    üzerinden yeniden bölüyordu; top10 zaten top3'ü içerdiği için manşetten
    inen haber gövdeye İKİNCİ kez giriyordu (28 girdi / 26 benzersiz). Test
    çağrı yerindeki ifadeyi kilitler.
    """
    import inspect
    kaynak = inspect.getsource(main.HaberSistemi.create_html)
    assert '_yayin_yonetmeni(' in kaynak
    parca = kaynak.split('_yy_govde = ', 1)[1][:300]
    assert 'if a not in set(top3_ids)' in parca, \
        'gövde listesi manşet id\'lerinden arındırılmıyor — mükerrer gövde riski'
    assert 'top10_ids, remaining_ids =' not in parca, \
        'takas sonrası top10/remaining yeniden bölünüyor — mükerrer gövde riski'


def test_takas_sonrasi_mutabakat_var():
    """Manşetten düşen haber gövdede DEĞİLSE gövdeye eklenmeli.

    2026-08-20 koşusunda SilkParasite (94 puan) manşete yedek olarak
    çıkarılmış, bu sırada top10/remaining'den çıkarılmıştı. Yönetmen onu
    gövdeye indirince dönecek yer kalmadı ve haber rapordan sessizce düştü.
    """
    import inspect
    kaynak = inspect.getsource(main.HaberSistemi.create_html)
    parca = kaynak.split('_yayin_yonetmeni(', 1)[1][:1600]
    assert 'top10_ids.insert(0, _eski)' in parca, \
        'manşetten düşen haber gövdeye geri eklenmiyor — haber kaybı riski'
    assert 'if i != _yeni' in parca, \
        'manşete çıkan haber gövdeden çıkarılmıyor — mükerrer gövde riski'


def test_takas_disi_eylemler_denetime_yazilir():
    """Kategori/başlık/paragraf düzeltmeleri kayda geçmeli.

    Önceden yalnızca stdout'a basılıyordu; koşu bittikten sonra katmanın
    gerçekten ne düzelttiği data/kalite_denetim.jsonl'den görülemiyordu.
    """
    records, content, arts = _veri()
    s = _sistem({'kategoriler': [{'id': 1, 'yeni': 'zafiyet_rutin',
                                  'neden': 'kurban yok'}],
                 'basliklar': [{'id': 2, 'yeni': 'Duzeltilmis Baslik 2'}],
                 'paragraflar': [{'id': 3, 'yeni': 'Olgu bozan metin.'}]})
    s._yayin_yonetmeni([1, 2, 3], [4], records, content, arts)
    turler = {(e['tur'], e.get('karar')) for e in s._yy_eylemler}
    assert ('kategori', None) in turler, 'kategori düzeltmesi kayda geçmedi'
    assert ('tr_title', 'uygulandi') in turler, 'başlık düzeltmesi kayda geçmedi'
    assert ('paragraph', 'reddedildi') in turler, \
        'olgu koruması reddi kayda geçmedi'


def test_govdenin_tamami_gosterilir():
    """Yönetmen gövdenin TAMAMINI görmeli — kırpma yok.

    Eski sürüm ilk 24 haberle sınırlıydı; kalabalık günlerde kuyruk haberleri
    kategori/dil düzeltmesi alamıyordu.
    """
    records, content, arts = _veri()
    for i in range(5, 40):
        records[i] = dict(records[4]); content[i] = dict(content[4])
        arts[i] = dict(arts[4])
    gorulen = {}

    def _yakala(prompt, **k):
        gorulen['p'] = prompt
        return {}
    s = _sistem(None)
    s._gemini_call_json = _yakala
    govde = list(range(4, 40))
    s._yayin_yonetmeni([1, 2, 3], govde, records, content, arts)
    for aid in govde:
        assert f'ID: {aid} |' in gorulen['p'], f'ID {aid} yönetmene gösterilmedi'


def test_kategori_listesi_config_ile_senkron():
    """Yönetmene sunulan kategori listesi SCORING_CATEGORIES'ten türetilmeli.

    Liste elle yazılıyken yeni kategori eklendiğinde sessizce eskiyordu:
    yönetmen o etiketi hiç ÖNERMİYOR, dolayısıyla yanlış kategoriyi
    düzeltemiyordu.
    """
    from src import config
    metin = config.get_yayin_yonetmeni_prompt('a', 'b')
    for kat in config.SCORING_CATEGORIES:
        if kat in ('urun_icerik', 'siber_disi'):
            assert kat not in metin.split('Geçerli kategoriler:')[1][:400], \
                f'{kat} yönetmene önerilmemeli (haber silmeye eşdeğer)'
        else:
            assert kat in metin, f'{kat} yönetmene sunulmuyor'


def test_yeni_kategori_oncelik_tablosunda():
    """Her skorlama kategorisinin eşitlik-bozucu önceliği tanımlı olmalı.

    Tanımsız kategori KATEGORI_ONCELIK.get(...) ile sessizce 0 alır ve
    sıralamada en dibe düşer; ayrıca yönetmenin kategori düzeltmesi
    `yenikat not in KATEGORI_ONCELIK` kontrolüne takılıp uygulanmaz.
    """
    from src import config
    eksik = [k for k in config.SCORING_CATEGORIES
             if k not in config.KATEGORI_ONCELIK]
    assert not eksik, f'öncelik tablosunda eksik kategori: {eksik}'


def test_kapi_karari_yonetmenin_ustunde():
    """Manşet havuzundan BİLEREK düşürülmüş haber geri çıkarılamaz.

    2026-08-21 koşusunda Siemens PLC / yapay zeka saldırısı haberi 100 puanla
    manşet havuzundan düşürülmüştü — aynı olay bir gün önce zaten manşetti.
    Yönetmen onu gövdede görüp geri manşete çıkardı; üst üste iki gün aynı
    manşet yayımlandı.
    """
    records, content, arts = _veri()
    s = _sistem({'takaslar': [{'inen': 1, 'cikan': 4, 'neden': 'test'}]})
    top3, govde = s._yayin_yonetmeni(
        [1, 2, 3], [4], records, content, arts,
        manset_disi={4: 'olay son 7 günde 1 kez manşet oldu'})
    assert set(top3) == {1, 2, 3}, 'kapı kararı yönetmen tarafından ezildi'
    assert any(e.get('tur') == 'takas' and e.get('karar') == 'reddedildi'
               for e in s._yy_eylemler), 'red kayda geçmedi'


def test_kapi_disinda_kalan_takas_calisir():
    """Koruma fazla geniş olmamalı: yasaklı OLMAYAN takas uygulanır."""
    records, content, arts = _veri()
    records[5] = dict(records[4]); content[5] = dict(content[4])
    arts[5] = dict(arts[4])
    s = _sistem({'takaslar': [{'inen': 1, 'cikan': 5, 'neden': 'test'}]})
    top3, _ = s._yayin_yonetmeni(
        [1, 2, 3], [4, 5], records, content, arts,
        manset_disi={4: 'olay son 7 günde 1 kez manşet oldu'})
    assert 5 in top3, 'yasaklı olmayan takas uygulanmadı'


def test_takas_yon_alanlari_net_adlarla_okunur():
    """`cikan` bu kod tabanında iki karşıt anlamda kullanılıyordu (manşet
    seçim promptunda 'ayrılan', yönetmende 'yükselen'). 2026-08-21'de model
    gerekçesinde doğru kararı yazdı ama alanları TERS doldurdu."""
    records, content, arts = _veri()
    s = _sistem({'takaslar': [{'manşetten_dusen': 1, 'manşete_yukselen': 4,
                               'neden': 'test'}]})
    top3, _ = s._yayin_yonetmeni([1, 2, 3], [4], records, content, arts)
    assert set(top3) == {4, 2, 3}, 'net alan adları okunmadı'


def test_eski_alan_adlari_geriye_donuk_calisir():
    records, content, arts = _veri()
    s = _sistem({'takaslar': [{'inen': 1, 'cikan': 4, 'neden': 'test'}]})
    top3, _ = s._yayin_yonetmeni([1, 2, 3], [4], records, content, arts)
    assert set(top3) == {4, 2, 3}, 'eski alan adları artık okunmuyor'


def test_manset_disi_kategoriye_yukseltme_reddedilir():
    """Kategori düzeltmesi artık takastan ÖNCE uygulanır; bu yüzden takas
    kapısı GÜNCEL kategoriyi görür ve uygunsuz yükseltmeyi reddeder."""
    records, content, arts = _veri()
    s = _sistem({'kategoriler': [{'id': 4, 'yeni': 'zafiyet_rutin',
                                  'neden': 'yamalanmış'}],
                 'takaslar': [{'manşetten_dusen': 1, 'manşete_yukselen': 4,
                               'neden': 'test'}]})
    top3, _ = s._yayin_yonetmeni([1, 2, 3], [4], records, content, arts)
    assert set(top3) == {1, 2, 3}, 'manşete uygun olmayan kategori manşete çıktı'
    assert any(e.get('tur') == 'takas' and e.get('karar') == 'reddedildi'
               for e in s._yy_eylemler)


def test_zafiyet_haberi_yonetmen_eliyle_manşete_cikamaz():
    """Zafiyet haberinin raporda KENDİ bölümü var (Güvenlik Açıkları).

    ÖLÇÜLDÜ (2026-08-21): yönetmen Rus OAuth istismarını (94,
    nation_state_apt) manşetten indirip CISA'nın TrueConf yama talimatını
    (91, zafiyet_aktif_apt) manşete çıkardı.

    NOT: `zafiyet_aktif_apt` KRITIK3_HARIC listesinde DEĞİLDİR ve öyle
    kalmalı — az-haber günlerinde deterministik seçici manşeti onunla
    doldurabilmelidir. Yasak yalnızca YÖNETMEN takasına aittir.
    """
    records, content, arts = _veri()
    records[4]['kat'] = 'zafiyet_aktif_apt'
    view = {4: 'x'}
    s = _sistem({'takaslar': [{'manşetten_dusen': 1, 'manşete_yukselen': 4,
                               'neden': 'aktif istismar'}]})
    disi = s._manset_disi_ids([4], records, view.get)
    assert 4 in disi, 'zafiyet haberi manşet dışı sayılmadı'
    top3, _ = s._yayin_yonetmeni([1, 2, 3], [4], records, content, arts,
                                 manset_disi=disi)
    assert set(top3) == {1, 2, 3}, 'zafiyet haberi manşete çıktı'


def test_puan_ve_kategori_birlikte_dusen_takas_reddedilir():
    """Yönetmenin işi puanı ezmektir, ama iki eksende birden aşağı inen bir
    takasın dayanağı yoktur."""
    records, content, arts = _veri()
    # 1: nation_state_apt 96 (manşette) | 4: veri_ihlali 90 (gövdede)
    records[1].update({'kat': 'nation_state_apt', 'toplam': 96})
    records[4].update({'kat': 'veri_ihlali', 'toplam': 90})
    s = _sistem({'takaslar': [{'manşetten_dusen': 1, 'manşete_yukselen': 4,
                               'neden': 'test'}]})
    top3, _ = s._yayin_yonetmeni([1, 2, 3], [4], records, content, arts)
    assert set(top3) == {1, 2, 3}, 'çift düşüşlü takas uygulandı'
    assert any(e.get('neden') == 'puan ve kategori birlikte düşüyor'
               for e in s._yy_eylemler)


def test_puani_dusuk_ama_kategorisi_guclu_takas_calisir():
    """Koruma fazla geniş olmamalı: yamalanmış zafiyet yerine süren kampanya
    çıkarmak yönetmenin ASIL işidir ve puan düşse de geçerlidir."""
    records, content, arts = _veri()
    records[1].update({'kat': 'zafiyet_aktif_apt', 'toplam': 95})
    records[4].update({'kat': 'nation_state_apt', 'toplam': 92})
    s = _sistem({'takaslar': [{'manşetten_dusen': 1, 'manşete_yukselen': 4,
                               'neden': 'süren kampanya'}]})
    top3, _ = s._yayin_yonetmeni([1, 2, 3], [4], records, content, arts)
    assert 4 in top3, 'meşru takas engellendi'
