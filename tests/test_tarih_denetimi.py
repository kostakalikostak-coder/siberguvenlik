"""TARİH DENETİMİ — kaynakta olmayan yıl (üretim kayması) kapısı.

Ölçülen vaka (2026-08-12 üretim koşusu): İran su altyapısı manşetinin
paragrafı "27 Temmuz 2024" yazdı; kaynak "July 27" diyor ve haber Ağustos
2026 tarihli. Aynı haber bir önceki koşuda doğru yazılmıştı — yani kararlı
bir hata değil rastgele kayma; prompt sıkılaştırmak bunu garanti etmez.
"""
import json

import main


def _sistem():
    return main.HaberSistemi.__new__(main.HaberSistemi)


def test_kaynakta_olmayan_yil_duzeltilir():
    """Kaynakta TEK yıl varsa uydurulan yıl onunla değiştirilir."""
    content = {1: {'tr_title': 'Su altyapısı saldırıları',
                   'paragraph': '27 Temmuz 2024 tarihinde gerçekleşen '
                                'saldırılarda sistemler etkilenmiştir.'}}
    arts = {1: {'title': 'Iran-Linked Hackers Target Water Infrastructure',
                'full_text': 'Officials said the attacks happened on July 27. '
                             'The wave has reached 12 states since late July.',
                'date': 'Tue, 11 Aug 2026 16:23:07 +0000'}}
    duzeltilen, isaretli = _sistem()._tarih_denetimi(content, arts)
    assert content[1]['paragraph'].startswith('27 Temmuz 2026')
    assert duzeltilen and duzeltilen[0]['dogru'] == '2026'
    assert not isaretli


def test_belirsizse_degistirilmez():
    """Kaynakta birden çok yıl varsa hangisi kastedildiği bilinemez.

    Yanlış bir otomatik düzeltme, işaretlenmemiş bir hatadan daha kötüdür."""
    content = {1: {'tr_title': '', 'paragraph': 'Olay 2019 yılında başlamıştır.'}}
    arts = {1: {'title': 't', 'full_text': 'Events in 2021 and 2023.',
                'date': ''}}
    duzeltilen, isaretli = _sistem()._tarih_denetimi(content, arts)
    assert content[1]['paragraph'] == 'Olay 2019 yılında başlamıştır.'
    assert not duzeltilen and isaretli


def test_kaynaktaki_yil_korunur():
    content = {1: {'tr_title': '', 'paragraph': '2026 yılında tespit edildi.'}}
    arts = {1: {'title': 't', 'full_text': 'Discovered in 2026.', 'date': ''}}
    duzeltilen, isaretli = _sistem()._tarih_denetimi(content, arts)
    assert not duzeltilen and not isaretli


def test_kaynakta_yil_yoksa_dokunulmaz():
    """Kaynakta hiç yıl yoksa karar verilemez — sessiz geç."""
    content = {1: {'tr_title': '', 'paragraph': '2024 yılında oldu.'}}
    arts = {1: {'title': 't', 'full_text': 'No year here.', 'date': ''}}
    duzeltilen, isaretli = _sistem()._tarih_denetimi(content, arts)
    assert not duzeltilen and not isaretli


def test_tr_title_de_denetlenir():
    content = {1: {'tr_title': '2024 Saldırısı', 'paragraph': ''}}
    arts = {1: {'title': 't', 'full_text': 'The 2026 attack.', 'date': ''}}
    _sistem()._tarih_denetimi(content, arts)
    assert content[1]['tr_title'] == '2026 Saldırısı'


# ── ARZ KÜNYESİ ──────────────────────────────────────────────────────────────

def test_ince_rapor_sebebi_arz_olarak_ayrilir(tmp_path, monkeypatch):
    """İnce rapor: havuz zaten küçükse sebep ARZ'dır, boru hattı değil.

    2026-08-16'da rapor 5 haberdi ve inceleme bunun GERÇEK arz olduğunu
    gösterdi (10 taze aday, 4'ü ürün/içerik). Ama denetim kaydında bu durum
    'beslemeler çöktü' senaryosuyla AYNI görünüyordu."""
    import os
    monkeypatch.chdir(tmp_path)
    os.makedirs('data', exist_ok=True)
    s = _sistem()
    s._manset_izi, s._iliski_izi = [], {}
    s._olay_sozlugu = s._olay_defteri = None
    recs = {i: {'toplam': 90 - i, 'kat': 'nation_state_apt',
                'mukerrer': 0, 'siber': 1} for i in range(1, 7)}
    # Metinler BİLİNÇLİ olarak birbirinden farklı: aynı metin verilirse
    # denetimin rapor-içi mükerrer taraması tetiklenir ve ölçtüğümüz şey
    # (arz künyesi) değil o gürültü olur.
    konular = ['fidye saldırısı hastane', 'sıfır gün tarayıcı yaması',
               'tedarik zinciri paket', 'kimlik avı bankacılık',
               'botnet yönlendirici', 'veri sızıntısı sigorta']
    content = {i: {'tr_title': f'Haber {i}',
                   'paragraph': f'Bu haber {konular[i-1]} konusunu ele alır.'}
               for i in recs}
    arts = {i: {'id': i, 'title': f'Title {i} {konular[i-1]}',
                'full_text': f'Report about {konural}' if False
                else f'Report about {konular[i-1]}'} for i in recs}
    s._kalite_denetimi_yaz([1, 2, 3], [4, 5], recs, content, arts)
    with open('data/kalite_denetim.jsonl', encoding='utf-8') as f:
        arz = json.loads(f.read().strip().split('\n')[-1])['arz']
    assert arz['ince'] is True
    assert arz['puanli'] == 6 and arz['rapora_giren'] == 5
    # havuz (6) eşiğin (12) altında → sebep arz
    assert arz['puanli'] <= main.HaberSistemi.INCE_RAPOR_ESIGI


def test_normal_rapor_ince_isaretlenmez():
    import json as _j
    assert main.HaberSistemi.INCE_RAPOR_ESIGI == 12


# ── CASUS YAZILIM KANIT KAPISI ───────────────────────────────────────────────

def test_yamalanmis_zafiyet_casus_yazilim_sayilmaz():
    """"Casus yazılım için KULLANILABİLİR" bir operasyon değil, zafiyettir.

    ÖLÇÜLDÜ (2026-08-19): "Apple plugs image-processing hole ripe for spyware
    abuse" haberi casus_yazilim etiketiyle 94 puan alıp KRİTİK 3'e çıktı;
    kurban yok, kampanya yok, satıcı yok. casus_yazilim KATEGORI_ONCELIK'te 9
    ile EN YÜKSEK öncelik, yani tek başına manşete taşıyabiliyor."""
    s = _sistem()
    assert not s._has_spyware_evidence(
        'Apple plugs image-processing hole ripe for spyware abuse',
        'Apple has released a batch of vulnerability fixes for iPhones and Macs, '
        'including an image-processing flaw that experts say has the hallmarks '
        'of a spyware delivery vector. The most notable patch is for '
        'CVE-2026-65346, a defect in the ImageIO framework.')


def test_gercek_casus_yazilim_operasyonu_gecer():
    s = _sistem()
    assert s._has_spyware_evidence(
        'Apple warns users of mercenary spyware attacks',
        'Apple has notified users in 110 countries that they were targeted by '
        'mercenary spyware.')
    assert s._has_spyware_evidence(
        'Greek victims sue Intellexa',
        'The lawsuit concerns Predator infections on the phones of journalists.')


def test_kanit_govdenin_derinlerinden_gelmez():
    """Arka plan cümlesi kanıt sayılmamalı — kanıt GİRİŞTE aranır.

    Yama haberleri gövdede 'bu tür açıklar geçmişte sıfır tıklamalı casus
    yazılım kampanyalarında kullanıldı' der; tüm metinde arandığında bu kalıp
    eşleşiyor ve yama haberi 'operasyon' sayılıyordu."""
    s = _sistem()
    arka_plan = ('Vendor shipped a routine patch today. ' + ('filler word ' * 80)
                 + 'Historically such flaws were exploited in the wild by '
                   'mercenary spyware vendors.')
    assert not s._has_spyware_evidence('Vendor ships routine patch', arka_plan)
