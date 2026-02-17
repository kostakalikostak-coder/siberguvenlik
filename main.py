#!/usr/bin/env python3
"""Siber Güvenlik Haberleri - Otomatik Günlük Rapor"""
import requests, time, os, json, hashlib
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET
from urllib.parse import urlparse
import google.generativeai as genai
from difflib import SequenceMatcher
from src.config import *

class HaberSistemi:
    def __init__(self):
        self.headers = HEADERS
        self.sources = NEWS_SOURCES
        self.selectors = CONTENT_SELECTORS
        self.rss_errors = []  # RSS hataları için
        self.used_links_file = "data/haberler_linkler.txt"
        self.rss_errors_file = "data/rss_errors.txt"
        
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
        except Exception as e:
            # Hatayı kaydet
            error_msg = f"{source_name}: {type(e).__name__} - {str(e)[:100]}"
            self.rss_errors.append(error_msg)
            return []
    
    def _similarity(self, a, b):
        """İki string arasındaki benzerlik oranı (0-1)"""
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()
    
    def _load_used_links(self):
        """Son 7 günün kullanılmış linklerini yükle"""
        if not os.path.exists(self.used_links_file):
            return set(), {}
        
        used_links = set()
        used_titles = {}
        cutoff = datetime.now() - timedelta(days=7)
        
        with open(self.used_links_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    date_str, link, title = line.split('\t', 2)
                    date = datetime.strptime(date_str, '%Y-%m-%d')
                    if date >= cutoff:
                        used_links.add(link)
                        used_titles[link] = title
                except:
                    pass
        
        return used_links, used_titles
    
    def _save_used_links(self, articles):
        """Kullanılan linkleri kaydet (7 günden eski olanları sil)"""
        now = datetime.now()
        cutoff = now - timedelta(days=7)
        
        # Eski kayıtları oku
        existing = []
        if os.path.exists(self.used_links_file):
            with open(self.used_links_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        date_str = line.split('\t')[0]
                        date = datetime.strptime(date_str, '%Y-%m-%d')
                        if date >= cutoff:
                            existing.append(line)
                    except:
                        pass
        
        # Yeni linkleri ekle
        today = now.strftime('%Y-%m-%d')
        for art in articles:
            if art.get('link'):
                existing.append(f"{today}\t{art['link']}\t{art['title']}")
        
        # Kaydet
        os.makedirs("data", exist_ok=True)
        with open(self.used_links_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(existing) + '\n')
    
    def _save_rss_errors(self):
        """RSS hatalarını kaydet (7 günden eski olanları sil)"""
        if not self.rss_errors:
            return
        
        now = datetime.now()
        cutoff = now - timedelta(days=7)
        
        # Eski hataları oku
        existing = []
        if os.path.exists(self.rss_errors_file):
            with open(self.rss_errors_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        date_str = line.split(' | ')[0]
                        date = datetime.strptime(date_str, '%Y-%m-%d %H:%M')
                        if date >= cutoff:
                            existing.append(line)
                    except:
                        pass
        
        # Yeni hataları ekle
        timestamp = now.strftime('%Y-%m-%d %H:%M')
        for error in self.rss_errors:
            existing.append(f"{timestamp} | {error}")
        
        # Kaydet
        os.makedirs("data", exist_ok=True)
        with open(self.rss_errors_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(existing) + '\n')
        
        print(f"⚠️  {len(self.rss_errors)} RSS hatası kaydedildi: {self.rss_errors_file}")
    
    def _filter_duplicates(self, all_news):
        """Tekrar eden haberleri filtrele (link + başlık benzerliği)"""
        used_links, used_titles = self._load_used_links()
        
        filtered = {}
        removed_count = 0
        
        for src, articles in all_news.items():
            filtered_articles = []
            for art in articles:
                link = art.get('link', '')
                title = art.get('title', '')
                
                # Link kontrolü
                if link in used_links:
                    removed_count += 1
                    continue
                
                # Başlık benzerliği kontrolü (%80+)
                is_similar = False
                for used_link, used_title in used_titles.items():
                    if self._similarity(title, used_title) >= 0.80:
                        is_similar = True
                        removed_count += 1
                        break
                
                if not is_similar:
                    filtered_articles.append(art)
            
            if filtered_articles:
                filtered[src] = filtered_articles
        
        if removed_count > 0:
            print(f"🔄 {removed_count} tekrar eden haber filtrelendi")
        
        return filtered
    
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
        
        # RSS hatalarını kaydet
        if self.rss_errors:
            self._save_rss_errors()
        
        # Tekrar edenleri filtrele
        all_news = self._filter_duplicates(all_news)
        
        # Toplam yeniden hesapla
        total = sum(len(arts) for arts in all_news.values())
        full_text_success = sum(1 for arts in all_news.values() for art in arts if art.get('success'))
        
        print(f"\n{'='*70}")
        print(f"📊 {total} haber (tekrarsız) | {full_text_success} tam metin")
        print(f"{'='*70}\n")
        return all_news
    
    def save_txt(self, news_data):
        """Ham RSS'i günlük kaydet (üzerine yaz)"""
        print("💾 TXT dosyaları kaydediliyor...")
        now = datetime.now()
        os.makedirs("data", exist_ok=True)
        
        txt = f"\n{'='*80}\n📅 {now.strftime('%d %B %Y').upper()} - SİBER GÜVENLİK HABERLERİ (HAM RSS)\n{'='*80}\n\n"
        
        # Kullanılan haberleri topla
        all_articles = []
        num = 0
        for src, articles in news_data.items():
            for art in articles:
                num += 1
                all_articles.append(art)
                txt += f"[{num}] {src} - {art['title']}\n{'─'*80}\n"
                txt += f"Tarih: {art['date']}\nLink: {art['link']}\n"
                if art.get('full_text') and art.get('word_count', 0) > 0:
                    txt += f"\n[TAM METİN - {art['word_count']} kelime]\n{art['full_text']}\n"
                else:
                    txt += f"\n⚠️  Tam metin çekilemedi\n"
                txt += f"\n(XXXXXXX, AÇIK - {art.get('domain','')}, {now.strftime('%d.%m.%Y')})\n\n{'='*80}\n\n"
        
        # HAM RSS - GÜNLÜK (Üzerine Yaz)
        with open("data/haberler_ham.txt", 'w', encoding='utf-8') as f:
            f.write(txt)
        
        print(f"✅ data/haberler_ham.txt (günlük - üzerine yazıldı)")
        
        # Kullanılan linkleri kaydet
        self._save_used_links(all_articles)
        
        return txt
        with open("data/haberler_ham.txt", 'w', encoding='utf-8') as f:
            f.write(txt)
        
        print(f"✅ data/haberler_ham.txt (günlük - üzerine yazıldı)")
        return txt
    
    def save_summary_to_archive(self, html_content):
        """Gemini'nin ürettiği HTML özetini TXT arşivine EKLE (asla silme)"""
        print("📚 Gemini özeti arşive ekleniyor...")
        now = datetime.now()
        
        # HTML'den text özeti çıkar (BeautifulSoup zaten import edilmiş)
        soup = BeautifulSoup(html_content, 'html.parser')
        
        archive_entry = f"\n{'='*80}\n📅 {now.strftime('%d %B %Y').upper()} - GEMİNİ ÖZET RAPORU\n{'='*80}\n\n"
        
        # Günlük özet
        exec_summary = soup.find('div', class_='executive-summary')
        if exec_summary:
            archive_entry += f"GÜNLÜK ÖZET:\n{exec_summary.get_text(strip=True)}\n\n"
        
        # Her haber
        news_items = soup.find_all('div', class_='news-item')
        for idx, item in enumerate(news_items, 1):
            title = item.find('b', class_='news-title')
            content = item.find('p', class_='news-content')
            source = item.find('p', class_='source')
            
            if title:
                archive_entry += f"[{idx}] {title.get_text(strip=True)}\n"
            if content:
                archive_entry += f"{content.get_text(strip=True)}\n"
            if source:
                archive_entry += f"{source.get_text(strip=True)}\n"
            archive_entry += "\n" + "─"*80 + "\n\n"
        
        # ARŞİVE EKLE (append - asla silme)
        with open(ARCHIVE_FILE, 'a', encoding='utf-8') as f:
            f.write(archive_entry)
        
        print(f"✅ {ARCHIVE_FILE} (arşiv - eklendi, asla silinmez)")
    
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
        
        # Son 30 gün linklerini ekle
        html = self._add_archive_links(html)
        
        # Kaydet
        os.makedirs("docs/raporlar", exist_ok=True)
        now = datetime.now()
        
        with open("docs/index.html", 'w', encoding='utf-8') as f:
            f.write(html)
        
        with open(f"docs/raporlar/{now.strftime('%Y-%m-%d')}.html", 'w', encoding='utf-8') as f:
            f.write(html)
        
        print("✅ docs/index.html")
        print(f"✅ docs/raporlar/{now.strftime('%Y-%m-%d')}.html")
        
        # Gemini özetini arşive ekle
        self.save_summary_to_archive(html)
        
        # 30 günden eski raporları sil
        self._cleanup_old_reports()
        
        return html
    
    def _add_archive_links(self, html):
        """HTML'e son 30 günün linklerini ekle"""
        from datetime import timedelta
        
        # Son 30 günün raporlarını bul
        reports = []
        for i in range(30):
            date = datetime.now() - timedelta(days=i)
            filepath = f"docs/raporlar/{date.strftime('%Y-%m-%d')}.html"
            if os.path.exists(filepath):
                reports.append({
                    'date': date.strftime('%d.%m.%Y'),
                    'filename': date.strftime('%Y-%m-%d')
                })
        
        # Hiç rapor yoksa (ilk gün) linkler ekleme
        if not reports:
            print("   ℹ️  Henüz arşiv yok (ilk gün)")
            return html
        
        # Arşiv linkleri HTML'i
        archive_html = """
    <div class="archive-section">
        <h3>📚 Arşiv - Son 30 Gün</h3>
        <div class="archive-links">
"""
        for report in reports:
            archive_html += f'            <a href="/siberguvenlik/raporlar/{report["filename"]}.html" class="archive-link">{report["date"]}</a>\n'
        
        archive_html += """        </div>
    </div>
"""
        
        # </body> etiketinden önce ekle
        if '</body>' in html:
            html = html.replace('</body>', archive_html + '\n</body>')
        elif '</html>' in html:
            # </body> yoksa </html>'den önce ekle
            html = html.replace('</html>', archive_html + '\n</html>')
        else:
            # İkisi de yoksa sona ekle
            html += archive_html
        
        print(f"   ✅ {len(reports)} günlük arşiv linki eklendi")
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
