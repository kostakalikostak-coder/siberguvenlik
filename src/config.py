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

# Gemini prompt (RESMİ TÜRKÇE) - YENİ GELİŞTİRİLMİŞ VERSİYON
def get_claude_prompt(news_content):
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

🚨 KRİTİK AŞAMA 2 - EN ÖNEMLİ 5 HABERİ BELIRLE:
Bu 7 kritere göre en kritik 5 haberi seç:

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

7️⃣ **CVSS 9.0+ AÇIKLAR + AKTİF EXPLOIT**
   - CVE numarası var + "actively exploited", "in the wild"
   - CVSS 9.0-10.0 arası puanlar
   - "Zero-day", "0-day" içeren haberler


🚨 AŞAMA 3 - YAPILANDIRILMIŞ RAPOR OLUŞTUR:

RAPOR YAPISI (SIRAYLA):

1️⃣ **BAŞLIK**: "{now.strftime('%d.%m.%Y')} Siber Güvenlik Haber Özetleri"

2️⃣ **YÖNETİCİ ÖZETİ BAŞLIĞI**

3️⃣ **"ÖNEMLİ GELİŞMELER" KUTUSU**: 
   - En kritik 5 haberin TAM CÜMLELİK özeti
   - Her biri sayfa içi link: <a href="#haber-N">N. CVE-2024-1234 açığı Microsoft sunucularında kritik güvenlik riski oluşturmaktadır.</a>
   - ZORUNLU: Tam cümle (özne + yüklem + nesne) + nokta ile bitiş

4️⃣ **GERİ KALAN 38 HABERİN 2 SÜTUNLU TABLOSU**:
   - 6. haber → id="haber-6", 7. haber → id="haber-7" vs.
   - Her biri TAM CÜMLELİK özet + sayfa içi link
   - ZORUNLU: Tam cümle yapısı (özne + yüklem + nesne) + nokta ile bitiş

5️⃣ **HABER PARAGRAFLARI (SIRALAMA ÖNEMLİ!)**:
   - ÖNCE: En önemli 5 haberin 100-130 kelime paragraf özetleri (id="haber-1" dan haber-5'e)
   - SONRA: Geri kalan 38 haberin paragraf özetleri (id="haber-6" dan haber-43'e)

KRİTİK KURALLALAR:
✅ 43 haber toplam (5 önemli + 38 normal)
✅ Önemli gelişmelerdeki haberler tekrar etmesin tabloda
✅ ID numaraları: 1-43 arası sürekli
✅ Sayfa içi linkler doğru çalışsın

KRİTİK DİL KURALI - RESMİ TÜRKÇE:
- yapılmıştır, edilmiştir, belirtilmektedir, ifade edilmektedir, tespit edilmiştir
- ASLA: yaptı, etti, söyledi, bulundu (günlük dil yasak)
- CVE, FBI, NSA, APT gibi kısaltmaların tamamı büyük harf

ANTİ-HALÜSİNASYON:
- SADECE verilen metni kullan
- TAHMİN YAPMA, VARSAYIMDA BULUNMA, KISALTMA YAPMA  
- VERİLEN TÜM UYGUN HABERLERİ YAZ! (Filtrelenenler hariç)
- ASLA YARIDA KESME! SON HABERE KADAR DEVAM ET!

⚠️ KAYNAK LİNKİ KURALI - KRİTİK:
- Her haberin kaynak satırındaki href değeri = haberin TAM URL'İ (https://... ile başlayan)
- Her haberin ham verisinde "Link: https://..." satırı var → O URL'i kullan
- ASLA domain.com yazmayacaksın href içine, SADECE tam URL!

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
                <p class="source"><b>(KAYNAK, AÇIK - <a href="HABERİN TAM URL LİNKİ (https://...)" target="_blank">domain.com</a>, {now.strftime('%d.%m.%Y')})</b></p>
            </div>
            
            [SONRA GERİ KALAN 38 HABERİN PARAGRAF ÖZETLERİ]
            <div class="news-item" id="haber-6">
                <div class="news-title"><b>Altıncı Haberin Başlığı</b></div>
                <p class="news-content">100-130 kelime paragraf özet, resmi Türkçe...</p>
                <p class="source"><b>(KAYNAK, AÇIK - <a href="HABERİN TAM URL LİNKİ (https://...)" target="_blank">domain.com</a>, {now.strftime('%d.%m.%Y')})</b></p>
            </div>
        </div>
    </div>
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

HAM HABERLER:
{news_content}

═══════════════════════════════════════════════════════════

ŞİMDİ SIRAYLA YAP:
1. Filtreleme → Uygun haberleri seç
2. En önemli 5'ini belirle (yukarıdaki 7 kritere göre)
3. Kalanları önem sırasına koy
4. HTML şablonunu doldur

ZORUNLU: Yukarıdaki şablonu AYNEN kullan, TÜM uygun haberleri dahil et!"""
