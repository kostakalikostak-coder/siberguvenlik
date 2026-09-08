"""Başlık ve gövde paragrafı ÖLÇÜ denetimleri.

Sınırlar prompt'ta yazılı ("EN FAZLA 8 KELİME (kesin sınır)", "110-130
kelime") ama LLM kelime saymakta güvenilir değil. Deterministik ağlar da
sınırlıydı: başlık ağı yalnızca sabit bir dolgu listesini atabiliyor, gövde
paragrafı için hiç ağ yoktu.

ÖLÇÜLDÜ (18 rapor, 2026-08-20..09-08): başlıkların %9-66'sı sınırı aşıyor.
08 Eylül raporunda 21 başlığın 14'ü 9-10 kelimeydi (KRİTİK 3'ün üçü dahil),
gövdedeki 18 paragrafın biri 83, ikisi 105 kelimeydi.
"""
import main


def _sistem(yanit):
    s = main.HaberSistemi.__new__(main.HaberSistemi)
    s._gemini_call_json = lambda *a, **k: yanit
    return s


UZUN = 'ABD Enerji Bakanliginin Elektrik Sebekesi Guvenliginde Yapay Zeka Kullanmasi'
KISA = 'Enerji Bakanligi Sebeke Guvenliginde Yapay Zeka Kullaniyor'


def _icerik(baslik, para='Ayrintili bir ozet metni. ' * 20):
    return {1: {'tr_title': baslik, 'paragraph': para}}


def _art(kelime=200):
    return {1: {'id': 1, 'title': '', 'full_text': 'kaynak metin ' * kelime}}


# ── Başlık ────────────────────────────────────────────────────────────────

def test_uzun_baslik_yeniden_yazilir():
    cnt = _icerik(UZUN)
    _sistem({'tr_title': KISA})._enforce_baslik_uzunlugu([1], cnt, _art())
    assert cnt[1]['tr_title'] == KISA
    assert len(cnt[1]['tr_title'].split()) <= main.TR_TITLE_MAX_WORDS


def test_sinir_icindeki_baslik_llme_gitmez():
    cnt = _icerik(KISA)
    cagri = []
    s = main.HaberSistemi.__new__(main.HaberSistemi)
    s._gemini_call_json = lambda *a, **k: cagri.append(1) or {}
    s._enforce_baslik_uzunlugu([1], cnt, _art())
    assert not cagri, 'sınır içindeki başlık için gereksiz LLM çağrısı'


def test_hala_uzun_yanit_reddedilir():
    """Regresyon olmasın: yeni başlık da sınırı aşıyorsa orijinal kalır."""
    cnt = _icerik(UZUN)
    _sistem({'tr_title': UZUN + ' Ve Daha Fazlasi'})._enforce_baslik_uzunlugu(
        [1], cnt, _art())
    assert cnt[1]['tr_title'] == UZUN


def test_bos_yanit_reddedilir():
    cnt = _icerik(UZUN)
    _sistem({'tr_title': ''})._enforce_baslik_uzunlugu([1], cnt, _art())
    assert cnt[1]['tr_title'] == UZUN


def test_llm_sessizse_baslik_korunur():
    cnt = _icerik(UZUN)
    _sistem(None)._enforce_baslik_uzunlugu([1], cnt, _art())
    assert cnt[1]['tr_title'] == UZUN


# ── Gövde paragrafı ───────────────────────────────────────────────────────

def test_kisa_govde_paragrafi_uzatilir():
    cnt = _icerik(KISA, 'kisa ozet ' * 30)          # 60 kelime
    uzun = 'genisletilmis ozet ' * 60               # 120 kelime
    _sistem({'paragraph': uzun})._enforce_govde_paragraf_uzunlugu(
        [1], cnt, _art())
    assert len(cnt[1]['paragraph'].split()) >= main.HaberSistemi.GOVDE_PARA_MIN_WORDS


def test_kaynak_kisaysa_denenmez():
    """Uzatacak malzeme yoksa deneme yalnızca halüsinasyon riski katardı."""
    cnt = _icerik(KISA, 'kisa ozet ' * 30)
    cagri = []
    s = main.HaberSistemi.__new__(main.HaberSistemi)
    s._gemini_call_json = lambda *a, **k: cagri.append(1) or {}
    s._enforce_govde_paragraf_uzunlugu([1], cnt, _art(kelime=10))
    assert not cagri, 'kaynak metin kısayken uzatma denendi'


def test_kisalan_yanit_reddedilir():
    kisa = 'kisa ozet ' * 30
    cnt = _icerik(KISA, kisa)
    _sistem({'paragraph': 'daha da kisa'})._enforce_govde_paragraf_uzunlugu(
        [1], cnt, _art())
    assert cnt[1]['paragraph'] == kisa


def test_govde_esigi_kritik3_ile_ayni():
    """İki taraf sessizce ayrışmasın."""
    assert (main.HaberSistemi.GOVDE_PARA_MIN_WORDS
            == main.HaberSistemi.KRITIK3_PARA_MIN_WORDS)


def test_kanca_rapor_kesinlestikten_sonra():
    """Onarım LLM çağrısı harcar; sonradan elenecek habere para ödenmemeli."""
    import inspect
    kaynak = inspect.getsource(main.HaberSistemi.create_html)
    i_son = kaynak.index("_senkron('manset_puan_tersinelik')")
    i_bas = kaynak.index('_enforce_baslik_uzunlugu(')
    i_gov = kaynak.index('_enforce_govde_paragraf_uzunlugu(')
    assert i_son < i_gov < i_bas or i_son < i_bas, \
        'ölçü denetimi rapor kesinleşmeden çalışıyor'


def test_butce_siniri_var():
    """İhlal sayısı bazı günlerde 14'e çıkıyor; hepsini onarmak koşuyu şişirir."""
    assert 1 <= main.HaberSistemi.METIN_ONARIM_BUTCESI <= 20
    cnt = {i: {'tr_title': UZUN, 'paragraph': 'x ' * 200} for i in range(30)}
    art = {i: {'id': i, 'title': '', 'full_text': 'k ' * 200} for i in range(30)}
    cagri = []
    s = main.HaberSistemi.__new__(main.HaberSistemi)
    s._gemini_call_json = lambda *a, **k: cagri.append(1) or {'tr_title': KISA}
    s._enforce_baslik_uzunlugu(list(cnt), cnt, art)
    assert len(cagri) == main.HaberSistemi.METIN_ONARIM_BUTCESI
