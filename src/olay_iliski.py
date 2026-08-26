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
from difflib import SequenceMatcher
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


# ── KİMLİK SAYILMAYAN KÖKLER ─────────────────────────────────────────────
# Haber metinlerinde YAPISAL olarak sık geçen, olayı ayırt etmeyen kökler.
# DF tabanlı sözlük bunları YETERİNCE BÜYÜK bir derlemde zaten yakalar, ama
# derlem boyutu tesadüfe bağlıdır: 2026-08-24'te rapor geçmişi 8 günden 31
# güne doldurulunca dört değerli sınıflandırıcının etiket isabeti 18'den 16'ya
# düştü — çünkü DF eşiği farklı sözcükleri jenerik saymaya başladı. Sabit
# liste bu kaymayı taşımaz.
#
# Ölçülen sahte eşleşmeler:
#   ad:cvss              SAP Commerce ↔ Adobe ColdFusion
#   ad:tuesday           Ivanti EPM ↔ SonicWall GMS ("Patch Tuesday")
#   ad:altyapı,güvenliğ  iki farklı CISA duyurusu
_MANSIZ_AD = {
    'cvss', 'altyapı', 'güvenliğ', 'güvenlik', 'zafiyet', 'saldırı',
    'tuesday', 'salı', 'advertis', 'reklam', 'commerce', 'update',
    'güncelle', 'patch', 'yama', 'critical', 'kritik',
}


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
    kimlikler |= {'ad:' + a for a in sozluk.ayirt_edici(kesin)
                  if a not in _MANSIZ_AD}
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
# 'pkg:' BİLİNÇLİ OLARAK YÜKSEK DERECEDE DEĞİLDİR. Paket adı sezgisel olarak
# güçlü bir kimlik gibi durur ama ÇIKARICISI ölçülebilir biçimde gürültülüdür:
# 157 görünümlük derlemde 139 farklı "paket adı" üretti ve HİÇBİRİ gerçek paket
# değildi (said, yeni, veya, poisoned, within, hundreds...), buna karşılık
# gerçek paketlerin (litellm, keyv, trivy) hiçbiri yakalanmadı. Sebep yapısal:
# ekosistem işaretinin (npm/PyPI/paket) 90 karakterlik penceresindeki HER
# küçük-harfli sözcük aday sayılıyor.
#
# ÖLÇÜLEN MALİYET (2026-08-18 gölge kümeleme gerekçesi): "LiteLLM tedarik
# zinciri saldırısı" ile "Snowflake GitHub deposunda komut enjeksiyonu" —
# farklı iki olay — tek başına 'ortak=pkg:affected' yüzünden AYNI_GELISME
# sayıldı. Tek bir 'affected' sözcüğü iki haberi birleştirmeye yetti.
#
# Düşük dereceye indirmek golden set'te GERİLEME YARATMIYOR (18/23 etiket,
# 20/23 politika — değişmedi), yani sinyal zaten taşımadığı bir ağırlık
# taşıyordu. Artık tek başına yetmez; ikinci bir kimlik gerekir.
_YUKSEK_DERECE = ('cve:', 'kod:')
# Yalnızca düşük dereceli (özel ad) kimlik varsa aranan asgari ortak sayısı.
MIN_ORTAK_AD = 2
# Yeni gelişme sayılmak için gereken asgari YENİ özel ad sayısı (yüksek
# dereceli yenilik yoksa). Ölçümle seçildi: 2'de CEVA çifti ('european',
# 'steam' — ikisi de yan ayrıntı) yanlışlıkla yeni gelişme sayılıyordu;
# 3'te Suisun çifti (4 yeni kurban/yer adı) doğru biçimde yeni kalıyor.
MIN_YENI_AD = 3

# Manşet geçmişi sorgusunda TEK ortak kimlikle eşleşmek için gereken konu
# örtüşmesi (bkz. OlayDefteri._manset_kaydi). Gövde elemesinin
# KIMLIK_ILE_KONU_MIN'i (0.10) İKİ ortak kimlik varsayar; tek kimliğe
# uygulanınca çok gevşek kalıyor.
#
# ÖLÇÜLDÜ (2026-08-21 raporu, 38 haber — kaç haber manşete yasaklanıyor):
#   0.10 → 12/38   (aşırı: manşet havuzu açlıktan zayıf haberlere düşüyor)
#   0.20 →  3/38
#   0.25 →  2/38   ← seçildi; Siemens tekrarını (topic=0.37) hâlâ yakalıyor
#   yalnızca same_event → 2/38   (0.25 ek maliyet getirmiyor, ek kapsam veriyor)
MANSET_TEK_KIMLIK_KONU = 0.25

# Başlık düzeyi kod adı eşleşmesinin geçerli sayılması için gereken asgari konu
# örtüşmesi. Kod adı çıkarıcı şirket/platform adlarını da yakalıyor; ölçümde
# 'codename:tiktok' iki apayrı TikTok haberini birleştirdi (topic=0.03).
# Gerçek kod adı eşleşmelerinin ölçülen konu örtüşmesi 0.20-0.48 aralığında.
KOD_ADI_KONU_MIN = 0.15
# Gövde düzeyi kod adı için asgari konu örtüşmesi. Başlıktakinden YÜKSEKTİR:
# gövdede geçen bir ad haberin öznesi olmayabilir (araştırmacı, kurum, örnek).
GOVDE_KOD_ADI_KONU_MIN = 0.35

# Aktör (APT adı) eşleşmesinin geçerli sayılması için gereken asgari konu
# örtüşmesi. Aktör olayın faili, kimliği değil — aynı grup farklı operasyonlar
# yapar. CVE eşleşmesi bundan MUAFTIR (yapısal kimliktir, 'actor:cve...').
AKTOR_KONU_MIN = 0.25



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
# Ortak AD olmasa bile "aynı olay" demeye yeten konu örtüşmesi (bkz. ayni_olay
# filtre 2). Ölçülen sahte eşleşmelerin en yükseği 0.27 idi; eşik onun çok
# üstünde tutulur ki yalnızca neredeyse birebir metinler geçsin.
KONU_KESINLIK = 0.75


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
    # KİMLİK KÜMESİ FARKI TEK BAŞINA YETMEZ — B'nin METNİNDE geçen bir ad
    # "yeni" değildir. `olay_kimlikleri` özel ad çıkarımı yapar ve bu çıkarım
    # kaçırabilir (küçük harfle yazılmış ad, farklı cümle konumu); kaçırılan
    # her ad sahte bir yenilik üretir.
    # ÖLÇÜLDÜ (2026-08-26): ABD'nin İran bağlantılı aktörlere yaptırımı 25 ve
    # 26 Ağustos'ta ÜST ÜSTE yayımlandı. Çift AYNI OLAY olarak tanındı ama
    # dört "yeni ad" yüzünden GELISME sayıldı: 'tahran' ve 'economic' B'nin
    # metninde AYNEN geçiyordu (biri cümle içinde, diğeri küçük harfle),
    # yalnızca kimlik çıkarımı onları görmemişti. Gerçekten yeni olan tek ad
    # 'outcast' idi — eşiğin (3) altında.
    if yeni:
        b_metin = _metin(view_b).lower()
        yeni = {k for k in yeni
                if (k.split(':', 1)[-1] or '\0') not in b_metin}
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
    # _MANSIZ_AD burada da uygulanır: bu ikinci yol 'aday' (cümle başı)
    # adlarını ekler ve olay_kimlikleri'ndeki filtreyi ATLIYORDU —
    # 'ad:cvss' ve 'ad:tuesday' sahte eşleşmeleri buradan geliyordu.
    ortak_kimlik |= {'ad:' + a for a in _ortak_adlar(view_a, view_b, sozluk)
                     if a not in _MANSIZ_AD}

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

    def _manset_kaydi(self, view):
        """Manşet geçmişi sorgusu için kayıt bulur — GEVŞEK eşleşmeyle.

        NEDEN GEVŞEK: defterin dört değerli sınıflandırıcısı GÖVDE ELEMESİ için
        ayarlanmıştır ve orada yanlış-pozitifin maliyeti yüksektir (haber
        SİLİNİR). Manşet tekrarı sorusunda maliyet TERSİNE döner: yanlış pozitif
        yalnızca bir manşet yuvasını başka habere bırakır (telafi edilebilir),
        yanlış negatif ise ÜST ÜSTE AYNI MANŞET demektir — kullanıcının sürekli
        bildirdiği arıza.

        ÖLÇÜLDÜ (2026-08-21): "Kritik Altyapılardaki Siemens PLC Cihazlarının
        Yapay Zekayla Hedeflenmesi" (08-20 manşeti) ile "ABD Kurumlarının
        Siemens S7 PLC Cihazlarına Yönelik Yapay Zeka Destekli Saldırı Uyarısı"
        (08-21) AYNI olaydır; `dedup.same_event` ikisini eşleştiriyor
        ('entity:siemens+topic=0.37') ama defter ILISKISIZ diyor — paylaşılan
        tek düşük dereceli kimlik (`ad:siemens`) MIN_ORTAK_AD=2 eşiğini
        geçmiyor, konu örtüşmesi de KONU_TEK_BASINA=0.42'nin altında kalıyor.
        Sonuç: Siemens PLC haberi üst üste iki gün manşet oldu.

        Bu yüzden defterin KENDİ eşiğini düşürmek yerine (gövde elemesinde
        gerileme yaratırdı) manşet sorgusuna çapraz-gün `same_event` yedeği
        eklendi. Gövde politikası DEĞİŞMEZ.
        """
        kayit, _ = self.esle(view)
        if kayit is not None:
            return kayit
        kimlik = olay_kimlikleri(view, self.sozluk)
        for k in self.kayitlar:
            if not k['manset_gunleri']:
                continue          # yalnızca manşet olmuş olaylar sorgulanır
            for gecmis_view in k['views']:
                # (a) TEK ortak olay kimliği + zayıf konu desteği yeter.
                #     Gövde elemesi MIN_ORTAK_AD=2 ister; orada tek bir ortak
                #     ad yanlış birleştirme üretiyordu. Manşet sorgusunda
                #     maliyet tersine döndüğü için eşik 1'e iner.
                if kimlik & olay_kimlikleri(gecmis_view, self.sozluk) and \
                        _konu_ortusmesi(view, gecmis_view) >= MANSET_TEK_KIMLIK_KONU:
                    return k
                # (b) Çapraz-gün same_event — kimlik çıkarımı boş kalsa bile
                #     kurum/ürün adı üzerinden eşleşmeyi yakalar.
                if dedup.same_event(view, gecmis_view, cross_day=True):
                    return k
        return None

    def manset_gunu_sayisi(self, view):
        """Bu görünümün olayı son günlerde kaç kez MANŞET olmuş?

        Faz 2 politikasının çekirdeği: 'mukerrer' bayrağı gibi kaba bir yasak
        yerine ÖLÇÜLEN bir gerçek. Olay hiç manşet olmamışsa 0 döner ve haber
        puanına göre serbestçe manşete çıkabilir."""
        kayit = self._manset_kaydi(view)
        return len(kayit['manset_gunleri']) if kayit else 0

    def son_manset_gunu(self, view):
        kayit = self._manset_kaydi(view)
        if not kayit or not kayit['manset_gunleri']:
            return None
        return kayit['manset_gunleri'][-1]


def defter_kur(gunler, sozluk=None):
    """Kısayol: [(gun, views, manset_views), ...] → kurulmuş OlayDefteri."""
    return OlayDefteri(sozluk=sozluk).gunleri_isle(gunler)


# ── GÜN İÇİ KÜMELEME ─────────────────────────────────────────────────────────
def kumele(views_by_id, sozluk=None, ayni_gun=True, gevsek=False,
           explain=False):
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

    explain=True ise (gruplar, {id: gerekçe}) döner. GEREKÇE ŞART: kümeleme
    gölge modda gözlem için çalışıyor ve gözlemin tek amacı yanlış
    birleştirmeleri BULMAK. Yalnızca "şu ikisi birleşti" bilgisi bunu
    sağlamıyor — 2026-08-13'te gölge kayıt bir yanlış birleştirme gösterdi
    ama hangi sinyalin sebep olduğu kayıtlı olmadığı için olay kayıtlı
    veriyle yeniden üretilemedi (denetim canlı bellekteki tam metinlerle
    koşar; rapor_gecmis görünümleri kırpılmıştır).
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
    gruplar, gerekceler = [], {}
    for aid in views_by_id:
        view = views_by_id[aid]
        for grup in gruplar:
            temsilci = views_by_id[grup[0]]
            iliski, neden = iliski_belirle(view, temsilci, ayni_gun=ayni_gun,
                                           sozluk=sozluk, explain=True)
            if iliski in kabul:
                grup.append(aid)
                gerekceler[aid] = f'{iliski}: {neden}'
                break
        else:
            gruplar.append([aid])
    if explain:
        return gruplar, gerekceler
    return gruplar


# ─────────────────────────────────────────────────────────────────────────────
# TEK TANIM — "bu iki haber aynı olayı mı anlatıyor?"
# ─────────────────────────────────────────────────────────────────────────────
# NEDEN VAR (ölçülmüş kök neden): sistemde "aynı olay"ın ÜÇ ayrı tanımı vardı
# ve farklı katmanlar farklı tanımı kullanıyordu — `dedup.same_event`,
# buradaki dört değerli `iliski_belirle` ve LLM. Ölçüm (son 10 günün
# yayımlanmış raporları, çapraz-gün): same_event 32 çifti "aynı olay" sayıyor,
# dört değerli sınıflandırıcı bunların yalnızca 2'sine AYNI_GELISME diyor
# (25 YENI_GELISME, 5 ILISKISIZ). Politika yalnızca AYNI_GELISME'yi elediği
# için 30 çift rapora giriyordu. Denetim ise ÜÇÜNCÜ bir eşikle bakıyordu;
# "denetim kaçak buldu ama politika bulmadı" bu yüzden mümkündü.
#
# POLİTİKA KARARI (kullanıcı, 2026-08-24): aynı olay bir kez yayımlanır.
# "Yeni gelişme" olması onu mükerrer olmaktan ÇIKARMAZ — CameraSwarm'ın ertesi
# gün kamera sayısıyla dönmesi de mükerrerdir. Bu yüzden AYNI_GELISME ve
# YENI_GELISME'nin İKİSİ de mükerrer sayılır; ayrım yalnızca gerekçe için
# tutulur.
#
# İKİNCİ SİNYAL VE NEDEN FİLTRELENİYOR: kimlik çıkarımı bazı gerçek
# özdeşlikleri kaçırıyor (Siemens PLC 08-20↔08-21: tek ortak ad, MIN_ORTAK_AD=2
# eşiğinin altında). Bu boşluğu `dedup.same_event` kapatıyor — ama onun DF
# tabanlı jeneriklik filtresi YOK ve ölçümde şu sahte birleşmeleri üretiyor:
#   entity:cvss              → GitLab ↔ Citrix
#   entity:altyapı,güvenliğ  → iki farklı CISA duyurusu
#   codename-body:anssi      → Adobe ↔ Zimbra ↔ Microsoft bültenleri
#   codename-body:watchtowr  → MLflow ↔ GitLab (araştırmacı firma adı)
# Bu yüzden same_event'in gerekçesi AYRIŞTIRILIR ve dayandığı adlar sözlüğün
# ayırt-edicilik testinden geçmezse sinyal SAYILMAZ.

# Gerekçesi sözlükle doğrulanması gereken sinyaller (ad tabanlı olanlar).
_DOGRULANACAK_SINYAL = ('entity:', 'codename-body:')
# Doğrulama gerektirmeyenler: başlık düzeyi kod adı ve CVE yapısal kimliktir.
_GUVENILIR_SINYAL = ('codename:', 'actor:cve')


def _gerekce_adlari(gerekce):
    """'entity:cvss,siemens+topic=0.25' → ('entity:', {'cvss','siemens'})."""
    for on in _DOGRULANACAK_SINYAL:
        if gerekce.startswith(on):
            govde = gerekce[len(on):].split('+topic')[0]
            return on, {a.strip() for a in govde.split(',') if a.strip()}
    return None, set()


def aday_anahtarlari(view):
    """`ayni_olay`ın EŞLEŞEBİLMESİ için ortak olması gereken anahtar kümesi.

    KAYIPSIZ ÖN FİLTRE: ayni_olay'ın kabul ettiği DÖRT yolun dördü de ortak bir
    ad/kod adı/CVE/aktör gerektirir (entity, codename, codename-body,
    same_event:actor). Salt konu örtüşmesi ve salt başlık benzerliği zaten
    reddediliyor. Dolayısıyla bu kümelerin kesişimi BOŞSA ayni_olay kesinlikle
    False döner ve karşılaştırma hiç yapılmayabilir.

    Neden gerekli: bellek 7 günden 30 güne çıkınca karşılaştırma sayısı ~4
    katına çıkıyor (ölçüm: 38 haberlik gün × 527 geçmiş görünüm ≈ 20.000 çift,
    çift başına 1.57 ms → ~31 s). Ön filtre bunu ortak anahtarı olan birkaç
    yüz çifte indirir.
    """
    blob = _metin(view)
    return (dedup.extract_actors(blob)
            | dedup.extract_codenames(blob)
            | {a.lower() for a in (ozel_adlar(view)[0] | ozel_adlar(view)[1])})


def ayni_olay(a, b, sozluk=None, ayni_gun=False, explain=False):
    """Aynı olay mı? TEK tanım — kapı, eleme ve denetim bunu çağırır.

    TASARIM, ÖLÇÜMLE SEÇİLDİ (data/mukerrer_golden.json, 38 elle etiketli çift):
        same_event      27/38  kaçan=0   sahte=11
        dört-değerli    22/38  kaçan=2   sahte=14
        ikisinin OR'u   23/38  kaçan=0   sahte=15   ← daha KÖTÜ
    same_event 21 gerçek mükerrerin 21'ini de yakalıyor; tek sorunu sahte
    eşleşmeleri. Bu yüzden temel same_event'tir ve buradaki iş onu ELEMEKTİR,
    ikinci bir tanımla birleştirmek değil.

    Sahte eşleşmelerin ölçülen dağılımı ve elenme gerekçeleri:
      codename-body:anssi     ×6  Adobe↔Zimbra↔Microsoft↔Oracle CERT-FR bültenleri
      codename-body:watchtowr ×1  MLflow/FUXA ↔ GitLab (araştırmacı firma adı)
      entity:cvss             ×1  GitLab ↔ Citrix
      entity:altyapı,güvenliğ ×1  iki farklı CISA duyurusu
      trtitle-xday=0.77       ×1  Stripe API anahtarları ↔ AWS erişim anahtarları
      codename:tiktok         ×1  TikTok senatör baskısı ↔ TikTok gizlilik davası
    """
    def _ret(v, why=''):
        return (v, why) if explain else v

    tamam, gerekce = dedup.same_event(a, b, explain=True,
                                      cross_day=not ayni_gun)
    if not tamam:
        return _ret(False, '')

    # (1) GÖVDE KOD ADI + GÜÇLÜ KONU DESTEĞİ. Bu sinyal ilk ölçümde 11 sahte
    #     eşleşmenin 7'sini üretiyordu; kaynağı kurum (ANSSI) ve araştırmacı
    #     firma (watchTowr) adlarının gövdede kod adı sanılmasıydı. Onlar
    #     dedup.CODENAME_DENYLIST'e alındıktan sonra sinyal temizlendi, ama
    #     gövde düzeyi kod adı başlık düzeyinden daha zayıf olduğu için
    #     KONU KAPISI korunur (gerçek eşleşme jarservice: topic=0.48).
    if gerekce.startswith('codename-body:'):
        konu = _konu_ortusmesi(a, b)
        if konu < GOVDE_KOD_ADI_KONU_MIN:
            return _ret(False, '')
        return _ret(True, f'{gerekce}')

    # (2) SALT BAŞLIK BENZERLİĞİ YETMEZ. Ad taşımadığı için doğrulanamaz;
    #     ölçümde Stripe↔AWS anahtar ifşası çiftini birleştirdi.
    #
    #     TEK İSTİSNA — METİN NEREDEYSE AYNIYSA. Ad yokluğu "doğrulanamaz"
    #     demektir, "farklı" demek değildir; iki metin birbirinin kopyasıysa
    #     aynı olay olduğu ortadadır. ÖLÇÜLDÜ (mukerrer_golden, 38 çift):
    #     bu yoldan gelen İKİ sahte eşleşmenin konu örtüşmesi 0.27 ve 0.21;
    #     eşik 0.75 ikisini de dışarıda bırakır. Kural olmadan aynı olayın
    #     farklı sözcüklerle yazılmış ama gövdesi birebir aynı iki kopyası
    #     (ör. aynı paragrafın iki başlıkla dolaşması) mükerrer sayılmıyordu.
    if gerekce.startswith(('trtitle', 'topic=')):
        konu = _konu_ortusmesi(a, b)
        if konu >= KONU_KESINLIK:
            return _ret(True, f'konu-kesinlik={konu:.2f}')
        return _ret(False, '')

    # (3) ÖZEL AD SİNYALİ SÖZLÜKTEN GEÇMELİ. same_event'te DF tabanlı
    #     jeneriklik filtresi YOK; 'cvss', 'altyapı', 'güvenlik' gibi kökler
    #     olay kimliği sayılıyor.
    if gerekce.startswith('entity:'):
        adlar = {x.strip() for x in
                 gerekce[len('entity:'):].split('+topic')[0].split(',')
                 if x.strip()}
        if sozluk is not None:
            adlar = sozluk.ayirt_edici(adlar)
        adlar -= _MANSIZ_AD
        if not adlar:
            return _ret(False, '')
        return _ret(True, f'entity:{",".join(sorted(adlar))}')

    # (4) BAŞLIK KOD ADI + ASGARİ KONU DESTEĞİ. Kod adı güçlü sinyaldir ama
    #     şirket/platform adları da kod adı gibi çıkarılıyor: TikTok'a yönelik
    #     senatör baskısı ile TikTok gizlilik davası 'codename:tiktok' ile
    #     birleşti (konu örtüşmesi 0.03 — apayrı olaylar).
    if gerekce.startswith('codename:'):
        konu = _konu_ortusmesi(a, b)
        if konu < KOD_ADI_KONU_MIN:
            return _ret(False, '')
        return _ret(True, f'{gerekce}+topic={konu:.2f}')

    # (5) AKTÖR EŞLEŞMESİ TEK BAŞINA YETMEZ — aktör olayın FAİLİDİR, kimliği
    #     değil; aynı fail farklı olaylar yapar (bu modülün varlık sebebi,
    #     modül başlığı A maddesi). Bellek 30 güne çıkınca risk büyüyor: aynı
    #     APT 30 gün içinde birçok ayrı operasyonla görünüyor.
    #
    #     ÖLÇÜLDÜ (2026-08-24, geçmiş arşivden geri doldurulduktan sonra):
    #     "Mustang Panda'nın CoolClient'ı çekirdek rootkit'le güncellemesi"
    #     ile "Mustang Panda'nın QuickFox üzerinden tedarik zinciri saldırısı"
    #     'actor:mustangpanda+topic=0.19' ile eşleşti — apayrı iki operasyon.
    #     Gerçek eşleşmelerin konu örtüşmesi ölçümde 0.29-0.31 (APT36/PATCHCORD,
    #     UAT-10147); eşik ikisinin arasına konur.
    #     CVE MUAFTIR: 'actor:cve2026...' bir aktör değil, yapısal zafiyet
    #     kimliğidir; aynı CVE iki haberde geçiyorsa aynı zafiyettir.
    if gerekce.startswith('actor:') and not gerekce.startswith('actor:cve'):
        konu = _konu_ortusmesi(a, b)
        if konu < AKTOR_KONU_MIN:
            return _ret(False, '')
        return _ret(True, f'same_event:{gerekce}')

    return _ret(True, f'same_event:{gerekce}')


# ─────────────────────────────────────────────────────────────────────────────
# LLM HAKEMİ İÇİN ADAY SEÇİMİ
# ─────────────────────────────────────────────────────────────────────────────
# NEDEN VAR: deterministik `ayni_olay` YÜKSEK İSABETLİDİR (elle etiketli 38
# çiftte sahte=0) ama YAPISAL bir kör noktası vardır — ortak ad/kod adı/CVE
# taşımayan ya da konu örtüşmesi düşük kalan mükerrerleri göremez. Bunlar
# tipik olarak ÇAPRAZ-DİL çiftlerdir: İngilizce özgün haber ile Türkçe
# yeniden yazım aynı olayı anlatır ama sözcük örtüşmesi 0.05-0.19'da kalır.
#
# ÖLÇÜLDÜ (data/dedup_golden.json): 11 gerçek mükerrer çift bu yüzden
# kaçıyor — Mozilla GPG anahtarı (topic 0.05), Gunra fidye yazılımı (0.11),
# CEVA Logistics (0.12), DeadLock (0.14), Delta Wi-Fi (0.12), kötü amaçlı SIM
# (0.15), Deepfake sertifika dolandırıcısı (ORTAK ANAHTAR BİLE YOK).
#
# Çözüm: deterministik kesin olanları eler, KARARSIZ kalanların en olası
# birkaçı LLM'e sorulur. Bu fonksiyon "en olası birkaçı"nı seçer.

# Adaylık için asgari benzerlik. Altındakiler LLM'e hiç sorulmaz.
#
# BAĞLAYICI DEĞİLDİR — ölçüldü (2026-08-26, son 7 gün): ön elemeyi geçen 433
# çiftin yalnızca 9'u bu eşiğin altında kalıyor, çünkü `aday_benzerligi`
# 0-1 arası değil TOPLAMSAL bir puan döndürüyor (ortak ad sayısı + 3×konu +
# başlık) ve tipik çift 1.4-1.6 alıyor. Eşiği 0.30'a indirmek sorulan çift
# sayısını HİÇ değiştirmedi. Gerçek kapı `ADAY_UST_SINIR`dir.
ADAY_BENZERLIK_MIN = 0.55
# Haber başına LLM'e taşınacak en fazla geçmiş aday sayısı.
#
# 3 → 8 (2026-08-26). Asıl darboğaz buydu: deterministiğin kaçırdığı 9 gerçek
# mükerrerin 8'i ortak anahtar TAŞIYOR ve 1.36-6.36 puan alıyor, yani hepsi
# eşiği geçiyor; tek engelleri günün rekabetinde ilk 3'e girememeleriydi
# (CEVA Logistics 1.36 ile tipik yığının bile altında).
#
# MALİYET ÖLÇÜLDÜ (son 7 gün, rapora giren haberler üzerinden ×3.5 havuz
# tahminiyle): üst sınır 3 iken günde ~148 çift / ~12 çağrı, 8 iken ~418 çift
# / ~35 çağrı. Yani günde ~23 ek çağrı. 12'lik gruplar ve flash model ile bu,
# koşunun çeviri geçişlerinin yanında küçük kalıyor.
#
# Sınırsız YAPILMADI: aynı ölçümde üst sınır kaldırılınca günde ~8.855 çift /
# ~738 çağrı çıkıyor — 60 katı maliyet, üstelik ek çiftlerin ezici çoğunluğu
# puan yığınının dibinde ve alakasız.
ADAY_UST_SINIR = 8


def _ayirt_edici_ortak(ka, kb, sozluk):
    """İki anahtar kümesinin AYIRT EDİCİ kesişimi.

    Jenerik kökler ('şirket', 'zararlı', 'saldırgan', 'vulnerability')
    filtrelenmezse sıralama tamamen bozuluyor: ölçümde endüstriyel TSN
    protokolü haberi, 'vulnerab/analytic/operatio' ortaklığıyla yapay zeka
    ajanı haberinin üstüne çıktı.
    """
    ortak = {k for k in (ka & kb) if len(k) >= 5}
    if sozluk is not None:
        ortak = sozluk.ayirt_edici(ortak)
    return ortak - _MANSIZ_AD


def aday_benzerligi(a, b, ka=None, kb=None, sozluk=None):
    """0'dan büyük bir benzerlik puanı — LLM adaylarını SIRALAMAK için.

    Karar VERMEZ; yalnızca "hangi geçmiş kayıt bu habere en yakın" sorusunu
    ucuza cevaplar. Ağırlıklar ölçümle seçildi: gerçek mükerrerler bu puanda
    daima 1. sırada ve ikinciyle arasında belirgin fark var.
    """
    ka = aday_anahtarlari(a) if ka is None else ka
    kb = aday_anahtarlari(b) if kb is None else kb
    ortak = _ayirt_edici_ortak(ka, kb, sozluk)
    topic = _konu_ortusmesi(a, b)
    baslik = SequenceMatcher(
        None, (a.get('tr_title') or '').lower(),
        (b.get('tr_title') or '').lower()).ratio()
    return len(ortak) * 1.0 + topic * 3.0 + baslik * 1.0, ortak, topic


def llm_adaylari(view, gecmis_kayitlar, sozluk=None, ust_sinir=ADAY_UST_SINIR):
    """[(puan, kayit, ortak, topic), ...] — en olası geçmiş eşleşmeler.

    gecmis_kayitlar: [(anahtar_kümesi, herhangi_bir_etiket, view), ...]
    """
    ka = aday_anahtarlari(view)
    sirali = []
    for kb, etiket, ev in gecmis_kayitlar:
        puan, ortak, topic = aday_benzerligi(view, ev, ka, kb, sozluk)
        if puan >= ADAY_BENZERLIK_MIN:
            sirali.append((puan, etiket, ev, sorted(ortak), topic))
    sirali.sort(key=lambda x: -x[0])
    return sirali[:ust_sinir]


# ─────────────────────────────────────────────────────────────────────────────
# MÜKERRER KARARI — üç değerli
# ─────────────────────────────────────────────────────────────────────────────
# NEDEN ÜÇ DEĞERLİ: "aynı olay bir kez yayımlanır" kuralı tek başına ölçüldü
# ve raporu ÇÖKERTTİ. 2026-08-24 koşusu: günün en yüksek puanlı SEKİZ
# haberinin sekizi de mükerrer diye elendi (Mustang Panda 94, Fransa 678.000
# mükellef 94, UAT-10147 91, QUICSILVER 89...), rapor 10 habere düştü ve
# manşet ATM dolandırıcılığı + Uber para cezasından seçilmek zorunda kaldı.
#
# Sebep politikanın kendisi: büyük siber olaylar doğaları gereği GÜNLERCE
# sürer. "Bir kez yayımla" kuralı onları tamamen siler ve geriye yalnızca tek
# günlük küçük haberler kalır — yani kural, raporun kalitesini sistematik
# olarak düşürür.
#
# Ayrım şudur: kullanıcının şikâyet ettiği şey AYNI HABERİN TEKRAR TEKRAR
# GÖRÜNMESİ, özellikle MANŞETTE. Gerçekten yeni bir olgu getiren devam
# haberleri (yeni kurban sayısı, yeni CVE, yeni ülke, aktif istismara geçiş)
# okuyucu için değerlidir — ama manşet olmamalıdır.

TAM_MUKERRER = 'TAM_MUKERRER'      # aynı olay, yeni olgu YOK → rapordan çıkar
GELISME = 'GELISME'                # aynı olay, YENİ olgu var → gövde, manşet YASAK
FARKLI = 'FARKLI'                  # ayrı olaylar → serbest


# Haberin GİRİŞİNDE yeni bir olgu duyuran DURUM DEĞİŞİMİ işaretleri.
# `_yeni_gelisme_mi` yalnızca kimlik sınıfına (CVE/paket/kod adı/özel ad)
# bakar; oysa siber haberlerinde yeni olgu çoğu zaman bir SAYI ya da bir
# DURUM DEĞİŞİMİDİR. ÖLÇÜLDÜ (2026-08-24): "Fransa'da 678.000 mükellefin
# verisi sızdırıldı" haberi, 17 Ağustos'taki "yüz binlerce mükellef"
# haberiyle TAM_MUKERRER sayıldı — oysa 678.000 rakamı yeni bir olgudur.
_DURUM_DEGISIMI = (
    'aktif olarak istismar', 'aktif istismar', 'istismar edilmeye',
    'yama yayımla', 'yama yayınla', 'güncelleme yayımla', 'yamalanmış',
    'tutukla', 'gözaltı', 'dava açıl', 'iddianame', 'suçlama',
    'itiraf', 'üstlen', 'kabul et', 'istifa',
    'genişle', 'yayıl', 'artmış', 'yükselmiş', 'ikiye katla',
    'geri çek', 'iptal et', 'kapat', 'çökert', 'ele geçir',
    'exploited in the wild', 'now exploited', 'patch released',
    'arrested', 'indicted', 'charged', 'sentenced', 'resigned',
)
# Yeni sayı sayılmak için gereken en az basamak (yıllar hariç tutulur).
_SAYI_RE = re.compile(r'\b\d[\d.,]{2,}\b')


def _yeni_olgu_var(a, b):
    """A'nın GİRİŞİNDE, B'de olmayan bir SAYI ya da DURUM DEĞİŞİMİ var mı?"""
    ag = _metin(_giris_view(a)).lower()
    bt = _metin(b).lower()

    def _sayilar(metin):
        out = set()
        for h in _SAYI_RE.findall(metin):
            temiz = h.replace('.', '').replace(',', '')
            if len(temiz) < 3 or (len(temiz) == 4 and temiz.startswith('20')):
                continue          # yıl değil, anlamlı büyüklük aranıyor
            out.add(temiz)
        return out

    yeni_sayi = _sayilar(ag) - _sayilar(bt)
    if yeni_sayi:
        return True, f'yeni sayı: {sorted(yeni_sayi)[:3]}'
    for im in _DURUM_DEGISIMI:
        if im in ag and im not in bt:
            return True, f'durum değişimi: {im}'
    return False, ''


def mukerrer_karari(a, b, sozluk=None, ayni_gun=False, explain=False):
    """`a` (bugünkü haber) ile `b` (geçmiş/başka haber) arasındaki karar.

    Dönüş: TAM_MUKERRER | GELISME | FARKLI  (explain=True ise (karar, gerekçe))
    """
    tamam, neden = ayni_olay(a, b, sozluk=sozluk, ayni_gun=ayni_gun,
                             explain=True)
    if not tamam:
        return (FARKLI, '') if explain else FARKLI
    var, yeni = _yeni_gelisme_mi(a, b, sozluk or BOS_SOZLUK)
    if var:
        karar, ek = GELISME, f'{neden} yeni={yeni}'
    else:
        olgu, olgu_neden = _yeni_olgu_var(a, b)
        if olgu:
            karar, ek = GELISME, f'{neden} {olgu_neden}'
        else:
            karar, ek = TAM_MUKERRER, neden
    return (karar, ek) if explain else karar
