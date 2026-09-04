"""Gövdede manşetten BELİRGİN güçlü haber kaldıysa takas.

Puan bandı (MANSET_PUAN_TOLERANSI) TAVANA görelidir: tavan 95 iken taban 70'e
iner ve 79 puanlık bir manşet "zayıf" sayılmaz, gövdede 92 puanlık uygun bir
haber dursa bile. Bant kötü manşeti engelliyor ama DAHA İYİSİ varken vasat
olanı seçmeyi engellemiyor.

ÖLÇÜLDÜ (2026-09-04): manşetin üçüncü sırasında 79 puanlı BraZetsu zararlısı
vardı; gövdede manşete uygun sekiz haber ondan yüksekti.
"""
import main


def _kur(puanlar, kat=None):
    s = main.HaberSistemi.__new__(main.HaberSistemi)
    s._olay_sozlugu = None
    s._manset_yasak = set()
    s._olay_defteri = None
    s._manset_izi = []
    s._load_recent_report_views = lambda *a, **k: []
    s._load_recent_kritik3_views = lambda *a, **k: []
    rec = {i: {'kat': (kat or {}).get(i, 'nation_state_apt'),
               'toplam': p, 'siber': 1} for i, p in puanlar.items()}
    # Metinler BİRBİRİNDEN AYRI olmalı: şablon paragraflar konu örtüşmesini
    # 1.0'a çıkarıp her adayı "mevcut manşetle aynı olay" yapıyordu.
    _konu = {
        1: ('Volt Typhoon Grubunun Enerji Sebekesine Sizmasi',
            'Volt Typhoon adli aktorun ABD enerji sebekesindeki kontrol '
            'sistemlerine sizdigi tespit edilmistir.'),
        2: ('Zimbra Sunucularinda Kimlik Dogrulama Acigi',
            'Zimbra posta sunucularindaki CVE-2026-11111 acigi ile hesaplarin '
            'ele gecirildigi bildirilmistir.'),
        3: ('BraZetsu Zararlisinin Bankacilik Uygulamalarini Hedeflemesi',
            'BraZetsu adli zararli yazilimin Brezilya bankacilik '
            'uygulamalarini hedef aldigi aciklanmistir.'),
        4: ('Mustang Panda Grubunun Diplomatik Kurumlari Hedeflemesi',
            'Mustang Panda adli aktorun Avrupa diplomatik kurumlarina '
            'CoolClient arka kapisiyla sizdigi belirlenmistir.'),
        5: ('Hasbro Sirketinde Calisan Verilerinin Sizmasi',
            'Hasbro oyuncak sirketinde yasanan ihlalde calisan verilerinin '
            'sizdirildigi bildirilmistir.'),
    }
    cnt = {i: {'tr_title': _konu[i][0], 'paragraph': _konu[i][1]}
           for i in puanlar}
    art = {i: {'id': i, 'title': '', 'full_text': ''} for i in puanlar}
    return s, rec, cnt, art


def test_belirgin_tersinelik_duzeltilir():
    s, rec, cnt, art = _kur({1: 95, 2: 81, 3: 79, 4: 92, 5: 60})
    t3, t10, kal = s._manset_puan_tersinelik([1, 2, 3], [4, 5], [], rec, cnt, art)
    assert 4 in t3, 'gövdedeki 92 puanlı haber manşete çıkmadı'
    assert 3 not in t3, '79 puanlı haber manşette kaldı'
    assert 3 in t10, 'manşetten inen haber gövdeye alınmadı'
    assert 4 not in t10, 'manşete çıkan haber gövdede de kaldı'
    assert len(t3) == 3, 'KRİTİK 3 eksildi'


def test_esik_altindaki_fark_dokunulmaz():
    """1-7 puanlık farklar gürültüdür; ölçümde 39 alarmın 25'i böyleydi."""
    s, rec, cnt, art = _kur({1: 95, 2: 88, 3: 85, 4: 89})
    t3, _t10, _k = s._manset_puan_tersinelik([1, 2, 3], [4], [], rec, cnt, art)
    assert t3 == [1, 2, 3], 'eşik altındaki fark için manşet değişti'


def test_uygun_olmayan_kategori_manseti_almaz():
    s, rec, cnt, art = _kur({1: 95, 2: 81, 3: 79, 4: 99},
                            kat={4: 'zafiyet_rutin'})
    t3, _t10, _k = s._manset_puan_tersinelik([1, 2, 3], [4], [], rec, cnt, art)
    assert 4 not in t3, 'manşete uygun olmayan kategori manşete çıktı'


def test_manset_yasakli_haber_manseti_almaz():
    s, rec, cnt, art = _kur({1: 95, 2: 81, 3: 79, 4: 92})
    s._manset_yasak = {4}
    t3, _t10, _k = s._manset_puan_tersinelik([1, 2, 3], [4], [], rec, cnt, art)
    assert 4 not in t3, 'manşete yasaklı haber manşete çıktı'


def test_gecmiste_yayimlanmis_haber_manseti_almaz():
    s, rec, cnt, art = _kur({1: 95, 2: 81, 3: 79, 4: 92})
    s._load_recent_report_views = lambda *a, **k: [
        {'tr_title': cnt[4]['tr_title'], 'title': '',
         'paragraph': cnt[4]['paragraph'], 'full_text': ''}]
    t3, _t10, _k = s._manset_puan_tersinelik([1, 2, 3], [4], [], rec, cnt, art)
    assert 4 not in t3, 'geçmişte yayımlanmış haber manşete çıkarıldı'


def test_esik_alarmla_ayni_sabittir():
    """Alarm neyi bildiriyorsa bu katman onu düzeltmeli; ayrışırlarsa
    'bildirilen ama düzeltilmeyen' kalıcı bir alarm doğar."""
    import inspect
    kaynak = inspect.getsource(main.HaberSistemi._manset_puan_tersinelik)
    assert 'MANSET_TERSINELIK_MIN' in kaynak
    denetim = inspect.getsource(main.HaberSistemi._kalite_denetimi_yaz)
    assert 'MANSET_TERSINELIK_MIN' in denetim


def test_kanca_en_sonda():
    import inspect
    kaynak = inspect.getsource(main.HaberSistemi.create_html)
    i_grup = kaynak.index('_son_grup_bosalmasi(')
    i_ters = kaynak.index('_manset_puan_tersinelik(')
    assert i_grup < i_ters, 'tersinelik düzeltmesi son katman değil'
    assert "_senkron('manset_puan_tersinelik')" in kaynak
