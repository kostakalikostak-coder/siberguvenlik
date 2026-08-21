"""RAPOR DURUMU — değişmez bekçisinin sözleşmesi.

Bu testler, üretimde ÜÇ GÜNDE ÜÇ KEZ tekrarlayan ve her seferinde ancak
kullanıcı raporu okuyunca fark edilen arıza sınıflarını kilitler:
  • 2026-08-19 — aynı haber gövdede iki kez.
  • 2026-08-20 — SilkParasite (94 puan) rapordan sessizce düştü.
  • 2026-08-21 — defterin manşete yasakladığı haber manşete çıkarıldı.
"""
from src.rapor_durumu import (RaporDurumu, MUKERRER_GIRDI, NEDENSIZ_KAYIP,
                              YASAKLI_MANSET)


def _durum(**kw):
    kw.setdefault('yazdir', lambda s: None)
    kw.setdefault('manset', [1, 2, 3])
    kw.setdefault('top10', [4, 5, 6])
    kw.setdefault('kalan', [7, 8])
    return RaporDurumu(**kw)


def test_temiz_katman_ihlal_uretmez():
    d = _durum()
    m, t, k = d.senkronla('test', [1, 2, 3], [4, 5, 6], [7, 8])
    assert (m, t, k) == ([1, 2, 3], [4, 5, 6], [7, 8])
    assert d.ihlaller == []


def test_mukerrer_govde_girdisi_yakalanir_ve_tekillesir():
    """2026-08-19: takas sonrası gövde listesi yeniden bölününce haber
    listeye ikinci kez girmişti (28 girdi / 26 benzersiz)."""
    d = _durum()
    m, t, k = d.senkronla('yayin_yonetmeni', [1, 2, 3], [4, 5, 6], [7, 8, 4])
    assert k == [7, 8], 'mükerrer gövde girdisi tekilleştirilmedi'
    assert any(i['tur'] == MUKERRER_GIRDI and i['id'] == 4 for i in d.ihlaller)


def test_manset_govde_kesisimi_ihlal_degildir():
    """`top3_ids` bu kod tabanında `top10_ids`in ALT KÜMESİDİR ve gövdeyi
    çizen renderer manşeti kendisi dışlar. Bunu ihlal saymak her koşuda
    yanlış alarm üretirdi."""
    d = _durum(manset=[1, 2, 3], top10=[1, 2, 3, 4, 5], kalan=[7, 8])
    m, t, k = d.senkronla('test', [1, 2, 3], [1, 2, 3, 4, 5], [7, 8])
    assert t == [1, 2, 3, 4, 5], 'manşet kopyaları gövdeden düşürüldü'
    assert d.ihlaller == [], 'normal alt küme ilişkisi ihlal sayıldı'


def test_govde_ici_mukerrer_yakalanir_alt_kume_varken():
    """Alt küme ilişkisi korunurken GÖVDE İÇİ tekrar hâlâ yakalanmalı."""
    d = _durum(manset=[1, 2, 3], top10=[1, 2, 3, 4], kalan=[7])
    m, t, k = d.senkronla('test', [1, 2, 3], [1, 2, 3, 4], [7, 4])
    assert k == [7], 'gövde içi mükerrer tekilleştirilmedi'
    assert any(i['tur'] == MUKERRER_GIRDI and i['id'] == 4 for i in d.ihlaller)


def test_nedensiz_kayip_yakalanir_ve_geri_alinir():
    """2026-08-20: manşetten inen SilkParasite gövdede olmadığı için
    dönecek yer bulamadı ve 94 puanla rapordan düştü."""
    d = _durum()
    m, t, k = d.senkronla('yayin_yonetmeni', [9, 2, 3], [4, 5, 6], [7, 8])
    # 1 manşetten çıktı ama hiçbir yere inmedi → geri alınmalı
    assert 1 in t, 'nedensiz düşen haber geri alınmadı'
    assert t[0] == 1, 'geri alınan haber gövdenin başına konmadı'
    assert any(i['tur'] == NEDENSIZ_KAYIP and i['id'] == 1 for i in d.ihlaller)


def test_kayitli_neden_varsa_eleme_ihlal_degildir():
    d = _durum()
    m, t, k = d.senkronla('capraz_gun', [1, 2, 3], [4, 6], [7, 8],
                          nedenler={5: 'capraz_gun'})
    assert 5 not in (m + t + k), 'gerekçeli eleme geri alındı'
    assert d.ihlaller == [], 'gerekçeli eleme ihlal sayıldı'
    assert d.karar[5]['akibet'] == 'elenen'
    assert d.karar[5]['neden'] == 'capraz_gun'


def test_yasakli_manset_bildirilir():
    """2026-08-21: olay defteri Siemens PLC haberini manşetten düşürmüştü,
    yayın yönetmeni geri çıkardı; üst üste iki gün aynı manşet."""
    d = _durum(manset_yasak={6})
    m, t, k = d.senkronla('yayin_yonetmeni', [6, 2, 3], [4, 5, 1], [7, 8])
    assert any(i['tur'] == YASAKLI_MANSET and i['id'] == 6 for i in d.ihlaller)


def test_nedensiz_cikanlar_diger_kovasinin_yerini_alir():
    """Eskiden nedeni bilinmeyen eleme `diger` kovasına yazılıyor ve
    muhasebe tutuyordu — haber kaybolduğu gün bile."""
    d = _durum()
    d.senkronla('p5_kalite', [1, 2, 3], [4, 5], [7], nedenler={6: 'p5_kalite'})
    # 8 kayboldu ve geri alındı; 6 gerekçeliydi. 99 hiç rapora girmedi.
    nedensiz = d.nedensiz_cikanlar([1, 2, 3, 4, 5, 6, 7, 8, 99])
    assert 6 not in nedensiz, 'gerekçeli eleme alarma girdi'
    assert 99 in nedensiz, 'havuz dışı kalan haber alarma girmedi'


def test_ozet_denetime_yazilabilir():
    d = _durum()
    d.senkronla('test', [1, 2, 3], [4, 5, 6, 4], [7, 8])
    o = d.ozet()
    assert o['ihlal_sayisi'] == 1
    assert o['ihlal_turleri'] == {MUKERRER_GIRDI: 1}
    assert o['ihlaller'][0]['katman'] == 'test'


def test_ard_arda_katmanlar_durumu_tasir():
    d = _durum()
    d.senkronla('a', [1, 2, 3], [4, 5, 6], [7, 8], nedenler={})
    m, t, k = d.senkronla('b', [1, 2, 3], [4, 5], [7, 8],
                          nedenler={6: 'auditor_mukerrer'})
    assert 6 not in (m + t + k)
    assert d.ihlaller == []
    m, t, k = d.senkronla('c', [1, 2, 3], [4], [7, 8])
    assert 5 in t, 'ikinci katmanın nedensiz kaybı yakalanmadı'


def test_boru_hattindaki_her_mutasyon_katmani_bekciden_gecer():
    """Bekçi ancak HER katmandan sonra çağrılırsa işe yarar.

    Bu test kancaların yerinde olduğunu kilitler: yeni bir katman eklenip
    senkronla() çağrısı unutulduğunda, o katmanın ihlalleri yine sessiz
    kalırdı — kök nedenin ta kendisi.
    """
    import inspect
    import main
    kaynak = inspect.getsource(main.HaberSistemi.create_html)
    beklenen = {'p5_kalite', 'auditor_mukerrer', 'kesik_paragraf',
                'manset_butunluk', 'govde_ayni_olay', 'grup_geri_alma',
                'capraz_gun', 'capraz_gun_llm', 'yayin_yonetmeni'}
    for katman in beklenen:
        assert f"_senkron('{katman}')" in kaynak, \
            f'{katman} katmanından sonra değişmez denetimi yok'
    assert 'RaporDurumu(' in kaynak, 'durum nesnesi hiç kurulmuyor'


def test_capraz_gun_kancasi_neden_atamasindan_sonra():
    """Sıra kritik: çapraz-gün katmanları gerekçeyi ELEME SONRASI yazıyor.

    Kanca gerekçeden önce çağrılırsa senkronla() elemeyi 'nedensiz' sayıp
    haberi rapora GERİ ALIR — yani doğru çalışan bir katmanı bozar.
    """
    import inspect
    import main
    kaynak = inspect.getsource(main.HaberSistemi.create_html)
    for katman in ('capraz_gun', 'capraz_gun_llm'):
        i_neden = kaynak.index(f"eleme_nedeni[_rid] = '{katman}'")
        i_kanca = kaynak.index(f"_senkron('{katman}')")
        assert i_neden < i_kanca, \
            f'{katman}: kanca gerekçe atamasından ÖNCE çağrılıyor'


def test_bastan_gelen_yasakli_manset_tekrar_tekrar_bildirilmez():
    """Üç manşet garantisi için kapı bilerek gevşeyebilir; bu tasarlanmış
    durumu her katmanda ihlal saymak yanlış alarm üretirdi. Yalnızca
    manşete YENİ giren yasaklı id bildirilir."""
    d = _durum(manset=[6, 2, 3], top10=[4, 5, 1], kalan=[7, 8],
               manset_yasak={6})
    d.senkronla('p5_kalite', [6, 2, 3], [4, 5, 1], [7, 8])
    assert d.ihlaller == [], 'baştan gelen gevşetme ihlal sayıldı'
    d.senkronla('yayin_yonetmeni', [6, 2, 5], [4, 3, 1], [7, 8])
    assert d.ihlaller == [], 'değişmeyen yasaklı manşet yeniden bildirildi'


def test_uygunsuz_manset_kategorisi_bildirilir():
    """2026-08-21: yönetmen Entra ID haberini zafiyet_aktif_apt iken manşete
    çıkardı, ardından AYNI katman onu zafiyet_rutin'e indirdi — manşete asla
    giremeyecek bir kategori manşette kaldı."""
    from src.rapor_durumu import UYGUNSUZ_KATEGORI
    katlar = {3: 'zafiyet_rutin', 2: 'nation_state_apt', 1: 'nation_state_apt'}
    d = _durum(manset=[1, 2], top10=[3, 4], kalan=[7],
               kat_fn=katlar.get, manset_disi_katlar={'zafiyet_rutin'})
    d.senkronla('a', [1, 2], [3, 4], [7])
    assert d.ihlaller == [], 'gövdedeki uygunsuz kategori ihlal sayıldı'
    d.senkronla('yayin_yonetmeni', [3, 2], [1, 4], [7])
    assert any(i['tur'] == UYGUNSUZ_KATEGORI and i['id'] == 3
               for i in d.ihlaller), 'uygunsuz manşet kategorisi yakalanmadı'
