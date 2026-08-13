"""OLAY İLİŞKİSİ — dört değerli sınıflandırıcının davranış kapısı.

Golden set'e karşı ölçüm scripts/dedup_olc.py'de; burası o ölçümü KAPIYA
çevirir ve sınıflandırıcının sözleşmesini (hangi girdi hangi sınıfa düşer)
birim düzeyinde sabitler.
"""
import json
import os

import pytest

from src import olay_iliski as oi

GOLDEN = os.path.join(os.path.dirname(__file__), '..', 'data', 'dedup_golden.json')

# Politika sonucu doğru olması gereken asgari çift sayısı (bkz. dedup_olc.py
# 3. bölüm). 22 çiftin 19'u. Kalan 3 kaçak GÜVENLİ yöndedir ve anlamsal
# yargı gerektirir; LLM denetim katmanları kapsar.
TABAN_POLITIKA = 19
# Etiket düzeyinde doğruluk tabanı.
TABAN_ETIKET = 17


def _ciftler():
    if not os.path.exists(GOLDEN):
        pytest.skip('data/dedup_golden.json yok')
    with open(GOLDEN, encoding='utf-8') as f:
        return json.load(f)['ciftler']


def _sozluk():
    views = []
    for p in ('data/rapor_gecmis.json', 'data/kritik3_gecmis.json'):
        yol = os.path.join(os.path.dirname(__file__), '..', p)
        if not os.path.exists(yol):
            continue
        with open(yol, encoding='utf-8') as f:
            for rec in json.load(f):
                views.extend(rec.get('views', []) or [])
    return oi.OlaySozlugu(views)


def _politika(etiket):
    return (etiket not in oi.RAPORA_GIRER, etiket not in oi.MANSETE_CIKAR)


def test_etiket_dogrulugu_gerilemez():
    sozluk = _sozluk()
    dogru = sum(
        1 for c in _ciftler()
        if oi.iliski_belirle(c['a'], c['b'], ayni_gun=c['ayni_gun'],
                             sozluk=sozluk) == c['iliski'])
    assert dogru >= TABAN_ETIKET, f'REGRESYON: {dogru} < {TABAN_ETIKET}'


def test_politika_dogrulugu_gerilemez():
    """Asıl ölçüm: etiketin raporda yol açtığı DAVRANIŞ doğru mu?"""
    sozluk = _sozluk()
    dogru, hatalar = 0, []
    for c in _ciftler():
        tahmin = oi.iliski_belirle(c['a'], c['b'], ayni_gun=c['ayni_gun'],
                                   sozluk=sozluk)
        if _politika(tahmin) == _politika(c['iliski']):
            dogru += 1
        else:
            hatalar.append(f'{c["ad"]}: {c["iliski"]} → {tahmin}')
    assert dogru >= TABAN_POLITIKA, (
        f'REGRESYON: {dogru} < {TABAN_POLITIKA}\n'
        + '\n'.join('   • ' + h for h in hatalar))


def test_farkli_haberler_asla_elenmez():
    """ILISKISIZ/AYNI_AKTOR etiketli çiftler ASLA eleme sonucu doğurmamalı."""
    sozluk = _sozluk()
    for c in _ciftler():
        if c['iliski'] not in (oi.ILISKISIZ, oi.AYNI_AKTOR_FARKLI_OLAY):
            continue
        tahmin = oi.iliski_belirle(c['a'], c['b'], ayni_gun=c['ayni_gun'],
                                   sozluk=sozluk)
        assert tahmin in oi.RAPORA_GIRER, (
            f'{c["ad"]}: farklı haber elendi ({tahmin})\n   {c["not"]}')


def test_ayni_aktor_farkli_olay_ayrilir():
    """Aynı aktör + farklı kurban = AYNI OLAY DEĞİL (08-12 Sandworm vakası)."""
    a = {'tr_title': '', 'paragraph': '', 'title': 'Sandworm targets IT staff',
         'full_text': 'The group ran fake job interviews against Ukrainian '
                      'IT specialists using a trojanized VPN client.'}
    b = {'tr_title': '', 'paragraph': '', 'title': 'Sandworm sabotages plant',
         'full_text': 'The group disabled a steam turbine at a combined heat '
                      'and power plant in Poland via a private APN.'}
    assert oi.iliski_belirle(a, b) == oi.AYNI_AKTOR_FARKLI_OLAY


def test_baslik_duzeni_ozel_ad_uretmez():
    """Başlık-Düzeni cümleden özel ad çıkarılmaz (Flaw/Exploited/Wild)."""
    view = {'tr_title': '', 'paragraph': '',
            'title': 'Cisco ASA and FTD Flaw Exploited in the Wild',
            'full_text': 'Cisco ASA and FTD Flaw Exploited in the Wild Can '
                         'Trigger Remote DoS'}
    kesin, _ = oi.ozel_adlar(view)
    for gurultu in ('flaw', 'exploite', 'wild', 'trigger'):
        assert gurultu not in kesin, f'{gurultu} özel ad sanıldı'


def test_tek_ortak_ozel_ad_yetmez():
    """Tek bir ortak özel ad 'aynı olay' demeye yetmemeli."""
    a = {'tr_title': '', 'paragraph': 'Bir satıcı ürününde Commerce modülü '
                                      'için kritik yama yayımlandı.',
         'title': '', 'full_text': ''}
    b = {'tr_title': '', 'paragraph': 'Başka bir satıcı Commerce ürününde '
                                      'ayrı bir açığı kapattı.',
         'title': '', 'full_text': ''}
    assert oi.iliski_belirle(a, b) == oi.ILISKISIZ


def test_ozel_ad_uretim_oncesi_calisir():
    """paragraph BOŞken de özel ad bulunmalı (mükerrer doğrulamasının şartı).

    src.dedup.extract_entities bu görünümde boş döner — modül başlığı B."""
    view = {'tr_title': '', 'paragraph': '', 'title': 'Suisun City hit',
            'full_text': 'Officials in Suisun City said the network was shut '
                         'down after malware disabled dispatch systems.'}
    kesin, _ = oi.ozel_adlar(view)
    assert 'suisun' in kesin


def test_sozluk_kucuk_derlemde_jenerik_saymaz():
    """Derlem küçükken DF güvenilmezdir; hiçbir ad jenerik sayılmamalı."""
    s = oi.OlaySozlugu([{'paragraph': 'Test Kurumu bir açıklama yaptı.'}] * 3)
    assert s.jenerik_mi('test') is False


# ── OLAY DEFTERİ ─────────────────────────────────────────────────────────────

def _v(tr_title='', paragraph='', title='', full_text=''):
    return {'tr_title': tr_title, 'paragraph': paragraph,
            'title': title, 'full_text': full_text}


def test_defter_ayni_olayi_gunler_boyunca_baglar():
    # Özel adlar bilinçli olarak cümle ORTASINDA: cümle başındaki büyük harf
    # özel adlıktan değil konumdan gelebilir, o yüzden yalnızca 'aday' sayılır
    # ve tek başına olay bağı kurmaz (bkz. ozel_adlar / _ortak_adlar).
    gun1 = _v(paragraph='Kuzey Kaliforniya\'daki Suisun City belediyesi '
                        'ağını kapattı ve Solano ilçesi sevk merkezine geçti.')
    gun2 = _v(paragraph='Yetkililer Suisun City sistemlerinin geri '
                        'yüklendiğini, Solano ilçesindeki yönlendirmenin '
                        'sona erdiğini açıkladı.')
    d = oi.defter_kur([('2026-08-11', [gun1], [gun1]),
                       ('2026-08-12', [gun2], [])])
    assert len(d.kayitlar) == 1, 'aynı olay iki kayda bölündü'
    assert d.kayitlar[0]['gunler'] == ['2026-08-11', '2026-08-12']
    assert d.manset_gunu_sayisi(gun2) == 1
    assert d.son_manset_gunu(gun2) == '2026-08-11'


def test_defter_ayni_aktoru_farkli_olaya_baglamaz():
    """Aktör adı olay kimliği DEĞİLDİR (08-12 Sandworm vakası)."""
    polonya = _v(paragraph='Sandworm, Polonya\'da bir kojenerasyon tesisinde '
                           'buhar türbinini devre dışı bıraktı.')
    ukrayna = _v(paragraph='Sandworm, Ukraynalı yazılımcılara sahte mülakatla '
                           'zararlı bir VPN istemcisi yükletti.')
    d = oi.defter_kur([('2026-08-11', [polonya], [polonya]),
                       ('2026-08-12', [ukrayna], [])])
    assert len(d.kayitlar) == 2, 'aynı aktör farklı olayları birleştirdi'
    assert d.manset_gunu_sayisi(ukrayna) == 0, (
        'farklı olay, dünkü manşetin manşet geçmişini devraldı')


def test_defter_manset_gunu_sayisi_hic_manset_olmayanda_sifir():
    v = _v(paragraph='Tamamen yeni bir olay hakkında haber metni.')
    d = oi.defter_kur([('2026-08-12', [v], [])])
    assert d.manset_gunu_sayisi(v) == 0
    assert d.son_manset_gunu(v) is None


def test_aktor_kokleri_olay_kimliginden_dusulur():
    v = _v(paragraph='Lazarus grubu bir savunma şirketini hedef aldı.')
    assert 'ad:lazarus' not in oi.olay_kimlikleri(v)


# ── GÜN İÇİ KÜMELEME ─────────────────────────────────────────────────────────

def test_kumele_ayni_olayi_gruplar():
    a = _v(paragraph='Redmond bu ay 421 açığı kapattı; Lazarus bir sıfır günü '
                     'istismar etti.', title='421 bugs in Patch Tuesday')
    b = _v(paragraph='Microsoft bu ay 421 güvenlik açığını kapattı ve Lazarus '
                     'grubunun istismar ettiği sıfır günü yamaladı.',
           title='Microsoft Plugs Nearly 400 Holes')
    c = _v(paragraph='Bir fidye çetesi bir hastanenin sosyal medya hesabını '
                     'ele geçirdi.', title='Ransomware hijacks hospital page')
    gruplar = oi.kumele({1: a, 2: b, 3: c})
    assert sorted(len(g) for g in gruplar) == [1, 2]
    ikili = next(g for g in gruplar if len(g) == 2)
    assert set(ikili) == {1, 2}


def test_kumele_gecisli_zincirleme_yapmaz():
    """Temsilci tabanlıdır: A~B ve B~C, A~C demek DEĞİLDİR.

    İlk uygulama union-find'dı ve gerçek veride 64 adayın 28'ini tek gruba
    düşürdü (bkz. kumele docstring)."""
    a = _v(paragraph='Ortak Alfa Kurumu ve Beta Sistemi hakkında ayrıntılı '
                     'bir inceleme yayımlandı.')
    b = _v(paragraph='Beta Sistemi ile Gama Platformu arasındaki bağlantı '
                     'ayrıntılı biçimde incelendi.')
    c = _v(paragraph='Gama Platformu üzerinde yürütülen ayrı bir çalışma '
                     'ayrıntılı olarak duyuruldu.')
    gruplar = oi.kumele({1: a, 2: b, 3: c})
    assert not any(len(g) == 3 for g in gruplar), 'zincirleme birleştirme oldu'


def test_kumele_varsayilan_kati():
    """Varsayılan eşik KATI olmalı (ölçümle seçildi: 2 vs 4 yanlış birleştirme)."""
    import inspect
    imza = inspect.signature(oi.kumele)
    assert imza.parameters['gevsek'].default is False
