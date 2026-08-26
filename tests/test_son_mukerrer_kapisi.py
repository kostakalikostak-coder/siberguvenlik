"""SON MÜKERRER KAPISI — raporun tamamına, tek tanımla, muafiyetsiz.

Kök neden: mükerrer elemesi ~10 katmana dağılmıştı ve her katmanın KAPSAMI
farklıydı. En büyük boşluk manşetti: `_dedup_body_cross_day` yalnızca
top10/remaining üzerinde çalışır, `top3_ids` ayrı listedir.

ÖLÇÜLDÜ (2026-08-24): Threema DDoS haberinin kaydında `yerlesim=kritik3` ve
`eleme_nedeni=capraz_gun` yan yana duruyor — sistem haberi eledi ama karar
manşete uygulanmadı.
"""
import main


def _sistem(gecmis=(), sozluk=None, hakem=None):
    s = main.HaberSistemi.__new__(main.HaberSistemi)
    # LLM ÇAĞRISI STUB'LANIR: gerçek istemci ağa gider ve yeniden denemelerle
    # test başına ~47 s harcar. Hakemin kendi sözleşmesi ayrı test edilir.
    s._gemini_call_json = lambda *a, **k: (hakem if hakem is not None else {})
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


def test_manset_gelismeyi_indirir_ama_silmez():
    """Manşetteki haber aynı olayın DEVAMIYSA manşetten iner, gövdede KALIR.

    CAMERASWARM_B, CAMERASWARM_A'ya göre yeni bir olgu taşır (14.000 kamera)
    → GELISME. Kullanıcının şikâyeti MANŞET TEKRARI olduğu için manşetten
    inmesi şarttır; ama yeni olgu getirdiği için rapordan silinmez.
    """
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
    assert 1 not in t3, 'manşet tekrarı manşette kaldı'
    assert 4 in t3, 'manşet yedekle doldurulmadı'
    assert len(t3) == 3, 'KRİTİK 3 eksildi'
    assert 1 in (t10 + kal), 'yeni olgu getiren devam haberi rapordan silindi'
    assert 1 not in nedenler, 'gövdede kalan haber elenmiş gibi kaydedildi'


def test_manset_tam_mukerreri_hem_indirir_hem_siler():
    """Yeni olgu YOKSA haber manşetten iner VE rapordan çıkar."""
    aynen = ('Operation CameraSwarm ile Dahua Kameralarının Hedeflenmesi',
             'Operation CameraSwarm kampanyasında Dahua kameraları kimlik '
             'bilgisi saldırılarıyla hedef alınmıştır.')
    icerik = {1: aynen, 2: BAGIMSIZ, 3: BAGIMSIZ2,
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
    assert 1 not in t3 and 1 not in (t10 + kal), \
        'yeni olgu getirmeyen tekrar rapordan çıkmadı'
    assert nedenler.get(1, '').startswith('kapi_'), 'eleme nedeni kaydedilmedi'
    assert len(t3) == 3, 'KRİTİK 3 eksildi'


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


def test_denetim_ve_kapi_ayni_tanimi_kullanir():
    """"Denetim kaçak buldu ama politika bulmadı" YAPISAL olarak imkânsız olmalı.

    Kök nedenin ikinci yarısı buydu: denetim dört değerli sınıflandırıcıyı,
    eleme katmanları same_event'i kullanıyordu. İki farklı tanım = kapının
    göremediğini denetimin görmesi (ve tersi).
    """
    import inspect
    kapi = inspect.getsource(main.HaberSistemi._son_mukerrer_kapisi)
    denetim = inspect.getsource(main.HaberSistemi._kalite_denetimi_yaz)
    assert ('_olay.ayni_olay(' in kapi
            or '_olay.mukerrer_karari(' in kapi), 'kapı tek tanımı kullanmıyor'
    assert '_olay.mukerrer_karari(' in denetim, \
        'denetim tek tanımı kullanmıyor'
    assert 'iliski_belirle(' not in denetim, \
        'denetim hâlâ ayrı bir tanım kullanıyor — kapıyla ayrışır'
    # ÜÇ DEĞERLİ AYRIM DENETİMDE DE OLMALI: GELISME gövdede meşrudur,
    # manşette ihlaldir. Denetim 'her aynı olay kaçaktır' derse, kapının
    # bilerek gövdede bıraktığı devam haberlerini kalıcı sahte alarma çevirir
    # (2026-08-24: 2 'kaçak'ın biri tam olarak buydu).
    assert '_olay.TAM_MUKERRER' in denetim and '_olay.GELISME' in denetim, \
        'denetim üç değerli ayrımı uygulamıyor'


def test_bellek_arsivle_hizali():
    """Mustang Panda sınıfı: 10 gün önce yayımlanmış haber manşet oldu.

    Pencere 7 gündü, arşiv 30 gün tutuyor. Pencere arşivden kısa kaldığı
    sürece 7 günü aşan aralıkla dönen hiçbir olay görülemez.
    """
    from src import config
    assert config.REPORT_HISTORY_DAYS >= 30, 'rapor geçmişi arşivden kısa'
    assert config.KRITIK3_HISTORY_DAYS >= 30, 'manşet geçmişi arşivden kısa'


def test_on_filtre_kayipsizdir():
    """Ön filtre yalnızca HIZ içindir; eşleşme kümesini değiştirmemeli.

    ayni_olay'ın kabul ettiği dört yolun dördü de ortak bir ad/kod adı/CVE/
    aktör gerektirir; kesişim boşsa sonuç kesinlikle False'tur.
    """
    from src import olay_iliski as O
    a = {'tr_title': 'Operation CameraSwarm Kapsamında Kameraların Ele '
                     'Geçirilmesi', 'title': '',
         'paragraph': 'Operation CameraSwarm kampanyasında Dahua kameraları '
                      'ele geçirilmiştir.', 'full_text': ''}
    b = {'tr_title': 'Dahua Cihazlarına Yönelik Operation CameraSwarm '
                     'Kampanyası', 'title': '',
         'paragraph': 'Operation CameraSwarm kapsamında Dahua kameraları '
                      'hedef alınmıştır.', 'full_text': ''}
    c = {'tr_title': 'Fransa Vergi Dairesinde Veri İhlali', 'title': '',
         'paragraph': 'Fransa kamu maliyesi kurumundan mükellef verileri '
                      'sızdırılmıştır.', 'full_text': ''}
    assert O.aday_anahtarlari(a) & O.aday_anahtarlari(b), \
        'eşleşen çift ön filtreden geçemiyor — KAYIP'
    assert O.ayni_olay(a, b)
    assert not (O.aday_anahtarlari(a) & O.aday_anahtarlari(c)) or \
        not O.ayni_olay(a, c)


def test_erken_eleme_ve_son_kapi_ayni_tanimi_paylasir():
    """"Erken ele, geç doğrula": iki katman, TEK tanım.

    Son kapı doğruluk garantisidir ama boru hattının sonunda çalışır; orada
    düşen haberin yeri boş kalır. Geri-testte (22-24 Ağustos) yalnızca son
    kapı olsaydı 08-22 raporu 12→6, 08-23 5→2, 08-24 11→7 habere düşerdi.
    Aynı eleme sıralama anında yapılınca sıradaki aday yukarı kayar.
    """
    import inspect
    erken = inspect.getsource(main.HaberSistemi._erken_capraz_gun_mukerrer)
    assert '_olay.mukerrer_karari(' in erken, 'erken eleme farklı bir tanım kullanıyor'
    assert 'aday_anahtarlari' in erken, 'erken elemede ön filtre yok'
    siralama = inspect.getsource(main.HaberSistemi._rank_by_score)
    assert '_erken_capraz_gun_mukerrer(' in siralama, \
        'erken eleme sıralamaya bağlı değil — havuz refill edemez'


def test_erken_eleme_az_haber_kurtarmasindan_once():
    """Sıra: önce mükerrer düşsün, sonra taban gerekiyorsa devreye girsin.

    Ters sırada kurtarma, birazdan elenecek mükerrerleri havuza alır ve
    taban yanlış yerde tetiklenir.
    """
    import inspect
    kaynak = inspect.getsource(main.HaberSistemi._rank_by_score)
    i_erken = kaynak.index('_erken_capraz_gun_mukerrer(')
    i_kurtarma = kaynak.index('MIN_POOL')
    assert i_erken < i_kurtarma, 'erken eleme az-haber kurtarmasından sonra'


# ── LLM HAKEMİ ────────────────────────────────────────────────────────────
# Deterministik katman yüksek isabetlidir (elle etiketli 38 çiftte sahte=0)
# ama ortak ad/kod adı/CVE taşımayan mükerrerleri GÖREMEZ — çapraz-dil
# çiftleri (İngilizce özgün ↔ Türkçe yeniden yazım) sözcük örtüşmesi
# 0.05-0.19'da kalıyor. Hakem tam o boşluğu kapatır.

def test_hakem_kararsiz_cifti_mukerrer_ilan_edebilir():
    icerik = {1: ('Mozilla GPG Anahtarının Yenilenmesi',
                  'Mozilla, ifşa olan Firefox imzalama anahtarını '
                  'yenilemiştir.'),
              2: BAGIMSIZ, 3: BAGIMSIZ2,
              4: ('Akira Fidye Yazılımının Güvenli Modu Kullanması',
                  'Akira fidye yazılımı EDR atlatmak için güvenli modu '
                  'kullanmaktadır.')}
    gecmis = [{'tr_title': 'Mozilla Revokes Firefox and Thunderbird Linux '
                           'Signing Key', 'title': '',
               'paragraph': 'Mozilla revoked its Linux signing key after a '
                            'key compromise.', 'full_text': ''}]
    s, rec, cnt, art = _kur([1, 2, 3], [4], [], icerik, gecmis)
    s._load_recent_report_views = lambda *a, **k: gecmis
    s._gemini_call_json = lambda *a, **k: {
        'kararlar': [{'no': 1, 'ayni': True,
                      'olay': 'Mozilla imzalama anahtarının yenilenmesi'}]}
    nedenler = {}
    t3, t10, kal = s._son_mukerrer_kapisi([1, 2, 3], [1, 2, 3, 4], [],
                                          rec, cnt, art, nedenler)
    assert nedenler.get(1, '').startswith('kapi_capraz_gun_llm'), \
        'hakem kararı uygulanmadı'
    assert 1 not in t3, 'hakem mükerrer dediği hâlde manşette kaldı'
    assert 4 in t3, 'manşet yedekle doldurulmadı'


def test_hakem_farkli_derse_haber_kalir():
    icerik = {1: ('Mustang Panda QuickFox Tedarik Zinciri Saldırısı',
                  'Mustang Panda, QuickFox VPN yükleyicisine arka kapı '
                  'yerleştirmiştir.'),
              2: BAGIMSIZ, 3: BAGIMSIZ2}
    gecmis = [{'tr_title': "Mustang Panda'nın CoolClient Arka Kapısını "
                           'Güncellemesi', 'title': '',
               'paragraph': 'Mustang Panda CoolClient arka kapısını çekirdek '
                            'rootkit ile güncellemiştir.', 'full_text': ''}]
    s, rec, cnt, art = _kur([1, 2, 3], [], [], icerik, gecmis)
    s._load_recent_report_views = lambda *a, **k: gecmis
    s._gemini_call_json = lambda *a, **k: {
        'kararlar': [{'no': 1, 'ayni': False, 'olay': ''}]}
    nedenler = {}
    s._son_mukerrer_kapisi([1, 2, 3], [1, 2, 3], [], rec, cnt, art, nedenler)
    assert 1 not in nedenler, 'hakem FARKLI dediği hâlde haber elendi'


def test_hakem_yanit_vermezse_haber_kaybolmaz():
    """LLM erişilemediğinde haber KAYBETMEK, mükerrer yayımlamaktan kötüdür;
    deterministik katman zaten kesin olanları elemiştir."""
    icerik = {1: ('Mozilla GPG Anahtarının Yenilenmesi',
                  'Mozilla imzalama anahtarını yenilemiştir.'),
              2: BAGIMSIZ, 3: BAGIMSIZ2}
    gecmis = [{'tr_title': 'Mozilla Revokes Firefox Signing Key', 'title': '',
               'paragraph': 'Mozilla revoked its signing key.',
               'full_text': ''}]
    s, rec, cnt, art = _kur([1, 2, 3], [], [], icerik, gecmis)
    s._load_recent_report_views = lambda *a, **k: gecmis
    s._gemini_call_json = lambda *a, **k: None
    nedenler = {}
    s._son_mukerrer_kapisi([1, 2, 3], [1, 2, 3], [], rec, cnt, art, nedenler)
    assert 1 not in nedenler, 'LLM sessizken haber elendi'


def test_hakem_deterministigin_yakaladigini_tekrar_sormaz():
    """Maliyet: kesin olanlar LLM'e gitmemeli."""
    icerik = {1: CAMERASWARM_B, 2: BAGIMSIZ, 3: BAGIMSIZ2}
    gecmis = [{'tr_title': CAMERASWARM_A[0], 'title': '',
               'paragraph': CAMERASWARM_A[1], 'full_text': ''}]
    sorulan = []
    s, rec, cnt, art = _kur([1, 2, 3], [], [], icerik, gecmis)
    s._load_recent_report_views = lambda *a, **k: gecmis

    def _yakala(prompt, **k):
        # YALNIZCA mükerrer hakemi — gelişme hakemi BAŞKA bir soru sorar
        # ("devam mı tekrar mı") ve deterministiğin RAPORDA BIRAKTIĞI
        # haberleri sorması onun görevidir.
        if str(k.get('label', '')).startswith('MükerrerHakem'):
            sorulan.append(prompt)
        return {}
    s._gemini_call_json = _yakala
    s._son_mukerrer_kapisi([1, 2, 3], [1, 2, 3], [], rec, cnt, art, {})
    # Deterministik yakaladığı haber (ID 1) hakemin A tarafında GÖRÜNMEMELİ.
    # NOT: aynı geçmiş kayıt, BAŞKA haberlerin adayı olarak B tarafında
    # görünebilir — bu doğrudur ve maliyet israfı değildir.
    assert not any(f"A (" in p and CAMERASWARM_B[0][:30] in p
                   for p in sorulan), \
        'deterministik yakalanan haber yine de hakeme soruldu'


def test_hakem_rapor_ici_mukerreri_de_yakalar():
    """Kör nokta rapor İÇİNDE de var: aynı olayın iki farklı kaynaktan
    gelen, ortak ad taşımayan iki versiyonu."""
    icerik = {1: BAGIMSIZ, 2: BAGIMSIZ2,
              3: ('Deepfake Hatası Sertifika Dolandırıcısını Ele Verdi',
                  'Bir saniyelik deepfake aksaması, sahte dijital sertifika '
                  'düzenleyen dolandırıcının kimliğini açığa çıkarmıştır.'),
              4: ('Anlık Deepfake Arızası Dolandırıcının Kimliğini Açığa '
                  'Çıkardı',
                  'Görüntüdeki ani bozulma, dijital sertifika sahtekârının '
                  'gerçek yüzünü göstermiştir.')}
    s, rec, cnt, art = _kur([1, 2, 3], [4], [], icerik)
    rec[3]['toplam'] = 90
    rec[4]['toplam'] = 70
    def _hakem(prompt, **k):
        # Çift numarası boru hattının iç sırasına bağlıdır; testin ona
        # bağlanması kırılgan olur. Bunun yerine prompt AYRIŞTIRILIR ve
        # yalnızca İKİ TARAFI DA deepfake olan çifte 'ayni' denir.
        import re as _re
        kararlar = []
        for blok in _re.split(r'--- ÇİFT ', prompt)[1:]:
            no = int(blok.split(' ---')[0])
            kararlar.append({'no': no,
                             'ayni': blok.lower().count('deepfake') >= 2,
                             'olay': 'deepfake ile sertifika dolandırıcısının '
                                     'yakalanması'})
        return {'kararlar': kararlar}
    s._gemini_call_json = _hakem
    nedenler = {}
    t3, t10, kal = s._son_mukerrer_kapisi([1, 2, 3], [1, 2, 3, 4], [],
                                          rec, cnt, art, nedenler)
    hepsi = set(t3) | set(t10) | set(kal)
    assert 4 not in hepsi, 'rapor içi mükerrerin düşük puanlısı elenmedi'
    assert 3 in hepsi, 'yüksek puanlı temsilci de elendi'
    assert nedenler.get(4, '').startswith('kapi_rapor_ici_llm')


def test_kapi_haberi_kendisiyle_eslestirmez():
    """`top3_ids`, `top10_ids`in ALT KÜMESİDİR — tekilleştirilmeden
    birleştirilirse her manşet haberi listeye İKİ KEZ girer ve rapor içi
    tarama onu KENDİSİYLE eşleştirip eler.

    ÖLÇÜLDÜ (2026-08-24): karar izinde "ID 4 düştü — kapi_rapor_ici (ID 4 ile
    aynı olay)" satırı çıktı. LLM'in manşet için seçtiği iki haber tam olarak
    böyle elendi; yerlerine Uber para cezası ve ATM dolandırıcılığı geçti.
    """
    icerik = {1: BAGIMSIZ, 2: BAGIMSIZ2,
              3: ('Akira Fidye Yazılımının Güvenli Modu Kullanması',
                  'Akira fidye yazılımı EDR atlatmak için güvenli modu '
                  'kullanmaktadır.'),
              4: CAMERASWARM_A}
    s, rec, cnt, art = _kur([1, 2, 3], [1, 2, 3, 4], [], icerik)
    nedenler = {}
    # ÜRETİMDEKİ GİBİ: top3 ⊆ top10
    t3, t10, kal = s._son_mukerrer_kapisi([1, 2, 3], [1, 2, 3, 4], [],
                                          rec, cnt, art, nedenler)
    for aid, neden in nedenler.items():
        assert f'ID {aid} ile aynı olay' not in neden, \
            f'ID {aid} kendisiyle eşleştirilip elendi: {neden}'
    assert set(t3) == {1, 2, 3}, 'manşet kendi kendini eleyerek değişti'
    assert not nedenler, f'temiz raporda eleme oldu: {nedenler}'


def test_hakem_ayni_cifti_iki_kez_sormaz():
    """Hakem boru hattında İKİ KEZ koşuyor (seçim öncesi + son kapı) ve LLM
    deterministik olmadığı için aynı çifte iki farklı cevap verebiliyor.

    ÖLÇÜLDÜ (2026-08-24): seçim öncesi hakem Myanmar/QUICSILVER çiftine
    'farklı' dedi, haber manşet seçildi; son kapıdaki hakem AYNI çifte
    'aynı' deyip onu eledi ve yerine CISA günlükleme kılavuzu geçti.
    """
    s = _sistem()
    a = {'tr_title': 'Myanmar Hükümetini Hedef Alan Casusluk Kampanyası',
         'title': '', 'paragraph': 'QUICSILVER kampanyası.', 'full_text': ''}
    b = {'tr_title': 'Çin Bağlantılı Aktörün Myanmar Operasyonu', 'title': '',
         'paragraph': 'Myanmar kamu kurumları hedef alınmıştır.',
         'full_text': ''}
    cagri = []

    def _hakem(prompt, **k):
        cagri.append(prompt)
        return {'kararlar': [{'no': 1, 'ayni': False, 'olay': ''}]}
    s._gemini_call_json = _hakem

    ilk = s._mukerrer_llm_hakem([(1, a, b, '')])
    assert ilk[1][0] is False
    # İKİNCİ kez sorulursa LLM farklı cevap verebilir — önbellek buna izin
    # vermemeli.
    s._gemini_call_json = lambda *x, **k: {
        'kararlar': [{'no': 7, 'ayni': True, 'olay': 'aynı olay'}]}
    ikinci = s._mukerrer_llm_hakem([(7, a, b, '')])
    assert ikinci[7][0] is False, 'aynı çifte ikinci kez farklı karar verildi'
    assert len(cagri) == 1, 'çift ikinci kez LLM\'e soruldu'


# ── GELİŞME HAKEMİ — deterministik "devam haberi" kararının son sözü ───────

def _hakemli(gecmis, kararlar):
    """Yalnızca GelişmeHakemi çağrısına yanıt veren sistem."""
    s = _sistem(gecmis)

    def _cagri(prompt, **kw):
        if str(kw.get('label', '')).startswith('GelişmeHakemi'):
            return {'kararlar': kararlar}
        return {}
    s._gemini_call_json = _cagri
    return s


_ICERIK = {1: CAMERASWARM_B, 2: BAGIMSIZ, 3: BAGIMSIZ2,
           4: ('Akira Fidye Yazılımının Güvenli Modu Kullanması',
               'Akira fidye yazılımı EDR atlatmak için güvenli modu '
               'kullanmaktadır.')}
_GECMIS = [{'tr_title': CAMERASWARM_A[0], 'title': '',
            'paragraph': CAMERASWARM_A[1], 'full_text': ''}]


def _kapi(s, nedenler):
    records = {aid: {'kat': 'nation_state_apt', 'toplam': 90 - i, 'siber': 1}
               for i, aid in enumerate([1, 2, 3, 4])}
    content = {aid: _icerik(*_ICERIK[aid]) for aid in _ICERIK}
    arts = {aid: {'id': aid, 'title': '', 'full_text': ''} for aid in _ICERIK}
    s._load_recent_report_views = lambda *a, **k: _GECMIS
    return s._son_mukerrer_kapisi([1, 2, 3], [1, 2, 3, 4], [],
                                  records, content, arts, nedenler)


def test_hakem_tekrar_derse_gelisme_rapordan_duser():
    """2026-08-26: ABD'nin İran bağlantılı aktörlere yaptırımı iki gün üst
    üste yayımlandı. Deterministik ölçüt ('girişte 3 yeni özel ad') yüzeysel
    farkları gelişme sandı; bağlamı okuyan hakem bunu görebilir."""
    s = _hakemli(_GECMIS, [{'no': 1, 'karar': 'TEKRAR'}])
    nedenler = {}
    t3, t10, kal = _kapi(s, nedenler)
    assert 1 not in t3 and 1 not in (t10 + kal), \
        'hakem TEKRAR dedi ama haber raporda kaldı'
    assert nedenler.get(1, '').startswith('kapi_capraz_gun_gelisme')
    assert len(t3) == 3, 'KRİTİK 3 eksildi'


def test_hakem_gelisme_derse_govdede_kalir():
    """Gerçek devam haberi korunur — manşete çıkamaz ama rapordan silinmez."""
    s = _hakemli(_GECMIS, [{'no': 1, 'karar': 'GELISME',
                            'yeni': '14.000 kamera ele geçirildi'}])
    nedenler = {}
    t3, t10, kal = _kapi(s, nedenler)
    assert 1 in (t10 + kal), 'gerçek devam haberi rapordan silindi'
    assert 1 not in t3, 'devam haberi manşette kaldı'
    assert 1 not in nedenler


def test_hakem_yanit_vermezse_deterministik_karar_korunur():
    """LLM erişilemediğinde sistem eski davranışına düşer — haber KAYBETMEZ."""
    s = _sistem(_GECMIS)
    s._gemini_call_json = lambda *a, **k: None
    nedenler = {}
    t3, t10, kal = _kapi(s, nedenler)
    assert 1 in (t10 + kal), 'hakem sessizken haber düşürüldü'
    assert 1 not in t3


def test_hakem_ayni_cifti_iki_kez_sormaz():
    """Önbellek: mükerrer hakeminde LLM aynı çifte iki farklı cevap vermişti."""
    s = _sistem()
    cagri = {'n': 0}

    def _cagri(prompt, **kw):
        cagri['n'] += 1
        return {'kararlar': [{'no': 1, 'karar': 'TEKRAR'}]}
    s._gemini_call_json = _cagri
    a = {'tr_title': 'A', 'title': '', 'paragraph': 'a', 'full_text': ''}
    b = {'tr_title': 'B', 'title': '', 'paragraph': 'b', 'full_text': ''}
    assert s._gelisme_llm_hakem([(1, a, b, '')])[1][0] is True
    assert s._gelisme_llm_hakem([(1, a, b, '')])[1][0] is True
    assert cagri['n'] == 1, 'aynı çift ikinci kez LLM'"'"'e soruldu'
