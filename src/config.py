"""Config - Tüm ayarlar"""
import os
from datetime import datetime

# API Key (Gemini)
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '')

# Dosya yolları
ARCHIVE_FILE = "data/haberler_arsiv.txt"

# Haber kaynakları
NEWS_SOURCES = {
    'The Hacker News': 'https://feeds.feedburner.com/TheHackersNews',
    'BleepingComputer': 'https://www.bleepingcomputer.com/feed/',
    'Krebs on Security': 'https://krebsonsecurity.com/feed/',
    'Threatpost': 'https://threatpost.com/feed/',
    'Security Affairs': 'https://securityaffairs.com/feed',
    'Graham Cluley': 'https://grahamcluley.com/feed/',
    'SANS ISC': 'https://isc.sans.edu/rssfeed.xml',
    'Recorded Future': 'https://www.recordedfuture.com/feed',
    'Cyberscoop': 'https://cyberscoop.com/feed/',
    'The Register': 'https://www.theregister.com/security/cyber_crime/headlines.atom',
    'TechCrunch Security': 'https://techcrunch.com/category/security/feed/',
    'CSO Online': 'https://www.csoonline.com/feed/',
    'Infoblox Blog': 'https://blogs.infoblox.com/feed/',
}

# ===== MASTODON KAYNAKLARI =====
# Siber güvenlik alanında önde gelen araştırmacı ve kurumlar
# instance: Mastodon sunucusu, username: @ ile başlamayan kullanıcı adı
# Her post için: reblogs_count * 2 + favourites_count >= MIN_ENGAGEMENT_SCORE olmalı
MASTODON_SOURCES = [
    {'instance': 'infosec.exchange',  'username': 'ESETresearch',   'label': 'ESET Research'},
    {'instance': 'mastodon.social',   'username': 'campuscodi',      'label': 'Catalin Cimpanu'},
    {'instance': 'infosec.exchange',  'username': 'malwaretech',     'label': 'MalwareTech'},
    {'instance': 'infosec.exchange',  'username': 'thegrugq',        'label': 'thegrugq'},
    {'instance': 'infosec.exchange',  'username': 'alperovitch',     'label': 'Dmitri Alperovitch'},
]

# Minimum etkileşim skoru: reblogs*2 + favourites >= bu değer
MASTODON_MIN_ENGAGEMENT = 5

# Kaç saatlik postları çekelim (son N saat)
MASTODON_HOURS_BACK = 36

# Scraping ayarları
HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
CONTENT_SELECTORS = {
    'The Hacker News': [{'class': 'articlebody'}],
    'BleepingComputer': [{'class': 'articleBody'}],
    'Krebs on Security': [{'class': 'entry-content'}],
    'Security Affairs': [{'class': 'entry-content'}],
    'Graham Cluley': [{'class': 'entry-content'}],
    'The Register': [{'class': 'article_text_wrapper'}],
    'TechCrunch Security': [{'class': 'article-content'}],
    'CSO Online': [{'class': 'body-copy'}],
    'Infoblox Blog': [{'class': 'entry-content'}],
}

# ===== YENİ: ÖNEM SCORING SİSTEMİ (v2.0) =====
# Bu ağırlıklar haberleri kategorize etmek için kullanılır
IMPORTANCE_WEIGHTS = {
    'infrastructure_attack': {
        'weight': 100,
        'description': 'Enerji, sağlık, finans, hükümet altyapısına saldırı',
        'keywords': ['critical infrastructure', 'power grid', 'hospital', 'healthcare', 'financial', 'government', 'scada', 'industrial control']
    },
    'large_breach': {
        'weight': 80,
        'description': '5 milyon+ kullanıcı verisi ihlali',
        'keywords': ['5 million', '10 million', 'million users', 'data breach', 'personal information', 'credit card', 'ssn']
    },
    'zero_day_apt': {
        'weight': 95,
        'description': 'Zero-day + APT grubu aktivitesi',
        'keywords': ['zero-day', 'apt28', 'apt29', 'lazarus', 'nation-state', 'previously unknown', 'threat actor', 'advanced persistent']
    },
    'national_security': {
        'weight': 110,
        'description': 'Ulusal güvenlik / Türkiye',
        'keywords': ['national security', 'government agencies', 'türkiye', 'nato', 'avrupa birliği', 'gendarmerie', 'moi']
    },
    'geopolitical_critical': {
        'weight': 120,
        'description': 'Jeopolitik kritik durumlar (siber savaş, ülke çatışması)',
        'keywords': ['cyber warfare', 'nation-state conflict', 'diplomatic crisis', 'ukraine', 'russia', 'china', 'iran', 'north korea', 'election', 'voting']
    },
    'legal_regulation': {
        'weight': 50,
        'description': 'Yasal düzenlemeler ve yönetmelikler',
        'keywords': ['regulation', 'legislation', 'law', 'compliance', 'gdpr', 'kvkk', 'dpa', 'directive', 'act', 'bill']
    }
}

# Pattern tanımları (REGEX) - Otomatik kategorizasyon için
DETECTION_PATTERNS = {
    'cve': r'CVE-\d{4}-\d{4,5}',
    'apt_groups': r'\b(APT\d+|Lazarus|Wizard Spider|LockBit|Conti|REvil|DarkSide|Emotet|Conti|Aqua|Scattered)\b',
    'large_number': r'(\d+)\s*(?:million|M|B)',
    'sectors': r'\b(healthcare|health|hospital|energy|power|finance|bank|government|military|defense)\b',
    'countries': r'\b(Ukraine|Russia|China|Iran|Korea|Israel|US|USA|UK|United States)\b',
}

# Gemini prompt (RESMİ TÜRKÇE) - YENİ GELİŞTİRİLMİŞ VERSİYON
def get_claude_prompt(news_content, recent_events=''):
    now = datetime.now()
    return f"""Sen profesyonel siber güvenlik analistisin.

GÖREV: 130 haberi analiz et → En önemli 5'ini seç → Kalanları önem sırasına koy → HTML raporu oluştur.

🚨 KRİTİK AŞAMA 1 - HABERLERİ FİLTRELE:
Aşağıdaki türleri ÇIKAR (raporda gösterme):
❌ "Podcast yayınlandı", "Webinar duyurusu", "Ürün lansmanı", "Beta sürüm" 
❌ "İndirilebilir rapor", "Etkinlik katılımı", "Konferans programı"
❌ Basit patch/güncelleme haberleri (kritik olmayan)
❌ İnceleme yazıları, röportajlar, genel tavsiye makaleleri
✅ SADECE aktif tehdit, açık, saldırı, veri ihlali, kritik güncelleme haberlerini AL

🚨 KRİTİK AŞAMA 2 - KESINLIKLE 5 HABER SEÇ (NE FAZLA, NE AZ):
Bu 7 kritere göre kesinlikle 5 haberi seç (ZORUNLU - DAHA AZ VEYA FAZLA OLMASIN):

1️⃣ **KRİTİK ALTYAPI SALDIRISI** 
   - Enerji, sağlık, finans, hükümet sektörü
   - "Critical infrastructure", "power grid", "hospital systems"
   - APT grupları + devlet destekli saldırılar

2️⃣ **5 MİLYON+ KULLANICI VERİ İHLALİ**
   - "5 million", "10 million", "data breach" 
   - Büyük şirketler (Microsoft, Google, Amazon, Apple)
   - "Personal information", "credit card", "SSN"

3️⃣ **ZERO-DAY + APT GRUBU AKTİVİTESİ**
   - APT28, APT29, Lazarus, etc.
   - Nation-state actors
   - "Previously unknown vulnerability"

4️⃣ **ULUSAL GÜVENLİK / TÜRKİYE**
   - "National security", "government agencies"
   - Türkiye ile ilgili siber güvenlik haberleri
   - NATO, AB, Türk kurumları

5️⃣ **JEOPOLİTİK KRİTİK DURUMLAR**
   - Ülkeler arası siber savaş, siber diplomasi krizi
   - "Cyber warfare", "nation-state conflict", "diplomatic crisis"
   - Kritik ülke sistemlerine saldırı (Rusya-Ukrayna, ABD-Çin, İran, Kuzey Kore)
   - Seçim sistemleri, kritik altyapı hedefleme
   - Uluslararası hukuk/anlaşma ihlalleri

6️⃣ **YASAL DÜZENLEMELER**
   - Siber güvenlikle ilgili yeni çıkan yasalar, yasal düzenlemeler

🚨 AŞAMA 3 - YAPILANDIRILMIŞ RAPOR OLUŞTUR:

RAPOR YAPISI (SIRAYLA):

1️⃣ **BAŞLIK**: "{now.strftime('%d.%m.%Y')} Siber Güvenlik Haber Özetleri"

2️⃣ **YÖNETİCİ ÖZETİ BAŞLIĞI**

3️⃣ **"ÖNEMLİ GELİŞMELER" KUTUSU**: 
   - En kritik 5 haberin TAM CÜMLELİK özeti
   - Her biri sayfa içi link: <a href="#haber-N">N. CVE-2024-1234 açığı Microsoft sunucularında kritik güvenlik riski oluşturmaktadır.</a>
   - ZORUNLU: Tam cümle (özne + yüklem + nesne) + nokta ile bitiş

3️⃣ᵇ **"SOSYAL MEDYA SİNYALLERİ" KUTUSU** (Önemli Gelişmeler kutusunun hemen altına):
   - Ham veride [MASTODON_SCORE:N:N] etiketi olan haberler bunlardır
   - Bu haberleri signal-item olarak listele, her birinde signal-badge ile etkileşim göster
   - badge formatı: Paylaşım: {reblogs} · Beğeni: {favs}  (N yerine gerçek sayıyı yaz)
   - Sayfa içi link: <a href="#haber-N">haber başlığı veya kısa özet</a>
   - Mastodon haberi yoksa bu kutuyu tamamen çıkar (oluşturma)
   - Hiçbir ikon veya emoji kullanma

4️⃣ **GERİ KALAN 38 HABERİN 2 SÜTUNLU TABLOSU**:
   - 6. haber → id="haber-6", 7. haber → id="haber-7" vs.
   - Her biri TAM CÜMLELİK özet + sayfa içi link
   - ZORUNLU: Tam cümle yapısı (özne + yüklem + nesne) + nokta ile bitiş

5️⃣ **HABER PARAGRAFLARI (SIRALAMA ÖNEMLİ!)**:
   - ÖNCE: En önemli 5 haberin 100-130 kelime paragraf özetleri (id="haber-1" dan haber-5'e)
   - SONRA: Geri kalan TÜM haberlerin paragraf özetleri (id="haber-6"dan son habere kadar)
   - ⚠️ YARIDA BIRAKMAK YASAK — tabloda kaç haber varsa HEPSININ paragraf özeti olacak
   - Her news-item için news-content paragrafı ZORUNLUDUR, atlanamaz

KRİTİK KURALLALAR:
✅ KESINLIKLE 5 ÖNEMLI HABER SEÇ (daha az veya fazla değil, tamamen 5!)
✅ Tablodaki haber sayısı = paragraf sayısı (bire bir eşit olmalı)
✅ Önemli gelişmelerdeki haberler tekrar etmesin tabloda
✅ ID numaraları: 1'den son habere kadar sürekli
✅ Sayfa içi linkler doğru çalışsın
✅ ASLA eksik paragraf bırakma — her news-item'ın news-content'i dolu olacak

KRİTİK DİL KURALI - RESMİ TÜRKÇE:
- yapılmıştır, edilmiştir, belirtilmektedir, ifade edilmektedir, tespit edilmiştir
- ASLA: yaptı, etti, söyledi, bulundu (günlük dil yasak)
- CVE, FBI, NSA, APT gibi kısaltmaların tamamı büyük harf

ANTİ-HALÜSİNASYON:
- SADECE verilen metni kullan
- TAHMİN YAPMA, VARSAYIMDA BULUNMA, KISALTMA YAPMA  
- VERİLEN TÜM UYGUN HABERLERİ YAZ! (Filtrelenenler hariç)
- ASLA YARIDA KESME! SON HABERE KADAR DEVAM ET!

⚠️ KAYNAK SATIRI KURALI - KRİTİK:
Her haberin altında şu format var: (XXXXXXX, AÇIK - https://link, domain.com, GG.AA.YYYY)
  → href içine: tam https://... linkini yaz (ASLA domain.com değil)
  → görünen metin: domain.com
  → tarih: o satırdaki GG.AA.YYYY değerini AYNEN kopyala
⛔ ASLA bugünün tarihini yazma — her haberin tarihi farklıdır, ham veriden oku

ZORUNLU HTML ŞABLONU - AYNEN KULLAN:
```html
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Siber Güvenlik Raporu - {now.strftime('%d.%m.%Y')}</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        html {{ scroll-behavior: smooth; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #2c3e50;
            background: #f5f7fa;
            padding: 20px;
        }}
        .container {{
            max-width: 900px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 6px rgba(0,0,0,0.07);
        }}
        
        /* ŞIK BAŞLIK */
        .report-header {{
            background: linear-gradient(135deg, #1a237e 0%, #3949ab 100%);
            padding: 50px 30px;
            text-align: center;
            color: white;
        }}
        .report-header h1 {{
            font-size: 26px;
            font-weight: 600;
            margin: 0;
            letter-spacing: 0.3px;
        }}
        
        /* ÖNEMLİ GELİŞMELER KUTUSU - AÇIK PASTEL MAVİ */
        .important-news {{
            background: linear-gradient(135deg, #e3f2fd 0%, #f1f8ff 100%);
            color: #2c3e50;
            padding: 25px 30px;
            margin: 0;
            border: 1px solid #bbdefb;
            border-radius: 8px;
            margin-bottom: 20px;
        }}
        .important-news h2 {{
            color: #1565c0;
            font-size: 20px;
            font-weight: 600;
            margin-bottom: 20px;
        }}
        .important-summary {{
            display: grid;
            gap: 12px;
        }}
        .important-item {{
            background: rgba(255,255,255,0.7);
            padding: 12px 16px;
            border-radius: 6px;
            border-left: 4px solid #42a5f5;
        }}
        .important-item a {{
            color: #2c3e50;
            text-decoration: none;
            font-weight: 500;
            font-size: 15px;
        }}
        .important-item a:hover {{
            text-decoration: underline;
            color: #1565c0;
        }}
        
        /* YÖNETİCİ ÖZETİ */
        .executive-summary {{
            background: #f8f9fa;
            padding: 25px 30px;
            margin: 0;
            border-bottom: 1px solid #e1e8ed;
        }}
        .executive-summary h2 {{
            color: #1a237e;
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 15px;
            padding-bottom: 8px;
            border-bottom: 2px solid #1a237e;
        }}
        .executive-table {{
            width: 100%;
            border-spacing: 8px;
        }}
        .executive-table td {{
            background: white;
            padding: 12px 16px;
            border-radius: 6px;
            border-left: 3px solid #1a237e;
            vertical-align: top;
            width: 50%;
        }}
        .executive-table a {{
            color: #1a237e;
            text-decoration: none;
            font-weight: 500;
            font-size: 14px;
            line-height: 1.4;
        }}
        .executive-table a:hover {{
            text-decoration: underline;
        }}
        
        /* HABERLER BÖLÜMÜ */
        .news-section {{
            padding: 30px;
        }}
        .news-item {{
            background: #f8f9fa;
            margin-bottom: 25px;
            border-radius: 8px;
            padding: 20px;
            border-left: 4px solid #1a237e;
        }}
        .news-title {{
            color: #1a237e;
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 12px;
            line-height: 1.3;
        }}
        .news-content {{
            color: #2c3e50;
            font-size: 15px;
            line-height: 1.6;
            margin-bottom: 10px;
        }}
        .source {{
            color: #666;
            font-size: 13px;
            margin: 0;
        }}
        .source a {{
            color: #1a237e;
            text-decoration: none;
        }}
        .source a:hover {{
            text-decoration: underline;
        }}
        
        /* BAŞA DÖN BUTONU */
        /* SOSYAL MEDYA SİNYALLERİ KUTUSU */
        .mastodon-signals {{
            background: linear-gradient(135deg, #f3f0ff 0%, #faf8ff 100%);
            color: #2c3e50;
            padding: 25px 30px;
            margin: 0;
            border: 1px solid #ddd6fe;
            border-radius: 8px;
            margin-bottom: 20px;
        }}
        .mastodon-signals h2 {{
            color: #4c3d9e;
            font-size: 20px;
            font-weight: 600;
            margin-bottom: 20px;
        }}
        .mastodon-signals .signal-summary {{
            display: grid;
            gap: 12px;
        }}
        .mastodon-signals .signal-item {{
            background: rgba(255,255,255,0.7);
            padding: 12px 16px;
            border-radius: 6px;
            border-left: 4px solid #7c3aed;
            display: flex;
            align-items: baseline;
            gap: 12px;
        }}
        .mastodon-signals .signal-item a {{
            color: #2c3e50;
            text-decoration: none;
            font-weight: 500;
            font-size: 15px;
            flex: 1;
        }}
        .mastodon-signals .signal-item a:hover {{
            text-decoration: underline;
            color: #4c3d9e;
        }}
        .mastodon-signals .signal-badge {{
            display: inline-block;
            background: #ede9fe;
            border: 1px solid #c4b5fd;
            border-radius: 3px;
            padding: 2px 8px;
            font-size: 11px;
            font-weight: 600;
            color: #4c3d9e;
            white-space: nowrap;
            flex-shrink: 0;
        }}

        .back-to-top {{
            position: fixed;
            top: 50%;
            left: calc(50% - 450px - 48px);
            transform: translateY(-50%);
            width: 36px;
            height: 36px;
            background: #1a237e;
            color: white;
            border: none;
            border-radius: 50%;
            font-size: 18px;
            cursor: pointer;
            box-shadow: 0 2px 8px rgba(0,0,0,0.25);
            display: flex;
            align-items: center;
            justify-content: center;
            text-decoration: none;
            opacity: 0.85;
            transition: opacity 0.2s;
            z-index: 999;
        }}
        .back-to-top:hover {{
            opacity: 1;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="report-header">
            <h1>{now.strftime('%d.%m.%Y')} Siber Güvenlik Haber Özetleri</h1>
        </div>
        
        <!-- YÖNETİCİ ÖZETİ -->
        <div class="executive-summary">
            <h2>Yönetici Özeti</h2>
            
            <!-- ÖNEMLİ GELİŞMELER KUTUSU -->
            <div class="important-news">
                <h2>Önemli Gelişmeler</h2>
                <div class="important-summary">
                    [EN ÖNEMLİ 5 HABER BURADA - HER BİRİ TAM CÜMLE:]
                    <div class="important-item">
                        <a href="#haber-1">1. Microsoft Exchange sunucularında CVE-2024-1234 açığı kritik güvenlik riski oluşturmaktadır.</a>
                    </div>
                    <div class="important-item">
                        <a href="#haber-2">2. LockBit 4.0 fidye yazılımı dünya genelinde sağlık kurumlarını hedef almaktadır.</a>
                    </div>
                </div>
            </div>
            
            <!-- SOSYAL MEDYA SİNYALLERİ KUTUSU -->
            <div class="mastodon-signals">
                <h2>Sosyal Medya Sinyalleri</h2>
                <div class="signal-summary">
                    [MASTODON HABERLERİ BURADA - ham veride [MASTODON_SCORE:N:N] etiketi olan haberler:]
                    <div class="signal-item">
                        <a href="#haber-N">Mastodon haber başlığı veya kısa özet.</a>
                        <span class="signal-badge">Paylaşım: 12 · Beğeni: 8</span>
                    </div>
                </div>
            </div>
            
            <!-- GERİ KALAN 38 HABERİN 2 SÜTUNLU TABLOSU -->
            <table class="executive-table">
                [GERİ KALAN 38 HABERİN 2 SÜTUNLU TABLOSU - TAM CÜMLE ÖRNEKLER:]
                <tr>
                    <td><a href="#haber-6">6. Google Chrome'da sıfır gün açığı aktif olarak istismar edilmektedir.</a></td>
                    <td><a href="#haber-7">7. Cisco ağ cihazları için kritik güvenlik güncellemesi yayınlanmıştır.</a></td>
                </tr>
            </table>
        </div>
        
        <!-- HABERLER -->
        <div class="news-section">
            [ÖNEMLİ 5 HABERİN PARAGRAF ÖZETLERİ - ÖNCE BUNLAR]
            <div class="news-item" id="haber-1">
                <div class="news-title"><b>Birinci Önemli Haberin Başlığı</b></div>
                <p class="news-content">100-130 kelime paragraf özet, resmi Türkçe...</p>
                <p class="source"><b>(KAYNAK, AÇIK - <a href="[KAYNAK_LINK değeri]" target="_blank">[KAYNAK_DOMAIN değeri]</a>, [HABER_TARİHİ değeri])</b></p>
            </div>
            
            [SONRA GERİ KALAN 38 HABERİN PARAGRAF ÖZETLERİ]
            <div class="news-item" id="haber-6">
                <div class="news-title"><b>Altıncı Haberin Başlığı</b></div>
                <p class="news-content">100-130 kelime paragraf özet, resmi Türkçe...</p>
                <p class="source"><b>(KAYNAK, AÇIK - <a href="[KAYNAK_LINK değeri]" target="_blank">[KAYNAK_DOMAIN değeri]</a>, [HABER_TARİHİ değeri])</b></p>
            </div>
        </div>
    </div>
    <a href="#" class="back-to-top" title="Başa Dön" onclick="window.scrollTo({{top:0,behavior:'smooth'}});history.replaceState(null,'',window.location.pathname);return false;">↑</a>
</body>
</html>
```

BAŞLIK KURALLARI:
✓ İsim-fiil yapısı: "CVE-2024-1234'ün Microsoft Exchange Sunucularını Etkilemesi"
✓ SOMUT detaylar: Şirket/CVE/ülke adları dahil
✓ 7-9 kelime, her kelimenin ilk harfi büyük

ÖZET PARAGRAF KURALLARI:
✓ 100-130 kelime (MIN 100, MAX 130)
✓ 5N1K tüm sorular cevaplansın
✓ Resmi Türkçe (-mıştır, -edilmiştir)
✓ Normal cümle yapısı (başlık değil)

KRİTİK: 
- EN ÖNEMLİ 5 HABER → Hem "Kritik Gelişmeler" kutusunda HEM de haber paragraflarının en üstünde
- Kalan haberler → Önem sırasına göre sıralanmış
- Her habere id="haber-N" ve sayfa içi linkler
- Filtrelenenler (podcast/webinar/vb) raporda YOK

═══════════════════════════════════════════════════════════

SON 3 GÜNDE RAPORLANAN OLAYLAR (TEKRAR ALMA):
{recent_events if recent_events else "(Henüz arşiv yok)"}

⛔ Yukarıdaki olaylarla AYNI OLAYI anlatan haberler, farklı kaynak/başlıkla gelse bile ÇIKAR.
Aynı olay: aynı CVE numarası, aynı şirket/kurum adı + aynı saldırı türü, aynı tehdit aktörü + aynı hedef.

═══════════════════════════════════════════════════════════

HAM HABERLER:
{news_content}

═══════════════════════════════════════════════════════════

ŞİMDİ SIRAYLA YAP:
1. Filtreleme → Uygun haberleri seç (130'den ~43)
2. KESINLIKLE 5 HABERİ SEÇ (daha az/fazla değil, tam 5!)
   - Eğer 5 tane kritik haber bulamazsan, biraz daha düşük seviyedeki haberlerden ekle
   - Ama TOPLAM 5 OLMASI ZORUNLU
3. Kalanları (38 haber) önem sırasına koy
4. HTML şablonunu doldur

ZORUNLU:
- Yukarıdaki şablonu AYNEN kullan
- TÜM uygun haberleri dahil et
- ÖNEMLİ 5 HABERİ ZORUNLU OLARAK SEÇ (eksik olmasın!)"""
