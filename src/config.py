
"""Config - Tüm ayarlar"""
import os
from datetime import datetime

# API Key
ANTHROPIC_API_KEY = os.getenv('ANTHROPIC_API_KEY', '')

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

# Claude prompt (RESMİ TÜRKÇE)
def get_claude_prompt(news_content):
    now = datetime.now()
    return f"""Sen profesyonel siber güvenlik analistisin.

GÖREV: Günlük haberleri HTML raporuna dönüştür.

KRİTİK DİL KURALI - RESMİ TÜRKÇE:
• yapılmıştır, edilmiştir, belirtilmektedir, ifade edilmektedir, tespit edilmiştir
• ASLA: yaptı, etti, söyledi, bulundu (günlük dil yasak)

ANTİ-HALÜSİNASYON:
• SADECE verilen metni kullan
• TAHMİN YAPMA, VARSAYIMDA BULUNMA

FORMAT:
1. GÜNLÜK ÖZET (en üstte):
   Başlık: "{now.strftime('%d %B %Y')} Haber Özeti"
   3-4 cümle, yönetici özeti tarzı, NORMAL cümle (sadece ilk harf büyük)

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
   
   • KAYNAK: <b>(XXXXXXX, AÇIK - domain.com, {now.strftime('%d/%m/%Y')})</b>

KRİTİK: 
• Başlıklar: İsim-fiil yapısı (LockBit 4.0'ın Yayılması), somut detaylar
• Özet paragraflar: Normal cümle, resmi Türkçe

CSS: Modern, responsive, profesyonel

HABERLER:
{news_content}

ÇIKTI: Sadece HTML kodu döndür (<!DOCTYPE html> ile başla)"""
