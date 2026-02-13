
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
}

# Gemini prompt (RESMİ TÜRKÇE)
def get_claude_prompt(news_content):
    now = datetime.now()
    return f"""Sen profesyonel siber güvenlik analistisin.

GÖREV: Günlük haberleri HTML raporuna dönüştür.

KRİTİK DİL KURALI - RESMİ TÜRKÇE:
- yapılmıştır, edilmiştir, belirtilmektedir, ifade edilmektedir, tespit edilmiştir
- ASLA: yaptı, etti, söyledi, bulundu (günlük dil yasak)
- USS, NPC, FBI gibi kısaltmaların tamamı büyük harf

ANTİ-HALÜSİNASYON:
- SADECE verilen metni kullan
- TAHMİN YAPMA, VARSAYIMDA BULUNMA, KISALTMA YAPMA
- ZORUNLU: MÜKERRER OLMAYAN HİÇBİR HABERİ ATLAMA! TÜM HABERLERİ YAZ! 

FORMAT:
1. GÜNLÜK ÖZET (en üstte):
   Başlık: "{now.strftime('%d.%m.%Y')} Siber Güvenlik Haber Özetleri"
    Başlık: "Yönetici Özeti"
   10 cümle, yönetici özeti tarzı, NORMAL cümle (sadece ilk harf büyük), maddeler halinde

TASARIM KURALLARI:
- Ana başlık: Merkeze hizalı, büyük ve belirgin, alt çizgi yok
- Yönetici özeti kutusu: Yumuşak gri arka plan, solda ince lacivert şerit (3px), yuvarlatılmış köşeler
- Temiz, modern, kurumsal görünüm
- Aşırı çizgi, kalın border kullanma

2. HER HABER:
   • BAŞLIK: <b>Her Kelimenin İlk Harfi Büyük (Title Case)</b> - 7-9 kelime
     
     BAŞLIK KURALLARI:
     ✓ İsim-fiil yapısı kullan (-mA, -mAsI, -İşİ)
     ✓ SOMUT detaylar: şirket/yazılım/kişi adları, CVE numaraları, ülke isimleri
     ✓ "Yeni", "bir", "bazı" gibi belirsiz kelimeler KULLANMA
     
     YANLIŞ: <b>Yeni Fidye Yazılımı Hastane Sistemlerini Hedef Almıştır</b>
     DOĞRU:  <b>LockBit 4.0'ın Sağlık Sektörünü Hedef Alması</b>
     DOĞRU:  <b>Microsoft Exchange'de Kritik Güvenlik Açığının Tespit Edilmesi</b>
     DOĞRU:  <b>CVE-2024-1234'ün 100 Bin Sunucuyu Etkilemesi</b>
   
   • ÖZET PARAGRAF: Normal cümle yapısı, resmi Türkçe, 120 kelime max, 5N1K dahil
     Sadece cümle başları ve özel isimler büyük
   
   • KAYNAK: <b>(XXXXXXX, AÇIK - domain.com, {now.strftime('%d.%m.%Y')})</b>

KRİTİK: 
- Başlıklar: İsim-fiil yapısı (LockBit 4.0'ın Yayılması), somut detaylar
- Özet paragraflar: Normal cümle, resmi Türkçe
- ASLA prompt metnini HTML'e ekleme!
- HER SEFERINDE AYNI HTML YAPISINI KULLAN!

ZORUNLU HTML ŞABLONU - AYNEN KULLAN:
```html
<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Siber Güvenlik Raporu - [TARİH]</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
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
        
        /* ŞIK BAŞLIK - Gradient arka plan */
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
        
        /* YÖNETİCİ ÖZETİ - Profesyonel kutu */
        .executive-summary {{
            background: #f8f9fa;
            padding: 35px;
            margin: 0;
            border-bottom: 1px solid #e1e8ed;
        }}
        .executive-summary h2 {{
            color: #1a237e;
            font-size: 20px;
            font-weight: 600;
            margin-bottom: 20px;
            padding-bottom: 12px;
            border-bottom: 2px solid #1a237e;
        }}
        .executive-summary ul {{
            list-style: none;
            padding-left: 0;
        }}
        .executive-summary li {{
            padding: 8px 0 8px 20px;
            position: relative;
            color: #4a5568;
        }}
        .executive-summary li:before {{
            content: "•";
            color: #3949ab;
            font-weight: bold;
            position: absolute;
            left: 0;
        }}
        
        /* HABERLER BÖLÜMÜ */
        .news-section {{
            padding: 40px;
        }}
        .news-item {{
            margin-bottom: 35px;
            padding-bottom: 30px;
            border-bottom: 1px solid #e1e8ed;
        }}
        .news-item:last-child {{
            border-bottom: none;
        }}
        .news-title {{
            color: #283593;
            font-size: 20px;
            font-weight: 600;
            margin-bottom: 15px;
            line-height: 1.4;
        }}
        .news-content {{
            color: #4a5568;
            font-size: 15px;
            line-height: 1.8;
            text-align: justify;
            margin-bottom: 12px;
        }}
        .source {{
            color: #718096;
            font-size: 13px;
            font-style: italic;
        }}
        
        /* ARŞİV LİNKLERİ */
        .archive-section {{
            padding: 30px 40px;
            background: #f8f9fa;
            border-top: 1px solid #e1e8ed;
        }}
        .archive-section h3 {{
            color: #1a237e;
            font-size: 16px;
            font-weight: 600;
            margin-bottom: 15px;
        }}
        .archive-links {{
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
        }}
        .archive-link {{
            display: inline-block;
            padding: 8px 14px;
            background: white;
            color: #4a5568;
            text-decoration: none;
            border-radius: 6px;
            font-size: 13px;
            border: 1px solid #e1e8ed;
            transition: all 0.2s;
        }}
        .archive-link:hover {{
            background: #1a237e;
            color: white;
            border-color: #1a237e;
            transform: translateY(-1px);
        }}
        
        @media (max-width: 600px) {{
            .container {{ border-radius: 0; }}
            .report-header {{ padding: 30px 20px; }}
            .executive-summary, .news-section {{ padding: 25px; }}
            .news-title {{ font-size: 18px; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="report-header">
            <h1>[TARİH] Siber Güvenlik Haber Özetleri</h1>
        </div>
        
        <div class="executive-summary">
            <h2>Yönetici Özeti</h2>
            <ul>
                <li>[ÖZET MADDELER BURAYA]</li>
            </ul>
        </div>
        
        <div class="news-section">
            [HABERLER BURAYA]
        </div>
    </div>
</body>
</html>
```

BU ŞABLONU KULLANARAK:
- [TARİH] yerine tarihi yaz
- [ÖZET MADDELER BURAYA] yerine 10 maddelik özeti yaz
- [HABERLER BURAYA] yerine her haberi şu formatta ekle:
  <div class="news-item">
      <div class="news-title"><b>Başlık</b></div>
      <p class="news-content">Özet paragraf...</p>
      <p class="source"><b>(KAYNAK)</b></p>
  </div>

NOT: Arşiv linkleri otomatik eklenecek, sen sadece </body>'den önce bitir.

═══════════════════════════════════════════════════════════

HABERLER:
{news_content}

═══════════════════════════════════════════════════════════

SADECE yukarıdaki şablonu doldurarak temiz HTML döndür (hiç açıklama ekleme)"""
