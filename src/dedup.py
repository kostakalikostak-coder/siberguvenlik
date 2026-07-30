"""
Aynı-olay (same-event) tespiti — KRİTİK 3 ve rapor genelinde mükerrer haberleri
DETERMİNİSTİK (LLM'den bağımsız) olarak engellemek için.

NEDEN AYRI BİR MODÜL:
main._filter_duplicates yalnızca HAM İngilizce başlık üzerinde çalışır
(SequenceMatcher / keyword-Jaccard / kod adı). Aynı olayı farklı sözcüklerle
anlatan iki kaynak haberini (ör. "Signal Recovery Keys" vs "Fake Support Texts")
eşik altında kaldığı için KAÇIRIR; bu haberler hem dedup'ı geçer hem de LLM
(Pass 1 eleme / Pass 4 top3) onları gözden kaçırırsa KRİTİK 3'e iki kez girebilir.

Bu modül, LLM üretimi ZENGİN Türkçe içerik (tr_title + paragraf) + ham metin
üzerinde, sözcük örtüşmesinden bağımsız güçlü sinyallerle çalışır:
  1) Ortak ayırt edici kampanya/kod adı (FortiBleed, SharkLoader...).
  2) Ortak yapısal tehdit-aktörü/CVE tanımlayıcısı (UNC5792, APT29, CVE-2026-1234)
     + konu örtüşmesi. (Aynı aktörün FARKLI saldırısını yanlışlıkla birleştirmemek
     için tek başına aktör örtüşmesi yetmez; konu da örtüşmeli.)
  3) Yüksek içerik (keyword-Jaccard) örtüşmesi.
  4) Türkçe başlık benzerliği (SequenceMatcher).

Hiçbiri ham veriye/LLM'e güvenmez; saf string işidir, kolayca test edilir.
"""
import re
from difflib import SequenceMatcher

# "X-as-a-Service" iş-modeli terimleri (PhaaS, RaaS, MaaS, CaaS, DaaS, XaaS...).
# CamelCase heuristiği bunları ("PhaaS" → küçük→büyük geçişi 'a'→'S') yanlışlıkla
# AYIRT EDİCİ kod adı sanıp FARKLI olayları (ör. iki ayrı kimlik-avı operasyonu)
# birleştiriyordu. Bunlar jenerik kategori adlarıdır, bir olayın parmak izi
# DEĞİLDİR; kod adı çıkarımında elenir. (RaaS/MaaS <5 harf zaten uzunlukta
# elenir; asıl kaçak ≥5 harfli 'phaas' idi. Desen hepsini kapsar.)
_GENERIC_AAS_RE = re.compile(r'^[a-z]{1,4}aas$')

# Yaygın vendor/ürün adları — tek başına "aynı olay" sinyali DEĞİLDİR; kod adı
# sayılmaz. (main.config._CODENAME_DENYLIST ile aynı liste; tek kaynak burada.)
CODENAME_DENYLIST = {
    'fortigate', 'fortinet', 'fortios', 'fortisandbox', 'fortiweb', 'fortimanager',
    'windows', 'microsoft', 'macos', 'ipados', 'iphone', 'iphones', 'ipad', 'ipads',
    'github', 'gitlab', 'linkedin', 'whatsapp', 'youtube', 'facebook', 'instagram',
    'openai', 'chatgpt', 'powershell', 'javascript', 'typescript', 'nodejs',
    'wordpress', 'bleepingcomputer', 'crowdstrike', 'virustotal', 'cloudflare',
    'paypal', 'mongodb', 'postgresql', 'mysql', 'kubernetes', 'dropbox', 'onedrive',
    'sharepoint', 'teamviewer', 'anydesk', 'lastpass', 'bitlocker', 'sentinelone',
    'sonicwall', 'paloalto', 'checkpoint', 'proofpoint', 'mimecast', 'manageengine',
    'autogen', 'deepseek', 'blackberry', 'quickbooks', 'salesforce', 'servicenow',
    'pytorch', 'tensorflow', 'macbook', 'airpods', 'playstation',
    # TR/sık geçen ek gürültü
    'cobalt', 'anyconnect', 'cloudstrike', 'androidos',
}


# ALL-CAPS kod adı çıkarımında (LONGLEASH, DCRAT...) kod adı SAYILMAYAN yaygın
# akronim / jenerik büyük-harf sözcükler. Çapraz-gün dedup "ortak kod adı"nı
# tek başına AYNI OLAY sayar; bu yüzden iki farklı haberde de geçebilen ortak
# akronimlerin (RANSOM, THREAT, HTTPS...) yanlış-pozitif üretmesi engellenir.
# <5 harfli akronimler (CVE, FBI, NATO, EDR, VPN, DNS, SQL...) zaten uzunluk
# eşiğinde elenir; buraya yalnızca ≥5 olanlar gerekir.
_ACRONYM_DENYLIST = {
    # güvenlik/teknik akronim + jenerik büyük-harf sözcükler (EN)
    'https', 'oauth', 'mitre', 'owasp', 'hipaa', 'ransom', 'malware', 'threat',
    'attack', 'exploit', 'botnet', 'phish', 'alert', 'update', 'report',
    'breaking', 'notice', 'warning', 'critical', 'advisory', 'bulletin',
    'security', 'privacy', 'network', 'server', 'router', 'backdoor', 'trojan',
    'spyware', 'stealer', 'loader', 'ransomware', 'breach', 'leaked', 'hacked',
    # kurum / satıcı / ülke (ALL-CAPS yazıldığında)
    'linux', 'chrome', 'google', 'apple', 'adobe', 'cisco', 'oracle', 'azure',
    'intel', 'nvidia', 'amazon', 'nginx', 'apache', 'ubuntu', 'debian',
    'europol', 'interpol', 'russia', 'china', 'iran', 'korea', 'ukraine',
    # TR jenerik büyük-harf sözcükler
    'siber', 'guvenlik', 'saldiri', 'zararli', 'yazilim', 'devlet', 'hukumet',
    'kurum', 'rapor', 'uyari', 'turkiye',
}


def extract_codenames(text):
    """Metinden ayırt edici kampanya/operasyon/zararlı kod adlarını çıkarır.

    İki heuristik (ikisi de ≥5 karakter, CODENAME/ACRONYM denylist hariç):
      • CamelCase — küçük→büyük harf geçişi (FortiBleed, SharkLoader).
      • ALL-CAPS  — tümü büyük harf, salt harf (LONGLEASH, DOGLEASH, MARKIRAT,
        DCRAT). Tehdit istihbaratında zararlı/operasyon adları çoğu kez tümüyle
        büyük harf yazılır; yalnızca CamelCase aramak bunları kaçırıyordu.
    Bunlar nadir ve olaya özgüdür; aynı olayı farklı sözcüklerle anlatan
    haberleri bağlamak için güçlü sinyaldir."""
    out = set()
    for w in re.findall(r'[A-Za-z][A-Za-z0-9]+', text or ''):
        lw = w.lower()
        if len(w) < 5 or lw in CODENAME_DENYLIST or lw in _ACRONYM_DENYLIST:
            continue
        if _GENERIC_AAS_RE.match(lw):   # PhaaS/RaaS/MaaS... jenerik, kod adı değil
            continue
        is_camel   = re.search(r'[a-z][A-Z]', w)
        is_allcaps = w.isupper() and w.isalpha()
        if is_camel or is_allcaps:
            out.add(lw)
    return out


# Yapısal tehdit-aktörü / zafiyet tanımlayıcıları — bir olayın çok güçlü
# "parmak izi"dir. Aynı tanımlayıcı iki haberde de geçiyorsa büyük olasılıkla
# aynı kampanya/zafiyettir (konu örtüşmesiyle birlikte değerlendirilir).
_ACTOR_ID_RE = re.compile(
    r'\b(?:'
    r'UNC\d{3,5}'            # Mandiant uncategorized (UNC5792)
    r'|UAT-?\d{3,5}'         # Cisco Talos untargeted/actor (UAT-7810, UAT-5918)
    r'|UAC-\d{3,4}'          # CERT-UA (UAC-0185)
    r'|TAG-\d{2,4}'          # Google TAG (TAG-110)
    r'|CL-(?:STA|CRI|UNK)-\d{3,4}'  # Unit42 cluster (CL-STA-0048)
    r'|DEV-\d{3,5}'          # Microsoft eski (DEV-0537)
    r'|STORM-\d{3,5}'        # Microsoft (Storm-2077)
    r'|APT[\s-]?\d{1,3}'     # APT29, APT 41
    r'|TA\d{3,4}'            # Proofpoint (TA505)
    r'|FIN\d{1,2}'           # FIN7
    r'|CVE-\d{4}-\d{4,7}'    # zafiyet
    r'|GHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}'  # GitHub advisory
    r')\b',
    re.IGNORECASE,
)

# Trend Micro aktör deseni (Water/Earth/Void + Özel-ad): "Earth Lusca",
# "Water Curupira", "Void Rabisu". _ACTOR_ID_RE IGNORECASE olduğu için buraya
# konamaz (jenerik "water/earth/void" sözcükleriyle yanlış eşleşirdi); bu yüzden
# BÜYÜK/küçük-harfe DUYARLI ayrı desen: yalnızca "Water Xxxx" gibi Özel-ad kalıbı.
_TREND_ACTOR_RE = re.compile(r'\b(?:Water|Earth|Void) [A-Z][a-z]{3,}\b')

# Sektör-standardı aktör adlandırma taksonomisi: <Özel-ad> <ülke/motivasyon eki>.
# CrowdStrike (Bear=Rusya, Panda=Çin, Kitten=İran, Chollima=K.Kore, Spider=suç),
# Microsoft (Blizzard=Rusya, Typhoon=Çin, Sandstorm=İran, Sleet=K.Kore,
# Tempest=suç) ve benzerleri sürekli YENİ ad üretir; elle tutulan _NAMED_ACTORS
# listesi kaçınılmaz olarak geride kalır. Gerçek maliyeti 2026-07-29'da görüldü:
# "Laundry Bear" listede olmadığı için AYNI olay 24 ve 29 Temmuz'da iki kez
# KRİTİK 3 manşeti oldu (çapraz-gün dedup ortak aktör bulamadı). Desen listeyi
# TAMAMLAR, yerini almaz.
#
# Son ek Baş-harfi büyük VEYA tümü büyük olmalı; böylece jenerik akış metni
# ("the bear", "a storm") eşleşmez. Önekte [A-Z][A-Za-z]{2,} hem "Laundry" hem
# "LAUNDRY" biçimini yakalar — 24 Temmuz kaydı ALL-CAPS, 29 Temmuz kaydı
# Başlık-Düzeni yazılmıştı ve eski kod bu yüzden ikisini eşleştirememişti.
# Not: bare "Storm" Microsoft'un numaralı geçici adlarında (Storm-1234) zaten
# _ACTOR_ID_RE ile yakalanır; buradaki karşılığı "Pawn Storm"/"Dark Storm" gibi
# adlandırılmış gruplar içindir. 'Hawk' bilinçli olarak YOK: gerçek arşiv
# taramasında yalnızca "The HAWK"/"Direct HAWK" gibi çöp üretti.
_ACTOR_TAXONOMY_SUFFIXES = (
    'Bear|Panda|Kitten|Chollima|Spider|Jackal|Buffalo|Tiger|Leopard|Crane|Lynx'
    '|Blizzard|Typhoon|Sandstorm|Sleet|Tempest|Cyclone|Hail|Dust|Flood|Rain'
    '|Storm|Wolf|Dragon|Serpent|Scorpion'
)
_TAXONOMY_ACTOR_RE = re.compile(
    r'\b([A-Z][A-Za-z]{2,})\s+(?:' + _ACTOR_TAXONOMY_SUFFIXES + '|'
    + _ACTOR_TAXONOMY_SUFFIXES.upper() + r')\b'
)

# Taksonomi desenine UYAN ama tehdit aktörü OLMAYAN adlar (güvenlik satıcısı,
# ürün, jenerik tamlama). Arşivin tamamı taranarak belirlendi; "Arctic Wolf"
# özellikle önemli: bir satıcı adı olarak 15 kez geçiyor ve elenmezse alakasız
# iki haberi "ortak aktör" sayıp yanlış birleştirebilirdi.
_TAXONOMY_DENYLIST = frozenset({
    'arcticwolf', 'comododragon', 'chinesedragon', 'operationdragon',
})

# Adlandırılmış aktör/operasyon takma adları (regex'e uymayanlar). Substring
# olarak aranır; düşük kelimeli ortak adlar bilinçli olarak listelenmemiştir.
_NAMED_ACTORS = (
    'star blizzard', 'sandworm', 'lazarus', 'fancy bear', 'cozy bear',
    'midnight blizzard', 'cozy', 'turla', 'kimsuky', 'andariel', 'kontti',
    'salt typhoon', 'volt typhoon', 'flax typhoon', 'silk typhoon',
    'lockbit', 'blackcat', 'alphv', 'cl0p', 'clop', 'scattered spider',
    'shinyhunters', 'fin7', 'wizard spider', 'gamaredon', 'mustang panda',
    'charming kitten', 'apt28', 'apt29', 'apt40', 'apt41',
    # Paralı-asker (mercenary) casus yazılım aileleri + satıcıları. Belirli bir
    # ürün/aile adı, aynı olayın çok güçlü parmak izidir (ör. "Pegasus" iki
    # haberde de geçiyorsa büyük olasılıkla aynı kampanya/vaka). Yaygın sözcükle
    # karışan jenerik adlar (graphite/paragon) BİLİNÇLİ olarak dışarıda; yalnızca
    # ayırt edici, tek-anlamlı adlar. (Rule 2 yine konu örtüşmesiyle birlikte
    # değerlendirir; tek başına aktör yetmez.)
    'pegasus', 'nso group', 'intellexa', 'predator', 'candiru', 'cytrox',
    'quadream', 'finfisher',
)


def extract_actors(text):
    """Metindeki tüm yapısal + adlandırılmış tehdit-aktörü/zafiyet kimliklerini
    normalize edilmiş bir kümeye çıkarır (boşluk/tire silinir, küçük harf)."""
    raw = text or ''
    blob = raw.lower()
    out = set()
    for m in _ACTOR_ID_RE.findall(blob):
        out.add(re.sub(r'[\s-]', '', m.lower()))
    for name in _NAMED_ACTORS:
        if name in blob:
            out.add(name.replace(' ', ''))
    # Trend Micro deseni yalnızca orijinal (büyük/küçük-harf korunmuş) metinde
    for m in _TREND_ACTOR_RE.findall(raw):
        out.add(re.sub(r'[\s-]', '', m.lower()))
    # Taksonomi deseni de büyük/küçük-harfe duyarlıdır → ham metinde aranır.
    for m in _TAXONOMY_ACTOR_RE.finditer(raw):
        norm = re.sub(r'[\s-]', '', m.group(0).lower())
        if norm not in _TAXONOMY_DENYLIST:
            out.add(norm)
    return out


# Konu örtüşmesi (keyword-Jaccard) için elenecek sık sözcükler (TR + EN).
_STOPWORDS = {
    # TR
    've', 'ile', 'bir', 'bu', 'şu', 'için', 'olan', 'olarak', 'gibi', 'daha',
    'çok', 'ancak', 'ası', 'göre', 'kadar', 'sonra', 'önce', 'her', 'tüm',
    'veya', 'ya', 'de', 'da', 'ki', 'ise', 'hem', 'ne', 'en', 'ait', 'üzere',
    'tarafından', 'arasında', 'içinde', 'üzerinde', 'yönelik', 'karşı',
    'edilmiştir', 'edildiği', 'olduğu', 'olduğunu', 'belirtilmektedir',
    'bildirilmektedir', 'yapılmıştır', 'etmiştir', 'etmektedir', 'açıklamıştır',
    'duyurmuştur', 'tespit', 'söz', 'konusu', 'ayrıca', 'ilgili', 'amacıyla',
    # EN
    'the', 'and', 'of', 'to', 'in', 'a', 'an', 'is', 'are', 'for', 'on', 'by',
    'with', 'as', 'at', 'from', 'that', 'this', 'it', 'its', 'has', 'have',
    'was', 'were', 'be', 'been', 'or', 'into', 'their', 'they', 'which', 'said',
    'new', 'also', 'using', 'used', 'use', 'after', 'over', 'than', 'who',
}


def event_keywords(text, limit=None):
    """Metni konu-örtüşmesi karşılaştırması için sadeleştirilmiş bir köke-indirgenmiş
    anahtar-kelime kümesine çevirir: küçük harf, noktalama atılır, stop-word ve
    kısa (<4) token'lar elenir, her token ilk 5 karaktere köklenir.

    limit verilirse yalnızca metnin BAŞINDAN itibaren ilk `limit` AYIRT EDİCİ
    (distinct) kök alınır. Haber metinleri ters-piramit yapısındadır (kilit
    olgular başta yoğunlaşır); uzun paragraflarda Jaccard, kuyruğa doğru dağılan
    ayrıntılarla SEYRELİR ve aynı olay eşik altında kalabilir. Baş-pencere,
    aynı olayın ortak çekirdeğini yakalayıp bu seyrelmeyi telafi eder."""
    blob = (text or '').lower()
    # CVE/aktör kimlikleri konu örtüşmesinde gürültü yapmasın (ayrı sinyal)
    blob = _ACTOR_ID_RE.sub(' ', blob)
    tokens = re.findall(r'[0-9a-zçğıöşü]+', blob, re.IGNORECASE)
    out = set()
    for t in tokens:
        if len(t) < 4 or t in _STOPWORDS:
            continue
        out.add(t[:5])
        if limit is not None and len(out) >= limit:
            break
    return out


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# ── Eşikler (bugünkü gerçek veriyle doğrulandı; bkz. tests/test_dedup.py) ──
_TOPIC_WITH_ACTOR = 0.10   # ortak aktör/CVE varsa düşük konu örtüşmesi yeter
_TOPIC_ALONE      = 0.42   # tek başına yüksek konu örtüşmesi
_TRTITLE_RATIO    = 0.62   # Türkçe başlık benzerliği
# Baş-pencere konu örtüşmesi için token sayısı (event_keywords limit). Uzun
# paragraflarda Jaccard seyrelmesini telafi eder (bkz. event_keywords docstring).
# Gerçek veriyle: aynı olayda baş-pencere ~0.43-0.48, farklı olayda ≤0.25.
_TOPIC_LEAD_TOKENS = 40

# ── Çapraz-GÜN eşikleri (cross_day=True) ──────────────────────────────────
# Aynı run içi karşılaştırmada aday havuzu küçük olduğu için gevşek Kural 4
# (saf TR başlık SequenceMatcher) sorun çıkarmaz. Ancak GÜNLER ARASI 43'er
# haberlik bloklarda jenerik Türkçe başlık kalıpları ("...Ele Geçirmesi",
# "...Kritik Güvenlik Açığı") yanlışlıkla eşleşip FARKLI olayları aynı sayar.
# Bu yüzden çapraz-günde Kural 4 DEVRE DIŞIDIR ve aktör+konu eşiği yükseltilir;
# yalnızca YÜKSEK ÖZGÜLLÜKTE sinyaller (ortak kod adı / ortak aktör+konu /
# yüksek konu örtüşmesi) kullanılır. Gerçek arşiv verisiyle doğrulandı.
_TOPIC_WITH_ACTOR_XDAY = 0.18

# ── Çapraz-GÜN birleşik başlık+konu sinyali (Kural 5) ─────────────────────
# Kural 4 (saf TR başlık benzerliği) çapraz-günde KAPALIDIR çünkü tek başına
# jenerik kalıplarda yanlış eşleşir. Ama iki sinyal BİRLİKTE kullanıldığında
# (Kural 2'nin "aktör + konu" felsefesi) ayrım yapılabilir hâle gelir.
# Eşikler gerçek kritik3/rapor geçmişi taranarak seçildi (2026-07-30 ölçümü).
_TRTITLE_XDAY            = 0.74   # başlık benzerliği alt sınırı
_TOPIC_WITH_TRTITLE_XDAY = 0.20   # eşlik etmesi gereken asgari konu örtüşmesi


def _bundle(view):
    """Bir haber 'görünümü'nü (tr_title/paragraph/title/full_text) metin
    bileşenlerine ayırır. Eksik alanlar boş string olur."""
    tr_title  = (view.get('tr_title') or '').strip()
    paragraph = (view.get('paragraph') or '').strip()
    en_title  = (view.get('title') or '').strip()
    full_text = (view.get('full_text') or '')[:2000]
    head_title = tr_title or en_title
    return head_title, paragraph, en_title, full_text


def same_event(view_a, view_b, explain=False, cross_day=False):
    """İki haber aynı olayı/kampanyayı/zafiyeti mi anlatıyor? (deterministik)

    view_*: {'tr_title','paragraph','title','full_text'} (eksik alanlar boş kabul).
    explain=True ise (bool, gerekçe) döner; aksi halde yalnızca bool.
    cross_day=True ise YALNIZCA yüksek-özgüllükte sinyaller kullanılır: gevşek
    TR başlık benzerliği (Kural 4) DEVRE DIŞI bırakılır ve aktör+konu eşiği
    yükseltilir. Günler-arası karşılaştırmada yanlış-pozitifi (jenerik Türkçe
    başlık kalıpları) önlemek için (bkz. _TOPIC_WITH_ACTOR_XDAY).
    """
    ha, pa, ea, fa = _bundle(view_a)
    hb, pb, eb, fb = _bundle(view_b)
    blob_a = ' '.join((ha, pa, ea, fa))
    blob_b = ' '.join((hb, pb, eb, fb))

    def _ret(val, why=''):
        return (val, why) if explain else val

    # 1) Ortak ayırt edici kod adı (başlık + TR başlık)
    ca = extract_codenames(ha + ' ' + ea)
    cb = extract_codenames(hb + ' ' + eb)
    shared_cn = ca & cb
    if shared_cn:
        return _ret(True, 'codename:' + ','.join(sorted(shared_cn)))

    # Konu örtüşmesi: paragraf VEYA (başlık+ham metin) üzerinden en yükseği.
    # Ayrıca paragrafın BAŞ-PENCERESİ (ilk _TOPIC_LEAD_TOKENS kök): uzun aynı-olay
    # paragraflarında tam-metin Jaccard'ı seyrelip eşik altında kaldığında
    # (kuyruğa dağılan farklı ayrıntılar) ortak çekirdeği yakalar.
    topic = max(
        _jaccard(event_keywords(pa), event_keywords(pb)),
        _jaccard(event_keywords(blob_a), event_keywords(blob_b)),
        _jaccard(event_keywords(pa, limit=_TOPIC_LEAD_TOKENS),
                 event_keywords(pb, limit=_TOPIC_LEAD_TOKENS)),
    )

    # 2) Ortak yapısal/adlandırılmış aktör veya CVE + konu örtüşmesi
    actors_a, actors_b = extract_actors(blob_a), extract_actors(blob_b)
    shared_actors = actors_a & actors_b
    actor_topic_min = _TOPIC_WITH_ACTOR_XDAY if cross_day else _TOPIC_WITH_ACTOR
    if shared_actors and topic >= actor_topic_min:
        return _ret(True, f'actor:{",".join(sorted(shared_actors))}+topic={topic:.2f}')

    # 2c) GÖVDEDE ortak kod adı (başlıkta olmasa da) + konu örtüşmesi. Aynı
    #     zararlı/operasyon adı (LONGLEASH, DcRAT...) iki haberin metninde geçip
    #     konu da örtüşüyorsa aynı olaydır. Topic-kapılı olduğu için (aktör
    #     kuralıyla aynı felsefe) yanlış-birleştirme riski düşük; başlıkta ortak
    #     kod adı zaten Kural 1'de topic'siz yakalanır — bu, başlıkları farklı
    #     sözcüklerle yazılmış aynı-zararlı haberleri kurtarır.
    shared_cn_body = extract_codenames(blob_a) & extract_codenames(blob_b)
    if shared_cn_body and topic >= actor_topic_min:
        return _ret(True, f'codename-body:{",".join(sorted(shared_cn_body))}+topic={topic:.2f}')

    # 2b) Her iki haberde de yapısal kimlik (CVE/aktör) var ama ORTAK YOK →
    #     farklı olay. (Farklı CVE = farklı zafiyet; main._keyword_jaccard ile
    #     aynı felsefe.) Bu, "CVE-2026-XXXX Açığı" gibi kalıp başlıkların
    #     SequenceMatcher'da yanlışlıkla eşleşmesini (rule 4) engeller.
    if actors_a and actors_b and not shared_actors:
        return _ret(False, '')

    # 3) Yüksek içerik örtüşmesi tek başına
    if topic >= _TOPIC_ALONE:
        return _ret(True, f'topic={topic:.2f}')

    # 5) ÇAPRAZ-GÜN: yüksek TR-başlık benzerliği + ANLAMLI konu örtüşmesi.
    #    Bu boşluk üretimde ölçüldü (2026-07-30): "İran Bağlantılı Siber Tehdit
    #    Aktörlerinin ABD ... Su ve Enerji ..." haberi 24 ve 26 Temmuz'da iki kez
    #    KRİTİK 3 manşeti oldu. Aktör/kod adı çıkmıyor (İran yapısal bir aktör
    #    kimliği değil), konu örtüşmesi 0.29 ile _TOPIC_ALONE'un altında, başlık
    #    benzerliği ise 0.79 — yani güçlü sinyal VARDI ama Kural 4 çapraz-günde
    #    kapalı olduğu için kullanılmıyordu.
    #
    #    Eşikler gerçek geçmiş taranarak seçildi. Yakalananlar: İran su/enerji
    #    24-26 (0.79/0.33), Europol The Com 25-26 (0.76/0.22). Elenen
    #    yanlış-pozitifler — hepsi "... Kritik Güvenlik Açığının Aktif Olarak
    #    İstismar Edilmesi" jenerik kalıbından: ServiceNow↔SharePoint (0.73/0.26),
    #    Fastjson↔SharePoint (0.66/0.19), Bing↔SharePoint (0.65/0.16) ve
    #    İran→Minnesota (0.65/0.18 — bu bir GELİŞME/atıf haberi, mükerrer değil).
    #
    #    Stadler 23-24 (0.67/0.38) bilinçli olarak YAKALANMIYOR: eşiği oraya
    #    çekmek ServiceNow↔SharePoint'i de birleştirirdi. Kaçan bir mükerrer,
    #    birleştirilen iki FARKLI haberden daha az zararlıdır.
    #    NOT: Kural 2b yukarıda zaten devrede — iki haberde de yapısal kimlik
    #    (CVE/aktör) varsa ve ORTAK değilse buraya hiç gelinmez.
    if cross_day and ha and hb:
        ratio = SequenceMatcher(None, ha.lower(), hb.lower()).ratio()
        if ratio >= _TRTITLE_XDAY and topic >= _TOPIC_WITH_TRTITLE_XDAY:
            return _ret(True, f'trtitle-xday={ratio:.2f}+topic={topic:.2f}')

    # 4) Türkçe başlık benzerliği — yalnızca AYNI RUN içinde. Çapraz-günde
    #    jenerik TR başlık kalıpları yanlış-pozitif ürettiği için atlanır.
    if not cross_day and ha and hb:
        ratio = SequenceMatcher(None, ha.lower(), hb.lower()).ratio()
        if ratio >= _TRTITLE_RATIO:
            return _ret(True, f'trtitle={ratio:.2f}')

    return _ret(False, '')


def nearmiss_signal(view_a, view_b, cross_day=True):
    """Gözlem amaçlı: iki haber ORTAK bir parmak izi (aktör-ID veya kod adı)
    paylaşıyor AMA same_event yine de AYNI OLAY demiyorsa (konu örtüşmesi eşiğin
    altında kaldığı için), bunu açıklayan bir dize döndürür; aksi halde None.

    Amaç, sessiz kaçışları (aynı olayın farklı gün tekrar seçilmesi gibi)
    veriyle görünür kılmaktır — davranışı DEĞİŞTİRMEZ, yalnızca raporlanır."""
    if same_event(view_a, view_b, cross_day=cross_day):
        return None
    ha, pa, ea, fa = _bundle(view_a)
    hb, pb, eb, fb = _bundle(view_b)
    blob_a = ' '.join((ha, pa, ea, fa))
    blob_b = ' '.join((hb, pb, eb, fb))
    shared = (extract_actors(blob_a) & extract_actors(blob_b)) | \
             (extract_codenames(blob_a) & extract_codenames(blob_b))
    if not shared:
        return None
    topic = max(
        _jaccard(event_keywords(pa), event_keywords(pb)),
        _jaccard(event_keywords(blob_a), event_keywords(blob_b)),
        _jaccard(event_keywords(pa, limit=_TOPIC_LEAD_TOKENS),
                 event_keywords(pb, limit=_TOPIC_LEAD_TOKENS)),
    )
    return f'shared={",".join(sorted(shared))} topic={topic:.2f} (eşik altı)'


def pick_distinct(ordered_ids, get_view, n=3, exclude_views=None):
    """Sıralı aday listesinden, çiftler-arası AYNI-OLAY OLMAYAN en fazla n haber
    seçer (sıra korunur). KRİTİK 3 garantisinin çekirdeği.

    ordered_ids: öncelik sırasına dizili haber ID'leri (en iyi başta).
    get_view:    id -> {'tr_title','paragraph','title','full_text'} fonksiyonu.
    exclude_views: SON GÜNLERDE KRİTİK 3'e girmiş haberlerin görünüm listesi.
        Bunlardan biriyle aynı olayı anlatan aday ATLANIR (çapraz-gün, yüksek
        özgüllük: cross_day=True). Böylece aynı olay üst üste iki gün KRİTİK 3
        manşeti olamaz. None ise çapraz-gün kontrolü yapılmaz (eski davranış).
    Döndürür: seçilen ID listesi (≤ n).
    """
    excl = exclude_views or []
    picked = []
    for aid in ordered_ids:
        if aid in picked:
            continue
        view = get_view(aid)
        if any(same_event(view, ev, cross_day=True) for ev in excl):
            continue
        if any(same_event(view, get_view(p)) for p in picked):
            continue
        picked.append(aid)
        if len(picked) >= n:
            break
    return picked


def parse_cross_day_dupes(data, candidate_ids):
    """Çapraz-gün LLM yanıtından ({"duplicates":[...]}) yalnızca GEÇERLİ bugünkü
    aday ID'lerini içeren bir küme çıkarır. Bozuk/None yanıt → boş küme (güvenli:
    hiçbir haber elenmez). Pür fonksiyon; LLM'siz test edilir."""
    idset = set(candidate_ids)
    out = set()
    if not isinstance(data, dict):
        return out
    for x in (data.get('duplicates', []) or []):
        try:
            xi = int(x)
        except (TypeError, ValueError):
            continue
        if xi in idset:
            out.add(xi)
    return out


def drop_duplicates_against(candidate_ids, reference_ids, get_view):
    """reference_ids'teki herhangi bir haberle aynı-olay olan adayları (ve aday
    listesi içindeki kendi mükerrerlerini) eler. Sıra korunur.

    Gövde haberlerini KRİTİK 3 ile (ve birbirleriyle) tekilleştirmek için."""
    kept = []
    for aid in candidate_ids:
        if aid in kept or aid in reference_ids:
            continue
        view = get_view(aid)
        if any(same_event(view, get_view(r)) for r in reference_ids):
            continue
        if any(same_event(view, get_view(k)) for k in kept):
            continue
        kept.append(aid)
    return kept
