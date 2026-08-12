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
    # CERT/danışma-belgesi önekleri. _ADVISORY_ID_RE tam kimliği (CERTFR-2026-
    # AVI-0948) zaten metinden siler; bunlar önekin TEK BAŞINA (numarasız)
    # geçtiği durumlar için ikinci ağdır.
    'certfr', 'certeu', 'certua', 'certbund', 'icsa', 'icsma', 'msrc',
    # Spec / paket / protokol adları — CamelCase oldukları için kod adı
    # sanılıyorlardı. 2026-07-31: 'AsyncAPI' geçen bir haber SIRF bu yüzden
    # zafiyet_aktif_apt etiketini korudu (bkz. main._has_apt_evidence).
    'asyncapi', 'openapi', 'graphql', 'restapi', 'webassembly', 'websocket',
    'jsonschema', 'openssl', 'openssh', 'openstack', 'openshift',
    # TR jenerik büyük-harf sözcükler
    'siber', 'guvenlik', 'saldiri', 'zararli', 'yazilim', 'devlet', 'hukumet',
    'kurum', 'rapor', 'uyari', 'turkiye',
    # Windows/yetki bağlamının kaçınılmaz sözcükleri. ÖLÇÜLDÜ (2026-08-12,
    # scripts/dedup_olc.py): 'SYSTEM' o günün 79 ham haberinin 8'inde geçti
    # ("SYSTEM-level privileges" kalıbı) ve ShieldBreak Defender-atlatma PoC'si
    # ile Yama Salısı toplamını 'codename-body:system+topic=0.13' gerekçesiyle
    # AYNI OLAY saydırdı — iki alakasız haber birleşti. 'winsock' aynı gün 8
    # haberde geçti (tek bir CVE'nin bileşen adı, olay kimliği değil).
    'system', 'systems', 'winsock',
    # Haber sitesi adları: her alıntıda geçer, olayın parmak izi değildir.
    'techcrunch', 'bloomberg', 'reuters', 'wired',
    # Yaygın web/teknoloji bileşen adları (CamelCase oldukları için kod adı
    # sanılıyorlardı).
    'webgl', 'webrtc', 'webhook', 'webview',
}


# Danışma-belgesi (advisory) kimlikleri: CERT/ICS-CERT/satıcı bülten numaraları.
# Bunlar bir OLAYIN parmak izi DEĞİL, YAYIN NUMARASIDIR — her ANSSI bülteninde
# 'CERTFR' geçer. extract_codenames önce bu aralıkları metinden siler; aksi hâlde
# önek ALL-CAPS kod adı sanılır ve TÜM bültenler "aynı olay" olur.
# Ölçülen zarar (2026-07-31): Citrix XenServer ve Node.js bültenleri MS Edge
# bültenine 'codename-body:certfr' gerekçesiyle mükerrer sayılıp elendi; ardından
# çapa da çapraz-güne takılınca o günün DÖRT ANSSI bülteni birden rapordan düştü.
_ADVISORY_ID_RE = re.compile(
    r'\b(?:'
    r'CERT[\s-]?(?:FR|EU|UA|BUND|IN|EE)[\s-]?\d{4}[\s-]?[A-Z]{2,4}[\s-]?\d+'
    r'|ICSA?[\s-]?\d{2}[\s-]?\d{3}[\s-]?\d{2}[A-Z]?'   # ICSA-26-123-01
    r'|ICSMA[\s-]?\d{2}[\s-]?\d{3}[\s-]?\d{2}'
    r'|VU#\s?\d{4,}'                                    # CERT/CC
    r'|MSRC[\s-]?\d{4,}'
    r'|KB\d{6,}'                                        # Microsoft KB
    r'|VMSA[\s-]?\d{4}[\s-]?\d{4}'                      # VMware
    r'|RHSA[\s-]?\d{4}:\d+'                             # Red Hat
    r'|DSA[\s-]?\d{4}[\s-]?\d'                          # Debian
    r'|USN[\s-]?\d{4}[\s-]?\d'                          # Ubuntu
    r')\b',
    re.IGNORECASE,
)


# ── AÇIK KAYNAK PAKET ADLARI ────────────────────────────────────────────────
# extract_codenames CamelCase veya TÜMÜ-BÜYÜK (≥5 harf) arar. Paket ekosistem-
# lerinde (npm, PyPI, crates, RubyGems...) ad kuralı ise KÜÇÜK HARF ve çoğu kez
# kısadır: 'keyv', 'cacheable', 'chalk', 'lodash'. Yani bir tedarik zinciri
# olayının EN AYIRT EDİCİ tanımlayıcısı, kod adı çıkarımına tümüyle görünmezdi
# — aktör (APT adı) ve özel ad (büyük harfle başlayan) sinyalleri de bu adları
# yakalayamaz.
#
# 2026-08-06 vakası: keyv/cacheable npm solucanı 05-08'de KRİTİK 3 manşetiydi,
# 06-08'de yeniden manşet oldu. same_event(cross_day=True) False döndü çünkü
# codename/actor/entity kümelerinin ÜÇÜ DE boştu. Bu tek bir haberin kaçması
# değil, bütün bir haber sınıfının (paket tedarik zinciri — tam da günlerce
# süren ve tekrar tekrar manşet olan sınıf) korumasız kalmasıydı.
#
# Yanlış-pozitif riski, iki koşulla sınırlanır:
#   • Metinde ekosistem işareti bulunmalı (npm/PyPI/paket/registry...).
#   • Aday, o işaretin YAKININDA (±_PKG_WINDOW karakter) geçmeli.
# Ayrıca same_event'te tek başına değil KONU ÖRTÜŞMESİNE KAPILI kullanılır
# (kod adı gövde kuralıyla aynı felsefe).
_PKG_ECOSYSTEM_RE = re.compile(
    r'\b(?:npm|pypi|pip|packagist|composer|nuget|rubygems|crates|cargo|'
    r'maven|gradle|homebrew|vsix|paket|paketi|paketine|paketleri|paketlerine|'
    r'paketlerinin|kütüphane|kütüphanesi|kütüphaneleri|registry|repository|'
    r'depo|deposu)\b',
    re.I)
_PKG_WINDOW = 90

# Aday token deseni. İKİ ŞART kritik (ilk ölçümde ikisi de eksikti ve kural
# kullanılamaz derecede gürültülüydü — 9 eşleşmenin 8'i yanlış pozitifti):
#   • TÜRKÇE HARF SINIRI: [a-z] sınıfı ı/ş/ğ/ü/ö/ç içermediği için "solucanı"
#     ortasından 'olucan', "saldırı"dan 'sald' gibi SAHTE token'lar çıkıyordu.
#     Lookbehind/lookahead Türkçe harfleri de kapsar → token ancak gerçek sözcük
#     sınırında başlar/biter, Türkçe sözcüğün ortasından parça alınmaz.
#   • BÜYÜK HARF DE SINIRDIR: "Paketlerine" içindeki 'aketlerine' engellenir.
# Paket adları kural gereği ASCII'dir; Türkçe harf içeren aday zaten paket adı
# değildir ve desene uymaz.
_TR_LETTER = 'A-Za-zçğıöşüÇĞİÖŞÜ'
_PKG_TOKEN_RE = re.compile(
    r'(?<![' + _TR_LETTER + r'0-9@])'
    r'@?[a-z][a-z0-9]*(?:[-_./][a-z0-9]+)*'
    r'(?![' + _TR_LETTER + r'0-9])')

# Paket kuralı için AYRI (ve yüksek) konu eşiği. Aktör/kod adı eşiği (0.14)
# burada yetmiyor: paket adı, kampanya kod adına göre daha zayıf bir kimliktir
# (aynı ekosistemden iki ayrı olay 'npm' bağlamını paylaşır). Ölçümde yanlış
# pozitiflerin tamamı topic≤0.18'de, gerçek eşleşme ise 0.24'te toplandı.
_TOPIC_WITH_PACKAGE = 0.22

# Ekosistem yakınında sık geçen ama paket ADI olmayan sözcükler.
_PKG_DENYLIST = {
    'package', 'packages', 'paket', 'paketi', 'paketler', 'paketleri', 'registry',
    'malicious', 'version', 'versions', 'install', 'installed', 'download',
    'downloads', 'library', 'libraries', 'module', 'modules', 'supply', 'chain',
    'attack', 'attacks', 'worm', 'malware', 'compromised', 'developer',
    'developers', 'security', 'researchers', 'account', 'accounts', 'maintainer',
    'maintainers', 'token', 'tokens', 'credential', 'credentials', 'published',
    'release', 'releases', 'ecosystem', 'yazilim', 'yazılım', 'zararlı', 'zararli',
    'saldırı', 'saldiri', 'sürüm', 'surum', 'sürümleri', 'yayınlanan', 'kullanılan',
    'güvenlik', 'guvenlik', 'araştırmacı', 'arastirmaci', 'hesapları', 'hesaplari',
    'solucan', 'solucanı', 'solucani', 'ekosistem', 'ekosisteminde', 'yönelik',
    'yonelik', 'yüzlerce', 'yuzlerce', 'binlerce', 'içeren', 'iceren',
    'paketlerinde', 'paketlerini', 'paketlerde', 'kütüphanede', 'kutuphanede',
    # Jenerik İngilizce dolgu — konu kapısı bunları zaten zararsız kılıyor ama
    # iki ayrı olayı boş yere birbirine yaklaştırmasınlar.
    'that', 'this', 'these', 'those', 'into', 'other', 'others', 'with', 'from',
    'their', 'which', 'when', 'were', 'have', 'been', 'also', 'more', 'than',
    'such', 'used', 'using', 'after', 'before', 'could', 'would', 'about',
    'cloud', 'code', 'data', 'user', 'users', 'file', 'files', 'name', 'names',
    'dependency', 'dependencies', 'namespace', 'namespaces', 'project',
    'projects', 'source', 'open', 'build', 'builds', 'script', 'scripts',
}


def extract_package_names(text):
    """Ekosistem bağlamındaki küçük-harfli paket adlarını çıkarır.

    Yalnızca metinde bir ekosistem işareti (npm, PyPI, paket, registry...)
    varsa çalışır ve yalnızca o işaretin yakınındaki adayları döndürür.
    Nokta/tire/alt-çizgi içeren adlar (`@scope/name`, `foo-bar`) korunur.
    """
    t = text or ''
    if not _PKG_ECOSYSTEM_RE.search(t):
        return set()
    spans = [m.span() for m in _PKG_ECOSYSTEM_RE.finditer(t)]
    out = set()
    for m in _PKG_TOKEN_RE.finditer(t):
        w = m.group(0)
        core = w.lstrip('@')
        if len(core) < 4 or core in _PKG_DENYLIST or core in CODENAME_DENYLIST:
            continue
        if _PKG_ECOSYSTEM_RE.fullmatch(core):
            continue
        s, e = m.span()
        if any(s - _PKG_WINDOW <= es and ss <= e + _PKG_WINDOW for ss, es in spans):
            out.add(core)
    return out


def extract_codenames(text):
    """Metinden ayırt edici kampanya/operasyon/zararlı kod adlarını çıkarır.

    İki heuristik (ikisi de ≥5 karakter, CODENAME/ACRONYM denylist hariç):
      • CamelCase — küçük→büyük harf geçişi (FortiBleed, SharkLoader).
      • ALL-CAPS  — tümü büyük harf, salt harf (LONGLEASH, DOGLEASH, MARKIRAT,
        DCRAT). Tehdit istihbaratında zararlı/operasyon adları çoğu kez tümüyle
        büyük harf yazılır; yalnızca CamelCase aramak bunları kaçırıyordu.
    Bunlar nadir ve olaya özgüdür; aynı olayı farklı sözcüklerle anlatan
    haberleri bağlamak için güçlü sinyaldir.

    Danışma-belgesi kimlikleri (CERTFR-2026-AVI-0948, ICSA-26-123-01, VU#123456)
    ARAMADAN ÖNCE metinden silinir: bunlar olayın değil YAYININ numarasıdır ve
    öneki ('CERTFR') her bültende geçtiği için tüm bültenleri birbirine
    bağlıyordu (bkz. _ADVISORY_ID_RE)."""
    out = set()
    clean = _ADVISORY_ID_RE.sub(' ', text or '')
    for w in re.findall(r'[A-Za-z][A-Za-z0-9]+', clean):
        lw = w.lower()
        if len(w) < 5 or lw in CODENAME_DENYLIST or lw in _ACRONYM_DENYLIST:
            continue
        if _GENERIC_AAS_RE.match(lw):   # PhaaS/RaaS/MaaS... jenerik, kod adı değil
            continue
        # Tek harfin tekrarı (XXXXXXX, AAAAA) bir kod adı değil, REDAKSİYON
        # İŞARETİDİR. Arşiv/rapor paragrafları kaynak adını "(XXXXXXX, AÇIK -
        # domain, tarih)" kalıbıyla maskeliyor; ALL-CAPS ve ≥5 harf olduğu için
        # kod adı sayılıyordu. ÖLÇÜLDÜ (2026-08-12): 79 ham haberin 6'sında
        # geçti — Kural 2c (codename-body) üzerinden alakasız haberleri
        # birbirine bağlayabilecek bir sinyal.
        if len(set(lw)) == 1:
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


# ── ÖZEL AD (entity) çıkarımı ─────────────────────────────────────────────
# Olayın öznesi olan özel adlar (Minnesota, CareCloud, AnySign4PC) aktör/kod adı/
# CVE sinyallerinin HİÇBİRİNE girmez ama aynı olayın en güçlü göstergesidir.
#
# ⚠️ YALNIZCA TÜRKÇE PARAGRAFTAN çıkarılır — bilinçli bir tasarım kararıdır:
#   • İngilizce `title` çoğu kaynakta Title-Case'tir → her sözcük özel ad görünür.
#   • `full_text` site menü metni taşır ("Data Breaches", "Careers", "Analytics").
# 2026-07-31 ölçümünde bu iki alanı da kullanan ilk sürüm gövdede 31 yanlış
# eşleşme üretti; yalnızca paragrafa inince 4'e (sonra denylist ile 3'e) düştü.
#
# Türkçe paragraf normal cümle düzenindedir; cümle ORTASINDA büyük harfle
# başlayan token gerçek bir özel addır.
#
# CÜMLE BAŞI sorunu ve ÇAPRAZ ONAY çözümü (2026-08-01):
# İlk sürüm cümle başı token'ları TÜMÜYLE atıyordu. Bu, gerçek özel adları
# konumlarına göre görünmez kılıyordu: 01.08 raporunda Minnesota su saldırısı
# DÖRDÜNCÜ kez manşet oldu, çünkü o günkü paragrafta "Minnesota" cümle başında
# geçiyordu ("...bildirilmiştir. Minnesota eyaletindeki 30'dan fazla...").
#
# Korpus taraması (161 paragraf) ayrımı net gösterdi: jenerik cümle-başı
# token'lar Türkçe çekimli sıradan kelimelerdir ve cümle ORTASINDA HİÇ geçmezler
# (saldırganların 22/0, yapılan 8/0, araştırmacılar 5/0, şirket 7/0); gerçek özel
# adlar ise her iki konumda da görülür (minnesota 8/6, microsoft 14/20).
#
# Bu yüzden elle liste tutmak yerine ÇAPRAZ ONAY kuralı: cümle başındaki token
# ADAY'dır; yalnızca KARŞILAŞTIRILAN DİĞER haber onu cümle ORTASINDA kullanmışsa
# özel ad sayılır. İki tarafta da yalnızca cümle başında geçen token (ör. her iki
# paragrafın da "Bitsight tarafından..." diye cümleye başlaması) ONAYLANMAZ —
# liste bakımı gerektirmeyen, dile uygun ve kendi kendini güncelleyen bir kural.
_ENTITY_SENT_START_RE = re.compile(r'(?:^|[.!?:;•\n]\s+|["“(])\s*$')
_ENTITY_TOKEN_RE = re.compile(r'\b([A-ZÇĞİÖŞÜ][A-Za-zÇĞİÖŞÜçğıöşü0-9]{3,})')

# Cümle ortasında büyük harfle geçse de özel ad SAYILMAYAN sözcükler: ülke/ay
# adları, her haberde geçen kurum/satıcı adları ve Title-Case yazılan jenerik
# güvenlik terimleri. ('cyber' ölçümde tek yanlış-pozitifin sebebiydi:
# WordPress ↔ ServiceNow haberlerini "Cyber Express" üzerinden birleştiriyordu.)
_ENTITY_DENYLIST = frozenset({
    # ülke / bölge (TR + EN)
    'amerika', 'birleşik', 'devletleri', 'avrupa', 'birliği', 'kuzey', 'güney',
    'kore', 'çin', 'rusya', 'iran', 'i̇ran', 'israil', 'i̇srail', 'ukrayna',
    'i̇ngiltere', 'almanya', 'fransa', 'hollanda', 'kanada', 'avustralya',
    'japonya', 'hindistan', 'i̇spanya', 'i̇talya', 'brezilya', 'meksika',
    'china', 'russia', 'korea', 'ukraine', 'europe', 'america', 'israel',
    # ay adları (TR + EN)
    'ocak', 'şubat', 'mart', 'nisan', 'mayıs', 'haziran', 'temmuz', 'ağustos',
    'eylül', 'ekim', 'kasım', 'aralık',
    'january', 'february', 'march', 'april', 'june', 'july', 'august',
    'september', 'october', 'november', 'december',
    # her haberde geçen kurum / satıcı / kaynak
    'cisa', 'europol', 'interpol', 'ncsc', 'enisa', 'kisa', 'nist',
    'microsoft', 'google', 'apple', 'amazon', 'oracle', 'cisco', 'adobe',
    'kaspersky', 'sophos', 'fortinet', 'mandiant', 'proofpoint', 'bitdefender',
    'techcrunch', 'bleepingcomputer', 'reuters', 'securelist', 'krebs',
    # Title-Case yazılan jenerik güvenlik/teknoloji terimleri
    'cyber', 'security', 'services', 'service', 'systems', 'system', 'group',
    'internet', 'agency', 'department', 'ministry', 'technology', 'software',
    'network', 'platform', 'cloud', 'server', 'browser', 'update', 'report',
    'linux', 'windows', 'android', 'chrome', 'edge', 'firefox', 'safari',
    'azure', 'siber', 'güvenlik', 'yapay', 'zeka', 'zekâ',
    # Ulus/millet SIFATLARI ve yön sözcükleri. Ülke ADLARI yukarıda zaten var
    # ama sıfat biçimleri yoktu; İngilizce metinde tam da bu biçim geçer
    # ("North Korean operatives", "Spanish police"). ÖLÇÜLDÜ (2026-08-12):
    # Kuzey Koreli sahte kripto şirketi ↔ FBI federal kurum haberleri —
    # tamamen FARKLI iki olay — {korean, north, news, justice} ortak "özel
    # adları" yüzünden aynı olay sayıldı. Deepfake çiftinde de 'spain/spanish'
    # yeni gelişme sanıldı.
    'korean', 'chinese', 'russian', 'iranian', 'israeli', 'ukrainian',
    'spanish', 'french', 'german', 'british', 'dutch', 'indian', 'japanese',
    'american', 'european', 'north', 'south', 'east', 'west', 'spain',
    # Haber/kurum bağlamının kaçınılmaz sözcükleri
    'news', 'justice', 'federal', 'national', 'state', 'states', 'police',
    'ulusal', 'komisyon', 'bakanlık', 'başkanlık', 'i̇stihbarat', 'tehdit',
    'araştırma', 'araştırmacı', 'grubu', 'kimlik', 'güncelleme', 'acil',
})


def _entity_sets(view_or_text):
    """(kesin, aday) — özel ad kümeleri.

    kesin: cümle ORTASINDA büyük harfle geçen token'lar → kesin özel ad.
    aday : yalnızca cümle BAŞINDA geçenler → ancak karşı belge onaylarsa geçerli.

    Token'lar Türkçe çekim eklerini tolere etmek için ilk 8 karaktere köklenir:
    "Minnesota'da" / "Minnesota'daki" / "Minnesota" → 'minnesot'."""
    if isinstance(view_or_text, dict):
        text = view_or_text.get('paragraph') or ''
    else:
        text = view_or_text or ''
    kesin, aday = set(), set()
    for m in _ENTITY_TOKEN_RE.finditer(text):
        lw = m.group(1).lower()
        if lw in _ENTITY_DENYLIST or lw in CODENAME_DENYLIST:
            continue
        kok = lw[:8]
        if _ENTITY_SENT_START_RE.search(text[max(0, m.start() - 40):m.start()]):
            aday.add(kok)
        else:
            kesin.add(kok)
    return kesin, aday - kesin


def extract_entities(view_or_text):
    """Haberin KESİN özel adlarını döndürür (cümle ortasında geçenler).

    Girdi bir görünüm (dict) ise YALNIZCA 'paragraph' alanı kullanılır; düz metin
    verilirse doğrudan o metin taranır (test kolaylığı için).

    Cümle başındaki adaylar BURAYA girmez — onlar yalnızca iki haber
    karşılaştırılırken, karşı taraf cümle ortasında kullanmışsa sayılır
    (bkz. shared_entities)."""
    return _entity_sets(view_or_text)[0]


def shared_entities(view_a, view_b):
    """İki haberin PAYLAŞTIĞI özel adlar — çapraz onaylı.

    Bir token şu üç durumda ortak sayılır:
      • iki tarafta da cümle ortasında (kesin ∩ kesin),
      • birinde cümle ortasında, diğerinde cümle başında (kesin ∩ aday).
    İKİ TARAFTA DA yalnızca cümle başında geçen token sayılmaz: onun özel ad mı
    yoksa sıradan bir Türkçe cümle açıcısı mı olduğuna dair kanıt yoktur."""
    kesin_a, aday_a = _entity_sets(view_a)
    kesin_b, aday_b = _entity_sets(view_b)
    return (kesin_a & kesin_b) | (kesin_a & aday_b) | (aday_a & kesin_b)


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
#
# 0.18 → 0.14 (2026-07-31): ortak AKTÖR zaten yüksek-özgüllükte bir sinyaldir —
# "Laundry Bear", "APT29", "UNC5792" gibi adlar alakasız iki haberde rastgele
# birlikte geçmez — bu yüzden 0.18'lik konu eşiği gereksiz temkinliydi ve gerçek
# bir mükerreri kaçırdı: Laundry Bear (TA488) kampanyası 24, 29 ve 31 Temmuz'da
# ÜÇ KEZ manşet oldu. 31.07 manşeti "…half-click email attack FROM ZIMBRA TO
# OUTLOOK" idi, yani 29.07'de manşet olan Zimbra haberinin doğrudan devamı;
# ortak aktör VARDI (laundrybear) ama konu örtüşmesi 0.14 ile eşiğin 0.04
# altında kaldı.
#
# Ölçüm (scripts/dedup_backtest.py, tüm kritik3+rapor geçmişi):
#   0.16 → 1 yeni eşleşme   |  0.14 → 2 yeni eşleşme  |  0.12 → 4 yeni eşleşme
# 0.14'teki eşleşmelerin TAMAMI aynı Laundry Bear zinciri; yanlış-pozitif YOK.
_TOPIC_WITH_ACTOR_XDAY = 0.14

# ── Çapraz-GÜN birleşik başlık+konu sinyali (Kural 5) ─────────────────────
# Kural 4 (saf TR başlık benzerliği) çapraz-günde KAPALIDIR çünkü tek başına
# jenerik kalıplarda yanlış eşleşir. Ama iki sinyal BİRLİKTE kullanıldığında
# (Kural 2'nin "aktör + konu" felsefesi) ayrım yapılabilir hâle gelir.
# Eşikler gerçek kritik3/rapor geçmişi taranarak seçildi (2026-07-30 ölçümü).
_TRTITLE_XDAY            = 0.74   # başlık benzerliği alt sınırı
_TOPIC_WITH_TRTITLE_XDAY = 0.20   # eşlik etmesi gereken asgari konu örtüşmesi

# ── ORTAK ÖZEL AD sinyali (Kural 2d) ──────────────────────────────────────
# Bir olayın EN ayırt edici sinyali çoğu kez öznesinin ÖZEL ADIDIR (Minnesota,
# CareCloud, AnySign4PC). Aktör taksonomisi (Laundry Bear), kod adı (SIGNBT) ve
# CVE bunları KAPSAMAZ; sonuç olarak "Minnesota su sistemleri saldırısı" 29-30-31
# Temmuz 2026'da ÜÇ GÜN ÜST ÜSTE KRİTİK 3 manşeti oldu — hiçbir kural tetiklenmedi
# (29'a karşı başlık benzerliği 0.65 < 0.74; 30'a karşı ortak aktör/kod adı yok).
#
# Eşik TAHMİNLE değil ÖLÇÜMLE seçildi (scripts/dedup_backtest.py, 2026-07-31):
#   kritik3_gecmis (252 çift): 4 yeni eşleşme, 4'ü de DOĞRU, 0 yanlış
#   rapor_gecmis  (7595 çift): 4 yeni eşleşme, 3 doğru, 1 yanlış ('cyber' ortak
#                              sözcüğü — _ENTITY_DENYLIST'e eklenerek kapatıldı)
# 0.18'e indirmek gövdede yanlış-pozitifi 10'a çıkarıyordu (linux/chrome/azure/
# http gibi ortak sözcükler); 0.22 ölçülen en iyi ayrım noktasıdır.
_TOPIC_WITH_ENTITY = 0.22


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

    # 2e) ORTAK PAKET ADI + konu örtüşmesi. Açık kaynak paket adları küçük
    #     harflidir ve kod adı/aktör/özel ad sinyallerinin HİÇBİRİNE girmez
    #     (bkz. extract_package_names). Tedarik zinciri olayları günlerce
    #     sürdüğü için çapraz-gün korumasının en çok ihtiyaç duyduğu sinyal
    #     budur. Kod adı gövde kuralıyla aynı felsefe: topic-kapılı.
    shared_pkg = extract_package_names(blob_a) & extract_package_names(blob_b)
    if shared_pkg and topic >= max(actor_topic_min, _TOPIC_WITH_PACKAGE):
        return _ret(True, f'package:{",".join(sorted(shared_pkg))}+topic={topic:.2f}')

    # 2d) ORTAK ÖZEL AD + konu örtüşmesi. Olayın öznesi (Minnesota, CareCloud)
    #     aktör/kod adı/CVE sinyallerinin hiçbirine girmez; bu kural o boşluğu
    #     kapatır. Kural 2b'den ÖNCE gelmek ZORUNDA — 2b, ortak yapısal kimlik
    #     yoksa erken False döndüğü için sonrasına konursa bu kural hiç çalışmaz.
    #     (bkz. _TOPIC_WITH_ENTITY yorumundaki ölçüm)
    shared_ent = shared_entities(view_a, view_b)
    if shared_ent and topic >= _TOPIC_WITH_ENTITY:
        return _ret(True, f'entity:{",".join(sorted(shared_ent))}+topic={topic:.2f}')

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
    """Gözlem amaçlı: iki haber ORTAK bir parmak izi (aktör-ID, kod adı VEYA
    özel ad) paylaşıyor AMA same_event yine de AYNI OLAY demiyorsa (konu
    örtüşmesi eşiğin altında kaldığı için), bunu açıklayan bir dize döndürür;
    aksi halde None.

    Amaç, sessiz kaçışları (aynı olayın farklı gün tekrar seçilmesi gibi)
    veriyle görünür kılmaktır — davranışı DEĞİŞTİRMEZ, yalnızca raporlanır.

    ÖZEL AD parmak izi 2026-07-31'de eklendi: o güne kadar yalnızca aktör/kod
    adı aranıyordu ve tam da bu yüzden data/dedup_log.jsonl 30 gün boyunca HİÇ
    OLUŞMADI. Minnesota olayı üç gün üst üste manşet olurken ağ hiçbir uyarı
    üretmedi — çünkü olayı bağlayan tek sinyal (özel ad) bu fonksiyonun da
    görmediği sinyaldi."""
    if same_event(view_a, view_b, cross_day=cross_day):
        return None
    ha, pa, ea, fa = _bundle(view_a)
    hb, pb, eb, fb = _bundle(view_b)
    blob_a = ' '.join((ha, pa, ea, fa))
    blob_b = ' '.join((hb, pb, eb, fb))
    shared = (extract_actors(blob_a) & extract_actors(blob_b)) | \
             (extract_codenames(blob_a) & extract_codenames(blob_b)) | \
             shared_entities(view_a, view_b)
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


# ═══════════════════════════════════════════════════════════════════════════
# SÜREGELEN HİKÂYE ZİNCİRİ (manşet tekrarı) — same_event'ten AYRI bir kavram
# ═══════════════════════════════════════════════════════════════════════════
# same_event bir OLAY KİMLİĞİ testidir: "bu iki haber aynı olayı mı anlatıyor?"
# Ama bir hikâye günlerce YENİ gelişmelerle sürebilir ve her günün haberi
# gerçekten yenidir — mükerrer değildir. 2026-08-03'te ölçülen durum: Minnesota
# su tesisi saldırıları 29 Temmuz'dan beri ALTI GÜN üst üste KRİTİK 3 manşeti
# oldu; son gün FBI/EPA ortak uyarısıydı, yani gerçekten yeni bilgi. same_event
# hiçbir gün tetiklenmedi (ölçüldü: 08-03 ↔ 08-02 için ortak özel ad
# {rockwell, automati}, konu örtüşmesi 0.132 < 0.22 eşiği) — ÇÜNKÜ TETİKLENMESİ
# DE GEREKMİYORDU: bunlar farklı olaylar. Sorun mükerrerlik değil, MANŞET
# TEKRARIDIR ve ayrı bir ölçüte ihtiyaç duyar.
#
# Kural: bir hikâye son 7 günün EN AZ 3 AYRI GÜNÜNDE manşet olduysa, aynı
# hikâyeye bağlanan yeni aday KRİTİK 3'e ALINMAZ — gövdeye düşer. Silinmez:
# FBI/EPA uyarısı manşet olmayı hak etmiyor ama haber değeri taşıyor.
#
# Zincir bağı TEK ortak özel ad ile kurulur; tekrarın kendisi kanıttır. Ölçüm
# (kritik3_gecmis, 8 gün) üç eşiği karşılaştırdı:
#   ortak≥2 / zincir≥2 gün → 2 engelleme, ikisi de doğru ama 08-01 kaçıyor
#   ortak≥1 / zincir≥2 gün → 5 engelleme, 1'i YANLIŞ: "Midnight Blizzard"
#       manşeti, 07-29'daki Laundry Bear haberinin TAM METNİNDE geçen bir yan
#       söz ('blizzard') üzerinden alakasız bir zincire bağlanıyordu
#   ortak≥1 / zincir≥3 gün → 3 engelleme, ÜÇÜ DE DOĞRU (08-01/02/03 su zinciri)
# Sonuncusu seçildi: yanlış-pozitif YOK ve bir hikâye 3 gün manşet olduktan
# sonra 4. günde iniyor. Üç gün üst üste manşet, gerçekten büyük bir olay için
# savunulabilir; altı gün değil.
STORY_CHAIN_MIN_DAYS = 3      # zincir kaç AYRI günde manşet olmuş olmalı
# İKİ AYRI EŞİK. Zincir KURMAK güçlü bir iddiadır (iki haberin aynı hikâyenin
# parçası olduğunu söyler), kurulmuş bir zincire BAĞLANMAK zayıf kanıtla da
# güvenlidir (hikâyenin 3+ gün sürdüğü zaten kanıtlanmış). Tek eşik
# kullanıldığında ölçüm ikisinin de yanlış olduğunu gösterdi:
#   kurma≥1 → Midnight Blizzard manşeti (08-01, ayrı bir kampanya) Laundry
#     Bear zincirine bağlandı; bağlayan şey 07-29 haberinin TAM METNİNDE geçen
#     bir yan sözdü ('blizzard'). Zincir 3 güne ancak bu YANLIŞ üyeyle ulaştı.
#   kurma≥2 + bağlanma≥2 → su zinciri 08-03'te yakalanamazdı (o gün ortak
#     yalnız {automati, rockwell}; 08-01/08-02 manşetleriyle ortak tek addı).
STORY_CHAIN_LINK_SHARED = 2   # zincir KURARKEN iki manşeti bağlayan ortak ad
STORY_CHAIN_MIN_SHARED = 1    # KURULMUŞ zincire adayı bağlayan ortak ad
_STORY_ENTITY_MIN_LEN = 5     # kısa kökler ('this','more') ayırt edici değil
_STORY_FULLTEXT_CHARS = 1500

# Hikâye zinciri özel adları PARAGRAF + TAM METİN üzerinden çıkarılır (same_event
# yalnız paragrafa bakar). Gerekçe ölçüldü: 08-03 manşetinin Türkçe paragrafı
# "Minnesota" demiyordu, ayırt edici adlar (Rockwell, PLC, Allen-Bradley) yalnız
# tam metinde geçiyordu. BAŞLIKLAR bilinçli olarak DIŞARIDA: İngilizce başlıklar
# Title-Case yazıldığı için cümle-içi/cümle-başı ayrımı orada çöküyor ve
# 'Saldırı', 'Systems', 'Federal' gibi çöp adlar üretiyor (ölçüldü).
#
# Tam metin navigasyon menüsü ve paylaş widget'ı taşıyor (Dark Reading'in
# "Cybersecurity Careers / Identity & Access Mgmt / Threats" yan menüsü, Reddit
# ve Flipboard butonları). Bunlar 153 belgelik geçmişte %5-16 belge frekansıyla
# çıkıyor, yani ayırt edici DEĞİL. Kök-tabanlı denylist ölçümle üretildi.
_STORY_STEM_DENYLIST = frozenset({
    # kaynak sitelerin navigasyon menüsü / paylaş widget'ı
    'cybersec', 'cyberatt', 'careers', 'breaches', 'analytic', 'identity',
    'mgmt', 'threats', 'access', 'operatio', 'intellig', 'research',
    'vulnerab', 'data', 'reddit', 'flipboar', 'facebook', 'linkedin',
    'whatsapp',
    # jenerik güvenlik terimleri
    'cvss', 'email', 'exploit', 'malware', 'ransomwa', 'phishing', 'attack',
    'attacks', 'targeted', 'exposed', 'infrastr', 'incident', 'advisory',
    # söylem sözcükleri / günler (cümle başı adayları)
    'monday', 'tuesday', 'wednesda', 'thursday', 'friday', 'saturday',
    'sunday', 'accordin', 'this', 'that', 'these', 'those', 'while', 'after',
    'before', 'between', 'since', 'more', 'neither', 'several', 'other',
    'dozens', 'state', 'statemen', 'urges', 'remove', 'both', 'investig',
    'following', 'during', 'under',
    # milliyet / yön sıfatları (_ENTITY_DENYLIST ülke ADLARINI tutuyor)
    'russian', 'chinese', 'iranian', 'korean', 'american', 'south', 'north',
    'federal',
    # Türkçe jenerikler
    'saldırga', 'yapılan', 'tehdit', 'araştırm', 'şirket', 'ajansı', 'altyapı',
    'güvenliğ', 'operasyo', 'kullanıc', 'sistemle', 'saldırı', 'bilgiler',
    'veriler', 'yetkilil', 'bilgi', 'servisi', 'teknoloj', 'resm', 'resmi',
    # DÜZENLEYİCİ / KURUMSAL KALIP — 2026-08-11'de zinciri çökerten asıl neden.
    # Kurumsal ihlal haberlerinin çoğu "U.S. Securities and Exchange
    # Commission" bildirimine atıf yapar; bu TEK KALIP üç ayırt edici-görünen
    # kök üretiyordu (securiti/exchange/commissi) ve iki ortak kök zinciri
    # kurmaya yettiği için Liechtenstein şirket kaydı, İngiliz polis
    # veritabanı, Çinli telekomlar, İsviçre SharePoint, IEH ve Levi Strauss
    # TEK HİKÂYE sayıldı. O gün 22 aday manşetten indi ve 77 puanlık bir
    # WordPress eklentisi haberi manşet oldu.
    'securiti', 'securit', 'commissi', 'komisyon', 'exchange', 'borsa',
    'menkul', 'kıymetle', 'sermaye',
    # Cümle başı büyük harften sızan yaygın sözcükler (Türkçe metinde her
    # cümlenin ilk sözcüğü büyük harfle başlar; _entity_sets bunları "aday"
    # sayar). Ölçüm: bu kökler 165 haberlik korpusta 2-7 kez geçiyor, yani
    # DF eşiğiyle ayıklanamıyorlar — adla ele alınmaları gerekiyor.
    'people', 'three', 'however', 'despite', 'followin', 'remains', 'ongoing',
    'because', 'select', 'scroll', 'there', 'behind', 'among', 'against',
    'police', 'polis', 'polise', 'crime', 'legal', 'hukuk', 'license',
    'database', 'veritaba', 'computer', 'informat', 'communic', 'i̇letişi',
    'telecom', 'telecomm', 'telekomü', 'national', 'ulusal', 'office',
    'ofisi', 'kurum', 'meclisi', 'komitesi', 'committe', 'congress', 'house',
    'temsilci', 'foundati', 'corporat', 'companie', 'şirketin', 'mobile',
    'patch', 'register', 'advertis', 'affected', 'based', 'ayrıca', 'olayın',
    'olağandı', 'geçtiğim', 'perşembe', 'rapor', 'zararlı', 'threat',
    'breach', 'black', 'özellikl', 'chain', 'token', 'critical', 'brand',
    'click', 'written', 'targets', 'applicat', 'environm', 'enterpri',
    'financia', 'artifici', 'adversar', 'continui', 'indicato', 'mitigati',
    'signific', 'visibili', 'telemetr', 'workspac', 'middle', 'multi',
})


def story_entities(view):
    """Hikâye zinciri için AYIRT EDİCİ özel adlar (paragraf + tam metin).

    same_event'in kullandığı extract_entities'ten AYRI tutulur: oradaki eşikler
    haftalarca gerçek veriyle ayarlandı, bu katman onları DEĞİŞTİRMEZ."""
    if not isinstance(view, dict):
        return set()
    metin = ' . '.join(x for x in (
        view.get('paragraph') or '',
        (view.get('full_text') or '')[:_STORY_FULLTEXT_CHARS]) if x)
    kesin, aday = _entity_sets(metin)
    return {t for t in (kesin | aday)
            if t not in _STORY_STEM_DENYLIST and len(t) >= _STORY_ENTITY_MIN_LEN}


def build_story_chains(k3_by_day, min_days=STORY_CHAIN_MIN_DAYS):
    """Son günlerin KRİTİK 3 manşetlerinden SÜREGELEN hikâyeleri çıkarır.

    k3_by_day: [(tarih, [görünüm, ...]), ...] — bugün HARİÇ, eskiden yeniye.
    Dönüş: [{'days': [tarih...], 'entities': {kök...}, 'title': ilk manşet}]
    yalnızca en az `min_days` AYRI günde manşet olmuş zincirler."""
    dugumler = [(gun, v, story_entities(v))
                for gun, views in (k3_by_day or []) for v in views]
    zincirler = []                      # [[(gun, view, entities), ...], ...]
    for dugum in dugumler:
        _, _, e = dugum
        eslesen = [z for z in zincirler
                   if any(len(e & e2) >= STORY_CHAIN_LINK_SHARED for _, _, e2 in z)]
        if not eslesen:
            zincirler.append([dugum])
            continue
        # Birden fazla zincire bağlanıyorsa onları BİRLEŞTİR: aynı hikâyenin
        # farklı günlerdeki anlatımları ayrı kümelerde kalırsa zincir hiçbir
        # zaman min_days'e ulaşmaz.
        hedef = eslesen[0]
        hedef.append(dugum)
        for z in eslesen[1:]:
            hedef.extend(z)
            zincirler.remove(z)
    out = []
    for z in zincirler:
        gunler = sorted({g for g, _, _ in z})
        if len(gunler) >= min_days:
            birlesik = set()
            for _, _, e in z:
                birlesik |= e
            out.append({'days': gunler, 'entities': birlesik,
                        'title': z[0][1].get('tr_title', '')})
    return out


def matching_story_chain(view, chains):
    """Aday süregelen bir hikâyeye mi bağlanıyor? Bağlanıyorsa zinciri döner.

    Dönen sözlüğe 'shared' anahtarı eklenir (loglanabilsin diye)."""
    e = story_entities(view)
    if not e:
        return None
    for z in (chains or []):
        ortak = e & z['entities']
        if len(ortak) >= STORY_CHAIN_MIN_SHARED:
            return dict(z, shared=sorted(ortak))
    return None
