"""OLAY İLİŞKİSİ — iki haber arasındaki ilişkiyi DÖRT DEĞERLİ olarak belirler.

NEDEN VAR (kök neden):
Boru hattı bugüne kadar tek bir ikili soru soruyordu: "bu iki haber aynı olay
mı?" (src.dedup.same_event) ve skorlayıcı da tek bir bit üretiyordu (mukerrer
0/1). Bu tek bit ÜÇ FARKLI durumu birbirine karıştırıyor ve üçüne de aynı
cezayı (eleme + manşet yasağı) uyguluyordu:

  1. Aynı olayın AYNI gelişmesi      → gerçek mükerrer, elenmeli
  2. Aynı olayın YENİ gelişmesi      → rapora girmeli (yeni kurban, yeni yama,
                                        yeni istismar, yeni atıf)
  3. Aynı AKTÖR, farklı olay         → tamamen serbest

Ölçülen maliyet (2026-08-12): İran'ın ABD su altyapısına saldırısı 96 puanla
günün 2. haberiydi ve (2) sınıfındaydı — yeni eyaletler (New Jersey, Alabama),
yeni saldırılar; mukerrer=1 yiyip manşetten düştü. Sandworm/UAC-0145 92 puanla
(3) sınıfındaydı — dünkü Polonya enerji sabotajıyla ortak olan tek şey aktördü;
o da manşetten düştü. Yerlerine 90 puanlı iki haber girdi.

İKİ YAPISAL DÜZELTME:

A) OLAY KİMLİĞİ ≠ AKTÖR KİMLİĞİ. Bir olayın kimliği KURBANI/HEDEFİ/ZAFİYETİ
   (Suisun City, AnMed, CVE-2026-20349, litellm paketi) ile belirlenir; aktör
   (Sandworm, Lazarus) olayın kimliği DEĞİL failidir. Aynı fail farklı olaylar
   yapar. same_event bu ikisini aynı torbada değerlendiriyordu.

B) ÖZEL AD SİNYALİ ARTIK ÜRETİM ÖNCESİ DE ÇALIŞIR. src.dedup.extract_entities
   sözlük girdisinde YALNIZCA 'paragraph' alanını okur. Mükerrer bayrağının
   doğrulandığı yer (main._mukerrer_dogrulandi) ise içerik üretiminden ÖNCEDİR
   ve elinde yalnızca title + full_text vardır — yani paragraph BOŞTUR. Sonuç:
   en güçlü olay-kimliği sinyali tam da en çok gerektiği yerde ÖLÜYDÜ.
   Ölçüldü (2026-08-12 golden set): Mozilla GPG, CEVA, Suisun çiftlerinin
   hepsinde extract_entities(a) = [] döndü.

   Buradaki çıkarıcı full_text'i de tarar. Bunun getirdiği gürültü üç ayrı
   mekanizmayla elenir (hiçbiri tek başına yetmedi, sırayla ölçülerek eklendi):
     • BAŞLIK KURALI — Başlık-Düzeni parçalardan özel ad çıkarılmaz
       (bkz. _govde_metinleri). Tek başına 4/20 → 12/20.
     • KİMLİK DERECESİ — tek bir ortak özel ad "aynı olay" demeye yetmez
       (bkz. _kimlik_yeterli). 12/20 → 15/20.
     • BELGE FREKANSI — jenerik adları derlemden öğrenir (bkz. OlaySozlugu).
       Türkçe paragraf sözcüklerinde etkilidir; İngilizce gövde sözcüklerinde
       7 günlük derlem henüz çok seyrektir ('coweta' ve 'fixed' ikisi de DF=1).

ÖLÇÜLEN SONUÇ (data/dedup_golden.json, 20 çift): etiket doğruluğu 15/20,
POLİTİKA doğruluğu 17/20. Kalan 3 kaçağın üçü de GÜVENLİ yöndedir (mükerreri
kaçırmak; hiçbiri farklı iki haberi birleştirmez) ve üçü de anlamsal
yargı gerektirir — Yama Salısı toplamı ile ESU bültenini, ya da Minnesota su
tesisleriyle New Jersey su tesislerini aynı kampanya saymak dünya bilgisi
ister. Bunlar LLM denetim katmanlarının işidir; deterministik katmanın işi
KESİN olmaktır, kapsayıcı olmak değil.
"""
import re
from collections import Counter

from . import dedup

# Dört değerli ilişki türleri.
AYNI_GELISME = 'AYNI_GELISME'                      # gerçek mükerrer
YENI_GELISME = 'YENI_GELISME'                      # aynı olay, yeni gelişme
AYNI_AKTOR_FARKLI_OLAY = 'AYNI_AKTOR_FARKLI_OLAY'  # ortak olan yalnızca fail
ILISKISIZ = 'ILISKISIZ'

ILISKILER = (AYNI_GELISME, YENI_GELISME, AYNI_AKTOR_FARKLI_OLAY, ILISKISIZ)

# Rapora GİRMESİ gereken ilişkiler (yalnızca AYNI_GELISME elenir).
RAPORA_GIRER = frozenset({YENI_GELISME, AYNI_AKTOR_FARKLI_OLAY, ILISKISIZ})
# MANŞETE çıkabilecek ilişkiler. YENI_GELISME manşete çıkabilir — 08-12'de
# İran su altyapısı tam da bu sınıftaydı ve günün 2. en yüksek puanlı haberiydi.
# Üst üste manşet olmayı engelleyen ayrı bir kural vardır (bkz. main:
# _derive_top3_by_score, olay defterinin manset_gunleri alanı).
MANSETE_CIKAR = frozenset({YENI_GELISME, AYNI_AKTOR_FARKLI_OLAY, ILISKISIZ})

# ── ÖZEL AD ÇIKARIMI (üretim öncesi de çalışır) ─────────────────────────────
# Başlık-Düzeni veya TÜMÜ-BÜYÜK token; Türkçe çekim ekleri için ilk 8 karaktere
# köklenir ("Minnesota'daki" → 'minnesot'), src.dedup._entity_sets ile aynı
# kural — iki taraf aynı kökü üretmezse karşılaştırma anlamsız olurdu.
_AD_TOKEN_RE = re.compile(r"\b([A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü0-9]{3,})(?:['’][a-zçğıöşü]+)?")
_KOK = 8

# Cümleye bölme: özel ad çıkarımı CÜMLE bazında yapılır (aşağıdaki başlık
# kuralının çalışabilmesi için).
_CUMLE_BOL_RE = re.compile(r'(?<=[.!?])\s+|\n+')

# BAŞLIK KURALI — bir cümlenin sözcüklerinin bu oranından fazlası büyük harfle
# başlıyorsa o parça bir BAŞLIKTIR (Title Case), düz cümle değil. Başlıklarda
# HER sözcük büyük harfle başlar; "Cisco ASA and FTD Flaw Exploited in the Wild"
# içinde 'Flaw', 'Exploited', 'Wild' özel ad DEĞİLDİR.
#
# Bu kural olmadan çıkarıcı İngilizce başlıklardan 'ad:access', 'ad:patches',
# 'ad:tuesday', 'ad:flaws', 'ad:fixed' gibi sıradan sözcükleri özel ad sanıyordu
# ve ölçümde 20 çiftin 17'si yanlış sınıflandı (2026-08-12).
_BASLIK_BUYUK_ORANI = 0.6
# Bir parçanın başlık sayılması için gereken asgari sözcük sayısı (çok kısa
# parçalarda oran anlamsızdır).
_BASLIK_MIN_SOZCUK = 4

# Belge frekansı bu ORANIN üstündeyse özel ad JENERİKTİR (olay kimliği değil).
# Ölçüm (167 görünüm): ayırt edici adlar DF≤3 (%1.8), jenerik olanlar DF≥6
# (%3.6). Eşik ikisinin arasına konur ve derlem büyüdükçe oran sabit kalır.
JENERIK_DF_ORANI = 0.03
# Derlem bu boyuttan küçükse DF ölçümü güvenilmez; sabit denylist'e düşülür.
MIN_DERLEM = 25


# ── ÖNBELLEK ────────────────────────────────────────────────────────────────
# Kimlik çıkarımı (regex ağırlıklı) aynı görünüm için defalarca çağrılır:
# defter kurulurken her ADAY her KAYIT ile karşılaştırılır, yani N görünüm
# O(N²) çift üretir ve her çift iki tarafı da yeniden çıkarır. ÖLÇÜLDÜ
# (2026-08-12 fikstürü, 139 görünüm): 8.780 çift için 17.644 kimlik çıkarımı,
# toplam 39 saniye. Görünüm içeriği koşu boyunca değişmediğinden sonuç
# önbelleğe alınabilir.
#
# Anahtar, görünümün METİN İÇERİĞİDİR (id() değil): id yeniden kullanılabilir
# ve aynı içerik farklı sözlük nesnelerinde gelebilir.
_ONBELLEK_SINIRI = 4096
_onbellek = {}


def _anahtar(view):
    if not isinstance(view, dict):
        return ('duz', str(view or ''))
    return (view.get('tr_title') or '', view.get('title') or '',
            view.get('paragraph') or '', (view.get('full_text') or '')[:2000])


def _onbellekli(ad, view, uret):
    """uret() sonucunu (ad, görünüm içeriği) anahtarıyla önbelleğe alır."""
    try:
        k = (ad, _anahtar(view))
    except TypeError:
        return uret()
    if k in _onbellek:
        return _onbellek[k]
    if len(_onbellek) >= _ONBELLEK_SINIRI:
        _onbellek.clear()      # basit taşma stratejisi: koşu içinde yeterli
    sonuc = uret()
    _onbellek[k] = sonuc
    return sonuc


def onbellek_temizle():
    """Koşular/testler arası yalıtım için."""
    _onbellek.clear()


def _metin(view, tam=True):
    """Görünümün taranacak metni. tam=False ise yalnızca başlıklar."""
    if not isinstance(view, dict):
        return str(view or '')
    parcalar = [view.get('tr_title') or '', view.get('title') or '']
    if tam:
        parcalar += [view.get('paragraph') or '',
                     (view.get('full_text') or '')[:2000]]
    return ' '.join(parcalar)


def _govde_metinleri(view):
    """Özel ad çıkarımının tarayacağı GÖVDE metinleri (başlıklar HARİÇ).

    Başlıklar (title/tr_title) bilinçli olarak dışarıdadır: İngilizce
    başlıklar Başlık-Düzeni yazılır ve HER sözcüğü büyük harfle başlar, yani
    özel ad ile sıradan sözcük ayırt EDİLEMEZ ("Cisco ASA and FTD Flaw
    Exploited in the Wild" → Flaw/Exploited/Wild özel ad değildir).

    Bu ayrım alan bazında yapılmak ZORUNDADIR: başlık ile paragraf tek dizede
    birleştirilirse cümleye bölme onları ayıramaz (aralarında nokta yoktur),
    başlığın büyük harf oranı paragrafın içinde erir ve Başlık-Düzeni kuralı
    sessizce devre dışı kalır. Ölçüldü (2026-08-12): birleşik dizeyle
    'ad:exploite', 'ad:wild', 'ad:look', 'ad:like' gibi sıradan başlık
    sözcükleri olay kimliği sanıldı.

    Başlıklardaki gerçek kimlikler kaybolmaz — kod adı, CVE, paket ve aktör
    çıkarıcıları başlıkları ayrıca tarar (bkz. olay_kimlikleri)."""
    if not isinstance(view, dict):
        return [str(view or '')]
    return [view.get('paragraph') or '', (view.get('full_text') or '')[:2000]]


def ozel_adlar(view):
    """(kesin, aday) özel ad kökleri — paragraph BOŞ olsa da çalışır.

    src.dedup.extract_entities'ten iki farkı var: (1) yalnızca 'paragraph'
    değil, full_text de taranır — üretim öncesi karşılaştırma (mükerrer
    doğrulaması) ancak böyle mümkündür; (2) başlıklar ve Başlık-Düzeni
    parçalar dışlanır (bkz. _govde_metinleri)."""
    return _onbellekli('ad', view, lambda: _ozel_adlar_ham(view))


def _ozel_adlar_ham(view):
    kesin, aday = set(), set()
    parcalar = []
    for alan in _govde_metinleri(view):
        parcalar.extend(_CUMLE_BOL_RE.split(alan))
    for parca in parcalar:
        sozcukler = parca.split()
        if not sozcukler:
            continue
        # Başlık mı? (bkz. _BASLIK_BUYUK_ORANI) — başlıklardan özel ad
        # çıkarılmaz; her sözcüğü büyük harfle başladığı için ayrım imkânsızdır.
        buyuk = sum(1 for s in sozcukler if s[:1].isupper())
        if (len(sozcukler) >= _BASLIK_MIN_SOZCUK
                and buyuk / len(sozcukler) > _BASLIK_BUYUK_ORANI):
            continue
        for m in _AD_TOKEN_RE.finditer(parca):
            lw = m.group(1).lower()
            if (lw in dedup._ENTITY_DENYLIST or lw in dedup.CODENAME_DENYLIST
                    or lw in dedup._ACRONYM_DENYLIST):
                continue
            if lw.isdigit():
                continue
            kok = lw[:_KOK]
            # Cümlenin İLK sözcüğü 'aday'dır: büyük harfi özel adlıktan değil
            # cümle başı olmaktan gelebilir. Ancak KARŞI belge onu cümle
            # ortasında kullanmışsa sayılır (bkz. _ortak_adlar).
            if m.start() == 0:
                aday.add(kok)
            else:
                kesin.add(kok)
    return kesin, aday - kesin


class OlaySozlugu:
    """Derlemden belge frekansı öğrenen özel ad filtresi.

    Elle tutulan denylist'in yapısal sorunu, YENİ jenerik sözcüğü asla
    bilmemesidir. DF ölçümü bunu tersine çevirir: bir ad ne kadar çok haberde
    geçiyorsa o kadar az ayırt edicidir. Derlem her gün büyüdüğü için filtre
    kendi kendine kalibre olur.

    Derlem küçükse (< MIN_DERLEM) DF güvenilmezdir; o durumda hiçbir adı
    jenerik saymaz — yalnızca src.dedup'un sabit denylist'leri devrede kalır.
    """

    def __init__(self, views=()):
        self.n = 0
        self.df = Counter()
        for v in views or ():
            self.ekle(v)

    def ekle(self, view):
        self.n += 1
        kesin, aday = ozel_adlar(view)
        for ad in (kesin | aday):
            self.df[ad] += 1

    def jenerik_mi(self, ad):
        if self.n < MIN_DERLEM:
            return False
        return self.df.get(ad, 0) / self.n > JENERIK_DF_ORANI

    def ayirt_edici(self, adlar):
        """Verilen ad kümesinden yalnızca AYIRT EDİCİ olanları döndürür."""
        return {a for a in adlar if not self.jenerik_mi(a)}


# Derlemsiz kullanım için: hiçbir adı jenerik saymayan boş sözlük.
BOS_SOZLUK = OlaySozlugu()


def _ortak_adlar(view_a, view_b, sozluk):
    """İki haberin ORTAK ve AYIRT EDİCİ özel adları.

    'Kesin' (cümle ortası) adlar doğrudan sayılır; 'aday' (cümle başı) adlar
    ancak KARŞI belge onları cümle ortasında kullanmışsa sayılır — böylece
    cümle başı büyük harfi yanlış özel ad üretmez."""
    ka, aa = ozel_adlar(view_a)
    kb, ab = ozel_adlar(view_b)
    ortak = (ka & kb) | (ka & ab) | (aa & kb)
    return sozluk.ayirt_edici(ortak)


def _aktor_kokleri(view):
    """Aktör adlarının özel-ad KÖKLERİ (ozel_adlar ile aynı köklemeyle).

    extract_actors normalize edilmiş adlar döndürür (boşluk/tire silinmiş,
    küçük harf: 'starblizzard'); ozel_adlar ise sözcük başına 8 karakterlik
    kökler üretir ('blizzard', 'star'). İki temsil doğrudan karşılaştırılamaz,
    bu yüzden aktör adları metindeki sözcüklerine bölünüp aynı biçimde
    köklenir."""
    return _onbellekli('aktorkok', view, lambda: _aktor_kokleri_ham(view))


def _aktor_kokleri_ham(view):
    kokler = set()
    blob = _metin(view)
    for ad in dedup.extract_actors(blob):
        if ad.startswith('cve'):
            continue
        kokler.add(ad[:_KOK])
    # Adlandırılmış aktörler metinde boşluklu geçer ("Star Blizzard"); her
    # sözcüğü ayrı ayrı köklenmelidir.
    dusuk = blob.lower()
    for ad in dedup._NAMED_ACTORS:
        if ad in dusuk:
            for sozcuk in ad.split():
                kokler.add(sozcuk[:_KOK])
    for m in dedup._TAXONOMY_ACTOR_RE.finditer(blob):
        kokler.add(m.group(1).lower()[:_KOK])
    for m in dedup._TREND_ACTOR_RE.finditer(blob):
        for sozcuk in m.group(0).split():
            kokler.add(sozcuk.lower()[:_KOK])
    return kokler


def olay_kimlikleri(view, sozluk=None):
    """Bir haberin OLAY kimliği: kurban/hedef/zafiyet/paket/kod adı.

    Aktör (Sandworm, Lazarus) BURAYA GİRMEZ — aktör olayın kimliği değil,
    failidir; aynı fail farklı olaylar yapar (bkz. modül başlığı, A maddesi)."""
    sozluk = sozluk or BOS_SOZLUK
    return _onbellekli(('kimlik', id(sozluk)), view,
                       lambda: _olay_kimlikleri_ham(view, sozluk))


def _olay_kimlikleri_ham(view, sozluk):
    blob = _metin(view)
    kimlikler = set()
    kimlikler |= {'cve:' + c for c in dedup.extract_actors(blob)
                  if c.startswith('cve')}
    kimlikler |= {'pkg:' + p for p in dedup.extract_package_names(blob)}
    kimlikler |= {'kod:' + k for k in dedup.extract_codenames(blob)}
    kesin, _ = ozel_adlar(view)
    # AKTÖR ADLARI OLAY KİMLİĞİNDEN ÇIKARILIR — modülün varlık sebebi olan
    # ayrımın uygulandığı yer burasıdır. Aktör adı aynı zamanda bir özel addır
    # ('Sandworm', 'Lazarus'), dolayısıyla ozel_adlar onu doğal olarak
    # yakalar; çıkarılmazsa "ortak aktör" sinyali "ortak olay kimliği" gibi
    # davranır ve tam da engellemek istediğimiz birleşmeyi yapar.
    # ÖLÇÜLDÜ (2026-08-12 olay defteri): Sandworm/Polonya ve Sandworm/UAC-0145
    # haberleri 'ad:sandworm' ortaklığı üzerinden AYNI OLAYA bağlandı.
    kesin = kesin - _aktor_kokleri(view)
    kimlikler |= {'ad:' + a for a in sozluk.ayirt_edici(kesin)}
    return kimlikler


def aktor_kimlikleri(view):
    """Bir haberin AKTÖR kimliği (CVE hariç — o zafiyet kimliğidir)."""
    return _onbellekli('aktor', view, lambda: {
        a for a in dedup.extract_actors(_metin(view))
        if not a.startswith('cve')})


def _konu_anahtarlari(view):
    """Görünümün üç anahtar-sözcük kümesi (paragraf / tüm metin / giriş).

    Görünüm başına BİR kez hesaplanır; _konu_ortusmesi her çift için iki
    tarafı da yeniden çıkarıyordu ve defter kurulumunun süresinin yarısı
    buradaydı (bkz. _onbellek yorumu)."""
    def _uret():
        ha, pa, ea, fa = dedup._bundle(view)
        blob = ' '.join((ha, pa, ea, fa))
        return (dedup.event_keywords(pa),
                dedup.event_keywords(blob),
                dedup.event_keywords(pa, limit=dedup._TOPIC_LEAD_TOKENS))
    return _onbellekli('konu', view, _uret)


def _konu_ortusmesi(view_a, view_b):
    """src.dedup.same_event ile AYNI konu örtüşmesi ölçüsü (tek kaynak)."""
    a_par, a_blob, a_giris = _konu_anahtarlari(view_a)
    b_par, b_blob, b_giris = _konu_anahtarlari(view_b)
    return max(
        dedup._jaccard(a_par, b_par),
        dedup._jaccard(a_blob, b_blob),
        dedup._jaccard(a_giris, b_giris),
    )


# Ortak kimlik sınıfları: 'cve:', 'pkg:', 'kod:' YÜKSEK DERECELİdir (tek başına
# olayı belirler); 'ad:' (özel ad) DÜŞÜK derecelidir — tek bir ortak özel ad
# rastlantı olabilir. ÖLÇÜLDÜ (2026-08-12): SAP Commerce Cloud ↔ Adobe
# ColdFusion yalnızca 'ad:commerce' yüzünden, Ivanti EPM ↔ SonicWall GMS
# yalnızca 'ad:advertis' yüzünden aynı olay sayıldı — ikisi de farklı satıcının
# ayrı yama bültenidir.
_YUKSEK_DERECE = ('cve:', 'pkg:', 'kod:')
# Yalnızca düşük dereceli (özel ad) kimlik varsa aranan asgari ortak sayısı.
MIN_ORTAK_AD = 2
# Yeni gelişme sayılmak için gereken asgari YENİ özel ad sayısı (yüksek
# dereceli yenilik yoksa). Ölçümle seçildi: 2'de CEVA çifti ('european',
# 'steam' — ikisi de yan ayrıntı) yanlışlıkla yeni gelişme sayılıyordu;
# 3'te Suisun çifti (4 yeni kurban/yer adı) doğru biçimde yeni kalıyor.
MIN_YENI_AD = 3


def _yuksek_derece_var(kimlikler):
    return any(k.startswith(_YUKSEK_DERECE) for k in kimlikler)


def _kimlik_yeterli(ortak):
    """Ortak kimlik kümesi 'aynı olay' demeye yeter mi?"""
    if _yuksek_derece_var(ortak):
        return True
    return len(ortak) >= MIN_ORTAK_AD


# Ortak OLAY KİMLİĞİ varken aranan asgari konu örtüşmesi. same_event'in aktör
# eşiğinden (0.30/0.34) DÜŞÜKTÜR ve bu bilinçlidir: olay kimliği (kurban adı,
# CVE, paket) aktörden çok daha ayırt edicidir, dolayısıyla daha az konu
# desteğine ihtiyaç duyar. Ölçüm (08-12): Suisun çifti topic=0.20 ile
# same_event'in 0.22 eşiğinin hemen ALTINDA kalıp kaçmıştı.
KIMLIK_ILE_KONU_MIN = 0.10
# Hiçbir ortak kimlik yokken tek başına "aynı olay" demek için gereken örtüşme.
KONU_TEK_BASINA = dedup._TOPIC_ALONE


# Haberin "girişi": başlık + gövdenin ilk bu kadar sözcüğü. Gazetecilik
# kuralı gereği YENİ gelişme buraya yazılır; gövdenin derinlerindeki adlar
# (arka plan, geçmiş vakalar, uzman adları) yeni gelişme DEĞİLDİR.
GIRIS_SOZCUK = 60


def _giris_view(view):
    """Görünümün yalnızca GİRİŞ kısmını taşıyan yeni bir görünüm."""
    govde = (view.get('paragraph') or '') or (view.get('full_text') or '')
    return {
        'tr_title': view.get('tr_title') or '',
        'title': view.get('title') or '',
        'paragraph': ' '.join(govde.split()[:GIRIS_SOZCUK]),
        'full_text': '',
    }


def _yeni_gelisme_mi(view_a, view_b, sozluk):
    """A, B'de OLMAYAN bir gelişme DUYURUYOR mu?

    NEDEN GİRİŞ İLE SINIRLI: ilk denemede ölçüt "A'nın tüm olay kimlikleri
    eksi B'ninkiler ≥ 2" idi ve 20 çiftin 16'sını YENI_GELISME saydı — aynı
    olayı anlatan iki kaynak her zaman farklı yan adlar (uzman adı, örnek
    ülke, arka plan vakası) kullandığı için fark kümesi ASLA boşalmıyor.
    Gazetecilik yapısı bu gürültüyü eler: yeni gelişme HABERİN GİRİŞİNDE
    duyurulur, gövdenin derinlerinde değil.

    Ayrıca yeni öğe KİMLİK SINIFINDAN olmalıdır (yeni CVE, yeni paket, yeni
    kod adı ya da yeni özel ad) — sıradan sözcükler zaten olay kimliği
    değildir.

    Belge frekansı burada AYIRT EDİCİ DEĞİLDİR ve bilinçli olarak
    kullanılmaz: 7 günlük derlem (167 görünüm) Türkçe paragraf sözcüklerinde
    yoğun, İngilizce gövde sözcüklerinde seyrektir; ölçümde 'coweta' (gerçek
    yeni kurban) ve 'fixed' (sıradan sözcük) ikisi de DF=1 çıktı."""
    ka = olay_kimlikleri(_giris_view(view_a), sozluk)
    kb = olay_kimlikleri(view_b, sozluk)          # B'nin TAMAMIYLA karşılaştır
    yeni = ka - kb
    # Yüksek dereceli tek bir yenilik (yeni CVE / yeni paket / yeni kod adı)
    # başlı başına yeni gelişmedir. Yalnızca özel ad varsa daha fazlası
    # aranır: aynı olayı anlatan iki kaynak girişte bile bir-iki farklı yan ad
    # kullanır (ölçüldü: CEVA çifti 'european'+'steam' ile yeni sanılmıştı).
    if _yuksek_derece_var(yeni):
        return True, sorted(yeni)[:4]
    return (len(yeni) >= MIN_YENI_AD, sorted(yeni)[:4])


def iliski_belirle(view_a, view_b, ayni_gun=False, explain=False, sozluk=None):
    """İki haber arasındaki ilişkiyi dört değerden biri olarak döndürür.

    view_a: BUGÜNKÜ aday. view_b: karşılaştırılan (geçmiş ya da aynı gün) haber.
    Sıra ÖNEMLİDİR — YENI_GELISME "A, B'ye göre yeni" demektir.
    explain=True ise (ilişki, gerekçe) döner.

    Karar sırası (en özgülden en gevşeğe):
      1. Ortak OLAY KİMLİĞİ + asgari konu örtüşmesi → aynı olay.
         Ardından yeni gelişme var mı diye bakılır (AYNI vs YENİ).
      2. Ortak kimlik yok ama ortak AKTÖR var → AYNI_AKTOR_FARKLI_OLAY.
      3. Hiçbir ortak kimlik yok, konu örtüşmesi tek başına yüksek → aynı olay.
      4. Aksi hâlde ILISKISIZ.
    """
    sozluk = sozluk or BOS_SOZLUK

    def _ret(iliski, neden=''):
        return (iliski, neden) if explain else iliski

    ka = olay_kimlikleri(view_a, sozluk)
    kb = olay_kimlikleri(view_b, sozluk)
    ortak_kimlik = ka & kb
    # Özel adlar ayrıca 'aday' (cümle başı) yoluyla da eşleşebilir; olay
    # kimliği kümesi yalnızca 'kesin' adları taşıdığı için burada tamamlanır.
    ortak_kimlik |= {'ad:' + a for a in _ortak_adlar(view_a, view_b, sozluk)}

    topic = _konu_ortusmesi(view_a, view_b)

    if _kimlik_yeterli(ortak_kimlik) and topic >= KIMLIK_ILE_KONU_MIN:
        yeni_var, yeni = _yeni_gelisme_mi(view_a, view_b, sozluk)
        etiket = ','.join(sorted(ortak_kimlik)[:4])
        if yeni_var:
            return _ret(YENI_GELISME,
                        f'ortak={etiket} topic={topic:.2f} yeni={yeni}')
        return _ret(AYNI_GELISME, f'ortak={etiket} topic={topic:.2f}')

    # ÇOK YÜKSEK konu örtüşmesi, ortak kimlik bulunamasa bile aynı olaydır ve
    # bu kontrol AKTÖR kuralından ÖNCE gelmek zorundadır. Aksi hâlde aynı
    # olayı anlatan iki haber, ortak kimlikleri denylist'e takıldığı için
    # (ör. Microsoft/Redmond satıcı adı olarak elenir) yalnızca aktörü
    # paylaşıyor görünür ve AYNI_AKTOR_FARKLI_OLAY sayılır — oysa topic=0.46
    # gibi bir örtüşme tesadüf değildir.
    if topic >= KONU_TEK_BASINA:
        yeni_var, yeni = _yeni_gelisme_mi(view_a, view_b, sozluk)
        if yeni_var:
            return _ret(YENI_GELISME, f'topic={topic:.2f} yeni={yeni}')
        return _ret(AYNI_GELISME, f'topic={topic:.2f}')

    ortak_aktor = aktor_kimlikleri(view_a) & aktor_kimlikleri(view_b)
    if ortak_aktor:
        return _ret(AYNI_AKTOR_FARKLI_OLAY,
                    f'aktör={",".join(sorted(ortak_aktor))} topic={topic:.2f}')

    return _ret(ILISKISIZ, f'topic={topic:.2f}')


# ── OLAY DEFTERİ ─────────────────────────────────────────────────────────────
class OlayDefteri:
    """Son günlerin haberlerini OLAYLARA gruplayan defter.

    NEDEN VAR: boru hattı bugüne kadar her gün her çifti YENİDEN karşılaştırıyor
    ve hiçbir yerde "bu olay" diye kalıcı bir kimlik tutmuyordu. Sonuç iki
    yönlü arıza: (a) aynı olay farklı sözcüklerle yazıldığında bağ kopuyor,
    (b) "bu olay kaç gündür manşette?" sorusu yanıtlanamadığı için manşet
    tekrarı ancak kaba bir 'mukerrer' bayrağıyla engellenebiliyor — o bayrak
    da YENİ gelişmeleri birlikte eliyor (08-12: İran su altyapısı 96 puan).

    Defter, kalıcı bir DOSYA DEĞİLDİR: her koşuda mevcut geçmişten (rapor_
    gecmis + kritik3_gecmis) yeniden kurulur. Böylece yeni bir durum dosyası
    ve ona bağlı bir sıfırlama prosedürü doğmaz (bkz. CLAUDE.md "Taze Rapor
    İçin Reset"); defterin doğruluğu tamamen sınıflandırıcıya bağlıdır ve
    sınıflandırıcı iyileştikçe geçmişe dönük olarak da düzelir.

    Kayıt alanları:
      olay_id        — sıralı, koşu içinde kararlı
      gunler         — olayın raporlandığı günler (artan)
      manset_gunleri — olayın KRİTİK 3 manşeti olduğu günler
      views          — temsilci görünümler (en yeniden eskiye, en fazla 3)
    """

    # Bir olayın temsilcisi olarak saklanan en fazla görünüm sayısı. Eşleştirme
    # bunların HEPSİNE bakar: olay geliştikçe sözcükleri değişir, tek temsilci
    # birkaç gün sonra artık eşleşmez.
    TEMSILCI = 3

    def __init__(self, sozluk=None):
        self.sozluk = sozluk or BOS_SOZLUK
        self.kayitlar = []

    def _yeni_kayit(self, gun, view, manset):
        kayit = {
            'olay_id': len(self.kayitlar) + 1,
            'gunler': [gun],
            'manset_gunleri': [gun] if manset else [],
            'views': [view],
        }
        self.kayitlar.append(kayit)
        return kayit

    def esle(self, view, explain=False):
        """Görünümü defterdeki bir olayla eşleştirir.

        Dönüş: (kayit, iliski, neden) — eşleşme yoksa (None, ILISKISIZ, ...).
        En GÜÇLÜ eşleşme kazanır: AYNI_GELISME > YENI_GELISME. Aktör-only
        eşleşmeler olay bağı KURMAZ (farklı olaydırlar)."""
        en_iyi, en_iyi_iliski, en_iyi_neden = None, ILISKISIZ, ''
        for kayit in self.kayitlar:
            for gecmis_view in kayit['views']:
                iliski, neden = iliski_belirle(
                    view, gecmis_view, explain=True, sozluk=self.sozluk)
                if iliski == AYNI_GELISME:
                    return (kayit, iliski, neden) if explain else (kayit, iliski)
                if iliski == YENI_GELISME and en_iyi is None:
                    en_iyi, en_iyi_iliski, en_iyi_neden = kayit, iliski, neden
        if explain:
            return en_iyi, en_iyi_iliski, en_iyi_neden
        return en_iyi, en_iyi_iliski

    def ekle(self, gun, view, manset=False):
        """Görünümü deftere işler; eşleşen olay varsa ona bağlar.

        Dönüş: (kayit, iliski) — iliski, görünümün DEFTERE göre durumudur."""
        kayit, iliski = self.esle(view)
        if kayit is None:
            return self._yeni_kayit(gun, view, manset), ILISKISIZ
        if gun not in kayit['gunler']:
            kayit['gunler'].append(gun)
            kayit['gunler'].sort()
        if manset and gun not in kayit['manset_gunleri']:
            kayit['manset_gunleri'].append(gun)
            kayit['manset_gunleri'].sort()
        # En yeni görünüm başa; olay geliştikçe temsilciler tazelenir.
        kayit['views'].insert(0, view)
        del kayit['views'][self.TEMSILCI:]
        return kayit, iliski

    def gunleri_isle(self, gunler):
        """[(gun, views, manset_views), ...] — defteri toplu kurar.

        Günler ESKİDEN YENİYE işlenmelidir; olay kimliği ilk görülme sırasına
        göre atanır ve temsilciler doğru sırayla tazelenir."""
        for gun, views, manset_views in gunler:
            manset_kimlik = {id(v) for v in (manset_views or ())}
            for v in views or ():
                self.ekle(gun, v, manset=id(v) in manset_kimlik)
        return self

    def manset_gunu_sayisi(self, view):
        """Bu görünümün olayı son günlerde kaç kez MANŞET olmuş?

        Faz 2 politikasının çekirdeği: 'mukerrer' bayrağı gibi kaba bir yasak
        yerine ÖLÇÜLEN bir gerçek. Olay hiç manşet olmamışsa 0 döner ve haber
        puanına göre serbestçe manşete çıkabilir."""
        kayit, _ = self.esle(view)
        return len(kayit['manset_gunleri']) if kayit else 0

    def son_manset_gunu(self, view):
        kayit, _ = self.esle(view)
        if not kayit or not kayit['manset_gunleri']:
            return None
        return kayit['manset_gunleri'][-1]


def defter_kur(gunler, sozluk=None):
    """Kısayol: [(gun, views, manset_views), ...] → kurulmuş OlayDefteri."""
    return OlayDefteri(sozluk=sozluk).gunleri_isle(gunler)


# ── GÜN İÇİ KÜMELEME ─────────────────────────────────────────────────────────
def kumele(views_by_id, sozluk=None, ayni_gun=True, gevsek=False):
    """Aynı günün adaylarını OLAY GRUPLARINA böler.

    NEDEN VAR: gün-içi mükerrer bugüne kadar DÖRT ayrı yerde, dört ayrı
    ölçütle aranıyordu (manşet-içi ayrıklık, gövde aynı-olay taraması,
    LLM auditor, çapraz-gün kalıntısı). Her biri kendi eşiğiyle aynı soruyu
    sorduğu için hem kaçırıyor hem çelişiyorlardı. Tek geçiş tek yanıt üretir
    ve sonraki katmanlar onu kullanır.

    Kümeleme AÇGÖZLÜ ve TEMSİLCİ TABANLIDIR: her aday yalnızca mevcut
    grupların TEMSİLCİSİYLE karşılaştırılır, eşleşirse o gruba katılır, yoksa
    yeni grup açar.

    GEÇİŞLİLİK (union-find) BİLİNÇLİ OLARAK KULLANILMAZ. İlk uygulama
    geçişliydi ve gerçek veride yıkıcı biçimde zincirlendi: 2026-08-12'nin 64
    adayından 28'i (SAP + ShieldBreak + Cisco + Kimwolf + SharePoint + Adobe +
    SonicWall + Chrome bildirimleri...) TEK gruba düştü. Sebep yapısaldır —
    A~B ve B~C bağlarının her biri tek başına zayıf olsa bile geçişlilik
    hepsini birleştirir ve tek bir yanlış-pozitif tüm günü tek olay yapar.
    Temsilci tabanlı kümeleme bu yayılmayı kapatır: yanlış bir bağın etkisi
    o grupla sınırlı kalır.

    Dönüş: [[id, ...], ...] — her grup bir olay; tek üyeli gruplar da döner.
    Grup içi sıra girdideki sıradır (çağıran genelde puan sırasında verir),
    dolayısıyla grubun İLK üyesi doğal temsilcidir.
    """
    # gevsek=True, aynı gün içindeki YENI_GELISME'yi de aynı olay sayar
    # (iki kaynak aynı olayı farklı ayrıntı derinliğinde anlatır, biri
    # diğerinden 'yeni' görünür). Kulağa doğru gelir ama ÖLÇÜM AKSİNİ SÖYLER
    # (2026-08-12, 64 aday):
    #     gevşek : 12 çok üyeli grup, 4'ü yanlış birleştirme
    #              (LiteLLM↔Mozilla GPG, SonicWall↔ICS↔SAP, Kuzey Kore↔Delta)
    #     katı   :  9 çok üyeli grup, 2'si yanlış birleştirme
    # Katı eşik hem daha az yanlış birleştirir hem gerçek grupları (Patch
    # Tuesday ×7, Delta ×4, Cisco ×3) yakalamaya devam eder. Varsayılan KATI.
    kabul = ((AYNI_GELISME, YENI_GELISME) if gevsek else (AYNI_GELISME,))
    gruplar = []
    for aid in views_by_id:
        view = views_by_id[aid]
        for grup in gruplar:
            temsilci = views_by_id[grup[0]]
            if iliski_belirle(view, temsilci, ayni_gun=ayni_gun,
                              sozluk=sozluk) in kabul:
                grup.append(aid)
                break
        else:
            gruplar.append([aid])
    return gruplar
