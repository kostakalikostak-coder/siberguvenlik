"""DAYANAKSIZ MÜKERRER ELEMESİ — hiçbir katmanın tek başına göremediği kayıp.

Ölçüt: bir haber "mükerrer" gerekçesiyle elendiyse raporda ya da geçmişte AYNI
OLAYI anlatan bir karşılığı olmalıdır. İkisi de yoksa o eleme dayanaksızdır.

ÖLÇÜLDÜ (2026-09-03): SonicWall SMA 1000 sıfır-gün açığının aktif istismarı
(89 puan) yedi ayrı kaynaktan geldi. Beşi p5_kalite, biri auditor_mukerrer,
biri de son kapının LLM hakemi tarafından elendi; olay rapora HİÇ girmedi.
Geçmişteki SonicWall haberleri farklı ürünlere aitti (GMS platformu 08-12,
NetExtender 08-27), yani çapraz-gün gerekçesi hatalıydı.

Mevcut `_restore_orphaned_groups` aynı işi yapıyor ama boru hattının
ORTASINDA koşuyor: o anda grubun başka üyeleri hâlâ raporda olduğu için
"temsil ediliyor" der ve geri alma yapmaz.
"""
import main


AYNI_A = ('SonicWall SMA 1000 Cihazlarında Sıfır Gün Açığının İstismarı',
          'Sonicwall firmasının SMA 1000 cihazlarındaki iki sıfır gün açığının '
          'CVE-2026-33101 ile aktif istismar edildiği bildirilmiştir.')
AYNI_B = ('SonicWall SMA 1000 Ürününde Kimlik Doğrulamasız Kod Çalıştırma',
          'Sonicwall firmasının SMA 1000 cihazlarındaki CVE-2026-33101 sıfır gün '
          'açığı kimlik doğrulaması olmadan kod çalıştırmaya izin vermektedir.')
BASKA = ('Akira Fidye Yazılımının Güvenli Modu Kullanması',
         'Akira fidye yazılımı EDR atlatmak için güvenli modu kullanmaktadır.')


def _kur(gecmis=()):
    s = main.HaberSistemi.__new__(main.HaberSistemi)
    s._olay_sozlugu = None
    s._load_recent_report_views = lambda *a, **k: list(gecmis)
    s._load_recent_kritik3_views = lambda *a, **k: []
    icerik = {1: dict(zip(('tr_title', 'paragraph'), AYNI_A)),
              2: dict(zip(('tr_title', 'paragraph'), AYNI_B)),
              3: dict(zip(('tr_title', 'paragraph'), BASKA))}
    rec = {1: {'kat': 'zafiyet_rutin', 'toplam': 89, 'siber': 1},
           2: {'kat': 'zafiyet_rutin', 'toplam': 85, 'siber': 1},
           3: {'kat': 'nation_state_apt', 'toplam': 70, 'siber': 1}}
    arts = {i: {'id': i, 'title': '', 'full_text': ''} for i in icerik}
    return s, rec, icerik, arts


def test_dayanaksiz_mukerrer_elemesi_geri_alinir():
    s, rec, cnt, art = _kur()
    nedenler = {1: 'kapi_capraz_gun_llm'}
    t3, t10, kal = s._son_grup_bosalmasi([3], [3], [], rec, cnt, art, nedenler)
    assert 1 in t10, 'karşılığı olmayan mükerrer elemesi geri alınmadı'
    assert 1 not in nedenler, 'geri alınan haber elenmiş olarak kayıtlı kaldı'


def test_kalite_elemesi_geri_alinmaz():
    """Koruma yalnızca MÜKERRER gerekçelerini denetler; kalite filtresini
    iptal etmek onun işi değildir."""
    s, rec, cnt, art = _kur()
    t3, t10, kal = s._son_grup_bosalmasi(
        [3], [3], [], rec, cnt, art, {1: 'p5_kalite'})
    assert 1 not in (t3 + t10 + kal), 'kalite elemesi geri alındı'


def test_en_yuksek_puanli_kopya_geri_alinir():
    """Aynı olayın iki kopyası da dayanaksız elendiyse yalnızca en güçlüsü
    döner — geri alma raporun içine yeni bir mükerrer sokamaz."""
    s, rec, cnt, art = _kur()
    rec[2]['toplam'] = 95
    t3, t10, kal = s._son_grup_bosalmasi(
        [3], [3], [], rec, cnt, art,
        {1: 'auditor_mukerrer', 2: 'auditor_mukerrer'})
    donen = [a for a in t10 if a in (1, 2)]
    assert donen == [2], f'en yüksek puanlı tek kopya dönmeliydi: {donen}'


def test_gecmiste_yayimlanmis_olay_geri_alinmaz():
    """Çapraz-gün elemesi BİLİNÇLİDİR — kullanıcının asıl şikâyeti odur."""
    gecmis = [{'tr_title': AYNI_A[0], 'title': '',
               'paragraph': AYNI_A[1], 'full_text': ''}]
    s, rec, cnt, art = _kur(gecmis)
    t3, t10, kal = s._son_grup_bosalmasi(
        [3], [3], [], rec, cnt, art, {1: 'capraz_gun', 2: 'capraz_gun'})
    assert 1 not in (t3 + t10 + kal), 'gerçek çapraz-gün mükerreri geri alındı'


def test_raporda_temsilcisi_kalan_olay_geri_alinmaz():
    s, rec, cnt, art = _kur()
    # ID 2 raporda: olay temsil ediliyor, ID 1 geri alınmamalı
    t3, t10, kal = s._son_grup_bosalmasi(
        [2], [2, 3], [], rec, cnt, art, {1: 'p5_kalite'})
    assert 1 not in (t3 + t10 + kal), 'olay temsil edilirken geri alma yapıldı'




def test_kanca_boru_hattinin_SONUNDA():
    import inspect
    kaynak = inspect.getsource(main.HaberSistemi.create_html)
    i_kapi = kaynak.index("_senkron('son_mukerrer_kapisi')")
    i_grup = kaynak.index('_son_grup_bosalmasi(')
    assert i_kapi < i_grup, 'koruma son kapıdan ÖNCE çalışıyor'
    assert "_senkron('son_grup_bosalmasi')" in kaynak, \
        'geri alma katmanı değişmez denetiminden geçmiyor'
