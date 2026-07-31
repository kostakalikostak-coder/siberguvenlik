"""
src/dedup.py — aynı-olay (same-event) tespiti ve KRİTİK 3 ayrıklık garantisi testleri.

Bu testler LLM veya ağ gerektirmez; saf string mantığını doğrular.
Senaryolar 28.06.2026 raporundaki gerçek mükerrer vakadan türetilmiştir:
iki kaynak aynı Rus istihbarat / Signal kimlik-avı kampanyasını FARKLI
başlıklarla anlatıyordu ve ikisi de KRİTİK 3'e girmişti.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import dedup


# ── Gerçek vakadan türetilmiş görünümler ───────────────────────────────────
SIGNAL_A = {  # securityaffairs — "Signal Recovery Keys"
    'tr_title': 'Rus İstihbaratının Signal Yedekleme Kurtarma Anahtarlarını Ele Geçirmesi',
    'title': 'New FBI Alert: Russian Intelligence Uses Signal Recovery Keys to Access Messages',
    'paragraph': ('FBI ve CISA, Rus istihbarat servislerinin Signal kullanıcılarını hedef '
                  'alan kimlik avı kampanyaları hakkında uyarı yayımlamıştır. FSB bağlantılı '
                  'UNC5792 ve UNC4221 gruplarının Ukraynalı yetkilileri, askeri personeli ve '
                  'gazetecileri hedef aldığı, sahte destek mesajlarıyla Signal yedekleme '
                  'kurtarma anahtarlarını ele geçirdiği bildirilmektedir.'),
    'full_text': '',
}
SIGNAL_B = {  # thehackernews — "Fake Support Texts"
    'tr_title': 'Rus İstihbarat Servislerinin Ukraynalı Yetkililerin Mesajlaşma Hesaplarını Ele Geçirmesi',
    'title': 'Ukraine Says Russian Intelligence Used Fake Support Texts to Steal Messaging Credentials',
    'paragraph': ('Ukrayna Güvenlik Servisi ve FBI, Rus istihbarat servislerinin hükümet '
                  'yetkililerini, askeri personeli ve aktivistleri hedef alan siber casusluk '
                  'operasyonunu ortaya çıkarmıştır. Saldırganlar sahte destek mesajlarıyla '
                  'kullanıcıları yedekleme kurtarma anahtarlarını paylaşmaya zorlamaktadır. '
                  'Faaliyetler Star Blizzard ve UNC5792 gruplarıyla ilişkilendirilmektedir.'),
    'full_text': '',
}
SHARKLOADER = {  # helpnetsecurity — farklı olay
    'tr_title': 'Bilinmeyen Saldırganların SharkLoader Yazılımıyla Hükümetleri Hedeflemesi',
    'title': 'Mystery hackers use novel SharkLoader dropper against governments, software devs',
    'paragraph': ('Kaspersky araştırmacıları, çok sayıda ülkedeki hükümet kurumlarını hedef '
                  'alan StrikeShark operasyonunu tespit etmiştir. Saldırganlar SharkLoader '
                  'yükleyicisiyle ağlara sızmakta ve Cobalt Strike ile kalıcılık sağlamaktadır.'),
    'full_text': '',
}
MOZILLA = {  # bleepingcomputer — farklı olay
    'tr_title': 'Mozilla Araştırmacılarının Yapay Zeka Kodlama Araçlarını Kötü Amaçlı Yazılım Çalıştırmakla Suçlaması',
    'title': 'Clean GitHub repo tricks AI coding agents into running malware',
    'paragraph': ('Mozilla 0DIN ekibi, otonom yapay zeka kodlama araçlarının temiz görünen '
                  'GitHub depoları üzerinden kötü amaçlı yazılım çalıştırmak üzere '
                  'kandırılabildiğini tespit etmiştir.'),
    'full_text': '',
}
OPENAI = {  # thehackernews — farklı olay (AI ama farklı konu)
    'tr_title': "OpenAI'ın GPT-5.6 Sol Modelini Siber Güvenlik Korumalarıyla Ön İzlemeye Sunması",
    'title': 'OpenAI Previews GPT-5.6 Sol With Restricted Access and Stronger Cyber Safeguards',
    'paragraph': ('OpenAI, GPT-5.6 Sol modelini sınırlı bir kullanıcı grubuna açmıştır. '
                  'Model savunma amaçlı kod incelemesi ve yama geliştirme için '
                  'tasarlanmıştır.'),
    'full_text': '',
}


def test_same_event_positive_signal_campaign():
    """Aynı kampanyayı farklı başlıkla anlatan iki haber → AYNI OLAY."""
    same, why = dedup.same_event(SIGNAL_A, SIGNAL_B, explain=True)
    assert same, f"Mükerrer yakalanamadı: {why}"
    assert 'unc5792' in why.lower() or 'topic' in why.lower()


def test_same_event_negatives():
    """Farklı olaylar AYNI OLAY sayılmamalı (yanlış-pozitif yok)."""
    assert not dedup.same_event(SIGNAL_A, SHARKLOADER)
    assert not dedup.same_event(SIGNAL_A, MOZILLA)
    assert not dedup.same_event(SHARKLOADER, MOZILLA)
    # Mozilla & OpenAI ikisi de "yapay zeka" ama farklı olay
    assert not dedup.same_event(MOZILLA, OPENAI)
    assert not dedup.same_event(SHARKLOADER, OPENAI)


def test_codename_shared():
    """Ortak ayırt edici kod adı tek başına yeterli sinyaldir."""
    a = {'tr_title': 'FortiBleed Kampanyasının Yayılması', 'title': 'FortiBleed spreads', 'paragraph': '', 'full_text': ''}
    b = {'tr_title': 'Yeni FortiBleed Saldırısı Tespit Edildi', 'title': 'New FortiBleed attack', 'paragraph': '', 'full_text': ''}
    assert dedup.same_event(a, b)


def test_codename_denylist_not_triggered():
    """Yaygın vendor adı (Fortinet) tek başına aynı-olay sinyali DEĞİLDİR."""
    a = {'tr_title': 'Fortinet Ürününde Açık', 'title': 'Fortinet flaw A', 'paragraph': 'Bir zafiyet bulundu.', 'full_text': ''}
    b = {'tr_title': 'Fortinet Cihazında Sorun', 'title': 'Fortinet flaw B', 'paragraph': 'Başka bir konu.', 'full_text': ''}
    assert not dedup.same_event(a, b)


def test_different_cve_not_same():
    """Farklı CVE'ler + farklı konu → aynı olay değil."""
    a = {'tr_title': 'Cisco CVE-2026-1111 Açığı', 'title': 'Cisco CVE-2026-1111', 'paragraph': 'Cisco router zafiyeti.', 'full_text': ''}
    b = {'tr_title': 'Apache CVE-2026-2222 Açığı', 'title': 'Apache CVE-2026-2222', 'paragraph': 'Apache sunucu sorunu.', 'full_text': ''}
    assert not dedup.same_event(a, b)


def test_cross_day_uat_actor_id_dedup():
    """Cisco Talos aktör kodu UAT-#### çapraz-günde parmak izi olmalı.
    Gerçek vaka (08-09.07.2026): UAT-7810 / LONGLEASH-ORB haberi iki gün üst
    üste KRİTİK 3'e girmişti; kod adları TÜMÜ BÜYÜK HARF (LONGLEASH) olduğu için
    CamelCase kod-adı sezgisine takılmıyor, tek ayırt edici imza UAT-7810."""
    day1 = {'tr_title': "Çinli UAT-7810'un LONGLEASH Zararlısıyla ORB Ağını Genişletmesi",
            'title': 'Chinese hackers develop LONGLEASH malware to expand ORB network',
            'paragraph': 'UAT-7810 Ruckus yönlendiricilerini ele geçirerek ORB ağını büyütüyor.',
            'full_text': ''}
    day2 = {'tr_title': "Çin Bağlantılı UAT-7810'un Yönlendiricilere Yönelik Yeni Arka Kapıları",
            'title': "China-Linked APT Expands Arsenal With New 'Leash' Backdoors",
            'paragraph': 'UAT-7810 yeni arka kapılarla ORB yönlendirici ağını genişletiyor.',
            'full_text': ''}
    assert dedup.same_event(day2, day1, cross_day=True), \
        'Ortak UAT-7810 aktör kodu + konu örtüşmesi aynı olay olarak görülmeli'


def test_allcaps_codename_shared():
    """TÜMÜ BÜYÜK HARF zararlı/operasyon adları (LONGLEASH, DCRAT) kod adı
    sayılmalı. Eskiden yalnızca CamelCase yakalanıyordu; ALL-CAPS adlar
    kaçıyordu (UAT-7810 vakasının bir yüzü)."""
    assert 'longleash' in dedup.extract_codenames('New LONGLEASH malware found')
    assert 'dcrat' in dedup.extract_codenames('deploys DCRAT payload')
    a = {'tr_title': 'X Grubunun LONGLEASH ile Ağ Kurması', 'title': 'Hackers deploy LONGLEASH',
         'paragraph': '', 'full_text': ''}
    b = {'tr_title': 'Yeni LONGLEASH Arka Kapısı', 'title': 'LONGLEASH backdoor analyzed',
         'paragraph': '', 'full_text': ''}
    assert dedup.same_event(a, b, cross_day=True), 'ortak ALL-CAPS kod adı aynı olay olmalı'


def test_acronym_not_codename():
    """Yaygın akronim/jenerik büyük-harf sözcükler (≥5) kod adı SAYILMAMALI —
    yoksa iki farklı haber ortak akronimle yanlışlıkla birleşir."""
    assert dedup.extract_codenames('CISA RANSOM THREAT HTTPS ATTACK REPORT SECURITY') == set()
    c = {'tr_title': 'CISA Kimlik Avı Uyarısı', 'title': 'CISA warns on phishing',
         'paragraph': 'CISA phishing advisory for federal email systems.', 'full_text': ''}
    e = {'tr_title': 'CISA KEV Kataloğu Güncellemesi', 'title': 'CISA adds bug to KEV',
         'paragraph': 'CISA known exploited vulnerabilities catalog update.', 'full_text': ''}
    assert not dedup.same_event(c, e, cross_day=True), 'ortak akronim aynı olay saymamalı'


def test_new_actor_taxonomies():
    """Büyük satıcı aktör taksonomileri tanınmalı (Google TAG, Unit42 CL-STA,
    Microsoft DEV/Storm, Trend Micro Earth/Water/Void, Cisco UAT)."""
    cases = {
        'TAG-110': 'tag110', 'CL-STA-0048': 'clsta0048', 'DEV-0537': 'dev0537',
        'Storm-2077': 'storm2077', 'UAT-7810': 'uat7810', 'Earth Lusca': 'earthlusca',
        'Water Curupira': 'watercurupira', 'Void Rabisu': 'voidrabisu',
    }
    for text, norm in cases.items():
        assert norm in dedup.extract_actors(text), f'{text} tanınmalı'


def test_trend_actor_case_sensitive():
    """Trend deseni büyük/küçük-harfe duyarlı: jenerik küçük-harf 'water/earth/
    void' aktör sayılmamalı (yanlış-pozitif önlemi)."""
    assert dedup.extract_actors('leaked into the water supply and earth around it') == set()


def test_nearmiss_signal_observability():
    """nearmiss_signal: ortak parmak izi var ama konu örtüşmesi eşik altıysa
    gözlem dizesi döner; same_event zaten AYNI OLAY diyorsa None döner."""
    # Ortak aktör ama tamamen farklı konu → yakın-kaçış sinyali
    a = {'tr_title': 'APT41 Enerji Şirketini Hedefledi', 'title': 'APT41 hits energy firm',
         'paragraph': 'APT41 enerji sektörü fidye saldırısı elektrik şebekesi.', 'full_text': ''}
    b = {'tr_title': 'APT41 Üniversite Ağına Sızdı', 'title': 'APT41 breaches university',
         'paragraph': 'APT41 akademik casusluk öğrenci verileri araştırma.', 'full_text': ''}
    sig = dedup.nearmiss_signal(a, b, cross_day=True)
    assert sig is not None and 'apt41' in sig
    # Aynı olay → None
    assert dedup.nearmiss_signal(SIGNAL_A, SIGNAL_B, cross_day=True) is None


def _views(mapping):
    return lambda i: mapping[i]


def test_pick_distinct_guarantees_no_duplicate_in_top3():
    """KRİTİK 3 garantisi: dup aday atlanır, sıradaki ayrık adayla doldurulur."""
    mapping = {4: SIGNAL_A, 1: SIGNAL_B, 9: SHARKLOADER, 3: MOZILLA, 2: OPENAI}
    # LLM sırası dup'ı 1. ve 2. sıraya koymuş olsun: [4, 1, 9, 3, 2]
    top3 = dedup.pick_distinct([4, 1, 9, 3, 2], _views(mapping), n=3)
    assert len(top3) == 3
    assert 4 in top3 and 1 not in top3, f"Dup [1] elenmeliydi: {top3}"
    # Sonuçta hiçbir çift aynı-olay olmamalı
    for i in range(len(top3)):
        for j in range(i + 1, len(top3)):
            assert not dedup.same_event(mapping[top3[i]], mapping[top3[j]])


def test_drop_duplicates_against_top3():
    """Gövde, KRİTİK 3 ile aynı-olay olan haberi göstermemeli."""
    mapping = {4: SIGNAL_A, 1: SIGNAL_B, 9: SHARKLOADER, 3: MOZILLA}
    body = dedup.drop_duplicates_against([1, 3, 9], reference_ids=[4], get_view=_views(mapping))
    assert 1 not in body, "Dup gövdeye sızdı"
    assert 3 in body and 9 in body


# ── ÇAPRAZ-GÜN (cross_day) dedup ────────────────────────────────────────────
# Gerçek arşiv verisinden türetildi: çapraz-günde Kural 4 (saf TR başlık
# benzerliği) jenerik Türkçe kalıpları yanlışlıkla eşleştiriyordu.
XDAY_BRAZIL = {  # 23 Haziran — farklı olay
    'tr_title': 'Bir Bilgisayar Korsanının Brezilya Ulusal Uyarı Sistemini Ele Geçirmesi',
    'title': '', 'full_text': '',
    'paragraph': 'Bir saldırgan Brezilya ulusal acil durum uyarı sistemine sızdı.',
}
XDAY_JAGUAR = {  # 27 Haziran — farklı olay, ama TR başlık kalıbı benzer
    'tr_title': "Rus Bilgisayar Korsanlarının Jaguar Land Rover'ın Sistemlerini Ele Geçirmesi",
    'title': '', 'full_text': '',
    'paragraph': 'Rus bağlantılı bir grup Jaguar Land Rover üretim ağına sızdı.',
}
XDAY_PIXELSMASH_A = {  # ortak kod adı — GERÇEK mükerrer
    'tr_title': 'FFmpeg Yazılımındaki PixelSmash Açığının Uzaktan Kod Yürütülmesine İzin Vermesi',
    'title': '', 'full_text': '',
    'paragraph': 'FFmpeg kütüphanesindeki PixelSmash açığı uzaktan kod yürütmeye olanak tanıyor.',
}
XDAY_PIXELSMASH_B = {
    'tr_title': 'FFmpeg Yazılımındaki PixelSmash Açığının Küresel Medya Sunucularını Riske Atması',
    'title': '', 'full_text': '',
    'paragraph': 'PixelSmash açığı dünya genelinde medya sunucularını tehdit ediyor.',
}


def test_cross_day_no_false_positive_from_trtitle():
    """Çapraz-günde jenerik TR başlık kalıbı (Kural 4) yanlış-pozitif ÜRETMEZ.

    Aynı haberler same-run modunda (cross_day=False) Kural 4 ile YANLIŞ eşleşir;
    cross_day=True bunu engellemelidir."""
    assert dedup.same_event(XDAY_BRAZIL, XDAY_JAGUAR), \
        "Sabit: same-run modunda Kural 4 bunları (yanlışlıkla) eşleştirir"
    assert not dedup.same_event(XDAY_BRAZIL, XDAY_JAGUAR, cross_day=True), \
        "Çapraz-günde farklı olaylar AYNI sayılmamalı (Kural 4 devre dışı olmalı)"


def test_cross_day_keeps_codename_match():
    """Çapraz-günde ortak kod adı (PixelSmash) GERÇEK mükerreri yine yakalar."""
    assert dedup.same_event(XDAY_PIXELSMASH_A, XDAY_PIXELSMASH_B, cross_day=True)


def test_pick_distinct_excludes_recent_kritik3():
    """exclude_views: son günlerde KRİTİK 3 olmuş olay bugün manşete ALINMAZ."""
    mapping = {7: XDAY_PIXELSMASH_B, 8: SHARKLOADER, 9: MOZILLA}
    # Dün PixelSmash manşetti → bugün PixelSmash (id 7) atlanmalı, diğerleri gelir
    picked = dedup.pick_distinct(
        [7, 8, 9], _views(mapping), n=3, exclude_views=[XDAY_PIXELSMASH_A])
    assert 7 not in picked, "Çapraz-gün mükerrer KRİTİK 3'e sızdı"
    assert 8 in picked and 9 in picked


def test_pick_distinct_without_exclude_views_unchanged():
    """exclude_views=None ise eski davranış korunur (geriye-uyumluluk)."""
    mapping = {4: SIGNAL_A, 1: SIGNAL_B, 9: SHARKLOADER, 3: MOZILLA, 2: OPENAI}
    top3 = dedup.pick_distinct([4, 1, 9, 3, 2], _views(mapping), n=3)
    assert len(top3) == 3 and 4 in top3 and 1 not in top3


# Gerçek üretim kaçağı (03.07.2026): aynı olay iki farklı kaynaktan farklı
# İngilizce başlık + farklı URL ile geldi; uzun TR paragraflarda tam-metin
# Jaccard 0.28'e SEYRELDİ (eşik 0.42 altı), tr_title benzerliği 0.56 (eşik 0.62
# altı), "Pegasus" CamelCase olmadığından kod adı çıkmadı → 4 kural da kaçırdı
# ve mükerrer rapora sızdı. Fix: (a) Pegasus vb. paralı-asker casus yazılımlar
# named-actor, (b) paragraf baş-penceresi topic (event_keywords limit).
KOULOGLOU_A = {
    'tr_title': "Avrupa Parlamentosu Üyesi Stelios Kouloglou'nun Pegasus Casus Yazılımıyla Hedeflenmesi",
    'title': 'European Parliament Member Investigating Spyware Was Hacked With Pegasus',
    'full_text': '',
    'paragraph': (
        "Citizen Lab tarafından yayımlanan rapor, eski Avrupa Parlamentosu Üyesi "
        "Stelios Kouloglou'nun mobil cihazının Pegasus casus yazılımıyla defalarca "
        "hedef alındığını ortaya çıkarmıştır. Kouloglou, saldırıların gerçekleştiği "
        "dönemde Avrupa Birliği bünyesinde ticari gözetim araçlarının kötüye "
        "kullanımını araştırmakla görevli PEGA Komitesi'nde görev yapmaktadır. Adli "
        "analizler cihaza 21 Ekim 2022 ile Mart 2023 tarihlerinde sızıldığını "
        "göstermektedir. Apple'ın akıllı ev yazılımındaki bir sıfır tıklama açığı "
        "kullanılmıştır."),
}
KOULOGLOU_B = {
    'tr_title': "Pegasus Casus Yazılımının PEGA Komitesi Üyesi Stelios Kouloglou'yu Hedeflemesi",
    'title': 'Someone infected a spyware probe overseer with spyware',
    'full_text': '',
    'paragraph': (
        "Citizen Lab tarafından yayımlanan rapor, Avrupa Birliği bünyesindeki casus "
        "yazılım suiistimallerini araştırmakla görevli PEGA Komitesi'nin üyesi "
        "Stelios Kouloglou'nun telefonuna Pegasus yazılımının bulaştırıldığını ortaya "
        "koymuştur. Yunan gazeteci ve eski Avrupa Parlamentosu üyesi Kouloglou'nun "
        "cihazı Ekim 2022 ve Mart 2023 tarihlerinde iki kez hedef alınmıştır. NSO "
        "Group tarafından geliştirilen bu paralı asker yazılımının demokratik "
        "süreçleri ihlal amacıyla kullanıldığı değerlendirilmektedir."),
}


def test_same_event_shared_spyware_family():
    """Aynı casus yazılım (Pegasus) + konu örtüşmesi → aynı olay (within-run)."""
    assert dedup.same_event(KOULOGLOU_A, KOULOGLOU_B)


def test_same_event_shared_spyware_family_cross_day():
    """Çapraz-günde de yakalanmalı (yüksek-özgüllük: ortak aktör + konu)."""
    assert dedup.same_event(KOULOGLOU_A, KOULOGLOU_B, cross_day=True)


def test_lead_window_topic_catches_diluted_long_paragraphs():
    """Baş-pencere topic'i, uzun paragrafta seyrelen Jaccard'ı telafi eder:
    Pegasus adını KALDIRSAK bile baş-pencere ortak çekirdeği yakalamalı."""
    import re as _re
    a = dict(KOULOGLOU_A); b = dict(KOULOGLOU_B)
    scrub = lambda s: _re.sub(r'(?i)pegasus|nso group', 'X', s)
    for v in (a, b):
        v['tr_title'] = scrub(v['tr_title']); v['title'] = scrub(v['title'])
        v['paragraph'] = scrub(v['paragraph'])
    assert dedup.same_event(a, b), "Baş-pencere topic seyrelmiş paragrafı yakalamalı"


def test_lead_window_no_false_positive_distinct_events():
    """Baş-pencere farklı olayları AYNI saymamalı (yanlış-pozitif üretmemeli)."""
    assert not dedup.same_event(MOZILLA, OPENAI)
    assert not dedup.same_event(SHARKLOADER, MOZILLA)


# ── Jenerik "X-as-a-Service" (-aaS) kod adı yanlış-pozitifi ────────────────
# 2026-07-08 raporunda "PhaaS" (Phishing-as-a-Service) CamelCase heuristiğiyle
# AYIRT EDİCİ kod adı sanılıp ÜÇ FARKLI olayı (Hollanda tutuklama / Google-Gemini
# davası / DEBULL araç seti) yanlışlıkla aynı olay işaretliyordu.

def test_generic_aas_not_a_codename():
    """PhaaS/RaaS/MaaS gibi iş-modeli terimleri kod adı SAYILMAZ."""
    assert dedup.extract_codenames('PhaaS platform') == set()
    assert 'phaas' not in dedup.extract_codenames('A new PhaaS kit emerged')
    # Gerçek kod adları ETKİLENMEZ
    assert 'debull' in dedup.extract_codenames('DEBULL toolkit PhaaS')
    assert 'fortibleed' in dedup.extract_codenames('FortiBleed exploit')
    assert 'dcrat' in dedup.extract_codenames('DcRAT malware')


def test_phaas_no_longer_merges_distinct_events():
    """Yalnızca 'PhaaS' paylaşan iki FARKLI olay artık AYNI sayılmaz."""
    a = {'tr_title': "Hollanda'da Kredi Kartı Kimlik Avı Operasyonu Tutuklaması",
         'paragraph': 'Hollanda polisi bir PhaaS operasyonunda iki kişiyi tutukladı.'}
    b = {'tr_title': "Google'ın Gemini Yapay Zekasını Kullanan Dolandırıcılara Davası",
         'paragraph': 'Google, Gemini yapay zekasını kötüye kullanan PhaaS aktörlerine dava açtı.'}
    assert not dedup.same_event(a, b)
    assert not dedup.same_event(a, b, cross_day=True)


# ── Çapraz-gün SEMANTİK dedup LLM yanıt ayrıştırma ────────────────────────

def test_parse_cross_day_dupes_valid():
    assert dedup.parse_cross_day_dupes({'duplicates': [7, '15']}, [7, 15, 20]) == {7, 15}


def test_parse_cross_day_dupes_filters_unknown_and_garbage():
    # Aday olmayan (99) ve sayı-olmayan ('x', None) girdiler elenmeli
    assert dedup.parse_cross_day_dupes(
        {'duplicates': [7, 99, 'x', None]}, [7, 15]) == {7}


def test_parse_cross_day_dupes_safe_on_bad_input():
    assert dedup.parse_cross_day_dupes(None, [7]) == set()
    assert dedup.parse_cross_day_dupes({}, [7]) == set()
    assert dedup.parse_cross_day_dupes({'duplicates': None}, [7]) == set()


# ── Kural 5: çapraz-gün başlık+konu birleşik sinyali ──────────────────────
# 2026-07-30 üretim ölçümü: "İran Bağlantılı Siber Tehdit Aktörlerinin ABD ...
# Su ve Enerji ..." haberi 24 ve 26 Temmuz'da iki kez KRİTİK 3 manşeti oldu.
# Aktör/kod adı çıkmıyor, konu örtüşmesi _TOPIC_ALONE'un altında, başlık
# benzerliği 0.79 — sinyal vardı ama Kural 4 çapraz-günde kapalıydı.

_IRAN_A = {
    'tr_title': "İran Bağlantılı Siber Tehdit Aktörlerinin ABD'deki Su ve Enerji "
                "Sağlayıcılarını Hedef Alması",
    'paragraph': "Amerika Birleşik Devletleri hükümeti, İran devlet desteğiyle hareket "
                 "eden siber tehdit aktörlerinin ülkedeki su ve enerji sağlayıcılarına "
                 "ait endüstriyel kontrol sistemlerini hedef aldığını açıklamıştır. "
                 "FBI, NSA ve CISA tarafından yayımlanan ortak uyarıda, saldırganların "
                 "internete açık programlanabilir mantık denetleyicilerini kullandığı "
                 "belirtilmiştir.",
}
_IRAN_B = {
    'tr_title': "İran Bağlantılı Siber Tehdit Aktörlerinin ABD Su ve Enerji Kontrol "
                "Sistemlerini Hedeflemesi",
    'paragraph': "CISA, FBI, NSA ve Enerji Bakanlığı tarafından yayımlanan güncel siber "
                 "güvenlik kılavuzunda, İran bağlantılı siber tehdit aktörlerinin "
                 "ABD'deki su ve enerji kontrol sistemlerine sızdığı bildirilmiştir. "
                 "Mart 2026 tarihinden bu yana süregelen faaliyetlerde, saldırganların "
                 "internete açık programlanabilir denetleyicileri kullandığı "
                 "kaydedilmiştir.",
}


def test_xday_title_topic_catches_repeat_headline():
    """Aynı olay iki gün KRİTİK 3 manşeti olmamalı (üretimde yaşandı)."""
    ok, why = dedup.same_event(_IRAN_A, _IRAN_B, explain=True, cross_day=True)
    assert ok, f"mükerrer manşet yakalanmadı (gerekçe: {why!r})"
    assert 'trtitle-xday' in why


def test_xday_title_topic_does_not_merge_generic_vuln_pattern():
    """Jenerik zafiyet başlığı kalıbı FARKLI ürünleri birleştirmemeli.

    Kural 4'ün çapraz-günde kapatılma sebebi tam olarak buydu; Kural 5 bu
    yanlış-pozitifi geri getirmemeli. Veriler ÜRETİMDEN alındı (28-29 Temmuz
    2026 raporları): başlık 0.73, konu 0.26 → başlık eşiğinin (0.74) ALTINDA
    kaldığı için eşleşmemeli. Bu test eşiği aşağı çekmeye karşı korumadır.
    """
    a = {'tr_title': "ServiceNow Yapay Zeka Platformundaki Kritik Güvenlik Açığının Aktif Olarak İstismar Edilmesi",
         'paragraph': "ServiceNow Yapay Zeka Platformu'nda tespit edilen ve CVE-2026-6875 olarak izlenen kritik bir uzaktan kod yürütme açığının, siber tehdit aktörleri tarafından aktif olarak istismar edildiği bildirilmiştir. Searchlight Cyber araştırmacıları tarafından 14 Temmuz 2026 tarihinde raporlanan bu açığın, kimlik doğrulaması gerektirmeden ServiceNow örneklerinin ve bağlı proxy sunucularının tam kontrolüne olanak tanıdığı belirtilmiştir. Tehdit istihbaratı firması Defused, 25 Temmuz 2026 itibarıyla saldırgan"}
    b = {'tr_title': "Microsoft SharePoint Sunucularındaki Kritik Güvenlik Açıklarının Aktif Olarak İstismar Edilmesi",
         'paragraph': "Microsoft SharePoint Server üzerinde tespit edilen ve uzaktan kod yürütülmesine olanak tanıyan kritik güvenlik açıklarının aktif olarak istismar edildiği bildirilmiştir. CERT-EU tarafından yayımlanan rapora göre, özellikle CVE-2026-50522 kodlu seri durumdan çıkarma hatasının, saldırganların etkilenen sistemlerde yetkisiz kod çalıştırmasına imkan tanıdığı belirtilmiştir. WatchTowr adlı güvenlik kuruluşunun 20 Temmuz 2026 tarihinde bir kavram kanıtlama kodu tanımladığı ve ardından aktif istismar f"}
    assert not dedup.same_event(a, b, cross_day=True)


def test_xday_title_topic_keeps_followup_development():
    """Atıf/gelişme haberi mükerrer SAYILMAMALI.

    26 Tem "İran → ABD su/enerji" ile 30 Tem "İran bağlantılı grubun Minnesota
    su tesislerini hedeflemesi" (Tenable atfı) ayrı haberlerdir: ikincisi yeni
    bilgi (fail atfı) taşır. Ölçülen: başlık 0.65, konu 0.18 → eşik altı.
    """
    b30 = {'tr_title': "İran Bağlantılı Grubun Minnesota Su Tesislerini Hedeflemesi",
           'paragraph': "Tenable araştırmacıları, Minnesota'da 30'dan fazla su tesisini "
                        "kesintiye uğratan koordineli siber saldırıların arkasında İran "
                        "bağlantılı tehdit aktörü CyberAv3ngers'ın bulunduğunu "
                        "belirtmiştir."}
    assert not dedup.same_event(_IRAN_B, b30, cross_day=True)


def test_xday_title_topic_needs_both_signals():
    """Tek sinyal yetmez: başlık benzer ama konu alakasızsa mükerrer değildir."""
    a = {'tr_title': "İran Bağlantılı Siber Tehdit Aktörlerinin ABD'deki Su ve Enerji "
                     "Sağlayıcılarını Hedef Alması",
         'paragraph': "Tamamen alakasız bir konu: bir mobil oyun şirketinin reklam "
                      "gelirlerini artırmak için yeni bir abonelik modeli duyurduğu "
                      "bildirilmiştir."}
    assert not dedup.same_event(a, _IRAN_B, cross_day=True)


# ── Danışma-belgesi kimlikleri kod adı DEĞİLDİR (2026-07-31 regresyonu) ────
def test_advisory_id_prefix_is_not_a_codename():
    """CERTFR-2026-AVI-#### gibi bülten numaraları OLAY parmak izi değildir.

    Gerçek vaka (31.07.2026): 'CERTFR' ALL-CAPS kod adı sanıldığı için ANSSI'nin
    MS Edge / Citrix XenServer / Node.js bültenleri 'codename-body:certfr'
    gerekçesiyle AYNI OLAY sayıldı; çapa da çapraz-güne takılınca o günün DÖRT
    ANSSI bülteni birden rapordan düştü.
    """
    edge = {'tr_title': 'Microsoft Edge Tarayıcısı İçin Güvenlik Güncellemesi Yayımlanması',
            'title': 'Multiples vulnérabilités dans Microsoft Edge',
            'paragraph': 'Tarayıcı motorundaki bellek bozulması hatalarının uzaktan '
                         'kod çalıştırılmasına imkan tanıdığı, kullanıcıların '
                         'sürümlerini güncellemesi gerektiği bildirilmiştir.',
            'full_text': 'Référence CERTFR-2026-AVI-0947 concernant le navigateur.'}
    nodejs = {'tr_title': "Node.js Bakım Sürümüyle Hizmet Reddi Kusurlarının Kapatılması",
              'title': 'Multiples vulnérabilités dans Node.js',
              'paragraph': 'Sunucu tarafı çalışma zamanında HTTP ayrıştırıcısını '
                           'ilgilendiren hizmet reddi kusurlarının bakım sürümüyle '
                           'kapatıldığı duyurulmuştur.',
              'full_text': 'Référence CERTFR-2026-AVI-0950 concernant la plateforme.'}
    # Asıl regresyon: ortak 'CERTFR' öneki kod adı olarak ÇIKARILMAMALI
    assert 'certfr' not in dedup.extract_codenames(edge['full_text'])
    assert not (dedup.extract_codenames(edge['full_text'])
                & dedup.extract_codenames(nodejs['full_text']))
    assert not dedup.same_event(edge, nodejs)
    assert not dedup.same_event(edge, nodejs, cross_day=True)


def test_spec_name_is_not_a_codename():
    """AsyncAPI/OpenAPI gibi spec adları CamelCase diye kod adı sayılmamalı.

    31.07.2026: metninde 'AsyncAPI' geçen bir haber SIRF bu yüzden
    main._has_apt_evidence'ı geçip zafiyet_aktif_apt etiketini korudu."""
    assert not dedup.extract_codenames('The AsyncAPI package was affected.')
    assert not dedup.extract_codenames('An OpenAPI schema and a GraphQL query.')


# ── ORTAK ÖZEL AD sinyali (Kural 2d) ──────────────────────────────────────
# Gerçek üretim verisi: data/kritik3_gecmis.json, 29 ve 31 Temmuz 2026 manşetleri.
_MINNESOTA_29 = {
    'tr_title': "Minnesota'daki Su Tesislerine Yönelik Eş Zamanlı Operasyonel "
                "Teknoloji Saldırılarının Gerçekleşmesi",
    'paragraph': "Amerika Birleşik Devletleri'nin Minnesota eyaletindeki 30'dan fazla "
                 "su ve atık su tesisinin, operasyonel teknoloji sistemlerini hedef "
                 "alan eş zamanlı siber saldırılara maruz kaldığı bildirilmiştir. "
                 "Minnesota Bilgi Teknolojileri Servisi tarafından yapılan açıklamada, "
                 "26 ve 27 Temmuz 2026 tarihlerinde gerçekleşen müdahalelerin bazı "
                 "bölgelerde su sistemlerinin kapatılmasına yol açtığı belirtilmiştir.",
}
_MINNESOTA_31 = {
    'tr_title': "Minnesota'da Su Sistemlerine Yönelik Koordineli Siber Saldırı "
                "Düzenlenmesi",
    'paragraph': "Amerika Birleşik Devletleri'nin Minnesota eyaletinde 30'dan fazla "
                 "topluluk su sistemine yönelik koordineli bir siber saldırı "
                 "gerçekleştiği bildirilmiştir. 26-27 Temmuz 2026 tarihlerinde "
                 "düzenlenen saldırıların, küçük ölçekli kamu hizmeti kuruluşlarını "
                 "ortak bir operasyonel teknoloji zafiyeti üzerinden hedef aldığı "
                 "belirtilmektedir. Minnesota Bilgi Teknolojileri Servisi, yerel acil "
                 "durum ilan edildiğini açıklamıştır.",
}


def test_entity_catches_repeat_headline_across_days():
    """Aynı olay ÜST ÜSTE GÜNLERDE manşet olamaz — özel ad sinyali (Kural 2d).

    Gerçek vaka: Minnesota su sistemleri saldırısı 29-30-31 Temmuz 2026'da ÜÇ GÜN
    KRİTİK 3 manşeti oldu. Ne ortak aktör ne ortak kod adı vardı; başlık
    benzerliği 0.65 ile çapraz-gün eşiğinin (0.74) altındaydı. Olayı bağlayan
    tek sinyal öznesinin özel adıydı: 'Minnesota'."""
    same, why = dedup.same_event(_MINNESOTA_29, _MINNESOTA_31,
                                 explain=True, cross_day=True)
    assert same, f'Üst üste manşet olan aynı olay yakalanamadı: {why}'
    assert 'minnesot' in why


def test_entity_blocks_repeat_headline_in_pick_distinct():
    """pick_distinct, dün manşet olmuş olayı bugün KRİTİK 3'e ALMAMALI."""
    views = {1: _MINNESOTA_31, 2: SHARKLOADER, 3: MOZILLA}
    picked = dedup.pick_distinct([1, 2, 3], views.get, n=3,
                                 exclude_views=[_MINNESOTA_29])
    assert 1 not in picked, 'Dünkü manşetin tekrarı KRİTİK 3\'e alınmamalı'
    assert picked == [2, 3]


def test_entity_does_not_merge_unrelated_breaches():
    """İki ALAKASIZ veri ihlali, ortak özel adı olmadığı için birleşmemeli.

    Yanlış-birleştirme, kaçırılan mükerrerden daha zararlıdır: 31.07.2026'da
    Analog Devices ihlali tam da böyle bir birleştirme zinciriyle rapordan
    tamamen kayboldu."""
    analog = {'tr_title': 'Analog Devices Şirketinin Veri İhlali Bildirmesi',
              'paragraph': 'Yarı iletken üreticisi Analog Devices, sistemlerine '
                           'yetkisiz erişim sağlandığını ve bir miktar verinin ele '
                           'geçirildiğini bildirmiştir. Şirket operasyonlarının '
                           'etkilenmediğini açıklamıştır.'}
    kt = {'tr_title': "Güney Kore'nin KT Corporation'a Veri İhlali Cezası Vermesi",
          'paragraph': 'Kişisel Bilgileri Koruma Komisyonu, telekomünikasyon devi KT '
                       'Corporation\'a veri koruma ihlalleri gerekçesiyle idari para '
                       'cezası uygulamıştır. Şirketin iç ağının aylarca tehlikeye '
                       'girdiği belirtilmiştir.'}
    assert not dedup.same_event(analog, kt)
    assert not dedup.same_event(analog, kt, cross_day=True)


def test_entity_requires_topic_overlap():
    """Ortak özel ad TEK BAŞINA yetmez — konu da örtüşmeli.

    Aynı yerin/kurumun FARKLI olayları (ör. bir eyaletteki siber saldırı ile
    aynı eyaletteki bir yasa değişikliği) birleştirilmemeli."""
    a = {'tr_title': 'Minnesota Su Tesislerine Siber Saldırı',
         'paragraph': "Amerika Birleşik Devletleri'nin Minnesota eyaletindeki su "
                      'tesislerinin operasyonel teknoloji sistemlerini hedef alan '
                      'saldırılara maruz kaldığı bildirilmiştir.'}
    b = {'tr_title': 'Minnesota Eyaletinde Kripto ATM Düzenlemesi',
         'paragraph': 'Minnesota eyaletinde kripto para kiosklarını sınırlandıran '
                      'yasa tasarısının senatodan geçtiği, dolandırıcılık '
                      'şikayetlerinin gerekçe gösterildiği aktarılmıştır.'}
    assert not dedup.same_event(a, b, cross_day=True)


def test_extract_entities_only_reads_turkish_paragraph():
    """Özel ad çıkarımı SADECE Türkçe paragraftan yapılır.

    İngilizce başlıklar Title-Case'tir (her sözcük özel ad görünür), full_text
    ise site menü metni taşır ('Data Breaches', 'Careers'). İlk sürüm bu iki
    alanı da okuyunca gövde ölçümünde 31 yanlış eşleşme üretmişti."""
    view = {'tr_title': 'Bir Başlık',
            'paragraph': 'Saldırının Minnesota eyaletinde gerçekleştiği bildirilmiştir.',
            'title': 'Hackers Target Water Systems In Several States',
            'full_text': 'Data Breaches Careers Analytics Newsletter'}
    ents = dedup.extract_entities(view)
    assert 'minnesot' in ents
    assert not {'hacker', 'target', 'water', 'careers', 'analytic'} & ents


def test_extract_entities_skips_sentence_start_and_denylist():
    """Cümle başı büyük harf ve jenerik kurum/ülke adları özel ad sayılmaz."""
    ents = dedup.extract_entities(
        'Microsoft bir yama yayımlamıştır. CISA ise Amerika Birleşik Devletleri '
        'genelinde Rockwell cihazları için uyarı yapmıştır.')
    assert 'rockwell' in ents          # gerçek özel ad
    assert 'microso' not in ents       # cümle başı + denylist
    assert 'cisa' not in ents          # denylist
    assert 'amerika' not in ents       # denylist
