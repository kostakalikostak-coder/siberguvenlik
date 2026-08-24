"""SON MÜKERRER KAPISI — raporun tamamına, tek tanımla, muafiyetsiz.

Kök neden: mükerrer elemesi ~10 katmana dağılmıştı ve her katmanın KAPSAMI
farklıydı. En büyük boşluk manşetti: `_dedup_body_cross_day` yalnızca
top10/remaining üzerinde çalışır, `top3_ids` ayrı listedir.

ÖLÇÜLDÜ (2026-08-24): Threema DDoS haberinin kaydında `yerlesim=kritik3` ve
`eleme_nedeni=capraz_gun` yan yana duruyor — sistem haberi eledi ama karar
manşete uygulanmadı.
"""
import main


def _sistem(gecmis=(), sozluk=None):
    s = main.HaberSistemi.__new__(main.HaberSistemi)
    s._manset_izi = []
    s._olay_sozlugu = sozluk
    s._manset_yasak = set()
    s._olay_defteri = None
    s._load_recent_report_views = lambda *a, **k: list(gecmis)
    s._load_recent_kritik3_views = lambda *a, **k: []
    s._enforce_kritik3_paragraph_length = lambda *a, **k: None
    return s


def _icerik(baslik, para):
    return {'tr_title': baslik, 'paragraph': para}


CAMERASWARM_A = ('Dahua Cihazlarına Yönelik Operation CameraSwarm Siber '
                 'Saldırı Kampanyası',
                 'Operation CameraSwarm kapsamında Dahua marka kameralar '
                 'kimlik bilgisi saldırılarıyla hedef alınmıştır.')
CAMERASWARM_B = ('Operation CameraSwarm Kapsamında 14.000 IP Kameranın Ele '
                 'Geçirilmesi',
                 'Operation CameraSwarm kampanyasında 14.000 Dahua kamerası '
                 'ele geçirilmiştir.')
BAGIMSIZ = ('Fransa Vergi Dairesinden Mükellef Verilerinin Sızdırılması',
            'Fransa kamu maliyesi kurumundan 678.000 mükellefin verisi '
            'sızdırılmıştır.')
BAGIMSIZ2 = ('Letonya Kurumunda Siber Saldırı Sonrası İstifalar',
             'Letonya kurumunda veri ihlali sonrası yetkililer istifa '
             'etmiştir.')


def _kur(top3, top10, kalan, icerik, gecmis=()):
    s = _sistem(gecmis)
    records = {aid: {'kat': 'nation_state_apt', 'toplam': 90 - i, 'siber': 1}
               for i, aid in enumerate(list(top3) + list(top10) + list(kalan))}
    content = {aid: _icerik(*icerik[aid]) for aid in icerik}
    arts = {aid: {'id': aid, 'title': '', 'full_text': ''} for aid in icerik}
    return s, records, content, arts


def test_manset_capraz_gun_muaf_degildir():
    """Manşetteki haber geçmişte yayımlandıysa DEĞİŞTİRİLİR."""
    icerik = {1: CAMERASWARM_B, 2: BAGIMSIZ, 3: BAGIMSIZ2,
              4: ('Akira Fidye Yazılımının Güvenli Modu Kullanması',
                  'Akira fidye yazılımı EDR atlatmak için güvenli modu '
                  'kullanmaktadır.')}
    gecmis = [{'tr_title': CAMERASWARM_A[0], 'title': '',
               'paragraph': CAMERASWARM_A[1], 'full_text': ''}]
    s, rec, cnt, art = _kur([1, 2, 3], [4], [], icerik, gecmis)
    s._load_recent_report_views = lambda *a, **k: gecmis
    nedenler = {}
    t3, t10, kal = s._son_mukerrer_kapisi([1, 2, 3], [1, 2, 3, 4], [],
                                          rec, cnt, art, nedenler)
    assert 1 not in t3, 'geçmişte yayımlanmış haber manşette kaldı'
    assert 4 in t3, 'manşet yedekle doldurulmadı'
    assert len(t3) == 3, 'KRİTİK 3 eksildi'
    assert nedenler.get(1, '').startswith('kapi_'), 'eleme nedeni kaydedilmedi'


def test_rapor_ici_mukerrer_yuksek_puanli_kalir():
    icerik = {1: BAGIMSIZ, 2: CAMERASWARM_A, 3: BAGIMSIZ2, 4: CAMERASWARM_B}
    s, rec, cnt, art = _kur([1, 2, 3], [4], [], icerik)
    rec[2]['toplam'] = 95
    rec[4]['toplam'] = 80
    nedenler = {}
    t3, t10, kal = s._son_mukerrer_kapisi([1, 2, 3], [1, 2, 3, 4], [],
                                          rec, cnt, art, nedenler)
    hepsi = set(t3) | set(t10) | set(kal)
    assert 2 in hepsi and 4 not in hepsi, \
        'rapor içi mükerrerin düşük puanlısı elenmedi'


def test_yedek_yoksa_manset_eksilmez():
    """KRİTİK 3 garantisi mükerrerden önce gelir."""
    icerik = {1: CAMERASWARM_B, 2: BAGIMSIZ, 3: BAGIMSIZ2}
    gecmis = [{'tr_title': CAMERASWARM_A[0], 'title': '',
               'paragraph': CAMERASWARM_A[1], 'full_text': ''}]
    s, rec, cnt, art = _kur([1, 2, 3], [], [], icerik, gecmis)
    s._load_recent_report_views = lambda *a, **k: gecmis
    t3, t10, kal = s._son_mukerrer_kapisi([1, 2, 3], [1, 2, 3], [],
                                          rec, cnt, art, {})
    assert len(t3) == 3, 'yedek yokken KRİTİK 3 eksildi'
    assert 1 in t3, 'yedek yokken haber yerinde bırakılmadı'


def test_temiz_rapor_degismez():
    icerik = {1: BAGIMSIZ, 2: BAGIMSIZ2,
              3: ('Akira Fidye Yazılımının Güvenli Modu Kullanması',
                  'Akira fidye yazılımı EDR atlatmak için güvenli modu '
                  'kullanmaktadır.')}
    s, rec, cnt, art = _kur([1, 2, 3], [], [], icerik)
    t3, t10, kal = s._son_mukerrer_kapisi([1, 2, 3], [1, 2, 3], [],
                                          rec, cnt, art, {})
    assert t3 == [1, 2, 3] and t10 == [1, 2, 3]


def test_kapi_boru_hattinda_ve_render_oncesinde():
    """Kapı çağrılmazsa hiçbir garanti yoktur — çağrı yerini kilitle."""
    import inspect
    kaynak = inspect.getsource(main.HaberSistemi.create_html)
    assert '_son_mukerrer_kapisi(' in kaynak, 'kapı boru hattına bağlı değil'
    i_kapi = kaynak.index('_son_mukerrer_kapisi(')
    i_yy = kaynak.index('_yayin_yonetmeni(')
    i_log = kaynak.index('_write_scoring_log(')
    assert i_yy < i_kapi < i_log, \
        'kapı yayın yönetmeninden SONRA ve log/render ÖNCESİNDE olmalı'
