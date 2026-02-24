"""
Siber Güvenlik Haberleri - Günlük Rapor Sistemi
v2.2 - Gemini 2.5 Flash + HTML Doğrulama + Eksik Paragraf Tamamlama
"""

import os
import time
import hashlib
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
import google.generativeai as genai

from src.config import (
    GEMINI_API_KEY, NEWS_SOURCES, HEADERS, CONTENT_SELECTORS,
    ARCHIVE_FILE, get_claude_prompt
)


# ===== YARDIMCI FONKSİYONLAR =====

def _calculate_content_hash(title, description):
    """Title + description'dan MD5 hash hesapla (16 karakter hex)"""
    content = f"{title or ''}{description or ''}".lower().strip()
    return hashlib.md5(content.encode('utf-8')).hexdigest()[:16]


def _normalize_url_advanced(link):
    """
    Gelişmiş URL normalizasyonu:
    - UTM parametrelerini kaldırma
    - Protocol standardizasyonu (http→https)
    - Query parametreleri sorting
    - The Register proxy URL'lerini çözme
    - Google FeedBurner redirect'lerini çözme
    - Trailing slash normalizasyonu
    """
    if not link:
        return ''

    try:
        # The Register proxy URL fix
        if 'go.theregister.com' in link:
            parsed = urlparse(link)
            qs = parse_qs(parsed.query)
            if 'td' in qs:
                link = qs['td'][0]

        # FeedBurner redirect fix
        if 'feedproxy.google.com' in link or 'feeds.feedburner.com' in link:
            try:
                r = requests.head(link, allow_redirects=True, timeout=5)
                if r.url and r.url != link:
                    link = r.url
            except:
                pass

        parsed = urlparse(link)

        # Protocol → https
        scheme = 'https'
        netloc = parsed.netloc.lower().replace('www.', '')
        path = parsed.path

        # UTM ve tracking parametrelerini kaldır
        utm_params = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_term',
                      'utm_content', 'ref', 'source', 'mc_cid', 'mc_eid'}
        qs = parse_qs(parsed.query, keep_blank_values=False)
        filtered_qs = {k: v for k, v in qs.items() if k.lower() not in utm_params}

        # Query parametrelerini sıralı birleştir
        query_string = urlencode(sorted(filtered_qs.items()), doseq=True)

        # Yeniden oluştur
        normalized = urlunparse((scheme, netloc, path, '', query_string, ''))

        # Trailing slash kaldır
        normalized = normalized.rstrip('/')

        return normalized
    except:
        # Parse hatası durumunda orijinalini döndür
        return link.rstrip('/')


def _parse_article_date(date_str, fallback):
    """RSS tarihini DD.MM.YYYY formatına çevirir (TR UTC+3), parse edilemezse bugünün tarihini kullanır"""
    from datetime import timezone, timedelta as td
    TR = timezone(td(hours=3))
    if not date_str:
        return fallback.strftime('%d.%m.%Y')
    date_str = date_str.strip()
    # Timezone-aware formatlar: UTC→TR dönüşümü yap
    for fmt in ['%a, %d %b %Y %H:%M:%S %z', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S.%f%z']:
        try:
            return datetime.strptime(date_str, fmt).astimezone(TR).strftime('%d.%m.%Y')
        except:
            pass
    # Z sonekini +00:00 ile değiştirip tekrar dene
    if date_str.endswith('Z'):
        try:
            return datetime.strptime(date_str[:-1], '%Y-%m-%dT%H:%M:%S.%f').replace(
                tzinfo=timezone.utc).astimezone(TR).strftime('%d.%m.%Y')
        except:
            pass
        try:
            return datetime.strptime(date_str[:-1], '%Y-%m-%dT%H:%M:%S').replace(
                tzinfo=timezone.utc).astimezone(TR).strftime('%d.%m.%Y')
        except:
            pass
    # Timezone-naive formatlar: olduğu gibi al
    for fmt in ['%a, %d %b %Y %H:%M:%S %Z', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d']:
        try:
            return datetime.strptime(date_str, fmt).strftime('%d.%m.%Y')
        except:
            pass
    return fallback.strftime('%d.%m.%Y')


# ===== ANA SİSTEM =====

class HaberSistemi:
    def __init__(self):
        self.headers = HEADERS
        self.sources = NEWS_SOURCES
        self.selectors = CONTENT_SELECTORS
        self.rss_errors = []
        self.used_links_file = "data/haberler_linkler.txt"
        self.rss_errors_file = "data/rss_errors.txt"

    def fetch_full_article(self, url, source_name):
        """Tam metin çeker"""
        try:
            print(f"      📄 Tam metin...", end='', flush=True)
            r = requests.get(url, headers=self.headers, timeout=15)
            soup = BeautifulSoup(r.text, 'html.parser')
            domain = urlparse(url).netloc.replace('www.', '')

            text = ""
            if source_name in self.selectors:
                for sel in self.selectors[source_name]:
                    el = soup.find(**sel)
                    if el:
                        text = self._extract(el)
                        break

            if not text:
                for tag in ['article', 'main']:
                    el = soup.find(tag)
                    if el:
                        text = self._extract(el)
                        if text:
                            break

            if not text:
                el = soup.find('div', class_=lambda c: c and any(x in str(c).lower() for x in ['content', 'article', 'body', 'post']))
                if el:
                    text = self._extract(el)

            wc = len(text.split()) if text else 0
            text = text.replace('\t', ' ').replace('\r', '')

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
        if not element:
            return ""
        parts = []
        for p in element.find_all(['p', 'h1', 'h2', 'h3', 'li']):
            t = p.get_text().strip()
            if len(t) > 20 and not any(x in t.lower() for x in ['cookie', 'subscribe', 'newsletter']):
                parts.append(t)
        return '\n\n'.join(parts)

    def fetch_rss(self, url, source_name):
        """RSS çeker"""
        try:
            r = requests.get(url, headers=self.headers, timeout=15)
            root = ET.fromstring(r.content)
            articles = []

            if root.tag.endswith('feed'):  # Atom
                for entry in root.findall('.//{http://www.w3.org/2005/Atom}entry')[:10]:
                    t = entry.find('{http://www.w3.org/2005/Atom}title')
                    l = entry.find('{http://www.w3.org/2005/Atom}link')
                    s = entry.find('{http://www.w3.org/2005/Atom}summary')
                    d = entry.find('{http://www.w3.org/2005/Atom}published')
                    if t is not None:
                        articles.append({
                            'title': t.text,
                            'link': l.get('href') if l is not None else '',
                            'description': s.text if s is not None else '',
                            'date': d.text if d is not None else '',
                            'source': source_name
                        })
            else:  # RSS
                for item in root.findall('.//item')[:10]:
                    t = item.find('title')
                    l = item.find('link')
                    d = item.find('description')
                    p = item.find('pubDate')
                    if t is not None:
                        articles.append({
                            'title': t.text,
                            'link': l.text if l is not None else '',
                            'description': d.text if d is not None else '',
                            'date': p.text if p is not None else '',
                            'source': source_name
                        })
            return articles
        except Exception as e:
            error_msg = f"RSS hatası - {source_name}: {str(e)[:100]}"
            self.rss_errors.append(error_msg)
            print(f"      ❌ RSS HATA: {str(e)[:50]}")
            return []

    def _load_used_links(self):
        """
        Kullanılan linkleri 7 günden yükle
        Backward compatibility: eski format (3 sütun) ve yeni format (4 sütun + hash) destekler
        """
        if not os.path.exists(self.used_links_file):
            return set(), {}, set()

        cutoff = datetime.now() - timedelta(days=7)
        used_links = set()
        used_titles = {}
        used_hashes = set()

        try:
            with open(self.used_links_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        parts = line.split('\t')
                        date_str = parts[0]
                        date = datetime.strptime(date_str, '%Y-%m-%d')

                        if date < cutoff:
                            continue

                        if len(parts) >= 4:
                            link, title, content_hash = parts[1], '\t'.join(parts[2:-1]), parts[-1]
                            used_links.add(_normalize_url_advanced(link))
                            used_titles[link] = title
                            used_hashes.add(content_hash)
                        elif len(parts) >= 3:
                            link, title = parts[1], '\t'.join(parts[2:])
                            used_links.add(_normalize_url_advanced(link))
                            used_titles[link] = title
                    except Exception:
                        continue
        except IOError as e:
            print(f"   ⚠️  Uyarı: Linkler dosyası okunurken hata - {e}")

        return used_links, used_titles, used_hashes

    def _similarity(self, a, b):
        """Başlık benzerliği"""
        return SequenceMatcher(None, a.lower(), b.lower()).ratio()

    def _save_used_links(self, articles):
        """Kullanılan linkleri kaydet (7 günden eski olanları sil)"""
        if not articles:
            return

        now = datetime.now()
        cutoff = now - timedelta(days=7)

        existing = []
        if os.path.exists(self.used_links_file):
            try:
                with open(self.used_links_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            parts = line.split('\t')
                            date_str = parts[0]
                            date = datetime.strptime(date_str, '%Y-%m-%d')
                            if date >= cutoff:
                                existing.append(line)
                        except:
                            pass
            except IOError:
                pass

        today = now.strftime('%Y-%m-%d')
        for art in articles:
            if art.get('link'):
                title = art.get('title', '')
                description = art.get('description', '')
                content_hash = _calculate_content_hash(title, description)
                existing.append(f"{today}\t{art['link']}\t{title}\t{content_hash}")

        os.makedirs("data", exist_ok=True)
        try:
            with open(self.used_links_file, 'w', encoding='utf-8') as f:
                f.write('\n'.join(existing) + '\n')
        except IOError as e:
            print(f"   ❌ Hata: Linkler dosyasına yazılamadı - {e}")

    def _save_rss_errors(self):
        """RSS hatalarını kaydet (7 günden eski olanları sil)"""
        if not self.rss_errors:
            return

        now = datetime.now()
        cutoff = now - timedelta(days=7)

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

        timestamp = now.strftime('%Y-%m-%d %H:%M')
        for error in self.rss_errors:
            existing.append(f"{timestamp} | {error}")

        os.makedirs("data", exist_ok=True)
        with open(self.rss_errors_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(existing) + '\n')

        print(f"⚠️  {len(self.rss_errors)} RSS hatası kaydedildi: {self.rss_errors_file}")

    def _normalize_link(self, link):
        """Link normalizasyonu (DEPRECATED - _normalize_url_advanced() kullan)"""
        return _normalize_url_advanced(link)

    def _filter_duplicates(self, all_news):
        """
        Tekrar eden haberleri filtrele (3 seviye: link + hash + benzerlik)
        """
        used_links, used_titles, used_hashes = self._load_used_links()

        filtered = {}
        removed_count = 0
        detail_removed = {'link': 0, 'hash': 0, 'similarity': 0}

        for src, articles in all_news.items():
            filtered_articles = []

            for art in articles:
                link = art.get('link', '')
                title = art.get('title', '')
                description = art.get('description', '')
                link_norm = _normalize_url_advanced(link)

                # Seviye 1: Link kontrolü
                if link_norm in used_links:
                    removed_count += 1
                    detail_removed['link'] += 1
                    continue

                # Seviye 2: Content hash kontrolü
                content_hash = _calculate_content_hash(title, description)
                if content_hash in used_hashes:
                    removed_count += 1
                    detail_removed['hash'] += 1
                    continue

                # Seviye 3: Başlık benzerliği
                is_similar = False
                for used_title in used_titles.values():
                    similarity = SequenceMatcher(None, title.lower(), used_title.lower()).ratio()
                    if similarity >= 0.85:
                        is_similar = True
                        removed_count += 1
                        detail_removed['similarity'] += 1
                        break

                if is_similar:
                    continue

                filtered_articles.append(art)

            if filtered_articles:
                filtered[src] = filtered_articles

        if removed_count > 0:
            print(f"🔄 {removed_count} tekrar eden haber filtrelendi")
            print(f"   ├─ URL: {detail_removed['link']}")
            print(f"   ├─ Hash: {detail_removed['hash']}")
            print(f"   └─ Benzerlik: {detail_removed['similarity']}")

        return filtered

    def _filter_old_articles(self, all_news):
        """Bugüne ait olmayan haberleri filtrele (UTC+3 Türkiye saatine göre)"""
        from datetime import timezone, timedelta as td
        TR = timezone(td(hours=3))
        today_tr = datetime.now(TR).date()
        yesterday_tr = today_tr - td(days=1)
        cutoff = yesterday_tr

        filtered = {}
        removed_count = 0

        for src, articles in all_news.items():
            filtered_articles = []
            for art in articles:
                art_date_str = _parse_article_date(art.get('date', ''), datetime.now())
                try:
                    parsed_date = datetime.strptime(art_date_str, '%d.%m.%Y').date()
                except:
                    filtered_articles.append(art)
                    continue

                if parsed_date >= cutoff:
                    filtered_articles.append(art)
                else:
                    removed_count += 1

            if filtered_articles:
                filtered[src] = filtered_articles

        if removed_count > 0:
            print(f"📅 {removed_count} eski tarihli haber filtrelendi")

        return filtered

    def topla(self):
        """Tüm haberleri topla"""
        print("=" * 70)
        print("📰 HABERLERİ TOPLAMA")
        print("=" * 70)
        print(f"🔍 {len(self.sources)} kaynak | ⏱️  15-25 dakika\n")

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
                        if res['success']:
                            full_text_success += 1
                        time.sleep(2)
                all_news[src] = articles
            else:
                print(f"   └─ ❌ Bulunamadı")

            time.sleep(1)

        if self.rss_errors:
            self._save_rss_errors()

        all_news = self._filter_duplicates(all_news)
        all_news = self._filter_old_articles(all_news)

        total = sum(len(arts) for arts in all_news.values())
        full_text_success = sum(1 for arts in all_news.values() for art in arts if art.get('success'))

        print(f"\n{'=' * 70}")
        print(f"📊 {total} haber (tekrarsız) | {full_text_success} tam metin")
        print(f"{'=' * 70}\n")
        return all_news

    def save_txt(self, news_data):
        """Ham RSS'i günlük kaydet (üzerine yaz)"""
        print("💾 TXT dosyaları kaydediliyor...")
        now = datetime.now()
        os.makedirs("data", exist_ok=True)

        txt = f"\n{'=' * 80}\n📅 {now.strftime('%d %B %Y').upper()} - SİBER GÜVENLİK HABERLERİ (HAM RSS)\n{'=' * 80}\n\n"

        all_articles = []
        num = 0
        for src, articles in news_data.items():
            for art in articles:
                num += 1
                all_articles.append(art)
                txt += f"[{num}] {src} - {art['title']}\n{'─' * 80}\n"
                txt += f"Tarih: {art['date']}\nLink: {art['link']}\n"
                if art.get('full_text') and art.get('word_count', 0) > 0:
                    txt += f"\n[TAM METİN - {art['word_count']} kelime]\n{art['full_text']}\n"
                else:
                    txt += f"\n⚠️  Tam metin çekilemedi\n"
                art_date = _parse_article_date(art.get('date', ''), now)
                txt += f"\n(XXXXXXX, AÇIK - {art.get('link', '')}, {art.get('domain', '')}, {art_date})\n\n{'=' * 80}\n\n"

        with open("data/haberler_ham.txt", 'w', encoding='utf-8') as f:
            f.write(txt)

        print(f"✅ data/haberler_ham.txt (günlük - üzerine yazıldı)")

        self._save_used_links(all_articles)

        return txt

    def save_summary_to_archive(self, html_content):
        """Gemini'nin seçtiği EN ÖNEMLİ 43 HABERİ TXT arşivine EKLE (sürekli birikim)"""
        print("📚 En önemli 43 haber arşive ekleniyor...")
        now = datetime.now()

        soup = BeautifulSoup(html_content, 'html.parser')

        archive_entry = f"\n{'=' * 80}\n📅 {now.strftime('%d %B %Y').upper()} - EN ÖNEMLİ 43 HABER (SEÇİLMİŞ)\n{'=' * 80}\n\n"

        news_items = soup.find_all('div', class_='news-item')[:43]

        for idx, item in enumerate(news_items, 1):
            title_elem = item.find('div', class_='news-title')
            content_elem = item.find('p', class_='news-content')
            source_elem = item.find('p', class_='source')

            if title_elem and content_elem:
                title = title_elem.get_text(strip=True).replace('<b>', '').replace('</b>', '')
                content = content_elem.get_text(strip=True)
                source = source_elem.get_text(strip=True) if source_elem else ""

                archive_entry += f"[{idx:2d}] {title}\n"
                archive_entry += f"─────────────────────────────────────────────────────────\n"
                archive_entry += f"{content}\n"
                if source:
                    archive_entry += f"{source}\n"
                archive_entry += "\n" + "─" * 80 + "\n\n"

        os.makedirs("data", exist_ok=True)
        with open(ARCHIVE_FILE, 'a', encoding='utf-8') as f:
            f.write(archive_entry)

        print(f"✅ {ARCHIVE_FILE} (en önemli {len(news_items)} haber arşivlendi)")

        self._check_archive_size()

    def _check_archive_size(self):
        """Arşiv boyutunu kontrol et ve 100 MB'ı geçince uyar (SİLMEZ)"""
        if not os.path.exists(ARCHIVE_FILE):
            return

        file_size = os.path.getsize(ARCHIVE_FILE) / (1024 * 1024)
        print(f"📦 Arşiv boyutu: {file_size:.1f} MB")

        if file_size >= 100:
            print("")
            print("=" * 70)
            print("🚨 UYARI: ARŞİV DOSYASI 100 MB'I AŞTI!")
            print("=" * 70)
            print(f"📁 Dosya: {ARCHIVE_FILE}")
            print(f"📏 Boyut: {file_size:.1f} MB")
            print(f"📅 Tarih: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
            print("")
            print("⚠️  Lütfen aşağıdaki adımlardan birini uygulayın:")
            print("   1. Dosyayı yedekleyip harici depolamaya taşıyın")
            print("   2. Eski kayıtları manuel olarak arşivleyin")
            print("")
            print("❌ Arşiv otomatik olarak SİLİNMEYECEKTİR.")
            print("=" * 70)
            print("")

    # ═══════════════════════════════════════════════════════════════
    # HTML OLUŞTURMA — DOĞRULAMA + TAMAMLAMA MEKANİZMALI (v2.1)
    # ═══════════════════════════════════════════════════════════════

    def create_html(self, txt_content):
        """Gemini ile HTML oluştur — DOĞRULAMA + TAMAMLAMA MEKANİZMALI"""
        print("🤖 Gemini API...")
        if not GEMINI_API_KEY:
            raise ValueError("❌ GEMINI_API_KEY yok!")

        genai.configure(api_key=GEMINI_API_KEY)

        # ═══════════════════════════════════════════
        # AŞAMA 1: Gemini'den HTML al (retry ile)
        # ═══════════════════════════════════════════
        html = None
        max_retries = 3
        for attempt in range(max_retries):
            try:
                print(f"   Deneme {attempt + 1}/{max_retries}...")

                prompt = get_claude_prompt(txt_content)
                model = genai.GenerativeModel('gemini-2.5-flash')

                response = model.generate_content(
                    prompt,
                    generation_config=genai.GenerationConfig(
                        max_output_tokens=65536,
                        temperature=0.7,
                    )
                )

                # ✅ YENİ: finish_reason logla
                if response.candidates:
                    finish_reason = response.candidates[0].finish_reason
                    print(f"   📝 Finish reason: {finish_reason}")
                    if str(finish_reason) not in ['STOP', 'FinishReason.STOP', '1']:
                        print(f"   ⚠️  Yanıt normal bitmedi! (reason: {finish_reason})")

                html = response.text
                break
            except Exception as e:
                print(f"   ⚠️  Hata: {e}")
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 10
                    print(f"   ⏳ {wait_time} saniye bekleyip tekrar deneniyor...")
                    time.sleep(wait_time)
                else:
                    print(f"   ❌ {max_retries} deneme başarısız, fallback HTML...")
                    return self._create_fallback_html(txt_content)

        if not html:
            return self._create_fallback_html(txt_content)

        # HTML temizle
        if html.startswith('```html'):
            html = html[7:]
        if html.startswith('```'):
            html = html[3:]
        if html.endswith('```'):
            html = html[:-3]
        html = html.strip()

        print(f"✅ HTML oluşturuldu ({len(html)} karakter)")

        # ═══════════════════════════════════════════
        # AŞAMA 2: DOĞRULAMA — Paragraf sayısı kontrolü
        # ═══════════════════════════════════════════
        validation = self._validate_html_completeness(html)

        # ═══════════════════════════════════════════
        # AŞAMA 3: EKSİK PARAGRAF TAMAMLAMA (max 2 tur)
        # ═══════════════════════════════════════════
        completion_attempts = 0
        max_completion_attempts = 2

        while not validation['is_valid'] and completion_attempts < max_completion_attempts:
            completion_attempts += 1
            print(f"\n   🔄 Tamamlama denemesi {completion_attempts}/{max_completion_attempts}...")

            html = self._complete_missing_paragraphs(html, txt_content, validation)
            validation = self._validate_html_completeness(html)

        if not validation['is_valid']:
            print(f"   ⚠️  {max_completion_attempts} tamamlama sonrası hâlâ eksik var")
            print(f"   📊 Final: Özet={validation['summary_count']}, Paragraf={validation['paragraph_count']}")
        else:
            print(f"   ✅ Tüm paragraflar tamam! ({validation['paragraph_count']} haber)")

        # ═══════════════════════════════════════════
        # AŞAMA 4: Mevcut post-processing
        # ═══════════════════════════════════════════
        html = self._fix_source_dates(html, txt_content)

        html_index = self._add_archive_links(html, is_archive=False)
        html_archive = self._add_archive_links(html, is_archive=True)

        # Kaydet
        os.makedirs("docs/raporlar", exist_ok=True)
        now = datetime.now()

        with open("docs/index.html", 'w', encoding='utf-8') as f:
            f.write(html_index)

        with open(f"docs/raporlar/{now.strftime('%Y-%m-%d')}.html", 'w', encoding='utf-8') as f:
            f.write(html_archive)

        print("✅ docs/index.html")
        print(f"✅ docs/raporlar/{now.strftime('%Y-%m-%d')}.html")

        self.save_summary_to_archive(html)
        self._cleanup_old_reports()

        return html

    def _validate_html_completeness(self, html):
        """HTML'deki yönetici özeti sayısı ile haber paragrafı sayısını karşılaştır"""
        import re

        soup = BeautifulSoup(html, 'html.parser')

        # Önemli gelişmeler (5 adet) + tablodaki haberler
        important_items = soup.find_all('div', class_='important-item')
        table_links = []
        exec_table = soup.find('table', class_='executive-table')
        if exec_table:
            table_links = exec_table.find_all('a')

        summary_count = len(important_items) + len(table_links)

        # Haber paragraflarını say
        news_items = soup.find_all('div', class_='news-item')
        paragraph_count = len(news_items)

        # Mevcut paragrafların ID'lerini çıkar
        existing_ids = set()
        for item in news_items:
            item_id = item.get('id', '')
            match = re.search(r'haber-(\d+)', item_id)
            if match:
                existing_ids.add(int(match.group(1)))

        # Eksik ID'leri bul
        if summary_count > 0:
            expected_ids = set(range(1, summary_count + 1))
            missing_ids = sorted(expected_ids - existing_ids)
        else:
            missing_ids = []

        last_paragraph_id = max(existing_ids) if existing_ids else 0
        is_valid = paragraph_count >= summary_count and len(missing_ids) == 0

        result = {
            'is_valid': is_valid,
            'summary_count': summary_count,
            'paragraph_count': paragraph_count,
            'missing_ids': missing_ids,
            'last_paragraph_id': last_paragraph_id
        }

        status = "✅ TAMAM" if is_valid else "❌ EKSİK"
        print(f"   📊 Doğrulama: Özet={summary_count}, Paragraf={paragraph_count} {status}")
        if missing_ids:
            print(f"   ⚠️  Eksik haber ID'leri: {missing_ids}")

        return result

    def _complete_missing_paragraphs(self, html, txt_content, validation):
        """Eksik haber paragraflarını Gemini'ye tamamlattır ve HTML'e ekle"""
        import re

        missing_ids = validation['missing_ids']
        last_id = validation['last_paragraph_id']

        if not missing_ids:
            return html

        print(f"   🔄 {len(missing_ids)} eksik paragraf tamamlanıyor (ID: {missing_ids[0]}-{missing_ids[-1]})...")

        # Mevcut HTML'den eksik haberlerin başlıklarını çıkar
        soup = BeautifulSoup(html, 'html.parser')
        all_titles = {}

        # Önemli gelişmelerden
        for item in soup.find_all('div', class_='important-item'):
            link = item.find('a')
            if link:
                match = re.search(r'#haber-(\d+)', link.get('href', ''))
                if match:
                    haber_id = int(match.group(1))
                    title_text = re.sub(r'^\d+\.\s*', '', link.get_text(strip=True))
                    all_titles[haber_id] = title_text

        # Tablodan
        exec_table = soup.find('table', class_='executive-table')
        if exec_table:
            for link in exec_table.find_all('a'):
                match = re.search(r'#haber-(\d+)', link.get('href', ''))
                if match:
                    haber_id = int(match.group(1))
                    title_text = re.sub(r'^\d+\.\s*', '', link.get_text(strip=True))
                    all_titles[haber_id] = title_text

        # Eksik başlıkları listele
        missing_titles = []
        for mid in missing_ids:
            title = all_titles.get(mid, f"Haber #{mid}")
            missing_titles.append(f"  - haber-{mid}: {title}")

        titles_text = "\n".join(missing_titles)

        # Tamamlama prompt'u
        completion_prompt = f"""Aşağıdaki siber güvenlik haberlerinin SADECE eksik paragraf özetlerini yaz.

HAM HABER METNİ:
{txt_content}

EKSİK HABER PARAGRAFLARI (SADECE bu ID'lerin paragraflarını yaz):
{titles_text}

HER PARAGRAF İÇİN ÇIKTI FORMATI (SADECE BU FORMATTA, BAŞKA HİÇBİR ŞEY YAZMA):

<div class="news-item" id="haber-N">
    <div class="news-title"><b>Haberin Başlığı</b></div>
    <p class="news-content">100-130 kelime paragraf özet, resmi Türkçe.</p>
    <p class="source"><b>(KAYNAK, AÇIK - <a href="LINK" target="_blank">domain.com</a>, TARİH)</b></p>
</div>

KURALLAR:
- SADECE eksik paragrafları yaz, CSS/başlık/açıklama YAZMA
- Her paragraf 100-130 kelime
- Resmi Türkçe (-mıştır, -edilmiştir)
- Sıra: haber-{missing_ids[0]}'den haber-{missing_ids[-1]}'e
- Kod bloğu (```) KULLANMA, direkt HTML yaz
"""

        try:
            model = genai.GenerativeModel('gemini-2.5-flash')
            response = model.generate_content(
                completion_prompt,
                generation_config=genai.GenerationConfig(
                    max_output_tokens=65536,
                    temperature=0.7,
                )
            )

            new_paragraphs = response.text

            # Temizle
            if new_paragraphs.startswith('```html'):
                new_paragraphs = new_paragraphs[7:]
            if new_paragraphs.startswith('```'):
                new_paragraphs = new_paragraphs[3:]
            if new_paragraphs.endswith('```'):
                new_paragraphs = new_paragraphs[:-3]
            new_paragraphs = new_paragraphs.strip()

            if not new_paragraphs:
                print("   ⚠️  Tamamlama yanıtı boş geldi")
                return html

            # Yeni paragraf sayısını kontrol et
            new_soup = BeautifulSoup(new_paragraphs, 'html.parser')
            new_items = new_soup.find_all('div', class_='news-item')
            print(f"   ✅ {len(new_items)} yeni paragraf alındı")

            if len(new_items) == 0:
                print("   ⚠️  Tamamlama yanıtında news-item bulunamadı")
                return html

            # HTML'e ekle: son news-item'ın source paragrafından sonra
            all_source_ends = list(re.finditer(
                r'</p>\s*</div>\s*(?=\s*(?:</div>|<div\s+class="news-item"|<a\s+href))',
                html
            ))

            if all_source_ends:
                insert_pos = all_source_ends[-1].end()
                html = html[:insert_pos] + "\n\n            " + new_paragraphs + "\n" + html[insert_pos:]
                print(f"   ✅ Eksik paragraflar HTML'e eklendi")
            else:
                # Fallback: </body> etiketinden önce
                body_close = html.rfind('</body>')
                if body_close > 0:
                    html = html[:body_close] + "\n" + new_paragraphs + "\n" + html[body_close:]
                    print(f"   ⚠️  Fallback: </body> önüne eklendi")
                else:
                    html += "\n" + new_paragraphs
                    print(f"   ⚠️  Fallback: HTML sonuna eklendi")

            return html

        except Exception as e:
            print(f"   ❌ Tamamlama hatası: {e}")
            return html

    def _fix_source_dates(self, html, txt_content):
        """Gemini'nin yazdığı hatalı tarihleri ham TXT'deki gerçek tarihlerle düzelt"""
        import re

        link_to_date = {}
        pattern = re.compile(
            r'[(]XXXXXXX, AÇIK - (https?://[^\s,]+),\s*[^,]+,\s*(\d{2}[.]\d{2}[.]\d{4})[)]'
        )
        for m in pattern.finditer(txt_content):
            link_to_date[m.group(1).strip()] = m.group(2).strip()

        if not link_to_date:
            return html

        source_pattern = re.compile(r'<p class="source">.*?</p>', re.DOTALL)
        href_pattern = re.compile(r'href="(https?://[^"]+)"')
        date_pattern = re.compile(r'\d{2}[.]\d{2}[.]\d{4}(?=[)])')

        def fix_source(m):
            src = m.group(0)
            href_m = href_pattern.search(src)
            if not href_m:
                return src
            href = href_m.group(1).strip()
            if href not in link_to_date:
                return src
            return date_pattern.sub(link_to_date[href], src)

        fixed_html = source_pattern.sub(fix_source, html)
        print("   ✅ Kaynak tarihleri düzeltildi")
        return fixed_html

    def _add_archive_links(self, html, is_archive=False):
        """HTML'e son 30 günün linklerini ekle"""

        reports = []
        for i in range(30):
            date = datetime.now() - timedelta(days=i)
            filepath = f"docs/raporlar/{date.strftime('%Y-%m-%d')}.html"
            if os.path.exists(filepath):
                reports.append({
                    'date': date.strftime('%d.%m.%Y'),
                    'filename': date.strftime('%Y-%m-%d')
                })

        if not reports:
            print("   ℹ️  Henüz arşiv yok (ilk gün)")
            return html

        link_prefix = "./" if is_archive else "./raporlar/"

        archive_html = """
    <div class="archive-section">
        <h3>📚 Arşiv - Son 30 Gün</h3>
        <div class="archive-links">
"""
        for report in reports:
            archive_html += f'            <a href="{link_prefix}{report["filename"]}.html" class="archive-link">{report["date"]}</a>\n'

        archive_html += """        </div>
    </div>
"""

        if '</body>' in html:
            html = html.replace('</body>', archive_html + '\n</body>')
        elif '</html>' in html:
            html = html.replace('</html>', archive_html + '\n</html>')
        else:
            html += archive_html

        print(f"   ✅ {len(reports)} günlük arşiv linki eklendi")
        return html

    def _create_fallback_html(self, txt_content):
        """Gemini API başarısız olursa basit HTML"""
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

        cutoff = datetime.now() - timedelta(days=30)
        deleted = 0

        for filepath in glob.glob("docs/raporlar/*.html"):
            try:
                filename = os.path.basename(filepath)
                if filename == '.gitkeep':
                    continue

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
    print("\n" + "=" * 70)
    print("🔒 SİBER GÜVENLİK HABERLERİ")
    print("=" * 70)
    print(f"📅 {datetime.now().strftime('%d %B %Y %H:%M')}")
    print("=" * 70 + "\n")

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

    print("\n" + "=" * 70)
    print("✨ TAMAMLANDI!")
    print("=" * 70)
    print("🌐 https://siberguvenlikhaberler.github.io/siberguvenlik/")
    print("=" * 70 + "\n")

    return 0


if __name__ == "__main__":
    exit(main())
