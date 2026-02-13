
#!/usr/bin/env python3
"""Siber Güvenlik Haberleri - Otomatik Günlük Rapor"""
import requests, time, os
from bs4 import BeautifulSoup
from datetime import datetime
from xml.etree import ElementTree as ET
from urllib.parse import urlparse
import google.generativeai as genai
from src.config import *

class HaberSistemi:
    def __init__(self):
        self.headers = HEADERS
        self.sources = NEWS_SOURCES
        self.selectors = CONTENT_SELECTORS
        
    def fetch_full_article(self, url, source_name):
        """Tam metin çeker"""
        try:
            print(f"      📄 Tam metin...", end='', flush=True)
            r = requests.get(url, headers=self.headers, timeout=15)
            soup = BeautifulSoup(r.content, 'html.parser')
            for el in soup(['script','style','nav','header','footer','aside']):
                el.decompose()
            
            text = ""
            if source_name in self.selectors:
                for sel in self.selectors[source_name]:
                    content = soup.find('div', sel) or soup.find('article', sel)
                    if content:
                        text = self._extract(content)
                        if len(text) > 500: break
            
            if not text or len(text) < 500:
                for el in [soup.find('article'), soup.find('div', class_='content'), soup.find('main')]:
                    if el:
                        t = self._extract(el)
                        if len(t) > len(text): text = t
            
            if not text or len(text) < 200:
                text = '\n\n'.join([p.get_text().strip() for p in soup.find_all('p') if len(p.get_text().strip()) > 50])
            
            wc = len(text.split())
            domain = urlparse(url).netloc.replace('www.', '')
            
            if wc > 100:
                print(f" ✅ ({wc})")
                return {'full_text': text, 'word_count': wc, 'success': True, 'domain': domain}
            print(f" ⚠️  ({wc})")
            return {'full_text': "", 'word_count': 0, 'success': False, 'domain': domain}
        except Exception as e:
            print(f" ❌ ({str(e)[:20]})")
            return {'full_text': "", 'word_count': 0, 'success': False, 'domain': ''}
    
    def _extract(self, element):
        """Temiz metin"""
        if not element: return ""
        parts = []
        for p in element.find_all(['p','h1','h2','h3','li']):
            t = p.get_text().strip()
            if len(t) > 20 and not any(x in t.lower() for x in ['cookie','subscribe','newsletter']):
                parts.append(t)
        return '\n\n'.join(parts)
    
    def fetch_rss(self, url, source_name):
        """RSS çeker"""
        try:
            r = requests.get(url, headers=self.headers, timeout=15)
            root = ET.fromstring(r.content)
            articles = []
            
            if root.tag.endswith('feed'):  # Atom
                for entry in root.findall('.//{http://www.w3.org/2005/Atom}entry')[:3]:
                    t = entry.find('{http://www.w3.org/2005/Atom}title')
                    l = entry.find('{http://www.w3.org/2005/Atom}link')
                    s = entry.find('{http://www.w3.org/2005/Atom}summary')
                    d = entry.find('{http://www.w3.org/2005/Atom}published')
                    if t is not None:
                        articles.append({'title': t.text, 'link': l.get('href') if l is not None else '',
                                       'description': s.text if s is not None else '', 'date': d.text if d is not None else '',
                                       'source': source_name})
            else:  # RSS
                for item in root.findall('.//item')[:3]:
                    t = item.find('title')
                    l = item.find('link')
                    d = item.find('description')
                    p = item.find('pubDate')
                    if t is not None:
                        articles.append({'title': t.text, 'link': l.text if l is not None else '',
                                       'description': d.text if d is not None else '', 'date': p.text if p is not None else '',
                                       'source': source_name})
            return articles
        except: return []
    
    def topla(self):
        """Tüm haberleri topla"""
        print("="*70)
        print("📰 HABERLERİ TOPLAMA")
        print("="*70)
        print(f"🔍 {len(self.sources)} kaynak | ⏱️  7-10 dakika\n")
        
        all_news = {}
        total = 0
        full_text_success = 0
        
        for idx, (src, url) in enumerate(self.sources.items(), 1):
            print(f"[{idx}/{len(self.sources)}] 🔍 {src}")
            articles = self.fetch_rss(url, src)
            
            if articles:
                print(f"   └─ ✅ {len(articles)} haber")
                total += len(articles)
                print(f"   └─ 📄 Tam metinler:")
                for i, art in enumerate(articles, 1):
                    if art['link']:
                        print(f"      [{i}/{len(articles)}]", end=' ', flush=True)
                        res = self.fetch_full_article(art['link'], src)
                        art.update(res)
                        if res['success']: full_text_success += 1
                        time.sleep(2)
                all_news[src] = articles
            else:
                print(f"   └─ ❌ Bulunamadı")
            
            time.sleep(1)
        
        print(f"\n{'='*70}")
        print(f"📊 {total} haber | {full_text_success} tam metin")
        print(f"{'='*70}\n")
        return all_news
    
    def save_txt(self, news_data):
        """TXT arşive ekle"""
        print("💾 TXT arşivi...")
        now = datetime.now()
        os.makedirs("data", exist_ok=True)
        
        txt = f"\n{'='*80}\n📅 {now.strftime('%d %B %Y').upper()} - SİBER GÜVENLİK HABERLERİ\n{'='*80}\n\n"
        
        num = 0
        for src, articles in news_data.items():
            for art in articles:
                num += 1
                txt += f"[{num}] {src} - {art['title']}\n{'─'*80}\n"
                txt += f"Tarih: {art['date']}\nLink: {art['link']}\n"
                if art.get('full_text') and art.get('word_count', 0) > 0:
                    txt += f"\n[TAM METİN - {art['word_count']} kelime]\n{art['full_text']}\n"
                else:
                    txt += f"\n⚠️  Tam metin çekilemedi\n"
                txt += f"\n(XXXXXXX, AÇIK - {art.get('domain','')}, {now.strftime('%d/%m/%Y')})\n\n{'='*80}\n\n"
        
        with open(ARCHIVE_FILE, 'a', encoding='utf-8') as f:
            f.write(txt)
        
        print(f"✅ {ARCHIVE_FILE}")
        return txt
    
    def create_html(self, txt_content):
        """Gemini ile HTML oluştur"""
        print("🤖 Gemini API...")
        if not GEMINI_API_KEY:
            raise ValueError("❌ GEMINI_API_KEY yok!")
        
        genai.configure(api_key=GEMINI_API_KEY)
        
        # Retry mekanizması (3 deneme)
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"   Deneme {attempt + 1}/{max_retries}...")
                
                prompt = get_claude_prompt(txt_content)
                
                model = genai.GenerativeModel('gemini-flash-latest')
                
                response = model.generate_content(
                    prompt,
                    generation_config=genai.GenerationConfig(
                        max_output_tokens=100000,
                        temperature=0.7,
                    )
                )
                
                html = response.text
                break  # Başarılı, döngüden çık
            except Exception as e:
                print(f"   ⚠️  Hata: {e}")
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 10
                    print(f"   ⏳ {wait_time} saniye bekleyip tekrar deneniyor...")
                    time.sleep(wait_time)
                else:
                    print(f"   ❌ {max_retries} deneme başarısız, fallback HTML...")
                    return self._create_fallback_html(txt_content)
        
        # HTML temizle
        if html.startswith('```html'): html = html[7:]
        if html.startswith('```'): html = html[3:]
        if html.endswith('```'): html = html[:-3]
        html = html.strip()
        
        print(f"✅ HTML oluşturuldu ({len(html)} karakter)")
        
        # Kaydet
        os.makedirs("docs/raporlar", exist_ok=True)
        now = datetime.now()
        
        with open("docs/index.html", 'w', encoding='utf-8') as f:
            f.write(html)
        
        with open(f"docs/raporlar/{now.strftime('%Y-%m-%d')}.html", 'w', encoding='utf-8') as f:
            f.write(html)
        
        print("✅ docs/index.html")
        print(f"✅ docs/raporlar/{now.strftime('%Y-%m-%d')}.html")
        
        # 30 günden eski raporları sil
        self._cleanup_old_reports()
        
        return html
    
    def _create_fallback_html(self, txt_content):
        """Claude API başarısız olursa basit HTML"""
        now = datetime.now()
        html = f"""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Siber Güvenlik Raporu - {now.strftime('%d %B %Y')}</title>
    <style>
        body {{ font-family: system-ui, sans-serif; max-width: 1200px; margin: 0 auto; padding: 20px; background: #f5f5f5; }}
        .header {{ background: #1e3c72; color: white; padding: 40px; border-radius: 10px; margin-bottom: 20px; }}
        .content {{ background: white; padding: 40px; border-radius: 10px; white-space: pre-wrap; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>🔒 Siber Güvenlik Günlük Raporu</h1>
        <p>{now.strftime('%d %B %Y %A')}</p>
        <p>⚠️ Gemini API bağlantı hatası - TXT içeriği gösteriliyor</p>
    </div>
    <div class="content">{txt_content}</div>
</body>
</html>"""
        
        # Kaydet
        os.makedirs("docs/raporlar", exist_ok=True)
        with open("docs/index.html", 'w', encoding='utf-8') as f:
            f.write(html)
        with open(f"docs/raporlar/{now.strftime('%Y-%m-%d')}.html", 'w', encoding='utf-8') as f:
            f.write(html)
        
        print("✅ Fallback HTML oluşturuldu")
        return html
    
    def _cleanup_old_reports(self):
        """30 günden eski raporları sil"""
        import glob
        from datetime import timedelta
        
        cutoff = datetime.now() - timedelta(days=30)
        deleted = 0
        
        for filepath in glob.glob("docs/raporlar/*.html"):
            try:
                filename = os.path.basename(filepath)
                if filename == '.gitkeep': continue
                
                # Dosya adından tarihi çıkar (YYYY-MM-DD.html)
                date_str = filename.replace('.html', '')
                file_date = datetime.strptime(date_str, '%Y-%m-%d')
                
                if file_date < cutoff:
                    os.remove(filepath)
                    deleted += 1
            except:
                pass
        
        if deleted > 0:
            print(f"🗑️  {deleted} eski rapor silindi (30+ gün)")
        else:
            print("📁 Arşiv temiz (30 gün içinde)")

def main():
    print("\n"+"="*70)
    print("🔒 SİBER GÜVENLİK HABERLERİ")
    print("="*70)
    print(f"📅 {datetime.now().strftime('%d %B %Y %H:%M')}")
    print("="*70+"\n")
    
    sistem = HaberSistemi()
    
    # 1. Topla
    haberler = sistem.topla()
    if not haberler:
        print("❌ Haber yok!")
        return 1
    
    # 2. TXT
    txt = sistem.save_txt(haberler)
    
    # 3. HTML
    sistem.create_html(txt)
    
    print("\n"+"="*70)
    print("✨ TAMAMLANDI!")
    print("="*70)
    print("🌐 https://siberguvenlikhaberler.github.io/siberguvenlik/")
    print("="*70+"\n")
    
    return 0

if __name__ == "__main__":
    exit(main())
