"""TARİH DENETİMİ — kaynakta olmayan yıl (üretim kayması) kapısı.

Ölçülen vaka (2026-08-12 üretim koşusu): İran su altyapısı manşetinin
paragrafı "27 Temmuz 2024" yazdı; kaynak "July 27" diyor ve haber Ağustos
2026 tarihli. Aynı haber bir önceki koşuda doğru yazılmıştı — yani kararlı
bir hata değil rastgele kayma; prompt sıkılaştırmak bunu garanti etmez.
"""
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
