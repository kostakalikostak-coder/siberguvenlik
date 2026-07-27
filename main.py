"""
Siber Güvenlik Haberleri - Günlük Rapor Sistemi
v2.2 - Gemini 2.5 Flash + HTML Doğrulama + Eksik Paragraf Tamamlama
"""

import os
import re
import json
import time
import hashlib
import html as _html_mod
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

# Rapor/dosya adı/idempotency gün damgaları TÜRKİYE gününe göre atılır.
# GitHub Actions UTC çalıştığından naive datetime.now() TR gece yarısı
# civarında (00:00-03:00 TR) bir ÖNCEKİ günü üretir: rapor dünkü dosyaya
# yazılır, "bugünün raporu var mı" kontrolü yanlış güne bakardı.
# Naive (tzinfo'suz) döner ki mevcut strftime/timedelta aritmetiğiyle ve
# naive datetime karşılaştırmalarıyla uyumlu kalsın. Saat-TABANLI mutlak
# kesim pencereleri (timedelta(hours=...) + .timestamp()) bilinçli olarak
# datetime.now() ile bırakıldı — onlar duvar saatinden bağımsızdır.
_TR_TZ = ZoneInfo("Europe/Istanbul")

def _now_tr():
    return datetime.now(_TR_TZ).replace(tzinfo=None)
from difflib import SequenceMatcher
from bs4 import BeautifulSoup
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from google import genai
from google.genai import types as genai_types

from src.config import (
    GEMINI_API_KEY, NEWS_SOURCES, HEADERS, CONTENT_SELECTORS,
    ARCHIVE_FILE, KRITIK3_HISTORY_FILE, KRITIK3_HISTORY_DAYS,
    REPORT_HISTORY_FILE, REPORT_HISTORY_DAYS,
    ENABLE_LLM_CROSS_DAY_DEDUP, CROSS_DAY_DEDUP_WINDOW_DAYS,
    SCORING_LOG_FILE, SCORING_LOG_MAX_LINES,
    SOCIAL_SIGNAL_CONFIG, SKIP_URL_PATTERNS, FEED_SUMMARY_MIN_WORDS, ARTICLE_PROXY,
    ARTICLE_PROXY_MAX_CALLS, ARTICLE_PROXY_BUDGET_SEC, REPORT_FLOOR,
    get_ranking_prompt, get_deep_analysis_prompt, get_summary_batch_prompt,
    get_top3_selection_prompt, get_top3_verification_prompt,
    get_legacy_json_prompt, get_quality_review_prompt, get_dedup_review_prompt,
    get_cross_day_dedup_prompt, get_register_audit_prompt,
    get_executive_summary_prompt, get_title_rescue_prompt,
    get_scoring_prompt, get_critique_prompt,
    SCORING_WEIGHTS, SCORING_CATEGORIES, ZAFIYET_KATEGORILERI,
    KRITIK3_HARIC_KATEGORILER, KATEGORI_ONCELIK,
    is_openrouter_active,
)
from src.http_utils import requests_get_with_retry as _requests_get_with_retry
# OpenRouter (Gemini 3 Flash) — PASİF altyapı. Yalnızca is_openrouter_active()
# True iken devreye girer; aksi halde tüm LLM çağrıları Gemini üzerinden gider.
from src import llm_client as _llm
# Aynı-olay (same-event) dedup — KRİTİK 3 içinde ve rapor genelinde mükerrer
# haberleri DETERMİNİSTİK (LLM'den bağımsız) olarak engeller. Bkz. src/dedup.py.
from src import dedup as _dedup
# Resmi-dil (register) denetimi — gövde paragraflarında laubali (-DI) basit geçmiş
# zamanı DETERMİNİSTİK tespit eder; düzeltmeyi Auditor'ın LLM adımı yapar.
from src import register as _register


# ===== YARDIMCI FONKSİYONLAR =====

def _extract_json_from_text(text):
    """AI yanıtından JSON nesnesini güvenli biçimde çıkarır."""
    text = text.strip()
    # Qwen3 thinking bloklarını temizle (<think>...</think>)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    # Doğrudan parse dene
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # ```json ... ``` bloğu
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # En dıştaki { ... } çiftini bul
    start = text.find('{')
    if start == -1:
        raise ValueError("Yanıtta JSON nesnesi bulunamadı")
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    break
    raise ValueError("Geçerli JSON çıkarılamadı")



def _normalize_id_content(data):
    """Pass 2/3 çıktısını {id: {...}} sözlüğüne indirger.

    Prompt id-anahtarlı sözlük ister: {"3": {...}, "7": {...}}. Ancak bazı
    modeller (ör. gemini-3-flash-preview) bunun yerine liste döndürebilir:
    [{"id": 3, "tr_title": ..., "paragraph": ...}, ...] veya tek anahtarlı
    bir sarmal {"articles": [...]}. Bu fonksiyon her şekli tek biçime çevirir;
    beklenmeyen tipte boş sözlük döner."""
    if isinstance(data, dict):
        # {"items": [...]} / {"results": [...]} gibi tek-listeli sarmalı aç
        if len(data) == 1:
            only_val = next(iter(data.values()))
            if isinstance(only_val, list):
                return _normalize_id_content(only_val)
        return data
    if isinstance(data, list):
        out = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            key = item.get('id', item.get('ID', item.get('Id')))
            if key is not None:
                out[key] = item
        return out
    return {}


def _calculate_content_hash(title, description):
    """Title + description'dan MD5 hash hesapla (16 karakter hex)"""
    content = f"{title or ''}{description or ''}".lower().strip()
    return hashlib.md5(content.encode('utf-8')).hexdigest()  # tam 32 karakter


# FeedBurner/feedproxy HEAD redirect çözümlemeleri için süreç-düzeyi önbellek.
# _normalize_url_advanced dedup döngülerinde aynı link için defalarca çağrılır;
# önbelleksiz her çağrı 5 sn'lik senkron bir HEAD isteği başlatıyordu (25 dk'lık
# Actions limitine baskı). Ayrıca HEAD aralıklı başarısız olunca aynı URL farklı
# normalize olup dedup'ı kaçırıyordu. Önbellek hem süreyi düşürür hem de bir run
# içindeki çözümlemeyi belirli (deterministik) kılar.
_HEAD_REDIRECT_CACHE = {}


def _resolve_feed_redirect(link):
    """FeedBurner/feedproxy linkinin nihai hedefini (HEAD ile) bir kez çözer, önbelleğe alır."""
    if link in _HEAD_REDIRECT_CACHE:
        return _HEAD_REDIRECT_CACHE[link]
    resolved = link
    try:
        r = requests.head(link, allow_redirects=True, timeout=5)
        if r.url and r.url != link:
            resolved = r.url
    except Exception:
        pass
    _HEAD_REDIRECT_CACHE[link] = resolved
    return resolved


def _normalize_url_advanced(link):
    """
    Gelişmiş URL normalizasyonu:
    - UTM parametrelerini kaldırma
    - Protocol standardizasyonu (http→https)
    - Query parametreleri sorting
    - The Register proxy URL'lerini çözme
    - Google FeedBurner redirect'lerini çözme (önbellekli, bkz. _resolve_feed_redirect)
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

        # FeedBurner redirect fix (önbellekli — tekrar çağrılarda ağ isteği yapmaz)
        if 'feedproxy.google.com' in link or 'feeds.feedburner.com' in link:
            link = _resolve_feed_redirect(link)

        parsed = urlparse(link)

        # Protocol → https
        scheme = 'https'
        netloc = parsed.netloc.lower().replace('www.', '')
        path = parsed.path

        # UTM ve tracking parametrelerini kaldır
        utm_params = {'utm_source', 'utm_medium', 'utm_campaign', 'utm_term',
                      'utm_content', 'ref', 'source', 'medium', 'mc_cid', 'mc_eid'}
        qs = parse_qs(parsed.query, keep_blank_values=False)
        filtered_qs = {k: v for k, v in qs.items() if k.lower() not in utm_params}

        # Query parametrelerini sıralı birleştir
        query_string = urlencode(sorted(filtered_qs.items()), doseq=True)

        # Yeniden oluştur
        normalized = urlunparse((scheme, netloc, path, '', query_string, ''))

        # Trailing slash kaldır
        normalized = normalized.rstrip('/')

        return normalized
    except Exception:
        return link.rstrip('/')



# Özetleme promptlarına gönderilen tam metin için uç-durum (outlier) tavanı.
# Haber metinleri "ters piramit" yapısındadır: olgular başta yoğunlaşır. Tipik siber
# güvenlik haberi ~400-900 kelimedir; bu tavan normal haberleri ETKİLEMEZ, yalnızca
# nadiren karşılaşılan çok uzun analiz/sayfa kazımalarını sınırlayarak Pass 2/3
# girdi token'ının kontrolsüz şişmesini önler. Kalite kaybı olmadan worst-case sınırı.
_FULLTEXT_PROMPT_WORD_CAP = 1500


def _cap_fulltext(text):
    """Tam metni özetleme promptu için _FULLTEXT_PROMPT_WORD_CAP kelimeye sınırlar."""
    words = (text or '').split()
    if len(words) <= _FULLTEXT_PROMPT_WORD_CAP:
        return text or ''
    return ' '.join(words[:_FULLTEXT_PROMPT_WORD_CAP])


# Kod adı çıkarımı TEK KAYNAKTAN gelir: src.dedup.extract_codenames. Böylece
# Seviye 5 (aynı-run) ve çapraz-gün dedup aynı mantığı (CamelCase + ALL-CAPS +
# ortak denylist) paylaşır; iki yerde ayrı düzeltme / drift riski ortadan kalkar.
# (Eskiden burada CamelCase-only bir kopya vardı; ALL-CAPS kod adlarını —
# LONGLEASH gibi — kaçırıyordu.)
_extract_codenames = _dedup.extract_codenames


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
        except Exception:
            pass
    # Z sonekini +00:00 ile değiştirip tekrar dene
    if date_str.endswith('Z'):
        try:
            return datetime.strptime(date_str[:-1], '%Y-%m-%dT%H:%M:%S.%f').replace(
                tzinfo=timezone.utc).astimezone(TR).strftime('%d.%m.%Y')
        except Exception:
            pass
        try:
            return datetime.strptime(date_str[:-1], '%Y-%m-%dT%H:%M:%S').replace(
                tzinfo=timezone.utc).astimezone(TR).strftime('%d.%m.%Y')
        except Exception:
            pass
    # Timezone-naive formatlar: olduğu gibi al
    for fmt in ['%a, %d %b %Y %H:%M:%S %Z', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d']:
        try:
            return datetime.strptime(date_str, fmt).strftime('%d.%m.%Y')
        except Exception:
            pass
    return fallback.strftime('%d.%m.%Y')




def fetch_social_signals(config):
    """
    Reddit (unauthenticated public API), Hacker News, GitHub Advisories ve
    HN, GitHub Advisories (max 2), Mastodon (infosec.exchange) kaynaklarından sinyal çeker.
    Reddit via Tavily ayrı havuzda top 3 seçilir, sona eklenir.
    Toplam: max 8 sinyal (5 ana + 3 Reddit).
    """
    hours_back     = config.get('hours_back', 24)
    cutoff_ts      = int((datetime.now() - timedelta(hours=hours_back)).timestamp())
    yesterday      = (datetime.now() - timedelta(hours=hours_back)).strftime('%Y-%m-%d')
    results        = []   # HN + Mastodon + GitHub (max 2) → ana havuz
    reddit_results = []   # Reddit via PullPush → ayrı havuz

    print("\n" + "=" * 70)
    print("📡 SOSYAL MEDYA SİNYALLERİ ÇEKILIYOR")
    print("=" * 70)

    # ── Mastodon (infosec.exchange) ───────────────────────────────────────────
    # Açık API, kimlik doğrulama gerektirmez. Hashtag timeline endpoint kullanılır.
    # Engagement skoru: favourites_count + reblogs_count * 2 + replies_count
    mastodon_cfg      = config.get('mastodon', {})
    mastodon_instance = mastodon_cfg.get('instance', 'infosec.exchange')
    mastodon_tags     = mastodon_cfg.get('hashtags', ['cybersecurity', 'infosec', 'vulnerability'])
    mastodon_limit    = mastodon_cfg.get('limit', 20)
    mastodon_min_eng  = mastodon_cfg.get('min_score', 2)
    mastodon_top_n    = mastodon_cfg.get('top_n', 3)
    mastodon_hours    = mastodon_cfg.get('hours_back', 48)
    mastodon_cutoff   = int((datetime.now() - timedelta(hours=mastodon_hours)).timestamp())
    mastodon_pool     = []
    seen_mastodon_ids = set()
    mastodon_fallback_instances = mastodon_cfg.get(
        'fallback_instances', ['mastodon.social', 'fosstodon.org'])
    instances_to_try = [mastodon_instance] + [
        i for i in mastodon_fallback_instances if i != mastodon_instance]

    for tag in mastodon_tags:
        tag_fetched = False
        for instance in instances_to_try:
            try:
                url = (f'https://{instance}/api/v1/timelines/tag/{tag}'
                       f'?limit={mastodon_limit}')
                r = _requests_get_with_retry(url, headers=HEADERS, timeout=(3, 5))
                if r.status_code == 422:
                    print(f"   Mastodon #{tag} ({instance}): HTTP 422, RSS feed deneniyor...")
                    # API auth gerektiriyor → RSS feed fallback (/tags/{tag}.rss kimlik doğrulama istemez)
                    try:
                        import email.utils as _eu
                        rss_url = f'https://{instance}/tags/{tag}.rss'
                        rr = _requests_get_with_retry(rss_url, headers=HEADERS, timeout=(3, 5))
                        if rr.status_code == 200:
                            root = ET.fromstring(rr.content)
                            items = root.findall('.//item')
                            rss_added = 0
                            for item in items:
                                pub_date_str = item.findtext('pubDate', '')
                                try:
                                    ts = _eu.mktime_tz(_eu.parsedate_tz(pub_date_str))
                                    if ts and ts < mastodon_cutoff:
                                        continue
                                except Exception:
                                    pass
                                link = item.findtext('link', '#')
                                if link in seen_mastodon_ids:
                                    continue
                                seen_mastodon_ids.add(link)
                                desc_html = item.findtext('description', '')
                                raw_text = BeautifulSoup(desc_html, 'html.parser').get_text(' ', strip=True)
                                raw_text = re.sub(r'https?://\S+', '', raw_text).strip()
                                raw_text = re.sub(r'#\w+', '', raw_text).strip()
                                raw_text = ' '.join(raw_text.split())
                                if len(raw_text) < 30:
                                    continue
                                if any(kw in raw_text.lower() for kw in (
                                        'autopsie', 'dossier', 'pour les', 'avec ', 'dans ',
                                        'selon ', 'mais ', 'sont ', 'cette ', 'über ', 'wurde ')):
                                    continue
                                mastodon_pool.append({
                                    'platform':   'mastodon',
                                    'source':     'Mastodon',
                                    'title':      raw_text[:200],
                                    'link':       link,
                                    'score':      1,
                                    'comments':   0,
                                    'favourites': 0,
                                    'reblogs':    0,
                                })
                                rss_added += 1
                            print(f"   Mastodon #{tag} ({instance}): RSS ile {rss_added} post eklendi")
                            # RSS 200 ama 0 post → bu instance işe yaramadı; break ETME,
                            # sıradaki fallback instance'ın (mastodon.social/fosstodon.org)
                            # API'sini dene. Aksi halde fallback zinciri hiç çalışmıyordu.
                            if rss_added > 0:
                                tag_fetched = True
                                time.sleep(0.5)
                                break
                            time.sleep(0.5)
                            continue
                        else:
                            print(f"   Mastodon #{tag} ({instance}): RSS HTTP {rr.status_code}")
                    except Exception as rss_e:
                        print(f"   Mastodon #{tag} ({instance}): RSS hatası: {rss_e}")
                    time.sleep(0.5)
                    continue
                if r.status_code != 200:
                    print(f"   Mastodon #{tag} ({instance}): HTTP {r.status_code}")
                    time.sleep(0.5)
                    continue
                for s in r.json():
                    sid = s.get('id', '')
                    if sid in seen_mastodon_ids:
                        continue
                    seen_mastodon_ids.add(sid)
                    try:
                        cdt = datetime.fromisoformat(
                            s.get('created_at', '').replace('Z', '+00:00'))
                        if cdt.timestamp() < mastodon_cutoff:
                            continue
                    except Exception:
                        pass
                    favs    = s.get('favourites_count', 0)
                    reblogs = s.get('reblogs_count', 0)
                    replies = s.get('replies_count', 0)
                    eng     = favs + reblogs * 2 + replies
                    if eng < mastodon_min_eng:
                        continue
                    # HTML içerikten düz metin çıkar
                    raw_text = BeautifulSoup(s.get('content', ''), 'html.parser')\
                                   .get_text(' ', strip=True)
                    raw_text = re.sub(r'https?://\S+', '', raw_text).strip()
                    raw_text = ' '.join(raw_text.split())
                    if len(raw_text) < 30:
                        continue
                    # Dil filtresi: sadece İngilizce ağırlıklı postlar
                    # Fransızca/diğer dil belirteci kelimeleri içeriyorsa atla
                    _lang_skip = ('autopsie', 'dossier', 'pour les', 'avec ',
                                  'dans ', 'selon ', 'mais ', 'sont ', 'cette ',
                                  'une vulnérabilité', 'les hackers', ' les ',
                                  'über ', 'wurde ', 'einer ', 'diesem ')
                    if any(kw in raw_text.lower() for kw in _lang_skip):
                        continue
                    # Kalite filtresi: konferans duyurusu / günlük digest / sadece hashtag postları atla
                    _low_quality = ('new event added', 'daily digest', 'your daily dose',
                                    'conference', 'meetup', '📅', '📌 ', '🗓',
                                    'read more:', 'stories you should not miss')
                    if any(kw in raw_text.lower() for kw in _low_quality):
                        continue
                    # Hashtag temizleme: #tag # tag # tag → temiz metin
                    raw_text = re.sub(r'#\w+', '', raw_text).strip()
                    raw_text = ' '.join(raw_text.split())
                    if len(raw_text) < 20:
                        continue
                    mastodon_pool.append({
                        'platform':   'mastodon',
                        'source':     'Mastodon',
                        'title':      raw_text[:200],
                        'link':       s.get('url', '#'),
                        'score':      eng,
                        'comments':   replies,
                        'favourites': favs,
                        'reblogs':    reblogs,
                    })
                print(f"   Mastodon #{tag} ({instance}): {mastodon_limit} post tarandı")
                tag_fetched = True
                time.sleep(0.5)
                break
            except Exception as e:
                print(f"   Mastodon #{tag} ({instance}) hatasi: {e}")
        if not tag_fetched:
            print(f"   Mastodon #{tag}: Tüm instancelar başarısız")

    mastodon_pool.sort(key=lambda x: x['score'], reverse=True)
    results.extend(mastodon_pool[:mastodon_top_n])
    print(f"   Mastodon toplam: {len(mastodon_pool)} nitelikli → "
          f"en iyi {min(mastodon_top_n, len(mastodon_pool))} eklendi")

    # ── Hacker News (Algolia API) ────────────────────────────────────────────
    # Skor modeli: combined_score = points + (num_comments × comment_weight)
    # ⚠️ search_by_date (tarih-sıralı) son 24 saatin EN YENİ hikayelerini döndürür;
    # bunların çoğu henüz oy almamış (<min_points) olduğundan filtre sonrası 0
    # nitelikli hikaye kalabiliyordu (21-22 Haz'da iki gün üst üste 0 oldu).
    # search endpoint relevance + popularity ağırlıklıdır: taze ama oy almış
    # hikayeleri üste taşır, böylece min_points filtresi sonrası sonuç kalır.
    hn_cfg         = config.get('hackernews', {})
    min_points     = hn_cfg.get('min_points', 15)
    hn_limit       = hn_cfg.get('limit', 25)
    comment_weight = hn_cfg.get('comment_weight', 3)
    try:
        # params dict kullanılarak URL encode sorunu giderildi (>= raw string sorun yapıyor)
        # points filtresi URL'den kaldırıldı → Python'da uygulanıyor (daha güvenilir)
        hn_params = {
            'query':          'security cybersecurity vulnerability malware breach',
            'tags':           'story',
            'numericFilters': f'created_at_i>{cutoff_ts}',
            'hitsPerPage':    hn_limit,
        }
        r = _requests_get_with_retry("https://hn.algolia.com/api/v1/search",
                                     headers=HEADERS, timeout=(3, 5),
                                     params=hn_params)
        if r.status_code == 200:
            hits  = r.json().get('hits', [])
            found = 0
            for hit in hits:
                points   = int(hit.get('points', 0) or 0)
                if points < min_points:
                    continue
                comments = int(hit.get('num_comments', 0) or 0)
                combined = points + comments * comment_weight
                item_url = hit.get('url') or \
                           f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
                domain = urlparse(item_url).netloc or 'news.ycombinator.com'
                results.append({
                    'platform': 'hackernews',
                    'source':   'HackerNews',
                    'title':    hit.get('title', ''),
                    'link':     item_url,
                    'score':    combined,
                    'comments': comments,
                    'date':     hit.get('created_at', ''),
                    'domain':   domain,
                    'full_text': '',
                    'success':  True,
                })
                found += 1
            print(f"   HackerNews: {found} nitelikli hikaye")
        else:
            print(f"   HackerNews: HTTP {r.status_code}")
    except Exception as e:
        print(f"   HackerNews hatasi: {e}")

    # ── GitHub Security Advisories ───────────────────────────────────────────
    # En güncel incelenmiş advisory'leri çeker; severity'ye göre önceliklendirir.
    # top_n=2 ile ana havuzda GitHub'un ağırlığı sınırlandırılır.
    severity_rank = {'critical': 4, 'high': 3, 'medium': 2, 'low': 1}
    gh_cfg        = config.get('github_advisories', {})
    min_severity  = gh_cfg.get('min_severity', ['critical', 'high', 'medium'])
    gh_limit      = gh_cfg.get('limit', 10)
    gh_top_n      = gh_cfg.get('top_n', 2)
    try:
        gh_url  = f"https://api.github.com/advisories?type=reviewed&per_page={gh_limit}"
        gh_hdrs = {
            'Accept':              'application/vnd.github+json',
            'X-GitHub-Api-Version': '2022-11-28',
            'User-Agent':          'siberguvenlik-bot/1.0',
        }
        r = _requests_get_with_retry(gh_url, headers=gh_hdrs, timeout=(3, 5))
        if r.status_code == 200:
            advisories = r.json()
            gh_pool = []
            for adv in advisories:
                sev = (adv.get('severity') or 'unknown').lower()
                if sev not in min_severity:
                    continue
                cvss_obj   = adv.get('cvss') or {}
                cvss_score = float(cvss_obj.get('score') or 0) if isinstance(cvss_obj, dict) else 0.0
                rank       = severity_rank.get(sev, 0)
                sort_score = rank * 10 + cvss_score  # nihai sıralama için karma puan
                gh_pool.append({
                    'platform':  'github_advisories',
                    'source':    'GitHub Advisory',
                    'title':     adv.get('summary') or adv.get('ghsa_id', ''),
                    'link':      adv.get('html_url') or
                                 f"https://github.com/advisories/{adv.get('ghsa_id', '')}",
                    'score':     sort_score,
                    'comments':  0,
                    'severity':  sev,
                    'cvss':      cvss_score,
                    'date':      adv.get('published_at', ''),
                    'domain':    'github.com',
                    'full_text': (adv.get('description') or '')[:300],
                    'success':   True,
                })
            gh_pool.sort(key=lambda x: x['score'], reverse=True)
            results.extend(gh_pool[:gh_top_n])
            print(f"   GitHub Advisories: {len(gh_pool)} nitelikli → "
                  f"en iyi {min(gh_top_n, len(gh_pool))} eklendi")
        else:
            print(f"   GitHub Advisories: HTTP {r.status_code}")
    except Exception as e:
        print(f"   GitHub Advisories hatasi: {e}")

    # ── Reddit (PullPush API — Pushshift halefi) ────────────────────────────
    # PullPush: ücretsiz, API key gerektirmez, Azure IP bloğu yok.
    # Tam post içeriği + yorum derinliği mevcut.
    # Kendi havuzunda tutulur (main_results sıralamasını etkilemez).
    reddit_cfg      = config.get('reddit', {})
    reddit_subs     = reddit_cfg.get('subreddits', ['cybersecurity', 'netsec'])
    reddit_size     = reddit_cfg.get('size', 25)
    reddit_top_n    = reddit_cfg.get('top_n', 3)
    reddit_hours    = reddit_cfg.get('hours_back', 48)
    # ── Reddit (Resmi RSS — Hot Feed) ──────────────────────────────────────────
    # PullPush arşivi ~10 ay geride kaldığı için kullanılamaz hale geldi.
    # Reddit'in /r/{sub}/hot.rss endpoint'i API key gerektirmez ve güncel veri döndürür.
    # JSON endpoint Azure/GH Actions'tan 403 alıyor; RSS daha geniş erişime sahip.
    _ATOM = 'http://www.w3.org/2005/Atom'
    # Düşük kalite post filtresi (kariyer/moderasyon/haftalık thread vb.)
    _REDDIT_SKIP = ('mentorship monday', 'career thread', 'hiring thread',
                    'monthly discussion', 'weekly thread', 'question thread',
                    'what are you working', 'show hn', '[hiring]', '[who is hiring]',
                    'who wants to be hired', 'megathread', 'ama:')
    rss_cutoff_dt = datetime.now() - timedelta(hours=reddit_hours)

    # Tek BİRLEŞİK feed: r/cybersecurity+netsec/hot.rss → tek HTTP isteği.
    # Önceki sürüm her subreddit için ayrı istek atıyordu; ikinci istek
    # reddit.com'un IP-bazlı rate limitine takılıp HTTP 429 alıyordu (GH Actions
    # paylaşımlı IP). Birleşik feed ikinci-istek 429'unu tamamen ortadan kaldırır.
    # Subreddit atıfı her entry'nin <category term="..."> alanından korunur.
    try:
        rss_pool = []
        seen_rss_links = set()
        combined_subs = '+'.join(reddit_subs)
        rss_url = (f'https://www.reddit.com/r/{combined_subs}/hot.rss'
                   f'?limit={reddit_size * max(1, len(reddit_subs))}')
        r = _requests_get_with_retry(rss_url, headers=HEADERS, timeout=(3, 5))
        if r.status_code != 200:
            print(f"   Reddit RSS r/{combined_subs}: HTTP {r.status_code}")
            top_reddit = []
        else:
            root = ET.fromstring(r.content)
            found = 0
            for entry in root.findall(f'{{{_ATOM}}}entry'):
                # Başlık
                title_el = entry.find(f'{{{_ATOM}}}title')
                title = (title_el.text or '').strip() if title_el is not None else ''
                if not title:
                    continue
                # Düşük kalite filtresi
                if any(kw in title.lower() for kw in _REDDIT_SKIP):
                    continue
                # Link
                link_el = entry.find(f'{{{_ATOM}}}link')
                url = link_el.get('href', '') if link_el is not None else ''
                if not url or url in seen_rss_links:
                    continue
                # Tarih filtresi
                updated_el = entry.find(f'{{{_ATOM}}}updated')
                if updated_el is not None and updated_el.text:
                    try:
                        post_dt = datetime.fromisoformat(
                            updated_el.text.replace('Z', '+00:00')).replace(tzinfo=None)
                        if post_dt < rss_cutoff_dt:
                            continue
                    except Exception:
                        pass   # Tarih parse edilemezse geç
                seen_rss_links.add(url)
                # Subreddit atıfı: entry'nin category term alanından (birleşik feed)
                cat_el = entry.find(f'{{{_ATOM}}}category')
                sub = cat_el.get('term', '') if cat_el is not None else ''
                sub = sub or (reddit_subs[0] if reddit_subs else 'reddit')
                # RSS'te upvote bilgisi yok; sıra indeksini ters puan olarak kullan
                # (hot feed zaten engagement'a göre sıralı)
                rss_pool.append({
                    'platform':     'reddit',
                    'source':       f'Reddit: r/{sub}',
                    'title':        title,
                    'link':         url,
                    'score':        (reddit_size * max(1, len(reddit_subs))) - found,
                    'comments':     0,
                    'full_text':    '',
                    'top_comments': [],
                    'subreddit':    sub,
                })
                found += 1
            print(f"   Reddit RSS r/{combined_subs}: {found} nitelikli post")
            reddit_results.extend(rss_pool)
            top_reddit = rss_pool[:reddit_top_n]
            print(f"   Reddit (RSS): toplam {len(rss_pool)} post → "
                  f"en iyi {len(top_reddit)} eklendi")
    except Exception as e:
        top_reddit = []
        print(f"   Reddit RSS hatası: {e}")

    # HN + Mastodon + GitHub (max 1): karma puana göre top 7
    results.sort(key=lambda x: x.get('score', 0), reverse=True)
    top_main = results[:7]

    # Reddit sonuçları her zaman sona eklenir (ayrı havuz)
    final = top_main + top_reddit

    print(f"\n   Sosyal sinyal toplami: {len(final)} icerik "
          f"({len(top_main)} ana + {len(top_reddit)} Reddit)")
    return final


# ===== ANA SİSTEM =====

class HaberSistemi:
    def __init__(self):
        self.headers = HEADERS
        self.sources = NEWS_SOURCES
        self.selectors = CONTENT_SELECTORS
        self.rss_errors = []
        self.used_links_file = "data/haberler_linkler.txt"
        self.rss_errors_file = "data/rss_errors.txt"
        self.social_data = []  # fetch_social_signals() sonuçları; topla() tarafından doldurulur
        # Kaynak-başına sağlık izleme: her koşuda hangi kaynaktan kaç ham madde
        # geldi, kaçı pencereye/deduba takılmadan kaldı ve fetch sonucu ne oldu.
        # Amaç: "200 OK ama 0 madde" (sessiz boş — engelleme/format değişikliği)
        # durumunu, gerçek hatadan ve normal hafta-sonu durgunluğundan ayırmak.
        self.source_stats = {}          # src -> {'raw': int, 'kept': int, 'status': str}
        self.source_health_file = "data/kaynak_saglik.txt"
        # Proxy fallback sayaçları (bkz. _article_proxy_fallback) — workflow
        # zaman bütçesini korumak için çağrı sayısı ve toplam süre sınırlanır.
        self._proxy_calls = 0
        self._proxy_seconds = 0.0

    # ─────────────────────────────────────────────────────────────────────────
    # 3-PASS MİMARİSİ — YARDIMCI METODLAR
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_articles_from_txt(txt_content):
        """
        save_txt() çıktısını yapılandırılmış makale listesine dönüştürür.
        Döndürür: [{'id': int, 'source': str, 'title': str, 'date': str,
                    'link': str, 'full_text': str, 'domain': str,
                    'art_date': str}, ...]
        Sosyal sinyal bloğu otomatik olarak çıkarılır.
        """
        # Sosyal sinyal bölümünü at
        social_sep = 'SOSYAL MEDYA SİNYALLERİ'
        if social_sep in txt_content:
            txt_content = txt_content[:txt_content.index(social_sep)]

        articles = []
        # Makaleler ══════ ayırıcısıyla ayrılır
        blocks = re.split(r'\n[═=]{40,}\n', txt_content)

        for block in blocks:
            block = block.strip()
            if not block:
                continue
            # Başlık satırı: [N] Kaynak - Başlık
            header = re.match(r'\[(\d+)\] (.+?) - (.+)', block)
            if not header:
                continue
            art_id  = int(header.group(1))
            source  = header.group(2).strip()
            title   = header.group(3).strip()

            date_m  = re.search(r'Tarih:\s*(.+)', block)
            link_m  = re.search(r'Link:\s*(\S+)', block)
            src_m   = re.search(
                r'\(XXXXXXX, AÇIK - (\S+),\s*(\S+),\s*(.+?)\)\s*$',
                block, re.MULTILINE
            )

            # Full text: [TAM METİN - N kelime] ile kaynak satırı arasındaki bölüm
            ft_start = block.find('[TAM METİN')
            ft_end   = block.rfind('\n\n(XXXXXXX')
            full_text = ''
            if ft_start != -1:
                ft_line_end = block.find('\n', ft_start)
                raw = block[ft_line_end + 1: ft_end if ft_end != -1 else None]
                full_text = raw.strip()

            articles.append({
                'id':       art_id,
                'source':   source,
                'title':    title,
                'date':     date_m.group(1).strip()  if date_m  else '',
                'link':     link_m.group(1).strip()  if link_m  else '',
                'domain':   src_m.group(2).strip()   if src_m   else '',
                'art_date': src_m.group(3).strip()   if src_m   else '',
                'full_text': full_text,
            })

        articles.sort(key=lambda a: a['id'])
        return articles

    def _gemini_call_json(self, prompt, max_output_tokens=4096, label=''):
        """
        Gemini API çağrısı yapar ve JSON yanıt döndürür.
        Retry: 4 deneme, sabit 15s bekleme; model sırası pro→pro→flash→flash.
        Başarısızlıkta None döndürür.

        OpenRouter aktifse (LLM_PROVIDER=openrouter + OPENROUTER_API_KEY) çağrı
        Gemini yerine OpenRouter (Gemini 3 Flash) üzerinden yapılır. Pasifken
        bu satırın hiçbir etkisi yoktur.
        """
        if is_openrouter_active():
            return _llm.generate_json(
                prompt, max_output_tokens=max_output_tokens, label=label,
            )

        if not GEMINI_API_KEY:
            print(f"   ⚠️  [{label}] GEMINI_API_KEY yok, atlanıyor.")
            return None

        _MODELS = ['gemini-2.5-pro', 'gemini-2.5-pro',
                   'gemini-2.5-flash', 'gemini-2.5-flash']
        client = genai.Client(api_key=GEMINI_API_KEY)

        # Kesilme (MAX_TOKENS) güvenliği: thinking modellerinde reasoning de
        # max_output_tokens bütçesinden harcanır; bütçe yetmezse çıktı yarıda
        # kesilir (yarım JSON → parse hatası). Bu durumda bütçe katlanıp aynı
        # istek tekrarlanır. _TRUNC_BUDGET_CAP sonsuz büyümeyi engeller.
        _TRUNC_BUDGET_CAP = 65536
        budget = max_output_tokens

        for attempt, model in enumerate(_MODELS):
            try:
                print(f"   [{label}] Deneme {attempt + 1}/4 [{model}] (bütçe={budget})...")
                # Büyük çıktılar (>8K token) için daha uzun HTTP timeout (ms)
                http_timeout_ms = 300_000 if budget > 8000 else 180_000
                response = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(
                        max_output_tokens=budget,
                        temperature=0.3,
                        http_options=genai_types.HttpOptions(timeout=http_timeout_ms),
                        safety_settings=[
                            genai_types.SafetySetting(
                                category='HARM_CATEGORY_DANGEROUS_CONTENT',
                                threshold='BLOCK_ONLY_HIGH',
                            ),
                            genai_types.SafetySetting(
                                category='HARM_CATEGORY_HARASSMENT',
                                threshold='BLOCK_ONLY_HIGH',
                            ),
                            genai_types.SafetySetting(
                                category='HARM_CATEGORY_HATE_SPEECH',
                                threshold='BLOCK_ONLY_HIGH',
                            ),
                            genai_types.SafetySetting(
                                category='HARM_CATEGORY_SEXUALLY_EXPLICIT',
                                threshold='BLOCK_ONLY_HIGH',
                            ),
                        ],
                    ),
                )
                fr = (response.candidates[0].finish_reason.name
                      if response.candidates else '')
                # PROHIBITED_CONTENT: içerik filtresi tetiklendi, retry faydasız
                if fr == 'PROHIBITED_CONTENT':
                    print(f"   [{label}] ⚠️  İçerik filtresi (PROHIBITED_CONTENT) — sonraki model deneniyor.")
                    continue
                # MAX_TOKENS: çıktı bütçeye takılıp kesildi → bütçeyi katla ve
                # sonraki denemede daha büyük bütçeyle tekrar dene. Bütçe her
                # kesilmede ikiye katlandığından birkaç deneme içinde yetişir.
                if fr == 'MAX_TOKENS' and budget < _TRUNC_BUDGET_CAP:
                    new_budget = min(budget * 2, _TRUNC_BUDGET_CAP)
                    print(f"   [{label}] ✂️  Çıktı kesildi (MAX_TOKENS, bütçe={budget}); "
                          f"bütçe {new_budget}'e çıkarılıp yeniden deneniyor.")
                    budget = new_budget
                    continue
                raw = response.text or ''
                data = _extract_json_from_text(raw)
                print(f"   [{label}] ✅ Başarılı.")
                return data
            except Exception as e:
                print(f"   [{label}] ⚠️  Hata [{type(e).__name__}]: {e}")
                if attempt < 3:
                    print(f"   [{label}] ⏳ 15s bekleniyor...")
                    time.sleep(15)

        print(f"   [{label}] ❌ Gemini 4 deneme başarısız.")
        return None

    @staticmethod
    def _sanitize_html(html):
        """
        <style> blokları DIŞINA sızan CSS yorumlarını (/* ... */) ve serbest HTML
        yorumlarını (<!-- ... -->) temizler. Legacy/Gemini çıktısında body'ye kaçan
        '/* YÖNETİCİ ÖZETİ */' gibi görünür artefaktları önler. <style> içindeki
        gerçek CSS yorumlarına DOKUNULMAZ.
        """
        if not html:
            return html
        # <style>...</style> bloklarını ayır; yalnızca dışındaki kısımları temizle
        parts = re.split(r'(<style[^>]*>.*?</style>)', html,
                         flags=re.DOTALL | re.IGNORECASE)
        cleaned = []
        for part in parts:
            if part[:6].lower() == '<style':
                cleaned.append(part)           # CSS bloğu — olduğu gibi koru
            else:
                part = re.sub(r'/\*.*?\*/', '', part, flags=re.DOTALL)   # sızan CSS yorumu
                part = re.sub(r'<!--.*?-->', '', part, flags=re.DOTALL)  # sızan HTML yorumu
                cleaned.append(part)
        return ''.join(cleaned)

    @staticmethod
    def _build_html(articles, top10_ids, remaining_ids, content_by_id, today_str,
                    top3_ids=None, exec_summary='', category_by_id=None,
                    promote_ids=None):
        """
        Yapılandırılmış içerikten tam HTML raporu üretir (kod tarafı assembly).
        content_by_id: {art_id: {'tr_title': str, 'paragraph': str}}
        top10_ids: sıralı ID listesi (önemli gelişmeler kutusu)
        remaining_ids: sıralı ID listesi (tablo + kalan paragraflar)
        exec_summary: en önemli 9 haberi özetleyen Yönetici Özeti paragrafı (opsiyonel)
        category_by_id: {art_id: kategori} — zafiyet bölümü yönlendirmesi kategori
            etiketiyle yapılır; verilmezse (legacy yol) keyword tespitine düşülür.
        """
        category_by_id = category_by_id or {}
        articles_by_id = {a['id']: a for a in articles}

        css = """
        * { margin: 0; padding: 0; box-sizing: border-box; }
        html { scroll-behavior: smooth; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
            line-height: 1.6;
            color: #2c3e50;
            background: #f5f7fa;
            padding: 20px;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 4px 6px rgba(0,0,0,0.07);
        }
        .report-header {
            background: #ffffff;
            padding: 14px 40px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 16px;
            flex-wrap: wrap;
            text-align: left;
            position: relative;
            border-bottom: 1px solid #e2e8f0;
        }
        .report-header::before {
            content: '';
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 5px;
            background: linear-gradient(90deg, #1d4ed8 0%, #6366f1 55%, #a855f7 100%);
        }
        .report-header h1 {
            font-size: 26px;
            font-weight: 600;
            margin: 0;
            text-align: left;
            letter-spacing: 0.3px;
            color: #1e293b;
        }
        .header-date { color: #2563eb; font-weight: 700; }
        .header-subtitle {
            font-size: 12px;
            font-weight: 400;
            color: #64748b;
            margin: 2px 0 0 0;
        }
        .important-news {
            background: linear-gradient(135deg, #e3f2fd 0%, #f1f8ff 100%);
            color: #2c3e50;
            padding: 25px 30px;
            margin: 0;
            border: 1px solid #bbdefb;
            border-radius: 8px;
            margin-bottom: 20px;
            position: relative;
        }
        .important-news h2 { color: #1565c0; font-size: 20px; font-weight: 600; margin-bottom: 20px; }
        .onemli-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            margin-bottom: 20px;
        }
        .onemli-header h2 { margin-bottom: 0; }
        .exec-brief {
            background: linear-gradient(135deg, #e3f2fd 0%, #f1f8ff 100%);
            color: #2c3e50;
            padding: 14px 24px;
            margin: 0 0 14px 0;
            border: 1px solid #bbdefb;
            border-radius: 8px;
            position: relative;
        }
        .exec-brief h2 { color: #1565c0; font-size: 17px; font-weight: 600; margin-bottom: 8px; }
        .exec-brief-paragraph { font-size: 14.5px; line-height: 1.55; text-align: justify; margin: 0; }
        .exec-brief-vuln-link { margin: 10px 0 0 0; text-align: right; }
        .exec-brief-vuln-link a {
            color: #e65100; font-weight: 700; font-size: 14.5px; text-decoration: none;
        }
        .exec-brief-vuln-link a:hover { text-decoration: underline; color: #bf360c; }
        .block-actions {
            display: flex;
            gap: 6px;
            flex-shrink: 0;
        }
        .block-actions-bottom {
            display: flex;
            justify-content: flex-end;
            gap: 6px;
            margin-top: 14px;
            margin-bottom: 20px;
        }
        .block-action-btn {
            background: rgba(255,255,255,0.92);
            border: 1px solid #90caf9;
            border-radius: 6px;
            padding: 5px 11px;
            cursor: pointer;
            font-size: 12px;
            font-weight: 600;
            color: #1565c0;
            display: flex;
            align-items: center;
            gap: 5px;
            transition: background 0.15s, border-color 0.15s;
            white-space: nowrap;
            user-select: none;
        }
        .block-action-btn:hover { background: #dbeeff; border-color: #42a5f5; }
        .block-action-btn.success { background: #e8f5e9; border-color: #66bb6a; color: #2e7d32; }
        .block-action-btn svg { flex-shrink: 0; }
        .drag-file-chip {
            display: inline-flex; align-items: center; gap: 6px;
            background: rgba(255,255,255,0.92);
            border: 1.5px dashed #90caf9;
            border-radius: 8px; padding: 5px 12px 5px 8px;
            font-size: 12px; font-weight: 600; color: #1565c0;
            cursor: grab; user-select: none; white-space: nowrap;
            transition: background 0.15s, border-color 0.15s, transform 0.15s, box-shadow 0.15s;
            flex-direction: column; gap: 1px;
        }
        .drag-file-chip-inner {
            display: inline-flex; align-items: center; gap: 5px;
        }
        .drag-file-chip-hint {
            font-size: 9px; font-weight: 400; color: #64b5f6;
            letter-spacing: 0.3px; line-height: 1;
        }
.drag-file-chip:hover {
            background: #dbeeff; border-color: #42a5f5;
            transform: translateY(-2px) rotate(-1deg);
            box-shadow: 0 4px 10px rgba(21,101,192,0.18);
        }
.drag-file-chip:hover .drag-file-chip-hint { color: #1565c0; }
        .drag-file-chip:active { cursor: grabbing; transform: scale(0.97); }
        .drag-file-chip.dragging { opacity: 0.45; transform: rotate(3deg); }
        .important-summary { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
        @media (max-width: 640px) { .important-summary { grid-template-columns: 1fr; } }
        .important-item {
            background: rgba(255,255,255,0.7);
            padding: 12px 16px;
            border-radius: 6px;
            border-left: 4px solid #42a5f5;
        }
        .important-item a { color: #2c3e50; text-decoration: none; font-weight: 500; font-size: 15px; }
        .important-item a:hover { text-decoration: underline; color: #1565c0; }
        .executive-summary {
            background: #f8f9fa;
            padding: 25px 30px;
            margin: 0;
            border-bottom: 1px solid #e1e8ed;
        }
        .executive-summary h2 {
            color: #1a237e; font-size: 18px; font-weight: 600;
            margin-bottom: 15px; padding-bottom: 8px; border-bottom: 2px solid #1a237e;
        }
        .executive-table { width: 100%; border-spacing: 8px; }
        .executive-table td {
            background: white; padding: 12px 16px; border-radius: 6px;
            border-left: 3px solid #1a237e; vertical-align: top; width: 50%;
        }
        .executive-table a {
            color: #1a237e; text-decoration: none; font-weight: 500;
            font-size: 14px; line-height: 1.4;
        }
        .executive-table a:hover { text-decoration: underline; }
        .news-section { padding: 30px; }
        .news-item {
            background: #f8f9fa; margin-bottom: 25px; border-radius: 8px;
            padding: 20px; border-left: 4px solid #1a237e;
        }
        .news-title { color: #1a237e; font-size: 18px; font-weight: 600; margin-bottom: 12px; line-height: 1.3; }
        .news-content { color: #2c3e50; font-size: 15px; line-height: 1.6; margin-bottom: 10px; text-align: justify; }
        .source { color: #666; font-size: 13px; margin: 0; }
        .source a { color: #1a237e; text-decoration: none; }
        .source a:hover { text-decoration: underline; }
        .social-signals {
            background: #f8faff; border: 1px solid #c7d7fd;
            border-radius: 8px; padding: 24px 28px; margin-bottom: 20px;
        }
        .social-signals h2 {
            color: #1e3a8a; font-size: 18px; font-weight: 700;
            margin-bottom: 16px; padding-bottom: 10px; border-bottom: 2px solid #dbeafe;
        }
        .social-signals .signal-list { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
        .social-signals .signal-item {
            background: #ffffff; border: 1px solid #e2e8f0;
            border-left: 4px solid #3b82f6; border-radius: 6px;
            padding: 12px 16px; display: flex; flex-direction: column; gap: 6px;
        }
        .social-signals .signal-item.reddit-item   { border-left-color: #ff4500; }
        .social-signals .signal-item.hn-item       { border-left-color: #ff6600; }
        .social-signals .signal-item.github-item   { border-left-color: #238636; }
        .social-signals .signal-item.mastodon-item { border-left-color: #6364ff; }
        .social-signals .signal-meta { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
        .social-signals .signal-platform-label {
            font-size: 10px; font-weight: 700; color: #ffffff; background: #64748b;
            text-transform: uppercase; letter-spacing: 0.05em; border-radius: 3px; padding: 2px 7px;
        }
        .social-signals .reddit-item .signal-platform-label   { background: #ff4500; }
        .social-signals .hn-item .signal-platform-label       { background: #ff6600; }
        .social-signals .github-item .signal-platform-label   { background: #238636; }
        .social-signals .mastodon-item .signal-platform-label { background: #6364ff; }
        .social-signals .signal-engagement {
            font-size: 11px; color: #475569; background: #f1f5f9; border-radius: 3px; padding: 2px 8px;
        }
        .social-signals .signal-item a {
            color: #1e293b; text-decoration: none; font-size: 13px;
            font-weight: 500; line-height: 1.45; display: block;
        }
        .social-signals .signal-item a:hover { color: #1e3a8a; text-decoration: underline; }
        @media (max-width: 640px) {
            .social-signals { padding: 16px; }
            .social-signals .signal-list { grid-template-columns: 1fr; }
            .social-signals .signal-meta { gap: 6px; }
        }
        .top3-section { margin-bottom: 24px; }
        .top3-section-label {
            font-size: 12px; font-weight: 700; letter-spacing: 0.08em;
            text-transform: uppercase; color: #1a237e; margin-bottom: 12px;
        }
        .top3-card {
            background: linear-gradient(135deg, #e8eaf6 0%, #f5f6fb 100%);
            border: 1px solid #c5cae9;
            border-left: 8px solid #1a237e; border-radius: 8px;
            padding: 18px 20px; margin-bottom: 14px;
            box-shadow: 0 4px 16px rgba(26,35,126,0.12);
        }
        .top3-card:last-child { margin-bottom: 0; }
        .top3-card-title {
            color: #1e293b; font-size: 15px; font-weight: 700;
            margin-bottom: 10px; line-height: 1.4;
        }
        .top3-card-paragraph {
            color: #374151; font-size: 14px; line-height: 1.65; margin-bottom: 10px; text-align: justify;
        }
        .top3-card .source { color: #64748b; font-size: 12px; margin: 0; }
        .top3-card .source a { color: #1a237e; text-decoration: none; }
        .top3-card .source a:hover { text-decoration: underline; }
        .vuln-section-heading {
            background: linear-gradient(135deg, #fff3e0 0%, #fff8f0 100%);
            border: 1px solid #ffcc80;
            border-left: 5px solid #e65100;
            border-radius: 8px;
            padding: 14px 20px;
            margin-bottom: 20px;
        }
        .vuln-section-heading h2 {
            color: #bf360c; font-size: 18px; font-weight: 700; margin: 0;
        }
        .news-item.vuln-item { border-left-color: #e65100; }
        .news-item.vuln-item .news-title { color: #bf360c; }
        .back-to-top {
            position: fixed; top: 50%; left: calc(50% - 450px - 48px);
            transform: translateY(-50%); width: 36px; height: 36px;
            background: #1a237e; color: white; border: none; border-radius: 50%;
            font-size: 18px; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.25);
            display: flex; align-items: center; justify-content: center;
            text-decoration: none; opacity: 0.85; transition: opacity 0.2s; z-index: 999;
        }
        .back-to-top:hover { opacity: 1; }
        /* ── DARK MODE ─────────────────────────────────────────── */
        /* Başlığın sağında, tek satırda: manuel buton + tema butonu bir arada
           (report-header'ın flex justify-content:space-between'i ile sağa yaslanır). */
        .header-actions {
            display: flex; align-items: center; gap: 8px; flex-shrink: 0; z-index: 10;
        }
        .theme-toggle {
            background: none; border: 1px solid #e2e8f0; border-radius: 20px;
            padding: 6px 12px; cursor: pointer; font-size: 12px; font-weight: 600;
            color: #64748b; display: flex; align-items: center; gap: 4px;
            transition: all 0.2s; user-select: none;
        }
        .theme-toggle:hover { background: #f1f5f9; border-color: #94a3b8; }
        .theme-toggle .sep { color: #cbd5e1; font-size: 11px; margin: 0 1px; }
        .theme-toggle svg { transition: opacity 0.2s; }
        .theme-toggle svg.dim { opacity: 0.25; }
        .manual-add-btn {
            background: #1d4ed8; color: #fff; border: none; border-radius: 20px;
            padding: 6px 13px; cursor: pointer; font-size: 12px; font-weight: 600;
            display: flex; align-items: center; gap: 6px;
            transition: background 0.2s; font-family: inherit; white-space: nowrap;
        }
        .manual-add-btn:hover { background: #1e40af; }
        @media (max-width: 640px) {
            .header-actions { gap: 6px; }
        }
        [data-theme="dark"] body { color: #e6edf3; background: #0d1117; }
        [data-theme="dark"] .container { background: #161b22; box-shadow: 0 4px 20px rgba(0,0,0,0.4); }
        [data-theme="dark"] .report-header { background: #161b22; border-bottom-color: #30363d; }
        [data-theme="dark"] .report-header h1 { color: #e6edf3; }
        [data-theme="dark"] .header-date { color: #79c0ff; }
        [data-theme="dark"] .header-subtitle { color: #8b949e; }
        [data-theme="dark"] .important-news {
            background: linear-gradient(135deg, #1c2d4a 0%, #162038 100%);
            border-color: #2d4a7a; color: #e6edf3;
        }
        [data-theme="dark"] .important-news h2 { color: #79c0ff; }
        [data-theme="dark"] .exec-brief {
            background: linear-gradient(135deg, #1c2d4a 0%, #162038 100%);
            border-color: #2d4a7a; color: #e6edf3;
        }
        [data-theme="dark"] .exec-brief h2 { color: #79c0ff; }
        [data-theme="dark"] .exec-brief-vuln-link a { color: #fb923c; }
        [data-theme="dark"] .exec-brief-vuln-link a:hover { color: #fdba74; }
        [data-theme="dark"] .block-action-btn { background: rgba(22,27,34,0.9); border-color: #388bfd; color: #79c0ff; }
        [data-theme="dark"] .block-action-btn:hover { background: #1c2d4a; border-color: #58a6ff; }
        [data-theme="dark"] .block-action-btn.success { background: #162312; border-color: #3fb950; color: #3fb950; }
        [data-theme="dark"] .drag-file-chip { background: rgba(22,27,34,0.9); border-color: #388bfd; color: #79c0ff; }
        [data-theme="dark"] .drag-file-chip:hover { background: #1c2d4a; border-color: #58a6ff; box-shadow: 0 4px 10px rgba(56,139,253,0.25); }
        [data-theme="dark"] .drag-file-chip-hint { color: #388bfd; }
        [data-theme="dark"] .drag-file-chip:hover .drag-file-chip-hint { color: #79c0ff; }
        [data-theme="dark"] .important-item { background: rgba(30,50,90,0.5); border-left-color: #388bfd; }
        [data-theme="dark"] .important-item a { color: #c9d1d9; }
        [data-theme="dark"] .important-item a:hover { color: #79c0ff; }
        [data-theme="dark"] .executive-summary { background: #1c2128; border-bottom-color: #30363d; }
        [data-theme="dark"] .executive-summary h2 { color: #79c0ff; border-bottom-color: #388bfd; }
        [data-theme="dark"] .executive-table td { background: #21262d; border-left-color: #388bfd; }
        [data-theme="dark"] .executive-table a { color: #79c0ff; }
        [data-theme="dark"] .news-section { background: #161b22; }
        [data-theme="dark"] .news-item { background: #21262d; border-left-color: #388bfd; }
        [data-theme="dark"] .news-title { color: #79c0ff; }
        [data-theme="dark"] .news-content { color: #c9d1d9; }
        [data-theme="dark"] .source { color: #8b949e; }
        [data-theme="dark"] .source a { color: #58a6ff; }
        [data-theme="dark"] .social-signals { background: #1a2233; border-color: #263557; }
        [data-theme="dark"] .social-signals h2 { color: #79c0ff; border-bottom-color: #263557; }
        [data-theme="dark"] .social-signals .signal-item { background: #21262d; border-color: #30363d; }
        [data-theme="dark"] .social-signals .signal-item a { color: #c9d1d9; }
        [data-theme="dark"] .social-signals .signal-item a:hover { color: #79c0ff; }
        [data-theme="dark"] .social-signals .signal-engagement { background: #30363d; color: #8b949e; }
        [data-theme="dark"] .top3-section-label { color: #79c0ff; }
        [data-theme="dark"] .top3-card {
            background: linear-gradient(135deg, #1c2d4a 0%, #182338 100%);
            border-color: #2d4a7a; border-left-color: #58a6ff;
            box-shadow: 0 4px 16px rgba(0,0,0,0.35);
        }
        [data-theme="dark"] .top3-card-title { color: #e6edf3; }
        [data-theme="dark"] .top3-card-paragraph { color: #c9d1d9; }
        [data-theme="dark"] .top3-card .source { color: #8b949e; }
        [data-theme="dark"] .top3-card .source a { color: #58a6ff; }
        [data-theme="dark"] .vuln-section-heading {
            background: linear-gradient(135deg, #2d1a0a 0%, #231305 100%);
            border-color: #5c3317; border-left-color: #f97316;
        }
        [data-theme="dark"] .vuln-section-heading h2 { color: #fb923c; }
        [data-theme="dark"] .news-item.vuln-item { border-left-color: #f97316; }
        [data-theme="dark"] .news-item.vuln-item .news-title { color: #f97316; }
        [data-theme="dark"] .back-to-top { background: #388bfd; }
        [data-theme="dark"] .theme-toggle { background: #161b22; border-color: #30363d; color: #8b949e; }
        [data-theme="dark"] .theme-toggle:hover { background: #21262d; border-color: #58a6ff; color: #e6edf3; }
        [data-theme="dark"] .theme-toggle .sep { color: #30363d; }
        [data-theme="dark"] .manual-add-btn { background: #388bfd; }
        [data-theme="dark"] .manual-add-btn:hover { background: #58a6ff; }
        /* ── MOBİL (≤640px): tam genişlik, sıkı kenar payı, küçük başlık/buton ── */
        @media (max-width: 640px) {
            body { padding: 0; }
            .container { border-radius: 0; box-shadow: none; }
            /* Başlık alanı */
            .report-header { padding: 14px 12px; }
            .report-header h1 { font-size: 17px; line-height: 1.35; }
            .header-actions { flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
            /* Bölüm kapsayıcıları — yatay boşluğu en aza indir */
            .exec-brief { padding: 16px 12px; }
            .executive-summary { padding: 14px 6px; }
            .important-news { padding: 14px 8px; }
            .news-section { padding: 14px 6px; }
            .social-signals { padding: 14px 10px; }
            .vuln-section-heading { padding: 12px 14px; }
            /* İç kartlar */
            .top3-card { padding: 14px 12px; }
            .news-item { padding: 14px 12px; margin-bottom: 16px; }
            /* Başlıkları küçült */
            .exec-brief h2, .important-news h2 { font-size: 17px; margin-bottom: 14px; }
            .executive-summary h2 { font-size: 16px; }
            .news-title { font-size: 16px; }
            .top3-card-title { font-size: 14.5px; }
            .vuln-section-heading h2 { font-size: 16px; }
            .social-signals h2 { font-size: 16px; }
            /* "Önemli Gelişmeler" başlık satırı — taşarsa alta sar */
            .onemli-header { flex-wrap: wrap; gap: 8px; }
            /* Butonlar — kompakt */
            .block-action-btn { padding: 4px 9px; font-size: 11px; }
            .block-actions, .block-actions-bottom { gap: 5px; }
            /* Mobilde sürükle-bırak (drag) butonu gereksiz — gizle */
            .drag-file-chip { display: none !important; }
            /* Gövde metinleri okunur boyut + sola hizalı (justify mobilde bozuk durur) */
            .exec-brief-paragraph { font-size: 15px; line-height: 1.7; text-align: left; }
            .news-content { font-size: 14.5px; }
            .top3-card-paragraph { font-size: 14px; }
        }
        """

        import re as _re
        _CVE_PAT = _re.compile(r'CVE-\d{4}-\d{4,7}', _re.IGNORECASE)
        _VULN_KW = (
            'güvenlik açığı', 'açık bulundu', 'açık kapatıldı', 'zafiyet',
            'yama yayınlandı', 'güvenlik yaması', 'sıfır gün', 'zero-day',
            'zero day', 'exploit', 'uzaktan kod çalıştırma',
            'sql injection', 'xss', 'path traversal', 'buffer overflow',
            'vulnerability', 'vulnerabilities', 'patched', 'critical flaw',
            'security flaw', 'arbitrary code',
        )

        def _safe_content(art_id):
            c = content_by_id.get(art_id, {})
            art = articles_by_id.get(art_id, {})
            tr_title  = c.get('tr_title')  or art.get('title', f'Haber #{art_id}')
            paragraph = c.get('paragraph') or art.get('full_text', '')[:500]
            # LLM içerik metni (tr_title/paragraph) HTML'e DOĞRUDAN gömülüyor;
            # model '<', '&' ya da bir etiket üretirse düzeni bozabilir/enjekte
            # edebilir. Bu iki alan düz Türkçe metindir → entity kaçışı güvenli.
            # quote=False: apostrof (Google'da) &#x27;e dönüşüp çirkinleşmesin.
            import html as _h
            tr_title  = _h.escape(str(tr_title), quote=False)
            paragraph = _h.escape(str(paragraph), quote=False)
            return tr_title, paragraph

        def _safe_source(art):
            # RSS'ten gelen link/domain/tarih href ve attribute içine gömülüyor;
            # tırnak içeren ya da javascript: şemalı bir link attribute'tan
            # kaçabilir. Yalnızca http/https'e izin ver, tırnak dahil escape et.
            import html as _h
            link = str(art.get('link') or '#').strip()
            if not re.match(r'^https?://', link, re.IGNORECASE):
                link = '#'
            link     = _h.escape(link, quote=True)
            domain   = _h.escape(str(art.get('domain') or ''), quote=False)
            art_date = _h.escape(str(art.get('art_date') or ''), quote=False)
            return link, domain, art_date

        _promote = set(promote_ids or [])

        def _is_vuln(art_id):
            # Gövde ince olduğunda "Önemli Gelişmeler"e terfi ettirilen güçlü
            # güvenlik açıkları normal haber gibi akışa girer (ve Güvenlik
            # Açıkları bölümünde TEKRAR gösterilmez — çift-render olmaz).
            if art_id in _promote:
                return False
            # Kategori etiketi varsa (yeni puan tabanlı yol) DETERMİNİSTİK ona güven:
            # zafiyet_rutin/zafiyet_aktif_apt → Güvenlik Açıkları bölümü. Etiket
            # yoksa (legacy yol) eski keyword tespitine düş.
            kat = category_by_id.get(art_id)
            if kat is not None:
                return kat in ZAFIYET_KATEGORILERI
            tr_title, _ = _safe_content(art_id)
            orig_title = articles_by_id.get(art_id, {}).get('title', '')
            combined = (tr_title + ' ' + orig_title).lower()
            if _CVE_PAT.search(combined):
                return True
            return any(kw in combined for kw in _VULN_KW)

        top3_set = set(top3_ids or [])

        # ── Haberleri vuln / normal olarak ayır (top3 hariç) ──────────────
        # Top3 haberleri yalnızca üstteki kartlarda gösterilir;
        # Önemli Gelişmeler listesi, Yönetici Özeti tablosu ve
        # haber paragrafları bölümüne kesinlikle dahil edilmez.
        all_ids = list(top10_ids) + list(remaining_ids)
        non_top3_ids = [aid for aid in all_ids if aid not in top3_set]
        vuln_ids     = [aid for aid in non_top3_ids if _is_vuln(aid)]
        regular_ids  = [aid for aid in non_top3_ids if not _is_vuln(aid)]

        # NOT: Önceden tek sayıda normal haber kalınca sonuncusu "iki sütun
        # simetrisi" için atılıyordu — LLM maliyeti harcanmış gerçek bir haber
        # sessizce kayboluyor, yönetici özeti ise ona atıf yapabiliyordu.
        # Tablo zaten tek elemanlı son satırı boş <td> ile düzgün render
        # ettiğinden (aşağıda len(pair)==1 dalı) haber artık ATILMAZ.

        # Numaralandırma: regular (1..N), vuln (N+1..M) — top3 numara almaz
        id_to_num = {}
        for i, aid in enumerate(regular_ids, 1):
            id_to_num[aid] = i
        for i, aid in enumerate(vuln_ids, len(regular_ids) + 1):
            id_to_num[aid] = i

        # ── Top 3 kartları (Önemli Gelişmeler altında) ────────────────────
        # Başlık linki doğrudan kaynak makaleye gider (iç anchor yok)
        top3_cards_html = ''
        if top3_ids:
            top3_cards_html += '            <div class="top3-section">\n'
            for art_id in top3_ids:
                art = articles_by_id.get(art_id, {})
                tr_title, paragraph = _safe_content(art_id)
                link, domain, art_date = _safe_source(art)
                top3_cards_html += (
                    f'                <div class="top3-card">\n'
                    f'                    <div class="top3-card-title">'
                    f'<a href="{link}" target="_blank" style="color:inherit;text-decoration:none;">'
                    f'{tr_title}</a></div>\n'
                    f'                    <p class="top3-card-paragraph">{paragraph}</p>\n'
                    f'                    <p class="source"><b>(XXXXXXX, AÇIK - '
                    f'<a href="{link}" target="_blank">{domain}</a>, {art_date})</b></p>\n'
                    f'                </div>\n'
                )
            top3_cards_html += '            </div>\n'

        # ── Yönetici Özeti tablosu (top3 hariç TÜM normal haberler) ───────
        # Önceden kritik-3 kartlarının hemen altında, aynı kutu içinde ilk 6
        # "diğer önemli haber" ayrı bir liste (important-summary) olarak
        # gösteriliyordu. Artık kritik-3 dışındaki bütün normal haberler,
        # kendi sıralama kriterimize göre (top10 önce, kalan sonra) bu tek
        # tabloda toplanıyor; üstteki kutuda yalnızca 3 kritik kart kalıyor.
        table_rows_html = ''
        rem_pairs = [regular_ids[i:i + 2] for i in range(0, len(regular_ids), 2)]
        for pair in rem_pairs:
            cells = ''
            for art_id in pair:
                num = id_to_num[art_id]
                tr_title, _ = _safe_content(art_id)
                cells += f'                    <td><a href="#haber-{num}">{num}. {tr_title}</a></td>\n'
            if len(pair) == 1:
                cells += '                    <td></td>\n'
            table_rows_html += f'                <tr>\n{cells}                </tr>\n'

        def _render_item(art_id, css_extra=''):
            art = articles_by_id.get(art_id, {})
            tr_title, paragraph = _safe_content(art_id)
            num      = id_to_num[art_id]
            link, domain, art_date = _safe_source(art)
            return (
                f'            <div class="news-item{css_extra}" id="haber-{num}">\n'
                f'                <div class="news-title"><b>{tr_title}</b></div>\n'
                f'                <p class="news-content">{paragraph}</p>\n'
                f'                <p class="source"><b>(XXXXXXX, AÇIK - '
                f'<a href="{link}" target="_blank">{domain}</a>, {art_date})</b></p>\n'
                f'            </div>\n'
            )

        # ── Haber paragrafları ────────────────────────────────────────────
        # Render sırası: regular (1..N) → sosyal sinyaller marker → vuln (N+1..M)
        # Top3 haberleri bu bölümde hiç render edilmez.
        news_items_html = ''

        # Normal haberler (1..N)
        for art_id in regular_ids:
            news_items_html += _render_item(art_id)

        # Sosyal sinyaller kutusu buraya enjekte edilecek (güvenlik açıklarından önce)
        news_items_html += '<!-- SOCIAL_SIGNALS_HERE -->\n'

        # Güvenlik Açıkları bölümü (N+1..M) — en sonda
        if vuln_ids:
            news_items_html += (
                '            <div class="vuln-section-heading" id="guvenlik-aciklari">\n'
                '                <h2>&#128272; Güvenlik Açıkları</h2>\n'
                '            </div>\n'
            )
            for art_id in vuln_ids:
                news_items_html += _render_item(art_id, ' vuln-item')

        # ── Yönetici Özeti kutusu (en önemli 9 haberin tek paragraf özeti) ─
        exec_brief_html = ''
        if exec_summary and exec_summary.strip():
            # Yönetici Özeti de LLM metni → HTML'e gömülmeden entity kaçışı yap.
            import html as _h
            exec_summary = _h.escape(exec_summary.strip(), quote=False)
            vuln_link_html = ''
            if vuln_ids:
                vuln_link_html = (
                    '            <p class="exec-brief-vuln-link">'
                    '<a href="#guvenlik-aciklari">&#128272; Güvenlik Açıkları &#8595;</a>'
                    '</p>\n'
                )
            exec_brief_html = (
                '        <div class="exec-brief" id="yonetici-ozeti-block">\n'
                '            <h2>Yönetici Özeti</h2>\n'
                f'            <p class="exec-brief-paragraph">{exec_summary.strip()}</p>\n'
                f'{vuln_link_html}'
                '        </div>\n\n'
            )

        html = f"""<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Siber Güvenlik Raporu - {today_str}</title>
    <style>{css}    </style>
    <script>(function(){{var m=(localStorage.getItem('theme')==='light')?'light':'dark';document.documentElement.setAttribute('data-theme',m);}})()</script>
</head>
<body>
    <div class="container">
        <div class="report-header">
            <div>
                <h1><span class="header-date">{today_str}</span> Siber Güvenlik Haber Özetleri</h1>
                <p class="header-subtitle">Rapor, saat 12:15 civarı güncellenmektedir.</p>
            </div>
            <div class="header-actions">
            <button class="theme-toggle" id="theme-toggle" onclick="toggleTheme()" title="Gece / Gündüz tema geçişi">
                <svg id="theme-icon-moon" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>
                <span class="sep">/</span>
                <svg id="theme-icon-sun" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>
            </button>
            </div>
        </div>

{exec_brief_html}        <div class="executive-summary">
            <div class="important-news" id="onemli-gelismeler-block">
                <div class="onemli-header">
                    <h2>Önemli Gelişmeler</h2>
                    <div class="block-actions">
                        <button class="block-action-btn" onclick="copyBlock(this)" title="Panoya kopyala">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                            Kopyala
                        </button>
                        <button class="block-action-btn" onclick="saveBlock(this)" title="Metin dosyası olarak kaydet">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                            Kaydet
                        </button>
                        <span class="drag-file-chip" draggable="true" data-filename="{today_str}.txt" title="Masaüstüne veya klasöre sürükleyin → .txt olarak kaydeder (Chrome/Edge)">
                            <span class="drag-file-chip-inner">
                                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                                {today_str}.txt
                            </span>
                            <span class="drag-file-chip-hint">↔ sürükle</span>
                        </span>
                    </div>
                </div>
{top3_cards_html}                <div class="block-actions-bottom">
                    <button class="block-action-btn" onclick="copyBlock(this)" title="Panoya kopyala">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
                        Kopyala
                    </button>
                    <button class="block-action-btn" onclick="saveBlock(this)" title="Metin dosyası olarak kaydet">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>
                        Kaydet
                    </button>
                    <span class="drag-file-chip" draggable="true" data-filename="{today_str}.txt" title="Chrome/Edge: masaüstüne sürükleyin → .txt olarak kaydeder">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/></svg>
                        {today_str}.txt
                    </span>
                </div>
            </div>

            <table class="executive-table">
{table_rows_html}            </table>
        </div>

        <div class="news-section">
{news_items_html}        </div>
    </div>
    <a href="#" class="back-to-top" title="Başa Dön"
       onclick="window.scrollTo({{top:0,behavior:'smooth'}});history.replaceState(null,'',window.location.pathname);return false;">↑</a>
<script>
// Mod döngüsü: Gece ↔ Gündüz (varsayılan: Gece)
// 'light' dışındaki her değer (eski 'auto' dahil) gece olarak normalize edilir.
function _applyTheme() {{
    var mode = (localStorage.getItem('theme') === 'light') ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', mode);
    document.getElementById('theme-icon-moon').classList.toggle('dim', mode !== 'dark');
    document.getElementById('theme-icon-sun').classList.toggle('dim',  mode !== 'light');
}}
function toggleTheme() {{
    var cur = (localStorage.getItem('theme') === 'light') ? 'light' : 'dark';
    localStorage.setItem('theme', cur === 'dark' ? 'light' : 'dark');
    _applyTheme();
}}
_applyTheme();
function _getBlockText() {{
    var lines = [];
    var cards = document.querySelectorAll('#onemli-gelismeler-block .top3-card');
    cards.forEach(function(card, i) {{
        var title   = (card.querySelector('.top3-card-title')    || {{}}).textContent || '';
        var para    = (card.querySelector('.top3-card-paragraph') || {{}}).textContent || '';
        var source  = (card.querySelector('.source')              || {{}}).textContent || '';
        lines.push(title.trim());
        lines.push('');
        lines.push(para.trim());
        lines.push('');
        lines.push(source.trim());
        if (i < cards.length - 1) {{ lines.push('-'.repeat(60)); lines.push(''); }}
    }});
    return lines.join('\\n');
}}
function _flashBtn(btn, label) {{
    var orig = btn.innerHTML;
    btn.classList.add('success');
    btn.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg> ' + label;
    setTimeout(function() {{ btn.classList.remove('success'); btn.innerHTML = orig; }}, 1800);
}}
function copyBlock(btn) {{
    var text = _getBlockText();
    if (navigator.clipboard && navigator.clipboard.writeText) {{
        navigator.clipboard.writeText(text).then(function() {{ _flashBtn(btn, 'Kopyalandı!'); }}).catch(function() {{ _fallbackCopy(text, btn); }});
    }} else {{ _fallbackCopy(text, btn); }}
}}
function _fallbackCopy(text, btn) {{
    var ta = document.createElement('textarea');
    ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try {{ document.execCommand('copy'); _flashBtn(btn, 'Kopyalandı!'); }} catch(e) {{ alert('Kopyalama desteklenmiyor.'); }}
    document.body.removeChild(ta);
}}
function saveBlock(btn) {{
    var text = _getBlockText();
    var blob = new Blob([text], {{type: 'text/plain;charset=utf-8'}});
    var url  = URL.createObjectURL(blob);
    var a    = document.createElement('a');
    a.href = url; a.download = '{today_str}.txt';
    document.body.appendChild(a); a.click();
    document.body.removeChild(a); URL.revokeObjectURL(url);
    _flashBtn(btn, 'Kaydedildi!');
}}
function initDragFile() {{
    document.querySelectorAll('.drag-file-chip').forEach(function(chip) {{
        chip.addEventListener('dragstart', function(e) {{
            var text = _getBlockText();
            var filename = chip.dataset.filename;
            var b64 = btoa(unescape(encodeURIComponent(text)));
            e.dataTransfer.setData('DownloadURL', 'text/plain:' + filename + ':data:text/plain;base64,' + b64);
            e.dataTransfer.setData('text/plain', text);
            e.dataTransfer.items.add(new File([text], filename, {{type: 'text/plain'}}));
            chip.classList.add('dragging');
        }});
        chip.addEventListener('dragend', function() {{ chip.classList.remove('dragging'); }});
    }});
}}
document.addEventListener('DOMContentLoaded', initDragFile);
</script>
</body>
</html>"""
        return html

    def _inject_manual_add(self, html, today_str):
        """'Manuel Haber Ekle' butonu + pop-up scriptini YALNIZCA anasayfaya ekler.

        Arşiv raporlarına eklenmez (buton sadece güncel rapor = index.html üzerinde
        çalışmalı). Asıl iş sunucu tarafındaki Vercel fonksiyonunda yapılır; bu
        dosya yalnızca arayüzü (docs/manual-add.js) yükler.

        Script'e ?v=<dosya içeriği hash'i> eklenir. Önceden ?v=<tarih> (yalnızca
        gün) kullanılıyordu: aynı gün içinde manual-add.js birden fazla kez
        değişirse (elle düzenleme / aynı günde birden fazla commit) sürüm dizesi
        AYNI kalıyor, tarayıcı/Pages CDN'i eski önbellek kopyasını sunmaya devam
        ediyordu (gerçekte yaşandı: 2026-07-02'de metin değişikliği bu yüzden
        canlıya yansımadı). İçerik hash'i, dosya her değiştiğinde otomatik olarak
        farklı bir URL üretir — manuel sürüm numarası bakımı gerekmez.
        """
        try:
            with open('docs/manual-add.js', 'rb') as _f:
                cache_bust = hashlib.md5(_f.read()).hexdigest()[:10]
        except IOError:
            cache_bust = today_str  # dosya okunamazsa eski davranışa düş
        # Buton, header-actions içine tema butonuyla AYNI SATIRDA (önüne)
        # eklenir — başlık sola yaslı, ikisi birlikte sağa yaslı tek grup olur.
        button_html = (
            '            <button class="manual-add-btn" onclick="openManualAddModal()" '
            'title="Rapora haber ekle / sil">\n'
            '                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" '
            'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" '
            'stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"/>'
            '<line x1="5" y1="12" x2="19" y2="12"/></svg>\n'
            '                Haber Ekle / Sil\n'
            '            </button>\n'
        )
        # header-actions'ın açılışından hemen sonra (tema butonundan ÖNCE) yerleştir.
        html = html.replace(
            '<div class="header-actions">\n',
            '<div class="header-actions">\n' + button_html,
            1,
        )
        html = html.replace(
            '</body>',
            f'    <script src="/siberguvenlik/manual-add.js?v={cache_bust}"></script>\n</body>',
            1,
        )
        return html

    def fetch_full_article(self, url, source_name):
        """Tam metin çeker — max 10 saniye, sonra geç"""
        import threading
        result = {'full_text': "", 'word_count': 0, 'success': False, 'domain': ''}
        _session_holder = [None]  # thread'den response'a erişim için

        def _fetch():
            try:
                r = _requests_get_with_retry(url, headers=self.headers, timeout=(3, 5), stream=True)
                _session_holder[0] = r
                chunks = []
                total_size = 0
                for chunk in r.iter_content(chunk_size=8192):
                    chunks.append(chunk)
                    total_size += len(chunk)
                    if total_size > 500_000:
                        break
                r.close()
                raw = b''.join(chunks).decode(r.encoding or 'utf-8', errors='replace')
                soup = BeautifulSoup(raw, 'html.parser')
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
                    el = soup.find('div', class_=lambda c: c and any(
                        x in str(c).lower() for x in ['content', 'article', 'body', 'post']))
                    if el:
                        text = self._extract(el)
                wc = len(text.split()) if text else 0
                text = text.replace('\t', ' ').replace('\r', '')
                if wc > 100:
                    result.update({'full_text': text, 'word_count': wc, 'success': True, 'domain': domain})
                else:
                    result['domain'] = domain
            except Exception:
                pass

        print(f"      📄 Tam metin...", end='', flush=True)
        t = threading.Thread(target=_fetch, daemon=True)
        t.start()
        t.join(timeout=10)
        if t.is_alive():
            # Timeout: bağlantıyı zorla kapat ki thread bloklanmasın
            try:
                if _session_holder[0] is not None:
                    _session_holder[0].close()
            except Exception:
                pass
            print(f" ⏱️  (timeout)")
        elif result['success']:
            print(f" ✅ ({result['word_count']})")
        else:
            print(f" ⚠️  (0)")
        return result

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

    def _feed_summary_fallback(self, art):
        """Makale sayfasından tam metin çıkmadıysa, RSS/Atom feed'inin kendi gövdesine
        (content:encoded / content / description) düş.

        Neden: bazı kaynaklar (Microsoft Security, Bellingcat, The Cyber Express,
        Citizen Lab ...) tam makale gövdesini feed'de gönderir ama makale sayfası
        JS-render/seçici-uyumsuzluğu nedeniyle kazınamaz → save_txt bunları
        `success=False` diye eler ve kaynak "sessiz" görünür.

        Halüsinasyon koruması KORUNUR: yalnızca feed gövdesi FEED_SUMMARY_MIN_WORDS
        (=100) kelime ve üzeriyse kabul edilir; ince özetler eskisi gibi elenir —
        metinler 100 kelime altına DÜŞMEZ."""
        html = (art.get('feed_html') or art.get('description') or '').strip()
        if not html:
            return
        try:
            soup = BeautifulSoup(html, 'html.parser')
        except Exception:
            return
        text = self._extract(soup)
        wc = len(text.split())
        if wc < FEED_SUMMARY_MIN_WORDS:
            # <p> yoksa (düz metin/<br> ile gelen feed) _extract boş/az döner — ham metne düş
            plain = soup.get_text(separator='\n').strip()
            if len(plain.split()) > wc:
                text, wc = plain, len(plain.split())
        if wc >= FEED_SUMMARY_MIN_WORDS:
            text = text.replace('\t', ' ').replace('\r', '')
            art.update({'full_text': text, 'word_count': wc,
                        'success': True, 'from_feed_summary': True})
            if not art.get('domain'):
                art['domain'] = urlparse(art.get('link', '')).netloc.replace('www.', '')

    def _article_proxy_fallback(self, art, source_name):
        """Son çare: makale gövdesi doğrudan kazınamadı VE feed-özeti de yetersizse,
        makaleyi temiz-IP okuyucu servisinden (ARTICLE_PROXY, ör. Jina Reader) çek.

        IP-engelli/JS-render/anti-bot makale sayfalarında işe yarar (proxy_probe
        2026-07-27: DFIR makalesi doğrudan 50 kelime → Jina 7038). Yalnızca doğrudan
        + feed-özet başarısız olan AZ sayıda makalede tetiklenir; sağlıklı kaynaklar
        buraya hiç düşmez. Halüsinasyon koruması korunur (>= FEED_SUMMARY_MIN_WORDS)."""
        if not ARTICLE_PROXY or not art.get('link'):
            return
        # Bütçe koruması: proxy yavaştır. Sınırsız bırakılırsa kötü bir günde
        # onlarca makale buraya düşer ve workflow'un 25 dk sınırı dolar (iş
        # yarıda ölür → yarım yazılmış dosya riski). Sınır dolunca sessizce atla.
        if self._proxy_calls >= ARTICLE_PROXY_MAX_CALLS:
            return
        if self._proxy_seconds >= ARTICLE_PROXY_BUDGET_SEC:
            return
        url = ARTICLE_PROXY.replace('{url}', art['link'])
        _t0 = time.time()
        try:
            # max_retries=1 + kısa okuma timeout'u: en kötü durumda ~26s/makale
            # (varsayılan 3 retry × 20s ≈ 87s idi).
            r = _requests_get_with_retry(url, headers=self.headers, timeout=(5, 12),
                                         max_retries=1)
            if r.status_code != 200:
                return
            text = r.text or ''
            # Jina markdown çıktısı başında meta ("Title:/URL Source:/Markdown Content:")
            # bulundurur — gövdeyi "Markdown Content:" sonrasından al.
            if 'Markdown Content:' in text:
                text = text.split('Markdown Content:', 1)[1]
            text = text.replace('\t', ' ').replace('\r', '').strip()
            wc = len(text.split())
            if wc >= FEED_SUMMARY_MIN_WORDS:
                art.update({'full_text': text[:20000], 'word_count': min(wc, 3000),
                            'success': True, 'from_article_proxy': True})
                if not art.get('domain'):
                    art['domain'] = urlparse(art.get('link', '')).netloc.replace('www.', '')
        except Exception:
            pass
        finally:
            # Başarı/başarısızlık fark etmez: harcanan süre ve deneme sayılır.
            self._proxy_calls += 1
            self._proxy_seconds += time.time() - _t0

    def _crawl_newsletter_links(self, newsletter_urls, source_name):
        """
        Newsletter/digest sayfasındaki iç makale linklerini çıkarır ve
        her birinin tam metnini çeker.

        Yalnızca source domain'ine ait, sayısal ID içeren makale URL'leri
        alınır (kategori/etiket/ana sayfa linkleri atlanır).
        Deduplication sonraki aşamada (_filter_duplicates) yapılır.
        """
        import re as _re
        crawled_articles = []
        seen_hrefs = set()

        for nl_entry in newsletter_urls:
            # (url, pub_date) tuple veya plain string (geriye dönük uyumluluk)
            if isinstance(nl_entry, tuple):
                nl_url, nl_pub_date = nl_entry
            else:
                nl_url, nl_pub_date = nl_entry, ''

            try:
                print(f"   └─ 📰 Newsletter çekiliyor: {nl_url[:70]}...")
                r = _requests_get_with_retry(nl_url, headers=self.headers, timeout=(3, 5))
                if r.status_code != 200:
                    print(f"      ⚠️  HTTP {r.status_code}")
                    continue
                soup = BeautifulSoup(r.content, 'html.parser')
                nl_domain = urlparse(nl_url).netloc.replace('www.', '')

                # Makale içerik alanını bul (entry-content veya article)
                content_el = None
                for sel in [{'class': 'entry-content'}, {'class': 'post-content'},
                            {'class': 'article-content'}]:
                    content_el = soup.find('div', **sel) or soup.find('article')
                    if content_el:
                        break
                if not content_el:
                    content_el = soup  # tüm sayfa fallback

                found = 0
                for a in content_el.find_all('a', href=True):
                    href = a.get('href', '').strip()
                    if not href or href.startswith('#'):
                        continue
                    # Sadece aynı domain'e ait linkler
                    parsed = urlparse(href)
                    link_domain = parsed.netloc.replace('www.', '')
                    if link_domain and link_domain != nl_domain:
                        continue
                    # Tam URL'ye dönüştür
                    if not parsed.scheme:
                        href = f"https://{nl_domain}{href}"
                    # Sayısal ID içeren makale URL'si mi? (/12345/ gibi)
                    if not _re.search(r'/\d{4,}/', href):
                        continue
                    # Newsletter URL'si değil
                    if any(pat in href.lower() for pat in SKIP_URL_PATTERNS):
                        continue
                    norm = _normalize_url_advanced(href)
                    if norm in seen_hrefs:
                        continue
                    seen_hrefs.add(norm)

                    title = a.get_text(strip=True)
                    if not title or len(title) < 15:
                        continue

                    crawled_articles.append({
                        'title':       title,
                        'link':        href,
                        'description': '',
                        'date':        nl_pub_date,  # newsletter'ın yayın tarihi
                        'source':      source_name,
                    })
                    found += 1

                print(f"      ✅ {found} makale linki bulundu")
                time.sleep(1)

            except Exception as e:
                print(f"      ❌ Newsletter crawl hatası: {e}")

        if not crawled_articles:
            return []

        # Her makale için tam metin çek
        print(f"   └─ 📄 Newsletter makaleleri ({len(crawled_articles)}) tam metin çekiliyor:")
        result = []
        for i, art in enumerate(crawled_articles, 1):
            print(f"      [{i}/{len(crawled_articles)}]", end=' ', flush=True)
            res = self.fetch_full_article(art['link'], source_name)
            art.update(res)
            if res['success']:
                result.append(art)
            time.sleep(0.5)
        print(f"   └─ ✅ {len(result)}/{len(crawled_articles)} newsletter makalesi tam metin çekildi")
        return result

    @staticmethod
    def _lenient_xml_parse(raw_bytes):
        """stdlib ET.fromstring KALICI olarak bozuk feed'lerde (ör. Industrial
        Cyber — her gün AYNI 'mismatched tag: line 74, column 2' hatasıyla
        düşer) tüm feed'i kaybettiriyor. Kaynağın kendi XML şablonundaki bir
        kaçış hatası (genelde description alanına kaçışsız HTML/`&` sızması)
        stdlib'in katı ayrıştırıcısını durduruyor.

        lxml (requirements.txt'te zaten var) recover=True ile bozuk kısımları
        ATLAYIP geri kalan geçerli öğeleri kurtarabilir. Kurtarılan ağaç stdlib
        ET Element'ine çevrilir ki çağıran kod (root.findall(...)) değişmeden
        çalışsın. lxml yoksa/kurtarma da başarısızsa None döner (orijinal
        ParseError çağıran tarafından rapor edilmeye devam eder — davranış
        REGRESSION değil, sadece kurtarma bir olasılık daha).
        """
        try:
            from lxml import etree as _lxml_etree
        except ImportError:
            return None
        try:
            parser = _lxml_etree.XMLParser(recover=True)
            lxml_root = _lxml_etree.fromstring(raw_bytes, parser=parser)
            if lxml_root is None:
                return None
            return ET.fromstring(_lxml_etree.tostring(lxml_root))
        except Exception:
            return None

    def fetch_rss(self, url, source_name):
        """RSS çeker — max 15 saniye timeout korumalı"""
        import threading
        result_holder = {'articles': [], 'error': None}

        def _fetch_rss():
            try:
                r = _requests_get_with_retry(url, headers=self.headers, timeout=(3, 5))
                if r.status_code != 200:
                    result_holder['error'] = Exception(f"HTTP {r.status_code}")
                    return
                try:
                    root = ET.fromstring(r.content)
                except ET.ParseError as parse_err:
                    root = self._lenient_xml_parse(r.content)
                    if root is None:
                        raise parse_err

                if root.tag.endswith('feed'):  # Atom
                    ATOM = '{http://www.w3.org/2005/Atom}'
                    for entry in root.findall(f'.//{ATOM}entry')[:40]:
                        t = entry.find(f'{ATOM}title')
                        l = entry.find(f'{ATOM}link')
                        s = entry.find(f'{ATOM}summary')
                        d = entry.find(f'{ATOM}published')
                        # Tam gövde çoğu Atom feed'inde <content>'te; <summary> sadece
                        # özet. feed_html en zengin olanı taşır (feed-özet fallback için).
                        c = entry.find(f'{ATOM}content')
                        feed_html = (c.text if c is not None and c.text else
                                     (s.text if s is not None else '')) or ''
                        # Boş <title></title> → t.text=None; sonraki title.lower()
                        # çağrıları AttributeError ile tüm koşuyu düşürür — atla.
                        if t is not None and (t.text or '').strip():
                            result_holder['articles'].append({
                                'title': t.text.strip(),
                                'link': (l.get('href') or '') if l is not None else '',
                                'description': (s.text or '') if s is not None else '',
                                'feed_html': feed_html,
                                'date': d.text if d is not None else '',
                                'source': source_name
                            })
                else:  # RSS
                    CONTENT_NS = '{http://purl.org/rss/1.0/modules/content/}'
                    for item in root.findall('.//item')[:40]:
                        t = item.find('title')
                        l = item.find('link')
                        d = item.find('description')
                        p = item.find('pubDate')
                        # Tam gövde çoğu WordPress feed'inde <content:encoded>'da;
                        # <description> sadece özet olabilir. feed_html en zengini taşır.
                        enc = item.find(f'{CONTENT_NS}encoded')
                        feed_html = (enc.text if enc is not None and enc.text else
                                     (d.text if d is not None else '')) or ''
                        # Aynı boş-başlık koruması (RSS dalı).
                        if t is not None and (t.text or '').strip():
                            result_holder['articles'].append({
                                'title': t.text.strip(),
                                'link': (l.text or '') if l is not None else '',
                                'description': (d.text or '') if d is not None else '',
                                'feed_html': feed_html,
                                'date': p.text if p is not None else '',
                                'source': source_name
                            })
            except Exception as e:
                result_holder['error'] = e

        t = threading.Thread(target=_fetch_rss, daemon=True)
        t.start()
        t.join(timeout=15)

        if t.is_alive():
            error_msg = f"RSS hatası - {source_name}: Timeout (15s)"
            self.rss_errors.append(error_msg)
            self.source_stats[source_name] = {'raw': 0, 'kept': 0, 'status': 'TIMEOUT'}
            print(f"      ⏱️  RSS TIMEOUT (15s) — geçiliyor")
            return []

        if result_holder['error']:
            e = result_holder['error']
            error_msg = f"RSS hatası - {source_name}: {str(e)[:100]}"
            self.rss_errors.append(error_msg)
            self.source_stats[source_name] = {'raw': 0, 'kept': 0,
                                              'status': f'HATA: {str(e)[:60]}'}
            print(f"      ❌ RSS HATA: {str(e)[:50]}")
            return []

        articles = result_holder['articles']
        raw = len(articles)
        if raw == 0:
            # HTTP 200 + geçerli XML ama 0 madde. Bu, sessiz arızanın en sinsi
            # biçimi: feed anti-bot sayfası/boş kabuk döndürmüş ya da öğe/başlık
            # şeması değişmiş olabilir. Timeout/HTTP hatası gibi loglanmadığı için
            # bugüne kadar "normal sakin gün"den ayırt edilemiyordu — artık iz bırak.
            self.rss_errors.append(f"SESSİZ BOŞ - {source_name}: 200 OK ama 0 madde")
            self.source_stats[source_name] = {'raw': 0, 'kept': 0, 'status': 'BOŞ (200/0 madde)'}
        else:
            self.source_stats[source_name] = {'raw': raw, 'kept': 0, 'status': 'OK'}
        return articles

    def _load_used_links(self):
        """
        Kullanılan linkleri 7 günden yükle
        Backward compatibility: eski format (3 sütun) ve yeni format (4 sütun + hash) destekler
        """
        if not os.path.exists(self.used_links_file):
            return set(), {}, set()

        cutoff = _now_tr() - timedelta(days=7)
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

    def _keyword_jaccard_similarity(self, title_a, title_b):
        """
        Anahtar kelime Jaccard benzerliği — farklı kaynaktan aynı olay tespiti.

        Türkçe çekim eklerini gidermek için 5 harflik kök alınır.
        CVE veya sürüm numaraları (1.2.3) farklıysa 0.0 döner (farklı olay).
        """
        a = title_a.lower()
        b = title_b.lower()

        # Farklı CVE numarası → kesinlikle farklı haber
        cve_a = set(re.findall(r'cve-\d{4}-\d+', a))
        cve_b = set(re.findall(r'cve-\d{4}-\d+', b))
        if cve_a and cve_b and cve_a != cve_b:
            return 0.0

        # Farklı sürüm numarası (örn. 7.2.1 vs 8.0.0) → büyük olasılıkla farklı haber
        ver_a = set(re.findall(r'\d+\.\d+[\.\d]*', a))
        ver_b = set(re.findall(r'\d+\.\d+[\.\d]*', b))
        if ver_a and ver_b and not ver_a & ver_b:
            return 0.0

        def stems(text):
            return {w[:5] for w in text.split() if len(w) >= 5}

        sa, sb = stems(a), stems(b)
        if not sa or not sb:
            return 0.0
        return len(sa & sb) / len(sa | sb)

    def _save_used_links(self, articles):
        """Kullanılan linkleri kaydet (7 günden eski olanları sil)"""
        if not articles:
            return

        now = _now_tr()
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
                        except Exception:
                            pass
            except IOError:
                pass

        today = now.strftime('%Y-%m-%d')
        for art in articles:
            if art.get('link'):
                title = art.get('title', '')
                description = art.get('description', '')
                content_hash = _calculate_content_hash(title, description)
                # Dosya satır-tabanlı TSV: başlıktaki sekme sütunları kaydırır,
                # newline kaydı böler ve okuyucuyu bozar — düz boşluğa çevir.
                clean_title = ' '.join(str(title).split())
                clean_link = ' '.join(str(art['link']).split())
                existing.append(f"{today}\t{clean_link}\t{clean_title}\t{content_hash}")

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

        now = _now_tr()
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
                    except Exception:
                        pass

        timestamp = now.strftime('%Y-%m-%d %H:%M')
        for error in self.rss_errors:
            existing.append(f"{timestamp} | {error}")

        os.makedirs("data", exist_ok=True)
        with open(self.rss_errors_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(existing) + '\n')

        print(f"⚠️  {len(self.rss_errors)} RSS hatası kaydedildi: {self.rss_errors_file}")

    def _save_source_health(self):
        """Kaynak-başına sağlık raporu — her koşuda üzerine yazılır.

        `data/kaynak_saglik.txt`: bugünkü koşuda her AKTİF kaynağın fetch
        sonucu (OK/BOŞ/TIMEOUT/HATA), ham madde sayısı ve pencere+dedup sonrası
        kalan sayısı. Sağlıklı ("üretiyor") kaynakları, sessiz arızalılardan
        ("200 ama 0 madde") ve gerçek hatalardan tek bakışta ayırmak için.

        Neden ayrı dosya: rss_errors.txt yalnızca sorunları biriktirir; burada
        TÜM kaynakların tam tablosu tutulur ki bir kaynağın "hiç mi üretmiyor,
        yoksa bugün mü sakin" olduğu kesinleşsin. Üretimi ENGELLEMEZ."""
        if not self.source_stats:
            return
        now = _now_tr()
        stamp = now.strftime('%Y-%m-%d %H:%M')

        # Konfigüre olup bu koşuda hiç denenmemiş kaynakları da göster (kod yolu
        # onları atlıyorsa fark edelim).
        rows = []
        for src in self.sources:
            st = self.source_stats.get(src)
            if st is None:
                rows.append((src, -1, -1, 'DENENMEDİ'))
            else:
                rows.append((src, st.get('raw', 0), st.get('kept', 0), st.get('status', '?')))

        # kept azalan; eşitlikte isim
        rows.sort(key=lambda r: (-r[2], -r[1], r[0]))

        uretiyor = sum(1 for r in rows if r[2] > 0)
        bos      = sum(1 for r in rows if r[3].startswith('BOŞ'))
        hatali   = sum(1 for r in rows if r[3].startswith(('HATA', 'TIMEOUT')))

        lines = []
        lines.append(f"# KAYNAK SAĞLIK RAPORU — {stamp}")
        lines.append(f"# aktif={len(rows)}  üretiyor={uretiyor}  boş(200/0)={bos}  hata/timeout={hatali}")
        lines.append(f"# {'KAYNAK':26} {'HAM':>4} {'KALAN':>6}  DURUM")
        for src, raw, kept, status in rows:
            raw_s  = '-' if raw  < 0 else str(raw)
            kept_s = '-' if kept < 0 else str(kept)
            lines.append(f"{src:26} {raw_s:>4} {kept_s:>6}  {status}")

        os.makedirs("data", exist_ok=True)
        with open(self.source_health_file, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')

        print(f"🩺 Kaynak sağlığı: {uretiyor} üretiyor / {bos} sessiz-boş / "
              f"{hatali} hata → {self.source_health_file}")

    def _filter_duplicates(self, all_news):
        """
        Tekrar eden haberleri filtrele (3 seviye: link + hash + benzerlik)
        """
        used_links, used_titles, used_hashes = self._load_used_links()

        filtered = {}
        removed_count = 0
        detail_removed = {'link': 0, 'hash': 0, 'similarity': 0, 'keyword': 0, 'codename': 0}

        # Seviye 5 (kod adı) yalnızca AYNI RUN içinde karşılaştırılır — 7 günlük
        # geçmişe karşı DEĞİL. Böylece aynı gün 3 kaynaktan gelen "FortiBleed"
        # haberleri tekilleşir, ama ertesi günkü FortiBleed GELİŞMESİ engellenmez.
        run_codenames = {}  # codename -> ilk görülen başlık

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
                # 0.72: aynı olay farklı tehdit aktörü adıyla raporlandığında
                # (örn. FishMonger vs Earth Lusca) yakalamak için 0.85'ten düşürüldü.
                is_similar = False
                for used_title in used_titles.values():
                    similarity = SequenceMatcher(None, title.lower(), used_title.lower()).ratio()
                    if similarity >= 0.72:
                        is_similar = True
                        removed_count += 1
                        detail_removed['similarity'] += 1
                        break

                if is_similar:
                    continue

                # Seviye 4: Anahtar kelime Jaccard örtüşmesi
                # Aynı olay farklı kaynaklardan farklı anlatımla geldiğinde
                # Seviye 3'ün kaçırdığı durumları yakalar (örn. "Grupların" vs "Saldırganların").
                for used_title in used_titles.values():
                    if self._keyword_jaccard_similarity(title, used_title) >= 0.45:
                        is_similar = True
                        removed_count += 1
                        detail_removed['keyword'] += 1
                        break

                if is_similar:
                    continue

                # Seviye 5: Ortak ayırt edici kod adı (aynı run içinde)
                # Başlıklar farklı sözcüklerle yazılsa bile aynı kampanya kod
                # adını (FortiBleed gibi) paylaşan haberler tek olaydır; Seviye
                # 3/4'ün eşik altında kalan bu durumu yakalar.
                art_codenames = _extract_codenames(title)
                shared_codename = next((c for c in art_codenames if c in run_codenames), None)
                if shared_codename:
                    removed_count += 1
                    detail_removed['codename'] += 1
                    continue

                # Geçen haberi mevcut run içindeki karşılaştırma havuzuna ekle
                used_titles[link_norm] = title
                used_links.add(link_norm)
                used_hashes.add(content_hash)
                for c in art_codenames:
                    run_codenames.setdefault(c, title)
                filtered_articles.append(art)

            if filtered_articles:
                filtered[src] = filtered_articles

        if removed_count > 0:
            print(f"🔄 {removed_count} tekrar eden haber filtrelendi")
            print(f"   ├─ URL: {detail_removed['link']}")
            print(f"   ├─ Hash: {detail_removed['hash']}")
            print(f"   ├─ Benzerlik: {detail_removed['similarity']}")
            print(f"   ├─ Anahtar kelime: {detail_removed['keyword']}")
            print(f"   └─ Kod adı: {detail_removed['codename']}")

        return filtered

    # Haber zaman penceresi (saat). 168 saat = 7 gün: URL deduplikasyon penceresi
    # (haberler_linkler.txt, 7 gün) ile HİZALI tutulur. Tekrar önleme görevini
    # zaten dedup üstlendiği için daha dar bir pencere hiçbir dedup faydası
    # sağlamaz; yalnızca GÜNLÜK YAYINLAMAYAN yüksek-değerli threat-intel
    # feed'lerini (ANSSI, NCSC, CERT-EU, NIST, Microsoft, Mandiant, CrowdStrike,
    # Unit 42, Talos, DFIR, SentinelOne, Proofpoint, Recorded Future) tamamen
    # eler ve rapor havuzunu açlığa sürüklerdi. 72s pencere 2026-07-01'de (3cf94c4)
    # eklendikten sonra günlük benzersiz havuz ~90'dan ~30'a düştü ve raporlar
    # 2-5 habere indi; 7 güne genişletmek bu regresyonu telafi eder. Her haber
    # yine dedup sayesinde yalnızca BİR KEZ raporlanır.
    NEWS_WINDOW_HOURS = 168

    def _news_cutoff_dt(self):
        """Haber zaman penceresinin alt sınırı (timezone-aware, UTC)."""
        from datetime import timezone, timedelta as td
        return datetime.now(timezone.utc) - td(hours=self.NEWS_WINDOW_HOURS)

    def _article_within_window(self, art, cutoff_dt):
        """Haberin yayın tarihi pencere içinde mi? Parse edilemezse güvenli
        tarafta kalıp True döner (haber dahil edilir).

        Tarih değil datetime karşılaştırması kullanılır: tarih bazlı kontrol
        sabah çalışmalarında ya tüm dünü doldurur ya da ABD iş saatlerindeki
        haberleri henüz yayınlanmadığı için dışarıda bırakırdı.
        """
        from datetime import timezone
        UTC = timezone.utc
        raw_date = art.get('date', '')
        art_dt = None

        # Timezone-aware formatları dene
        for fmt in [
            '%a, %d %b %Y %H:%M:%S %z',
            '%Y-%m-%dT%H:%M:%S%z',
            '%Y-%m-%dT%H:%M:%S.%f%z',
        ]:
            try:
                art_dt = datetime.strptime(raw_date, fmt)
                break
            except Exception:
                pass

        # Z sonekini dene
        if art_dt is None and isinstance(raw_date, str) and raw_date.endswith('Z'):
            for fmt in ['%Y-%m-%dT%H:%M:%S.%fZ', '%Y-%m-%dT%H:%M:%SZ']:
                try:
                    art_dt = datetime.strptime(raw_date, fmt).replace(tzinfo=UTC)
                    break
                except Exception:
                    pass

        # RFC 2822 isimli timezone'lar (GMT, EST, ...) — strptime %z bunları
        # kabul etmez; parsedate_to_datetime kabul eder. Bu fallback olmadan
        # "... GMT" tarihli eski haberler parse edilemeyip filtreyi atlardı.
        if art_dt is None and isinstance(raw_date, str) and raw_date.strip():
            try:
                import email.utils as _eu
                art_dt = _eu.parsedate_to_datetime(raw_date)
            except Exception:
                art_dt = None

        if art_dt is None:
            # Tarih parse edilemezse dahil et (güvenli taraf)
            return True

        if not art_dt.tzinfo:
            art_dt = art_dt.replace(tzinfo=UTC)

        return art_dt >= cutoff_dt

    def _filter_old_articles(self, all_news):
        """Pencere dışında (>NEWS_WINDOW_HOURS saat) kalan haberleri filtrele."""
        cutoff_dt = self._news_cutoff_dt()

        filtered = {}
        removed_count = 0

        for src, articles in all_news.items():
            filtered_articles = [a for a in articles
                                 if self._article_within_window(a, cutoff_dt)]
            removed_count += len(articles) - len(filtered_articles)
            if filtered_articles:
                filtered[src] = filtered_articles

        if removed_count > 0:
            print(f"📅 {removed_count} eski haber filtrelendi (>{self.NEWS_WINDOW_HOURS} saat)")

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

            # Tarih filtresini tam metin çekiminden ÖNCE uygula: pencere dışı
            # haberlerin tam metnini/newsletter sayfasını boş yere çekmeyelim.
            # (Tarihsiz haberler güvenli tarafta kalıp geçer; son filtrede
            # newsletter'dan türeyen haberler de tarihe göre tekrar elenir.)
            if articles:
                cutoff_dt = self._news_cutoff_dt()
                before = len(articles)
                articles = [a for a in articles
                            if self._article_within_window(a, cutoff_dt)]
                if before - len(articles) > 0:
                    print(f"   └─ 📅 {before - len(articles)} pencere dışı haber atlandı (>{self.NEWS_WINDOW_HOURS}s)")

            if articles:
                # Newsletter URL'lerini ayır: atlamak yerine içlerindeki
                # makale linklerini çıkararak pipeline'a sok
                regular_articles  = []
                newsletter_urls   = []  # list of (url, pub_date) tuples
                for a in articles:
                    link_lower = (a.get('link') or '').lower()
                    if any(pat in link_lower for pat in SKIP_URL_PATTERNS):
                        newsletter_urls.append((a['link'], a.get('date', '')))
                    else:
                        regular_articles.append(a)

                if newsletter_urls:
                    print(f"   └─ 🔗 {len(newsletter_urls)} newsletter sayfası genişletilecek")
                    crawled = self._crawl_newsletter_links(newsletter_urls, src)
                    regular_articles.extend(crawled)

                articles = regular_articles
                if not articles:
                    print(f"   └─ ❌ Bulunamadı (tümü filtrelendi)")
                    time.sleep(1)
                    continue

                print(f"   └─ ✅ {len(articles)} haber")
                total += len(articles)
                print(f"   └─ 📄 Tam metinler:")
                for i, art in enumerate(articles, 1):
                    if art['link'] and not art.get('success'):  # crawled makalelerde zaten çekildi
                        print(f"      [{i}/{len(articles)}]", end=' ', flush=True)
                        res = self.fetch_full_article(art['link'], src)
                        art.update(res)
                        # Makale sayfası kazınamadıysa feed'in kendi gövdesine düş
                        # (≥100 kelimeyse). Aksi halde bu haber save_txt'te elenirdi.
                        if not art.get('success'):
                            self._feed_summary_fallback(art)
                        # O da yetmezse: temiz-IP okuyucu proxy'si (Jina) son çare.
                        if not art.get('success'):
                            self._article_proxy_fallback(art, src)
                        if art.get('success'):
                            full_text_success += 1
                            if art.get('from_feed_summary'):
                                print(f"→ feed özeti ({art.get('word_count')})", flush=True)
                            elif art.get('from_article_proxy'):
                                print(f"→ proxy/jina ({art.get('word_count')})", flush=True)
                        time.sleep(0.5)
                    elif art.get('success'):
                        full_text_success += 1
                all_news[src] = articles
                if src in self.source_stats:
                    self.source_stats[src]['kept'] = len(articles)
            else:
                print(f"   └─ ❌ Bulunamadı")

            time.sleep(1)

        if self.rss_errors:
            self._save_rss_errors()
        self._save_source_health()

        # ── Sosyal medya sinyalleri (Reddit, HN, GitHub) ──
        # Haber akışından ayrı tutulur, sadece HTML enjeksiyonu için saklanır
        try:
            self.social_data = fetch_social_signals(SOCIAL_SIGNAL_CONFIG)
        except Exception as e:
            print(f"⚠️  Sosyal medya sinyalleri çekilemedi (network sorun): {str(e)[:100]}")
            self.social_data = []

        all_news = self._filter_duplicates(all_news)
        all_news = self._filter_old_articles(all_news)

        total        = sum(len(arts) for arts in all_news.values())
        full_text_ok = sum(1 for arts in all_news.values() for art in arts if art.get('success'))

        print(f"\n{'=' * 70}")
        print(f"📊 {total} haber (tekrarsız) | {full_text_ok} tam metin | 📡 {len(self.social_data)} sosyal sinyal")
        print(f"{'=' * 70}\n")
        return all_news

    def save_txt(self, news_data):
        """Ham RSS'i günlük kaydet (üzerine yaz)"""
        print("💾 TXT dosyaları kaydediliyor...")
        now = _now_tr()
        os.makedirs("data", exist_ok=True)

        # SESSION_DATE ilk satırda — git checkout mtime'a güvenmek yerine
        # içerik kontrolü için kullanılır (bkz. main() ham_exists_for_today kontrolü)
        txt = f"SESSION_DATE: {now.strftime('%Y-%m-%d')}\n"
        txt += f"\n{'=' * 80}\n📅 {now.strftime('%d %B %Y').upper()} - SİBER GÜVENLİK HABERLERİ (HAM RSS)\n{'=' * 80}\n\n"

        # DİKKAT: bu listeye YALNIZCA rapora giren (tam metni çıkan) haberler
        # eklenir; sonunda _save_used_links'e verilir. Tam metni çıkmayan haberi
        # "görüldü" diye işaretlemek onu 7 gün boyunca yakar (_filter_duplicates
        # Seviye-1 link eşleşmesiyle eler) — geçici bir timeout/503 yüzünden düşen
        # haber kalıcı kaybolur, ertesi gün selector düzeltilse bile geri gelmez.
        # (Ölçüm: 07-21..07-24 arası günde 9-14 haber böyle yakılmıştı.)
        kaydedilen_articles = []
        num = 0
        skipped_no_content = 0
        for src, articles in news_data.items():
            for art in articles:
                # Tam metni olmayan haberleri Gemini'ye gönderme — sadece başlık varsa
                # Gemini içerik üretemez ve halüsinasyon yapar, bu yüzden kesinlikle dışla
                if not art.get('success') or art.get('word_count', 0) == 0:
                    skipped_no_content += 1
                    continue
                kaydedilen_articles.append(art)
                num += 1
                kaynak_etiketi = (" | KAYNAK: feed özeti" if art.get('from_feed_summary')
                                  else " | KAYNAK: proxy okuyucu" if art.get('from_article_proxy')
                                  else "")
                txt += f"[{num}] {src} - {art['title']}\n{'─' * 80}\n"
                txt += f"Tarih: {art['date']}\nLink: {art['link']}\n"
                txt += f"\n[TAM METİN - {art['word_count']} kelime{kaynak_etiketi}]\n{art['full_text']}\n"
                art_date = _parse_article_date(art.get('date', ''), now)
                txt += f"\n(XXXXXXX, AÇIK - {art.get('link', '')}, {art.get('domain', '')}, {art_date})\n\n{'=' * 80}\n\n"

        if skipped_no_content > 0:
            print(f"⚠️  {skipped_no_content} haber tam metin olmadığı için rapor dışı bırakıldı (halüsinasyon önleme)")

        # Sosyal sinyaller — haber değil, arşiv amaçlı referans kaydı
        if self.social_data:
            txt += f"\n{'=' * 80}\n"
            txt += f"SOSYAL MEDYA SİNYALLERİ — HABER DEĞİL, SADECE REFERANS KAYDI\n"
            txt += f"[S1]-[S5] etiketleri haber sayılmaz, Gemini bu bölümü işlemez.\n"
            txt += f"{'=' * 80}\n\n"
            for i, art in enumerate(self.social_data, 1):
                platform = art.get('platform', 'unknown')
                score    = art.get('score', 0)
                comments = art.get('comments', 0)
                txt += f"[S{i}] {art['source']} | Skor: {score} | Yorum: {comments}\n"
                txt += f"Baslik: {art['title']}\n"
                txt += f"Link: {art['link']}\n"
                if art.get('full_text'):
                    txt += f"{art['full_text'][:200]}\n"
                txt += f"\n"

        with open("data/haberler_ham.txt", 'w', encoding='utf-8') as f:
            f.write(txt)

        print(f"✅ data/haberler_ham.txt (günlük - üzerine yazıldı)")

        # Yalnızca rapora GİREN haberler "görüldü" olarak işaretlenir. Elenenler
        # işaretlenmez ki ertesi gün yeniden denensin (bkz. yukarıdaki not).
        if skipped_no_content > 0:
            print(f"   ↩️  {skipped_no_content} haber 'görüldü' işaretlenmedi — "
                  f"ertesi gün yeniden denenecek")
        self._save_used_links(kaydedilen_articles)

        return txt

    # Siber sinyal sözlüğü (TR+EN) — bir haberin gerçekten siber boyutu olup
    # olmadığını deterministik kontrol etmek için. Geniş tutulur: amaç gerçek
    # siber haberi YANLIŞLIKLA elemek değil, "teknik arıza/saf diplomatik"
    # gibi siber boyutu OLMAYAN kartları top3'ten ayıklamaktır.
    _CYBER_SIGNALS = (
        'siber', 'cyber', 'hack', 'saldır', 'attack', 'zararlı', 'malware',
        'ransom', 'fidye', 'casus', 'spyware', 'surveillance', 'gözetim',
        'ihlal', 'breach', 'sızdır', 'sızma', 'sızıntı', 'leak', 'exfiltr',
        'exploit', 'açığ', 'açık', 'zafiyet', 'vulnerab', 'cve-', 'phish',
        'kimlik avı', 'botnet', 'apt', 'tehdit aktör', 'threat actor',
        'ddos', 'backdoor', 'arka kapı', 'trojan', 'truva', 'infostealer',
        'bilgi çal', 'keylogger', 'rootkit', 'zero-day', 'zero day', 'sıfır gün',
        'şifrele', 'encrypt', 'c2', 'command and control', 'intrusion',
        'ele geçir', 'compromise', 'takedown', 'çökert', 'deface', 'espionage',
        'casusluk', 'hacktivist', 'data breach', 'veri ihlal', 'kötü amaçlı',
        'virüs', 'worm', 'solucan', 'wiper', 'apt2', 'apt3', 'apt4',
        'darkweb', 'dark web', 'lockbit', 'pegasus', 'nso ', 'predator',
    )

    def _has_cyber_signal(self, *texts):
        """Verilen metinlerde (TR ve/veya EN) bir siber boyut sinyali var mı?"""
        blob = ' '.join(t for t in texts if t).lower()
        if not blob:
            return False
        return any(sig in blob for sig in self._CYBER_SIGNALS)

    def _cyber_text_for(self, art_id, content_by_id, articles_by_id):
        """Bir haber için siber-sinyal taraması yapılacak birleşik metni döndürür."""
        c = content_by_id.get(art_id, {})
        a = articles_by_id.get(art_id, {})
        return ' '.join((
            c.get('tr_title', ''), c.get('paragraph', ''),
            a.get('title', ''), ' '.join((a.get('full_text', '') or '').split()[:120]),
        ))

    @staticmethod
    def _dedup_view(art_id, content_by_id, articles_by_id):
        """src.dedup.same_event için bir haber 'görünümü' (tr_title/paragraph/
        title/full_text) kurar. LLM içeriği + ham metni birleştirir."""
        c = content_by_id.get(art_id, {})
        a = articles_by_id.get(art_id, {})
        return {
            'tr_title':  c.get('tr_title', ''),
            'paragraph': c.get('paragraph', ''),
            'title':     a.get('title', ''),
            'full_text': a.get('full_text', ''),
        }

    def _dedup_view_fn(self, content_by_id, articles_by_id):
        """pick_distinct / drop_duplicates_against için id→view fonksiyonu üretir."""
        return lambda aid: self._dedup_view(aid, content_by_id, articles_by_id)

    def _log_dedup_nearmiss(self, selected_ids, view_fn, recent_views,
                            path="data/dedup_log.jsonl"):
        """Gözlemlenebilirlik: manşete GİREN bir haber, geçmiş bir olayla ORTAK
        parmak izi (aktör-ID/kod adı) taşıyıp da same_event AYNI OLAY demediyse
        (konu örtüşmesi eşik altı), bu 'yakın-kaçışı' data/dedup_log.jsonl'a
        yazar. Davranışı DEĞİŞTİRMEZ; ileride sessiz mükerrer kaçışları veriyle
        yakalamak içindir. Hata olursa sessiz geçer (kritik yol değil)."""
        if not recent_views:
            return
        try:
            today = _now_tr().strftime('%Y-%m-%d')
            rows = []
            for aid in selected_ids:
                va = view_fn(aid)
                for ev in recent_views:
                    sig = _dedup.nearmiss_signal(va, ev, cross_day=True)
                    if sig:
                        rows.append({
                            'date': today,
                            'kept_title': (va.get('tr_title') or va.get('title') or '')[:160],
                            'past_title': (ev.get('tr_title') or ev.get('title') or '')[:160],
                            'signal': sig,
                        })
            if not rows:
                return
            os.makedirs("data", exist_ok=True)
            with open(path, 'a', encoding='utf-8') as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False) + '\n')
            print(f"   🔎 Dedup yakın-kaçış: {len(rows)} kayıt {path}'e yazıldı "
                  f"(ortak parmak izi ama eşik altı — denetim için)")
        except Exception as e:
            print(f"   ⚠️  Dedup yakın-kaçış logu yazılamadı: {e}")

    def _load_recent_kritik3_views(self, days=KRITIK3_HISTORY_DAYS):
        """Son `days` günde KRİTİK 3'e (üst manşet) giren haberlerin zengin
        görünümlerini (tr_title/paragraph/title/full_text) okur.

        Çapraz-gün deterministik dedup için referans kümesidir: bugünkü top3
        adayları bu görünümlerle `same_event(cross_day=True)` üzerinden
        karşılaştırılır; eşleşen aday KRİTİK 3'e ALINMAZ (gövdede serbest kalır).

        Dosya yoksa/bozuksa boş liste döner (eski güvenli davranış)."""
        try:
            if not os.path.exists(KRITIK3_HISTORY_FILE):
                return []
            with open(KRITIK3_HISTORY_FILE, 'r', encoding='utf-8') as f:
                records = json.load(f)
        except Exception as e:
            print(f"⚠️  KRİTİK 3 geçmişi okunamadı: {e}")
            return []

        today  = _now_tr().strftime('%Y-%m-%d')
        cutoff = (_now_tr() - timedelta(days=days)).strftime('%Y-%m-%d')
        views = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            # Yalnızca GEÇMİŞ günlerle karşılaştır: eski (< cutoff) VEYA BUGÜN/gelecek
            # (>= today) kayıtları HARİÇ TUT. Bugün hariç tutulmazsa, aynı gün 2. kez
            # üretimde bugünün KRİTİK 3'ü KENDİ arşiv kaydıyla çakışıp elenir ve
            # backfill boşa düşer (rapor boşalma zincirinin bir halkası). Önceki
            # sürüm yalnızca eskiyi atıyor, bugünü tutuyordu (yorumla çelişik bug).
            d = rec.get('date', '')
            if d < cutoff or d >= today:
                continue
            for v in rec.get('views', []):
                if isinstance(v, dict) and (v.get('tr_title') or v.get('paragraph')):
                    views.append(v)
        return views

    def _save_kritik3_history(self, top3_ids, content_by_id, articles_by_id):
        """Bugünkü KRİTİK 3 haberlerinin zengin görünümünü parmak-izi deposuna
        ekler ve `days` penceresinden eski kayıtları budar. Çapraz-gün dedup'ın
        referansını besler. İçerik yoksa sessizce atlar."""
        if not top3_ids:
            return
        today = _now_tr().strftime('%Y-%m-%d')
        view_fn = self._dedup_view_fn(content_by_id, articles_by_id)
        views = []
        for aid in top3_ids:
            v = view_fn(aid)
            views.append({
                'tr_title':  v.get('tr_title', ''),
                'paragraph': (v.get('paragraph', '') or '')[:600],
                'title':     v.get('title', ''),
                'full_text': (v.get('full_text', '') or '')[:600],
            })

        records = []
        if os.path.exists(KRITIK3_HISTORY_FILE):
            try:
                with open(KRITIK3_HISTORY_FILE, 'r', encoding='utf-8') as f:
                    records = json.load(f)
                if not isinstance(records, list):
                    records = []
            except Exception:
                records = []

        # Aynı gün tekrar çalışırsa bugünün kaydını değiştir (mükerrer blok olmasın)
        records = [r for r in records if isinstance(r, dict) and r.get('date') != today]
        # Pencereden eski kayıtları buda
        cutoff = (_now_tr() - timedelta(days=KRITIK3_HISTORY_DAYS)).strftime('%Y-%m-%d')
        records = [r for r in records if r.get('date', '') >= cutoff]
        records.append({'date': today, 'views': views})

        os.makedirs("data", exist_ok=True)
        try:
            with open(KRITIK3_HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(records, f, ensure_ascii=False, indent=1)
            print(f"📌 KRİTİK 3 parmak-izi kaydedildi ({len(views)} haber, "
                  f"{KRITIK3_HISTORY_FILE})")
        except IOError as e:
            print(f"   ❌ KRİTİK 3 geçmişi yazılamadı: {e}")

    def _load_recent_report_views(self, days=REPORT_HISTORY_DAYS):
        """Son `days` günde RAPORA giren TÜM haberlerin (KRİTİK 3 + gövde)
        zengin görünümlerini (tr_title/paragraph/title/full_text) okur.

        Çapraz-gün rapor-geneli deterministik dedup için referans kümesidir:
        bugünkü gövde adayları bu görünümlerle `same_event(cross_day=True)`
        üzerinden karşılaştırılır; eşleşen aday rapora ALINMAZ. Böylece bir olay,
        KRİTİK 3'te olsun gövdede olsun, son `days` günde raporlanmışsa FARKLI
        ID/URL/sözcüklerle tekrar rapora giremez. Bugünün/gelecek kayıtları
        HARİÇ tutulur (aynı-gün 2. üretimde kendi kopyasıyla çakışmayı önler;
        _load_recent_kritik3_views ile aynı mantık). Dosya yoksa/bozuksa boş
        liste döner (eski güvenli davranış)."""
        try:
            if not os.path.exists(REPORT_HISTORY_FILE):
                return []
            with open(REPORT_HISTORY_FILE, 'r', encoding='utf-8') as f:
                records = json.load(f)
        except Exception as e:
            print(f"⚠️  Rapor geçmişi okunamadı: {e}")
            return []

        today  = _now_tr().strftime('%Y-%m-%d')
        cutoff = (_now_tr() - timedelta(days=days)).strftime('%Y-%m-%d')
        views = []
        for rec in records:
            if not isinstance(rec, dict):
                continue
            d = rec.get('date', '')
            if d < cutoff or d >= today:
                continue
            for v in rec.get('views', []):
                if isinstance(v, dict) and (v.get('tr_title') or v.get('paragraph')):
                    views.append(v)
        return views

    def _save_report_history(self, rendered_ids, content_by_id, articles_by_id):
        """Bugün RAPORA giren TÜM haberlerin (KRİTİK 3 + gövde) zengin görünümünü
        parmak-izi deposuna ekler ve REPORT_HISTORY_DAYS penceresinden eski
        kayıtları budar. Çapraz-gün rapor-geneli dedup'ın referansını besler.
        İçerik yoksa sessizce atlar."""
        if not rendered_ids:
            return
        today = _now_tr().strftime('%Y-%m-%d')
        view_fn = self._dedup_view_fn(content_by_id, articles_by_id)
        views, seen = [], set()
        for aid in rendered_ids:
            if aid in seen:
                continue
            seen.add(aid)
            v = view_fn(aid)
            views.append({
                'tr_title':  v.get('tr_title', ''),
                'paragraph': (v.get('paragraph', '') or '')[:500],
                'title':     v.get('title', ''),
                'full_text': (v.get('full_text', '') or '')[:400],
            })

        records = []
        if os.path.exists(REPORT_HISTORY_FILE):
            try:
                with open(REPORT_HISTORY_FILE, 'r', encoding='utf-8') as f:
                    records = json.load(f)
                if not isinstance(records, list):
                    records = []
            except Exception:
                records = []

        # Aynı gün tekrar çalışırsa bugünün kaydını değiştir (mükerrer blok olmasın)
        records = [r for r in records if isinstance(r, dict) and r.get('date') != today]
        # Pencereden eski kayıtları buda
        cutoff = (_now_tr() - timedelta(days=REPORT_HISTORY_DAYS)).strftime('%Y-%m-%d')
        records = [r for r in records if r.get('date', '') >= cutoff]
        records.append({'date': today, 'views': views})

        os.makedirs("data", exist_ok=True)
        try:
            with open(REPORT_HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(records, f, ensure_ascii=False, indent=1)
            print(f"📌 Rapor parmak-izi kaydedildi ({len(views)} haber, "
                  f"{REPORT_HISTORY_FILE})")
        except IOError as e:
            print(f"   ❌ Rapor geçmişi yazılamadı: {e}")

    def _dedup_body_cross_day(self, body_ids, view_fn, recent_views, label=''):
        """Gövde adaylarından, son REPORT_HISTORY_DAYS günde RAPORLANMIŞ bir
        olayla AYNI olanları (deterministik same_event cross_day) eler. Sıra
        korunur. recent_views boşsa aday listesi değişmeden döner.

        Yüksek-özgüllük modu (cross_day=True): yalnızca ortak kod adı / ortak
        aktör+konu / yüksek konu örtüşmesi eşleşir; jenerik TR başlık kalıpları
        yanlış-pozitif üretmez (bkz. src.dedup.same_event)."""
        if not recent_views or not body_ids:
            return list(body_ids)
        kept, dropped = [], []
        for aid in body_ids:
            view = view_fn(aid)
            if any(_dedup.same_event(view, ev, cross_day=True) for ev in recent_views):
                dropped.append(aid)
                continue
            kept.append(aid)
        if dropped:
            print(f"   📅 Çapraz-gün rapor dedup{(' (' + label + ')') if label else ''}: "
                  f"son {REPORT_HISTORY_DAYS} günde raporlanan olay(lar) elendi {dropped}")
        return kept

    def _dedup_body_cross_day_llm(self, body_ids, content_by_id, articles_by_id,
                                  recent_views, label=''):
        """Çapraz-gün SEMANTİK mükerrer denetimi (Auditor'ın çapraz-gün eşi).

        Deterministik _dedup_body_cross_day yalnızca yüksek-özgüllük sinyaliyle
        çalışır ve "aynı olay, FARKLI sözcükler" çapraz-gün kopyalarını kaçırır.
        Bu geçiş, ELİNDE KALAN gövde adaylarını (deterministik pas sonrası) son
        günlerin raporlanmış haberleriyle LLM üzerinden karşılaştırır; bugün
        TEKRAR anlatılan (aynı gelişme) adayları eler. Yalnızca GÖVDE adaylarına
        uygulanır — KRİTİK 3 buraya hiç gelmez (çağıran koruma sağlar).

        recent_views/body_ids boşsa veya LLM başarısızsa aday listesi DEĞİŞMEZ
        (güvenli degrade; deterministik katmanlar zaten çalışmıştır)."""
        if not recent_views or not body_ids:
            return list(body_ids)

        today_lines = []
        for aid in body_ids:
            c = content_by_id.get(aid, {})
            a = articles_by_id.get(aid, {})
            tr_title = c.get('tr_title') or a.get('title', '')
            snippet = ' '.join((c.get('paragraph', '') or '').split()[:80])
            today_lines.append(f"=== HABER ID: {aid} ===\n"
                               f"Başlık: {tr_title}\nÖzet: {snippet}\n")

        recent_lines = []
        for i, ev in enumerate(recent_views, 1):
            tr_title = ev.get('tr_title') or ev.get('title', '')
            snippet = ' '.join((ev.get('paragraph', '') or '').split()[:80])
            recent_lines.append(f"--- [R{i}] Başlık: {tr_title}\nÖzet: {snippet}\n")

        data = self._gemini_call_json(
            get_cross_day_dedup_prompt('\n'.join(today_lines), '\n'.join(recent_lines)),
            max_output_tokens=512, label=label or 'Auditor-ÇaprazGünMükerrer')
        drop = _dedup.parse_cross_day_dupes(data, body_ids)
        if drop:
            print(f"   📅 Çapraz-gün SEMANTİK dedup{(' (' + label + ')') if label else ''}: "
                  f"son {REPORT_HISTORY_DAYS} günde raporlanan olayın tekrarı elendi "
                  f"{sorted(drop)}")
        return [aid for aid in body_ids if aid not in drop]

    def _dedup_review_llm(self, candidate_ids, content_by_id, articles_by_id,
                          protected_ids=None, label=''):
        """Pass 5.5 — AUDITOR ajanı: ADANMIŞ LLM MÜKERRER DENETİMİ (tek işi mükerrer bulmak).

        candidate_ids'teki TÜM haberleri LLM'e verip aynı-olay gruplarını alır;
        her grupta EN ZENGİN (en uzun kaynak metni) haberi tutar, diğerlerini
        'kaldırılacak' döndürür. Deterministik same_event'in (bag-of-words)
        kaçırdığı 'aynı olay, farklı sözcükler' mükerrerlerini SEMANTİK yakalar
        — asıl güvence katmanı budur (Pass 5 KONTROL 4 gömülü/güvenilmezdi).

        protected_ids (ör. KRİTİK 3) ASLA kaldırılmaz; grupta korunan bir üye
        varsa o tutulur, mükerrer gövde haberleri kaldırılır. LLM boş/başarısız
        dönerse boş küme (güvenli: deterministik katmanlar zaten çalışır).

        Döndürür: kaldırılacak id kümesi."""
        prot = set(protected_ids or [])
        ids = [i for i in candidate_ids
               if i in content_by_id or i in articles_by_id]
        if len(ids) < 2:
            return set()
        lines = []
        for aid in ids:
            c = content_by_id.get(aid, {})
            a = articles_by_id.get(aid, {})
            tr_title = c.get('tr_title') or a.get('title', '')
            snippet = ' '.join((c.get('paragraph', '') or '').split()[:90])
            lines.append(f"=== HABER ID: {aid} ===\n"
                         f"Başlık: {tr_title}\nÖzet: {snippet}\n")
        data = self._gemini_call_json(
            get_dedup_review_prompt('\n'.join(lines)),
            max_output_tokens=512, label=label or 'Auditor-MükerrerDenetimi')
        if not data:
            return set()
        idset = set(ids)

        def _completeness(i):
            a = articles_by_id.get(i, {}); c = content_by_id.get(i, {})
            return (len((a.get('full_text', '') or '').split()),
                    len((c.get('paragraph', '') or '').split()))

        remove = set()
        for group in (data.get('groups', []) or []):
            g = []
            for x in group:
                try:
                    xi = int(x)
                except (TypeError, ValueError):
                    continue
                if xi in idset and xi not in g:
                    g.append(xi)
            if len(g) < 2:
                continue
            # Korunan (KRİTİK 3) üye varsa onu tut; yoksa en zengin haberi tut.
            anchor = next((i for i in g if i in prot), None)
            if anchor is None:
                anchor = max(g, key=_completeness)
            for i in g:
                if i != anchor and i not in prot:
                    remove.add(i)
        return remove

    # ── KESİK PARAGRAF DENETİMİ (Auditor'ın ikinci görevi) ────────────────
    # Cümle-sonu noktalamaları ve onları izleyebilen kapanış işaretleri
    # (tırnak/parantez). Bir paragraf bunlardan biriyle bitmiyorsa cümle
    # ortasında kesilmiş (yarım) kabul edilir.
    _SENTENCE_END_CHARS = '.!?…'
    _CLOSER_CHARS = '"\'»”’)]}'

    @classmethod
    def _paragraph_looks_truncated(cls, text):
        """Paragraf cümle ortasında kesilmişse True döner. TAM sayılan bitişler:
        cümle-sonu noktalaması (. ! ? …) VEYA kapanış tırnak/parantezi (ör.
        `...şöyle dedi: "büyük tehdit"` gibi meşru alıntı bitişleri yanlış
        pozitif olmasın diye). Harf/virgül/iki nokta gibi bir karakterle biten
        paragraf KESİK sayılır. Boş metin bu kontrolün konusu değildir (onu
        Pass 5 kısa-özet kontrolü ele alır) → False."""
        s = (text or '').rstrip()
        if not s:
            return False
        last = s[-1]
        return last not in cls._SENTENCE_END_CHARS and last not in cls._CLOSER_CHARS

    @classmethod
    def _trim_to_last_sentence(cls, text):
        """Kesik paragrafı SON TAM cümlenin sonuna kırpar. Metinde hiç
        cümle-sonu noktalaması yoksa (kırpılacak tam cümle yok) metni
        değiştirmeden döndürür."""
        s = (text or '').rstrip()
        idx = max((s.rfind(c) for c in cls._SENTENCE_END_CHARS), default=-1)
        if idx == -1:
            return text
        end = idx + 1
        while end < len(s) and s[end] in cls._CLOSER_CHARS:
            end += 1
        return s[:end]

    def _audit_truncated(self, rendered_ids, protected_ids,
                         content_by_id, articles_by_id):
        """AUDITOR — kesik paragraf denetimi. rendered_ids'teki her haberin
        paragrafı cümle ortasında kesilmiş mi bakar; kesikse:
          1) Kaynak metni varsa (full_text>80 kelime) içeriği YENİDEN üretir
             (_gemini_call_json MAX_TOKENS'ta bütçeyi katlar → token kesilmesi
             düzelir).
          2) Yeniden üretim DE kesikse paragrafı SON TAM cümleye kırpar.
        protected_ids (KRİTİK 3) ASLA silinmez — yalnızca kırpılır. Kırpma
        sonrası GÖVDE haberi <25 kelime kalırsa gövdeden düşürülür.
        content_by_id'i YERİNDE günceller; gövdeden düşürülecek id kümesini
        döndürür."""
        prot = set(protected_ids or [])
        truncated = [aid for aid in rendered_ids
                     if self._paragraph_looks_truncated(
                         content_by_id.get(aid, {}).get('paragraph', ''))]
        if not truncated:
            print("   ✅ Kesik paragraf yok.")
            return set()
        print(f"   ✂️  Kesik paragraf tespit edildi: {truncated}")

        # 1) Kaynağı zengin olanları yeniden üret (kesme token'dan olabilir).
        regen = [aid for aid in truncated
                 if len(articles_by_id.get(aid, {}).get('full_text', '').split()) > 80]
        for rid in regen:
            content_by_id.pop(rid, None)
        for i in range(0, len(regen), 5):
            self._process_batch_with_split(
                regen[i:i + 5], articles_by_id, content_by_id,
                label_prefix='Auditor-KesikYeniden')

        # 2) Hâlâ kesik olanları son tam cümleye kırp; gövde çok kısaysa düşür.
        drop = set()
        for aid in truncated:
            c = content_by_id.get(aid, {})
            para = c.get('paragraph', '') or ''
            if self._paragraph_looks_truncated(para):
                trimmed = self._trim_to_last_sentence(para)
                # Kırpma yalnızca ANLAMLI metin bırakıyorsa uygulanır. Aksi halde
                # (ör. paragrafın en başındaki tek noktaya kadar kırpılıp "."
                # gibi dejenere sonuç doğması) orijinal kesik-ama-dolu metin
                # KORUNUR — KRİTİK 3'te silinemeyen bir haberde "." göstermektense
                # bilgilendirici kesik metni bırakmak yeğdir.
                if trimmed and trimmed != para and len(trimmed.split()) >= 15:
                    c = dict(c)
                    c['paragraph'] = trimmed
                    content_by_id[aid] = c
                    para = trimmed
                    print(f"   ✂️  ID {aid} son tam cümleye kırpıldı "
                          f"({'KRİTİK 3 korunuyor' if aid in prot else 'gövde'}).")
            if aid not in prot and len(para.split()) < 25:
                drop.add(aid)
                print(f"   🗑️  ID {aid} kırpma sonrası <25 kelime → gövdeden düşürüldü.")
        return drop

    def _audit_register(self, rendered_ids, content_by_id):
        """AUDITOR — anlatım / resmi-dil denetimi (üçüncü görev).

        rendered_ids'teki her paragrafta laubali (konuşma dili) CÜMLE SONU basit
        geçmiş zaman ("oldu, yaptı, etti, gerçekleşti") DETERMİNİSTİK aranır
        (src.register). Bulunanlar LLM'e verilip RESMİ register'a ("-mIştIr /
        -mAktAdIr") yeniden yazdırılır; olgular birebir korunur. content_by_id
        YERİNDE güncellenir. Laubali kip bulunmazsa/LLM başarısızsa paragraf
        DEĞİŞMEZ (güvenli degrade). KRİTİK 3 dahil TÜM rapor haberlerine uygulanır
        (yalnızca metin düzeltir, haber silmez)."""
        flagged = []
        for aid in rendered_ids:
            para = (content_by_id.get(aid, {}) or {}).get('paragraph', '') or ''
            hits = _register.find_casual_past_words(para)
            if hits:
                flagged.append((aid, hits))
        if not flagged:
            print("   ✅ Anlatım resmi (laubali geçmiş zaman yok).")
            return
        print(f"   ✍️  Laubali anlatım tespit edildi, resmileştiriliyor: "
              f"{[(aid, h) for aid, h in flagged]}")

        # Bir seferde tüm işaretli paragrafları LLM'e ver (genelde 0-3 adet).
        lines = []
        for aid, _ in flagged:
            para = content_by_id.get(aid, {}).get('paragraph', '') or ''
            lines.append(f"=== HABER ID: {aid} ===\nParagraf: {para}\n")
        data = self._gemini_call_json(
            get_register_audit_prompt('\n'.join(lines)),
            max_output_tokens=4096, label='Auditor-Resmileştirme')
        if not data or not isinstance(data, dict):
            print("   ⚠️  Resmileştirme LLM yanıtı boş — paragraflar korunuyor.")
            return

        flagged_ids = {aid for aid, _ in flagged}
        applied = []
        for rw in (data.get('rewrites', []) or []):
            if not isinstance(rw, dict):
                continue
            try:
                aid = int(rw.get('id'))
            except (TypeError, ValueError):
                continue
            new_para = (rw.get('paragraph', '') or '').strip()
            if aid not in flagged_ids or not new_para:
                continue
            old_para = content_by_id.get(aid, {}).get('paragraph', '') or ''
            # Güvenlik: yeniden yazım anlamlı uzunlukta olmalı ve laubali kipi
            # gerçekten temizlemeli; aksi halde orijinali koru (fact-drift önlemi:
            # yeni metin eskisinin ~%55'inden kısaysa bilgi kaybı riski → reddet).
            if len(new_para.split()) < max(15, int(0.55 * len(old_para.split()))):
                print(f"   ⚠️  ID {aid} resmileştirme fazla kısaldı — orijinal korunuyor.")
                continue
            if _register.has_casual_past(new_para):
                print(f"   ⚠️  ID {aid} resmileştirme hâlâ laubali — orijinal korunuyor.")
                continue
            c = dict(content_by_id.get(aid, {}))
            c['paragraph'] = new_para
            content_by_id[aid] = c
            applied.append(aid)
        if applied:
            print(f"   ✅ Resmileştirildi: {applied}")

    def _verify_top3(self, top3_ids, non_vuln_ids, content_by_id,
                     articles_by_id, label):
        """Pass 4.5 — seçili KRİTİK 3'ü LLM ile DENETLER/teyit eder.

        Seçili 3 + havuz (seçilmemiş non_vuln adaylar) içerikleriyle LLM'e
        verilir; LLM (1) her seçili haberin siber boyutunu ve çerçeve-içerik
        tutarlılığını teyit eder, (2) havuzda daha kritik bir haber varsa takas
        önerir. Dönen top3 doğrulanır (id'ler havuz içinde, tam 3, tekrarsız).
        Geçersiz/başarısız/değişiklik yoksa MEVCUT seçim korunur.
        """
        if not top3_ids:
            return top3_ids
        selected_set = set(top3_ids)
        pool_ids = [aid for aid in non_vuln_ids if aid not in selected_set]
        # Havuz boşsa kıyaslayacak alternatif yok; yine de teyit için çağırmaya
        # değmez — takas imkânı olmadığından mevcut seçim aynen kalır.
        if not pool_ids:
            return top3_ids

        def _brief(ids, cap=160):
            lines = []
            for aid in ids:
                a = articles_by_id.get(aid, {})
                c = content_by_id.get(aid, {})
                orig_title = a.get('title', '')
                tr_title   = c.get('tr_title', '')
                full_text  = a.get('full_text', '') or c.get('paragraph', '')
                snippet    = ' '.join(full_text.split()[:cap])
                lines.append(
                    f"=== HABER ID: {aid} ===\n"
                    f"Başlık: {orig_title}\n"
                    + (f"TR Başlık: {tr_title}\n" if tr_title else "")
                    + f"İçerik: {snippet}\n"
                )
            return '\n'.join(lines)

        # Havuz çok büyükse promptu şişirmemek için ilk ~12 adayla sınırla
        # (sıralama zaten kritiklik önceliğine göre; en güçlü adaylar baştadır).
        pool_capped = pool_ids[:12]
        data = self._gemini_call_json(
            get_top3_verification_prompt(_brief(top3_ids), _brief(pool_capped)),
            max_output_tokens=512,
            label=f'{label}-Denetim',
        )
        if not data or 'top3' not in data:
            return top3_ids

        valid = set(top3_ids) | set(pool_capped)
        revised = []
        for x in data.get('top3', []):
            if str(x).strip().lstrip('-').isdigit():
                aid = int(x)
                if aid in valid and aid not in revised:
                    revised.append(aid)
        # Güvenlik: tam 3 ayrık geçerli id gelmediyse mevcut seçimi koru.
        if len(revised) != 3:
            return top3_ids

        if set(revised) != set(top3_ids):
            cikan = set(top3_ids) - set(revised)
            giren = set(revised) - set(top3_ids)
            print(f"   🔎 Top3 denetim: {sorted(cikan)} çıkarıldı, "
                  f"{sorted(giren)} eklendi → {revised}")
            for ch in data.get('degisiklikler', []) or []:
                neden = (ch.get('neden') or '').strip()
                if neden:
                    print(f"      • {ch.get('cikan')}→{ch.get('giren')}: {neden[:160]}")
        else:
            print("   🔎 Top3 denetim: seçim doğrulandı, değişiklik yok.")
        return revised

    def _select_top3(self, non_vuln_ids, content_by_id, articles_by_id,
                     pool_ids, label):
        """Pass 4 top3 seçimi — TEK kaynak (hem ana hem legacy yol kullanır).

        Adımlar: brief üret → LLM seçimi → siber-boyut guard → <3 ise tamamla.
        Boş top3 ASLA döndürülmez (aday varsa fallback ile doldurulur).
        Top3 kararı tamamen LLM + siber-sinyal guard'ındadır; hiçbir haber
        deterministik olarak öne sabitlenmez.
        """
        top3_ids = []
        if non_vuln_ids:
            brief_lines = []
            for aid in non_vuln_ids:
                a = articles_by_id.get(aid, {})
                c = content_by_id.get(aid, {})
                orig_title = a.get('title', '')
                tr_title   = c.get('tr_title', '')
                full_text  = a.get('full_text', '') or c.get('paragraph', '')
                snippet    = ' '.join(full_text.split()[:160])
                brief_lines.append(
                    f"=== HABER ID: {aid} ===\n"
                    f"Başlık: {orig_title}\n"
                    + (f"TR Başlık: {tr_title}\n" if tr_title else "")
                    + f"İçerik: {snippet}\n"
                )
            top3_data = self._gemini_call_json(
                get_top3_selection_prompt('\n'.join(brief_lines),
                                          recent_events=self._load_recent_events()),
                max_output_tokens=1024,  # thinking modelinde 256 reasoning'e takılıp boş dönebiliyor
                label=label,
            )
            if top3_data and 'top3' in top3_data:
                raw_top3 = [int(x) for x in top3_data['top3']
                            if str(x).strip().lstrip('-').isdigit()]
                non_vuln_set = set(non_vuln_ids)
                top3_ids = [aid for aid in raw_top3 if aid in non_vuln_set][:3]
        if not top3_ids and non_vuln_ids:
            top3_ids = non_vuln_ids[:3]

        # ── SİBER-BOYUT GUARD ────────────────────────────────────────────
        # LLM, siber boyutu olmayan bir haberi (ör. "demiryolu teknik arızası")
        # top3'e koymuş olabilir. Seçilmiş bir kartta siber sinyal YOKSA ve
        # havuzda siber sinyalLİ kullanılmamış bir aday VARSA, kartı onunla
        # değiştir. Asla top3'ü boşaltmaz — yalnızca daha iyi aday varsa takas eder.
        replacement_pool = [aid for aid in non_vuln_ids
                            if aid not in set(top3_ids)
                            and self._has_cyber_signal(
                                self._cyber_text_for(aid, content_by_id, articles_by_id))]
        guarded = []
        for aid in top3_ids:
            if self._has_cyber_signal(
                    self._cyber_text_for(aid, content_by_id, articles_by_id)):
                guarded.append(aid)
            elif replacement_pool:
                repl = replacement_pool.pop(0)
                guarded.append(repl)
                print(f"   🧹 Siber-boyut guard: ID {aid} (siber sinyal yok) → "
                      f"ID {repl} ile değiştirildi.")
            else:
                guarded.append(aid)  # daha iyi aday yok, koru
        top3_ids = guarded

        # ── PASS 4.5 — TOP3 DENETİM/TEYİT (LLM ikinci görüş) ─────────────
        # Seçili 3'ü, içerikleriyle birlikte havuza karşı bağımsız bir gözle
        # denetle: (1) her seçili haberin kriterlere uygunluğunu ve çerçeve-
        # içerik tutarlılığını TEYİT et, (2) havuzda daha kritik bir haber
        # varsa en zayıf seçili haberle takas et. Deterministik değil, LLM
        # kararı; başarısız/şüpheli olursa mevcut seçim korunur.
        top3_ids = self._verify_top3(top3_ids, non_vuln_ids,
                                     content_by_id, articles_by_id, label)

        # ── AYNI-OLAY DEDUP + 3'E TAMAMLAMA — KRİTİK 3 GARANTİSİ ──────────
        # Öncelik sırası: (1) LLM/guard'ın seçtiği top3, (2) kalan tüm non_vuln
        # adaylar (yedek). pick_distinct bu sıradan ÇİFTLER-ARASI AYNI-OLAY
        # OLMAYAN ilk 3'ü seçer; bir aday daha önce seçilenle aynı olayı
        # anlatıyorsa ATLANIR ve yerine sıradaki ayrık aday gelir. Böylece
        # KRİTİK 3 içinde mükerrer haber İMKÂNSIZDIR.
        ordered_pool = []
        for aid in list(top3_ids) + list(non_vuln_ids):
            if aid not in ordered_pool:
                ordered_pool.append(aid)

        view_fn = self._dedup_view_fn(content_by_id, articles_by_id)
        before = list(top3_ids)
        # ── ÇAPRAZ-GÜN KRİTİK 3 DEDUP ────────────────────────────────────
        # Son 7 günde raporlanmış bir olay bugün TEKRAR KRİTİK 3 manşeti
        # OLAMAZ. Deterministik, LLM'den bağımsız; same_event(cross_day=True)
        # yüksek-özgüllük sinyalleriyle. Referans = son 7 gün KRİTİK 3 (kritik3_
        # gecmis.json) ∪ son 7 gün TÜM rapor (rapor_gecmis.json); böylece
        # geçmişte GÖVDEDE çıkmış bir olay da bugün manşete taşınıp mükerrer
        # olmaz. (İki depo geçişte birbirini tamamlar: rapor geçmişi henüz
        # birikmemişken kritik3 geçmişi çalışmayı sürdürür.)
        recent_k3 = self._load_recent_kritik3_views() + self._load_recent_report_views()
        top3_ids = _dedup.pick_distinct(ordered_pool, view_fn, n=3,
                                        exclude_views=recent_k3)

        dropped = [aid for aid in before if aid not in top3_ids]
        if dropped:
            print(f"   🔁 Aynı-olay dedup: KRİTİK 3'ten mükerrer ID(ler) elendi "
                  f"{dropped}, ayrık adaylarla dolduruldu → {top3_ids}")
        if recent_k3:
            xday_dropped = [aid for aid in before
                            if aid not in top3_ids
                            and any(_dedup.same_event(view_fn(aid), ev, cross_day=True)
                                    for ev in recent_k3)]
            if xday_dropped:
                print(f"   📅 Çapraz-gün KRİTİK 3 dedup: son {REPORT_HISTORY_DAYS} "
                      f"günde raporlanan olay(lar) manşetten elendi {xday_dropped}")
            # Gözlem: manşete GİREN adaylardan biri geçmiş bir olayla ORTAK
            # parmak izi taşıyıp yine de elenmediyse (konu eşiği altı), sessiz
            # kaçışı logla — davranış değişmez, yalnızca denetim için.
            self._log_dedup_nearmiss(top3_ids[:3], view_fn, recent_k3)
        return top3_ids[:3]

    def _load_recent_events(self, days=REPORT_HISTORY_DAYS):
        """Son `days` günde raporlanan haber BAŞLIKLARINI arşivden okur ve
        skorlama + Kritik 3 promptlarına 'tekrar alma' listesi olarak verir.
        Bu, GÜNLER ARASI MÜKERRER haberleri engeller.

        Pencere 7 gün: rapor-geneli dedup penceresiyle (REPORT_HISTORY_DAYS ve
        deterministik same_event neti) HİZALI olması için 5'ten 7'ye çekildi;
        böylece 'hiçbir veri son 7 günde mükerrer olmasın' güvencesi LLM ve
        deterministik katmanda aynı pencereyi görür. NOT (denge): 7 günlük
        pencere, 'geçen hafta yama → bu hafta aktif istismar' gibi GELİŞEN
        haberleri LLM'e mükerrer gibi gösterebilir; bunlar gerçek mükerrer
        DEĞİL, gelişmedir. Prompt bu listeyi mutlak yasak değil güçlü sinyal
        olarak kullanmalı. Başlık (160) + kod adı (80) üst sınırları korunduğundan
        token belirgin şişmez.

        Not: Bu metot olmadan recent_events her zaman boş kalıyordu; arşiv
        yazılıyor ama hiç GERİ OKUNMUYORDU — dedup fiilen çalışmıyordu.

        Arşiv yoksa/okunamazsa boş string döner (eski güvenli davranış).
        """
        try:
            if not os.path.exists(ARCHIVE_FILE):
                return ''
            size = os.path.getsize(ARCHIVE_FILE)
            with open(ARCHIVE_FILE, 'r', encoding='utf-8', errors='replace') as f:
                # Yalnızca son ~600 KB yeterli (birkaç günlük blok); tam dosya gereksiz
                if size > 600_000:
                    f.seek(size - 600_000)
                text = f.read()
            # Gün blokları '📅 <tarih> - EN ÖNEMLİ ...' başlığıyla ayrılır.
            blocks = re.split(r'\n=+\n📅 ', text)
            # ── AYNI-GÜN KORUMASI (kritik) ──────────────────────────────────
            # Bugünün raporu arşive zaten yazılmış olabilir (aynı gün 2. kez
            # üretim; ör. zamanlanmış tekrar çalışma). O blok dedup referansına
            # GİRERSE bugünün haberleri KENDİ arşiv kopyasıyla 'mükerrer' sayılıp
            # toplu elenir ve rapor BOŞALIR (07-01'de yaşandı). Bu yüzden bugünün
            # tarihli blok(lar)ı referanstan çıkar — dedup yalnızca ÖNCEKİ günlere
            # karşı çalışır. (Arşiv başlığı: save_summary_to_archive ile aynı format.)
            today_hdr = _now_tr().strftime('%d %B %Y').upper()
            blocks = [b for b in blocks if not b.lstrip().startswith(today_hdr)]
            recent = blocks[-days:] if len(blocks) > days else blocks
            titles = []
            for blk in recent:
                # Arşiv başlık satırları:  "[ 1] Başlık", "[10] Başlık" ...
                for m in re.finditer(r'\n\[\s*\d+\]\s*(.+)', blk):
                    t = m.group(1).strip()
                    if t and t not in titles:
                        titles.append(t)
            if not titles:
                return ''
            titles = titles[:160]  # token şişmesini önle

            # ── DİL-BAĞIMSIZ ENTITY İNDEKSİ (diller-arası dedup güçlendirmesi) ──
            # Arşiv başlıkları TÜRKÇE üretilir; gelen haberler ise İNGİLİZCE. TR
            # başlık ↔ EN haber eşleşmesi zayıftır. Ancak kampanya/zararlı/aktör
            # KOD ADLARI ve CVE'ler dilden bağımsızdır (FortiBleed, StealC,
            # UNC6508, APT28, CL-STA-1062, CVE-2026-1234). Son günlerin
            # bloklarından yalnızca YÜKSEK ÖZGÜLLÜKTE kodları çıkarıp ayrı bir
            # liste olarak veriyoruz; jenerik ülke/sözcükler (Rusya, Ukrayna)
            # KASTEN dışarıda bırakılır (aşırı eleme yapmamak için).
            # Her bloğun İLK satırı '📅 <tarih> - EN ÖNEMLİ 43 HABER...' başlığıdır;
            # entity taramasına dahil edilirse JUNE/HABER gibi gürültü üretir →
            # blok başlığını at, yalnızca gövdeden entity çıkar.
            bodies = []
            for blk in recent:
                nl = blk.find('\n')
                bodies.append(blk[nl + 1:] if nl != -1 else '')
            recent_text = '\n'.join(bodies)
            entity_pats = (
                r'CVE-\d{4}-\d{4,7}',                 # CVE kimlikleri
                r'\b[A-Za-z]+[A-Z][A-Za-z0-9]{2,}\b',  # CamelCase / iç-büyükharf (FortiBleed, REDCap)
                r'\b[A-Z][a-z]+[A-Z]+\b',              # sonu-büyükharf kod adı (StealC, GootB)
                r'\b[A-Z]{2,}[A-Z0-9]*-?[0-9]{2,}\b',  # kod+rakam (UNC6508, APT28, CL-STA-1062, STORM-2697)
                r'\b[A-Z]{4,}\b',                      # tümü-büyük >=4 (STOCKSTAY)
            )
            # Tek başına ayırt edici olmayan / yapısal gürültü sözcükleri
            _stop = {
                'NATO', 'CISA', 'CERT', 'APT', 'ABD', 'FBI', 'HABER', 'HABERLER',
                'SEÇILMIŞ', 'ÖNEMLI', 'JANUARY', 'FEBRUARY', 'MARCH', 'APRIL',
                'JUNE', 'JULY', 'AUGUST', 'SEPTEMBER', 'OCTOBER', 'NOVEMBER',
                'DECEMBER', 'OCAK', 'ŞUBAT', 'MART', 'NISAN', 'MAYIS', 'HAZIRAN',
                'TEMMUZ', 'AĞUSTOS', 'EYLÜL', 'EKIM', 'KASIM', 'ARALIK',
            }
            entities = []
            for pat in entity_pats:
                for m in re.finditer(pat, recent_text):
                    e = m.group(0)
                    if e.upper() in _stop:
                        continue
                    if e not in entities:
                        entities.append(e)
            entities = entities[:80]

            out = '\n'.join(f'• {t}' for t in titles)
            if entities:
                out += (
                    '\n\n🔑 ANAHTAR İSİMLER/KODLAR (son günler — bunlardan biri '
                    'bugünkü bir haberde geçiyorsa MÜKERRER kabul et):\n'
                    + ', '.join(entities)
                )
            return out
        except Exception as e:
            print(f"⚠️  Recent events yüklenemedi: {e}")
            return ''

    def save_summary_to_archive(self, html_content):
        """Gemini'nin seçtiği EN ÖNEMLİ 43 HABERİ TXT arşivine EKLE (sürekli birikim)"""
        print("📚 En önemli 43 haber arşive ekleniyor...")
        now = _now_tr()

        soup = BeautifulSoup(html_content, 'html.parser')

        today_header = f"📅 {now.strftime('%d %B %Y').upper()} - EN ÖNEMLİ 43 HABER (SEÇİLMİŞ)"

        # Aynı gün ikinci koşuda (manuel tetikleme / retry) blok mükerrer
        # eklenmesin: bugünün başlığı arşivde zaten varsa atla.
        if os.path.exists(ARCHIVE_FILE):
            try:
                with open(ARCHIVE_FILE, encoding='utf-8') as f:
                    if today_header in f.read():
                        print("   ℹ️  Bugünün arşiv bloğu zaten mevcut — eklenmedi.")
                        self._check_archive_size()
                        return
            except IOError:
                pass

        archive_entry = f"\n{'=' * 80}\n{today_header}\n{'=' * 80}\n\n"

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
            print(f"📅 Tarih: {_now_tr().strftime('%d.%m.%Y %H:%M')}")
            print("")
            print("⚠️  Lütfen aşağıdaki adımlardan birini uygulayın:")
            print("   1. Dosyayı yedekleyip harici depolamaya taşıyın")
            print("   2. Eski kayıtları manuel olarak arşivleyin")
            print("")
            print("❌ Arşiv otomatik olarak SİLİNMEYECEKTİR.")
            print("=" * 70)
            print("")

    # ═══════════════════════════════════════════════════════════════
    # PUAN TABANLI DETERMİNİSTİK SEÇİM (Skorlayıcı + Critique ajanları)
    # ═══════════════════════════════════════════════════════════════
    @staticmethod
    def _clamp_score(val, hi):
        """Bir rubrik boyutunu 0..hi aralığına güvenle sıkıştırır."""
        try:
            v = int(round(float(val)))
        except (TypeError, ValueError):
            return 0
        return max(0, min(hi, v))

    def _normalize_record(self, raw):
        """LLM'den gelen ham skor nesnesini güvenli, tam bir kayda dönüştürür.
        Geçersiz kategori → 'siber_disi'; eksik alanlar → 0. Toplam KOD hesaplar.
        """
        kat = str(raw.get('kat', '')).strip().lower()
        if kat not in SCORING_CATEGORIES:
            kat = 'siber_disi'
        siber = 1 if str(raw.get('siber', 0)).strip() in ('1', 'true', 'True', 'evet') else 0
        mukerrer = 1 if str(raw.get('mukerrer', 0)).strip() in ('1', 'true', 'True', 'evet') else 0
        rec = {
            'kat':      kat,
            'siber':    siber,
            'mukerrer': mukerrer,
            's': self._clamp_score(raw.get('s'), SCORING_WEIGHTS['stratejik']),
            'e': self._clamp_score(raw.get('e'), SCORING_WEIGHTS['etki']),
            'a': self._clamp_score(raw.get('a'), SCORING_WEIGHTS['aciliyet']),
            'k': self._clamp_score(raw.get('k'), SCORING_WEIGHTS['kaynak_guven']),
        }
        rec['toplam'] = self._record_total(rec)
        return rec

    @staticmethod
    def _record_total(rec):
        """Rubrik toplamı (0-100). siber kapısı kapalıysa toplam 0 (gündem dışı)."""
        if not rec.get('siber'):
            return 0
        if rec.get('kat') in ('urun_icerik', 'siber_disi'):
            return 0
        return rec['s'] + rec['e'] + rec['a'] + rec['k']

    def _score_articles(self, articles, recent_events):
        """SKORLAMA ajanı — her habere kategori + siber kapısı + rubrik puanı.
        Büyük listelerde token taşmasını önlemek için 45'lik batch'lerle çağırır
        (aynı-olay dedup zaten topla() içinde yapıldığından batch bağımsız güvenli).
        Dönüş: {art_id: normalize edilmiş kayıt}. Skorlanamayan haber çağıran
        tarafta güvenli varsayılana düşürülür.
        """
        records = {}
        batch_size = 45
        batches = [articles[i:i + batch_size]
                   for i in range(0, len(articles), batch_size)]
        for b_idx, batch in enumerate(batches):
            brief_lines = []
            for a in batch:
                # Kategori doğruluğu bu mimarinin bel kemiği; ilk ~120 kelime
                # (250 karakter değil) sınıflamayı belirgin biçimde güçlendirir.
                snippet = ' '.join((a['full_text'] or '').split()[:120])
                brief_lines.append(
                    f"=== HABER ID: {a['id']} ===\n"
                    f"Kaynak: {a['source']}\n"
                    f"Başlık: {a['title']}\n"
                    f"Özet: {snippet}\n"
                )
            data = self._gemini_call_json(
                get_scoring_prompt('\n'.join(brief_lines), recent_events=recent_events),
                max_output_tokens=8000,
                label=f'Skorlama-B{b_idx + 1}/{len(batches)}',
            )
            rows = (data or {}).get('skorlar', []) if isinstance(data, dict) else []
            batch_ids = {a['id'] for a in batch}
            for row in rows:
                try:
                    rid = int(row.get('id'))
                except (TypeError, ValueError):
                    continue
                if rid in batch_ids:
                    records[rid] = self._normalize_record(row)
        return records

    def _critique_scores(self, records, articles_by_id, recent_events, top_k=20):
        """CRITIQUE ajanı (kıdemli siber güvenlik/strateji/politika uzmanı) —
        Skorlayıcıdan BAĞIMSIZ ikinci görüş. En yüksek puanlı ~top_k adayı (artı
        tüm zafiyet_aktif_apt etiketlileri) denetler; yanlış kategori/sahte siber
        boyut/şişirilmiş puan bulursa düzeltir. records'u YERİNDE günceller ve
        {değişen id: gerekçe} sözlüğünü döndürür (log'a düzeltme nedeni yazılır).
        """
        if not records:
            return {}
        # Denetim kapsamı: en yüksek toplam puanlı top_k + tüm zafiyet_aktif_apt
        ranked = sorted(records.keys(),
                        key=lambda aid: records[aid]['toplam'], reverse=True)
        scope = list(ranked[:top_k])
        for aid, rec in records.items():
            if rec['kat'] == 'zafiyet_aktif_apt' and aid not in scope:
                scope.append(aid)
        if not scope:
            return {}

        brief_lines = []
        for aid in scope:
            rec = records[aid]
            a = articles_by_id.get(aid, {})
            snippet = ' '.join((a.get('full_text', '') or a.get('title', '')).split()[:150])
            brief_lines.append(
                f"=== HABER ID: {aid} ===\n"
                f"Mevcut skor: kat={rec['kat']} siber={rec['siber']} toplam={rec['toplam']} "
                f"(s={rec['s']} e={rec['e']} a={rec['a']} k={rec['k']})\n"
                f"Başlık: {a.get('title', '')}\n"
                f"İçerik: {snippet}\n"
            )
        data = self._gemini_call_json(
            get_critique_prompt('\n'.join(brief_lines), recent_events=recent_events),
            max_output_tokens=4096,
            label='Critique-Denetim',
        )
        changed = {}
        if not data or 'duzeltmeler' not in data:
            print("   🧐 Critique: düzeltme dönmedi, skorlar korunuyor.")
            return changed
        for fix in data.get('duzeltmeler', []) or []:
            try:
                fid = int(fix.get('id'))
            except (TypeError, ValueError):
                continue
            if fid not in records:
                continue
            old = dict(records[fid])
            new_rec = self._normalize_record(fix)
            neden = str(fix.get('neden', '')).strip()[:140]
            # ── TUTARLILIK KORUMASI: siber_disi ⇒ siber=0 olmalı ────────────
            # 'siber_disi' tanımı gereği siber boyutu OLMAYAN haberdir; critique
            # prompt'u bu kategoriyi verirken siber=0 yapmayı ŞART koşar. Bir
            # haberi siber_disi'ye çekip siber=1 bırakmak iç çelişkidir ve
            # skorlayıcının net siber saydığı bir haberi haksız yere sıfırlar
            # (2026-07-02: FortiBleed & BEC phishing kiti tam da bu çelişkiyle
            # elenmişti). Çelişkili düzeltmeyi REDDET — skorlayıcı kararı korunur.
            if new_rec['kat'] == 'siber_disi' and new_rec['siber'] == 1:
                print(f"   🛡️  Critique reddedildi ID {fid}: siber_disi + siber=1 "
                      f"çelişkisi, skorlayıcı kararı korunuyor"
                      + (f"  | {neden}" if neden else ""))
                continue
            records[fid] = new_rec
            if records[fid] != old:
                changed[fid] = neden
                print(f"   🧐 Critique düzeltti ID {fid}: "
                      f"{old['kat']}→{records[fid]['kat']} "
                      f"toplam {old['toplam']}→{records[fid]['toplam']}"
                      + (f"  | {neden}" if neden else ""))
        if not changed:
            print("   🧐 Critique: denetim tamam, düzeltme gerekmedi.")
        return changed

    def _rank_by_score(self, articles, records):
        """DETERMİNİSTİK sıralama — düzeltilmiş skorlara göre kod tarafında sırala.
        Dönüş: (top10_ids, remaining_ids, filtered_ids, category_by_id).
        - Elenen: siber kapısı kapalı VEYA kategori urun_icerik/siber_disi (toplam 0).
        - Sıra: toplam DESC; eşitlik bozucu: kategori önceliği → kaynak güveni →
          aciliyet → id (tam deterministik).
        """
        category_by_id = {}
        source_pos = {a['id']: idx for idx, a in enumerate(articles)}  # kararlı id sırası

        # ── GÜVENLİK TABANI: mükerrer eleme raporu ASLA boşaltamaz ───────────
        # Siber kapısından geçen (mükerrer hariç değerlendirilen) haberleri say.
        # Bunların büyük kısmı 'mükerrer' diye elenirse bu ANORMALDİR — neredeyse
        # her zaman aynı-gün yeniden üretim artefaktıdır (bugünün haberleri kendi
        # arşiv kopyasıyla kıyaslanır). Böyle bir durumda mükerrer bayrağını TÜMDEN
        # yok say; deterministik sıralama mükerrersiz yapılır. 07-01'de rapor bu
        # yüzden boşalmıştı; bu taban o zinciri kesin olarak kırar.
        def _cyber_ok(rec):
            return (rec.get('siber') and rec.get('toplam', 0) > 0
                    and rec.get('kat') not in ('urun_icerik', 'siber_disi'))
        cyber_pool = [a['id'] for a in articles
                      if records.get(a['id']) and _cyber_ok(records[a['id']])]
        muk = sum(1 for aid in cyber_pool if records[aid].get('mukerrer'))
        apply_mukerrer = True
        if cyber_pool and muk and (muk / len(cyber_pool) > 0.5 or len(cyber_pool) - muk < 12):
            apply_mukerrer = False
            print(f"   🛟 Güvenlik tabanı: {muk}/{len(cyber_pool)} siber haber 'mükerrer' "
                  f"işaretli (kalacak {len(cyber_pool) - muk}) — anormal, mükerrer elemesi "
                  f"YOK SAYILIYOR (aynı-gün yeniden üretim artefaktı; rapor boşalmasın).")

        ranked, filtered_ids = [], []
        for a in articles:
            aid = a['id']
            rec = records.get(aid)
            if rec is None:
                # Skorlanamayan haber: güvenli varsayılan — düşük öncelikli ama elenmez.
                rec = {'kat': 'veri_ihlali', 'siber': 1, 'mukerrer': 0, 's': 0,
                       'e': 0, 'a': 0, 'k': 0, 'toplam': 1}
                records[aid] = rec
            category_by_id[aid] = rec['kat']
            is_muk = bool(rec.get('mukerrer')) and apply_mukerrer
            # Elenenler: siber kapısı kapalı / ürün-içerik-dışı / MÜKERRER (çapraz-gün)
            if (rec['toplam'] <= 0 or is_muk
                    or rec['kat'] in ('urun_icerik', 'siber_disi') or not rec['siber']):
                filtered_ids.append(aid)
            else:
                ranked.append(aid)

        # ── AZ-HABER KURTARMA: baraj düşür — İNCE/BOŞ GÖVDE YAYIMLANMASIN ──────
        # Hafta sonu gibi az-haber günlerinde katı önemlilik eşiği (toplam<=0)
        # ranked havuzunu 3 KRİTİK + anlamlı gövde için yetersiz bırakabiliyor.
        # Eski çözüm top3'ü boşaltıp raporu inceltiyordu (istenmeyen). Bunun
        # yerine standardı KONTROLLÜ düşür: SADECE 'önemsiz' (toplam<=0) diye
        # elenmiş ama (a) siber kapısı AÇIK, (b) off-topic/ürün DIŞI, (c)
        # çapraz-gün MÜKERRER OLMAYAN (daha önce yayımlanmamış) haberleri havuza
        # geri al. 'Yeni + gerçek siber' şartı KORUNUR — yalnızca önemlilik
        # eşiği iner; mükerrer/off-topic/non-siber ASLA kurtarılmaz. Böylece az
        # haber günlerinde de KRİTİK 3 tam gövdeli haberlerle dolar, gövde
        # inceltilmez.
        MIN_POOL = 6  # 3 KRİTİK + en az 3 gövde haberi
        if len(ranked) < MIN_POOL:
            ranked_set = set(ranked)
            rescue = []
            for aid in filtered_ids:
                rec = records[aid]
                is_muk = bool(rec.get('mukerrer')) and apply_mukerrer
                if (rec.get('siber') and not is_muk
                        and rec['kat'] not in ('urun_icerik', 'siber_disi')
                        and aid not in ranked_set):
                    rescue.append(aid)
            rescue.sort(key=lambda aid: (
                -records[aid]['toplam'],
                -KATEGORI_ONCELIK.get(records[aid]['kat'], 0),
                source_pos.get(aid, 1 << 30),
            ))
            promoted = []
            for aid in rescue:
                if len(ranked) >= MIN_POOL:
                    break
                ranked.append(aid)
                promoted.append(aid)
            if promoted:
                promoted_set = set(promoted)
                filtered_ids = [i for i in filtered_ids if i not in promoted_set]
                print(f"   🌥️  Az-haber kurtarma: {len(promoted)} düşük-puanlı ama "
                      f"YENİ+siber haber havuza alındı (baraj düşürüldü, gövde "
                      f"inceltilmiyor): {promoted}")

        ranked.sort(key=lambda aid: (
            -records[aid]['toplam'],
            -KATEGORI_ONCELIK.get(records[aid]['kat'], 0),
            -records[aid]['k'],
            -records[aid]['a'],
            source_pos.get(aid, 1 << 30),
        ))
        top10_ids = ranked[:10]
        remaining_ids = ranked[10:]
        return top10_ids, remaining_ids, filtered_ids, category_by_id

    def _derive_top3_by_score(self, ranked_ids, records, content_by_id,
                              articles_by_id):
        """KRİTİK 3 — deterministik, GARANTİLİ 3 haber.

        Öncelik sırası (kademeli, her kademe bir öncekini tamamlar):
          1) KRITIK3_HARIC_KATEGORILER dışı (gerçek manşet adayları), çapraz-gün
             + aynı-olay AYRIK ilk 3.
          2) Yetmezse aynı uygun havuzdan çapraz-gün kısıtı GEVŞETİLEREK tamamla.
          3) Hâlâ <3 ise KRİTİK KRİTERLERE EN YAKIN güvenlik açıkları (en yüksek
             puanlı zafiyet; ranked_ids puan sırasında) manşete alınır.
          4) SON ÇARE: hâlâ <3 ise tüm sıralı havuzdan doldur.
        Rapor ≥3 haber içerdiği sürece KRİTİK 3 ASLA 3'ten az kalmaz. Aynı-olay
        ayrıklığı her kademede korunur (3'e ulaşmak zorunluysa en son gevşer).
        """
        view_fn = self._dedup_view_fn(content_by_id, articles_by_id)
        recent_k3 = self._load_recent_kritik3_views()

        # ranked_ids zaten puan sırasında; en güçlü adaylar başta.
        eligible = [aid for aid in ranked_ids
                    if records.get(aid, {}).get('kat') not in KRITIK3_HARIC_KATEGORILER]

        # Kademe 1 — tercih edilen adaylar, çapraz-gün + aynı-olay ayrık
        top3_ids = _dedup.pick_distinct(eligible, view_fn, n=3,
                                        exclude_views=recent_k3)

        # Kademe 2 — çapraz-gün gevşet, uygun havuzdan aynı-olay ayrık tamamla
        if len(top3_ids) < 3:
            top3_ids = _dedup.pick_distinct(list(top3_ids) + eligible, view_fn, n=3)

        # Kademe 3 — kritik kriterlere en yakın güvenlik açıklarını manşete al
        if len(top3_ids) < 3:
            vuln_pool = [aid for aid in ranked_ids
                         if aid not in top3_ids
                         and records.get(aid, {}).get('kat') in ZAFIYET_KATEGORILERI]
            if vuln_pool:
                print(f"   🛡️→🎯 KRİTİK 3 eksik ({len(top3_ids)}/3) — kritik "
                      f"kriterlere en yakın güvenlik açık(lar)ı manşete alınıyor.")
                top3_ids = _dedup.pick_distinct(list(top3_ids) + vuln_pool, view_fn, n=3)

        # Kademe 4 — SON ÇARE: aynı-olay ayrıklığını da gevşet, 3'e tamamla
        if len(top3_ids) < 3:
            for aid in ranked_ids:
                if aid not in top3_ids:
                    top3_ids.append(aid)
                if len(top3_ids) >= 3:
                    break

        return top3_ids[:3]

    def _write_scoring_log(self, articles, records, top10_ids, remaining_ids,
                           top3_ids, critique_changed):
        """Kalibrasyon/denetim log'u — her haber için bir JSONL satırı yazar.
        Rubrik ağırlıklarını gerçek raporlarla ayarlamak için: kategori/puan/
        yerleşim + Critique düzeltti mi. İşlevsel değil; hata olursa sessiz geçer.
        """
        try:
            date_str = _now_tr().strftime('%Y-%m-%d')
            top10_set, top3_set = set(top10_ids), set(top3_ids)
            remaining_set = set(remaining_ids)
            articles_by_id = {a['id']: a for a in articles}
            lines = []
            for aid in sorted(records.keys()):
                rec = records[aid]
                a = articles_by_id.get(aid, {})
                if aid in top3_set:
                    yerlesim = 'kritik3'
                elif aid in top10_set:
                    yerlesim = 'top10'
                elif aid in remaining_set:
                    yerlesim = 'govde'
                else:
                    yerlesim = 'elenen'
                lines.append(json.dumps({
                    'tarih':    date_str,
                    'id':       aid,
                    'kaynak':   a.get('source', ''),
                    'baslik':   (a.get('title', '') or '')[:200],
                    'kat':      rec.get('kat'),
                    'siber':    rec.get('siber'),
                    'mukerrer': rec.get('mukerrer'),
                    's': rec.get('s'), 'e': rec.get('e'),
                    'a': rec.get('a'), 'k': rec.get('k'),
                    'toplam':   rec.get('toplam'),
                    'critique': 1 if aid in critique_changed else 0,
                    'critique_neden': (critique_changed.get(aid, '')
                                       if isinstance(critique_changed, dict) else ''),
                    'yerlesim': yerlesim,
                }, ensure_ascii=False))

            os.makedirs(os.path.dirname(SCORING_LOG_FILE) or '.', exist_ok=True)
            existing = []
            if os.path.exists(SCORING_LOG_FILE):
                with open(SCORING_LOG_FILE, 'r', encoding='utf-8') as f:
                    existing = f.read().splitlines()
            all_lines = existing + lines
            # Dosya şişmesin: yalnızca en yeni SCORING_LOG_MAX_LINES satırı tut.
            if len(all_lines) > SCORING_LOG_MAX_LINES:
                all_lines = all_lines[-SCORING_LOG_MAX_LINES:]
            with open(SCORING_LOG_FILE, 'w', encoding='utf-8') as f:
                f.write('\n'.join(all_lines) + '\n')
            print(f"   📊 Skorlama log'u yazıldı: {len(lines)} haber "
                  f"({SCORING_LOG_FILE})")
        except Exception as e:
            print(f"   ⚠️  Skorlama log'u yazılamadı (atlanıyor): {str(e)[:120]}")

    # ═══════════════════════════════════════════════════════════════
    # HTML OLUŞTURMA — DOĞRULAMA + TAMAMLAMA MEKANİZMALI (v2.1)
    # ═══════════════════════════════════════════════════════════════

    def create_html(self, txt_content):
        """
        3-PASS MİMARİSİ:
          Pass 1 → Sıralama   (tüm başlıklar + 200 karakter özet → JSON: top10 + remaining + filtered)
          Pass 2 → Derin      (top-10 TAM METİN → JSON: tr_title + 120+ kelime paragraf)
          Pass 3 → Batch      (kalan haberler TAM METİN, 20'lik gruplar → JSON: tr_title + 100+ kelime)
          Assembly → Kod tarafı HTML oluşturur (yapısal hata imkânsız)

        Kalite garantisi:
          - Her haber TAM METNİNDEN özetlenir (halüsinasyon yok)
          - Her paragraf min 100 kelime (top-10: 120+)
          - Batch başarısız → split-retry (20→10→5→1); tek haber bile başarısız olursa
            full_text'in ilk 150 kelimesi ham olarak yerleştirilir
        Fallback: Pass 1 tamamen başarısız → _create_html_legacy().
        """
        if not GEMINI_API_KEY and not is_openrouter_active():
            print("⚠️  LLM anahtarı yok (GEMINI_API_KEY / OPENROUTER_API_KEY) — Fallback HTML oluşturuluyor...")
            return self._create_fallback_html(
                txt_content, error_type="NoAPIKey",
                error_message="GEMINI_API_KEY mevcut değil"
            )

        now      = _now_tr()
        today_str = now.strftime('%d.%m.%Y')

        # ── Makaleleri ayrıştır ──────────────────────────────────────────
        articles = self._parse_articles_from_txt(txt_content)
        if not articles:
            print("⚠️  Makale ayrıştırılamadı — Fallback HTML oluşturuluyor...")
            return self._create_fallback_html(
                txt_content, error_type="ParseError",
                error_message="txt_content'ten makale çıkarılamadı"
            )
        print(f"📋 {len(articles)} makale ayrıştırıldı.")

        # ════════════════════════════════════════════════════════════════
        # PASS 1 — PUAN TABANLI DETERMİNİSTİK SIRALAMA
        #   1a) SKORLAMA ajanı → her habere kategori + siber kapısı + rubrik puanı
        #   1b) CRITIQUE ajanı → bağımsız denetim, yanlış kategori/puan düzeltme
        #   1c) KOD → düzeltilmiş puanlara göre DETERMİNİSTİK sırala
        # ════════════════════════════════════════════════════════════════
        recent_events = self._load_recent_events()
        print("\n🔢 Pass 1a — Skorlama (kategori + siber kapısı + rubrik puanı)...")
        score_records = self._score_articles(articles, recent_events)
        articles_by_id = {a['id']: a for a in articles}

        # Skorlama tamamen başarısızsa (tek haber bile puanlanmadıysa) eski yola düş.
        if not score_records:
            print("⚠️  Skorlama başarısız — eski tek-çağrı yöntemine dönülüyor...")
            return self._create_html_legacy(txt_content)

        print(f"   ✅ {len(score_records)}/{len(articles)} haber skorlandı.")
        print("\n🧐 Pass 1b — Critique (bağımsız uzman denetimi)...")
        critique_changed = self._critique_scores(
            score_records, articles_by_id, recent_events)

        print("\n📐 Pass 1c — Deterministik sıralama...")
        top10_ids, remaining_ids, filtered_list, category_by_id = \
            self._rank_by_score(articles, score_records)
        filtered_ids = set(filtered_list)

        print(f"   Top-10: {top10_ids}")
        print(f"   Kalan : {len(remaining_ids)} haber  |  Filtrelenen: {len(filtered_ids)} haber")

        content_by_id = {}

        # ════════════════════════════════════════════════════════════════
        # PASS 2 — TOP-10 DERİN ANALİZ
        # ════════════════════════════════════════════════════════════════
        print("\n🔍 Pass 2 — Top-10 derin analiz başlıyor...")
        articles_by_id = {a['id']: a for a in articles}
        full_lines = []
        for art_id in top10_ids:
            a = articles_by_id.get(art_id)
            if not a:
                continue
            full_lines.append(
                f"=== HABER ID: {art_id} ===\n"
                f"Kaynak: {a['source']}\n"
                f"Başlık: {a['title']}\n"
                f"Tarih: {a['art_date']}\n"
                f"Link: {a['link']}\n\n"
                f"TAM METİN:\n{_cap_fulltext(a['full_text'])}\n"
            )
        if full_lines:
            deep_data = self._gemini_call_json(
                get_deep_analysis_prompt('\n'.join(full_lines),
                                         today=now.strftime('%Y-%m-%d')),
                max_output_tokens=16000,
                label='Pass2-DerinAnaliz',
            )
            if deep_data:
                for k, v in _normalize_id_content(deep_data).items():
                    try:
                        content_by_id[int(k)] = v
                    except (ValueError, TypeError):
                        pass

        # Pass 2 tek çağrıyla yapılır; kısmi/başarısız yanıtta (ör. çıktı token
        # kesilmesi) üst sıradaki top-10 haberler içeriksiz kalır ve raporda HAM
        # İNGİLİZCE görünür. İçeriği gelmeyen top-10 haberleri Pass 3'ün
        # split-retry mantığıyla küçük gruplar hâlinde tamamla.
        missing_top10 = [aid for aid in top10_ids if aid not in content_by_id]
        if missing_top10:
            print(f"   ⚠️  Pass 2 — {len(missing_top10)} top-10 haber içeriksiz, "
                  f"split-retry ile tamamlanıyor: {missing_top10}")
            for i in range(0, len(missing_top10), 5):
                self._process_batch_with_split(
                    missing_top10[i:i + 5], articles_by_id, content_by_id,
                    label_prefix='P2-Tamamla',
                )

        # ════════════════════════════════════════════════════════════════
        # PASS 3 — KALAN HABERLER (20'LİK BATCH'LER, TAM METİN)
        # ════════════════════════════════════════════════════════════════
        batch_size = 20
        batches = [remaining_ids[i:i + batch_size]
                   for i in range(0, len(remaining_ids), batch_size)]
        print(f"\n📦 Pass 3 — {len(remaining_ids)} kalan haber, {len(batches)} batch "
              f"(her biri tam metin, split-retry'lı)...")

        for b_idx, batch in enumerate(batches):
            self._process_batch_with_split(
                batch, articles_by_id, content_by_id,
                label_prefix=f'P3-B{b_idx + 1}/{len(batches)}',
            )

        # ════════════════════════════════════════════════════════════════
        # PASS 4 — GÜNÜN EN KRİTİK 3 HABERİ (DETERMİNİSTİK, puana göre)
        #   Kritik 3 = en yüksek puanlı, Kritik-3'e uygun ilk 3 (kod kararı).
        #   Zafiyet_rutin/urun_icerik/siber_disi hariç; zafiyet_aktif_apt puanı
        #   yeterse girebilir. Aynı-olay ve çapraz-gün dedup KORUNUR.
        #   LLM'in doğrudan "seç" demesi YERİNE kod en yüksek 3 puanı alır.
        # ════════════════════════════════════════════════════════════════
        print("\n🎯 Pass 4 — Günün en kritik 3 haberi (puana göre deterministik)...")
        ranked_all = list(top10_ids) + list(remaining_ids)  # zaten puan sırasında
        top3_ids = self._derive_top3_by_score(
            ranked_all, score_records, content_by_id, articles_by_id,
        )

        print(f"   Seçilen Top 3 ID: {top3_ids}")

        # Kalibrasyon/denetim log'u — kategori/puan/yerleşim + Critique izi.
        self._write_scoring_log(articles, score_records, top10_ids,
                                remaining_ids, top3_ids, critique_changed)

        # ════════════════════════════════════════════════════════════════
        # PASS 5 — KALİTE KONTROL (boş/İngilizce/kriter dışı/kopya)
        # ════════════════════════════════════════════════════════════════
        print("\n🔎 Pass 5 — Kalite kontrol başlıyor...")
        qr_lines = []
        for art_id in list(top10_ids) + list(remaining_ids):
            c = content_by_id.get(art_id, {})
            a = articles_by_id.get(art_id, {})
            tr_title   = c.get('tr_title') or a.get('title', '')
            paragraph  = c.get('paragraph', '')
            # Pass 5 kararları (kısa/İngilizce/kriter-dışı/kopya) başlık + ilk ~70 kelimeyle
            # güvenle verilir; 120→70 kısaltma kalite kaybı olmadan girdi token'ını azaltır.
            snippet    = ' '.join(paragraph.split()[:70])
            has_source = 'evet' if len(a.get('full_text', '').split()) > 80 else 'hayır'
            qr_lines.append(
                f"=== HABER ID: {art_id} ===\n"
                f"TR Başlık: {tr_title}\n"
                f"Paragraf: {snippet}\n"
                f"Kaynak Var: {has_source}\n"
            )

        qr_data = None
        if qr_lines:
            qr_data = self._gemini_call_json(
                get_quality_review_prompt('\n'.join(qr_lines)),
                max_output_tokens=512,
                label='Pass5-KaliteKontrol',
            )

        p5_remove     = set()
        p5_regenerate = []
        if qr_data:
            raw_remove = {int(i) for i in qr_data.get('remove', [])
                          if str(i).strip().lstrip('-').isdigit()}
            p5_regenerate = [int(i) for i in qr_data.get('regenerate', [])
                             if str(i).strip().lstrip('-').isdigit()]
            # LLM "remove" demiş ama kaynak metni yeterince zenginse yeniden üret
            regen_set = set(p5_regenerate)
            for rid in raw_remove:
                a = articles_by_id.get(rid, {})
                if len(a.get('full_text', '').split()) > 80 and rid not in regen_set:
                    # Kaynak var → sadece içerik üretimi bozuk, kaldırma
                    c = content_by_id.get(rid, {})
                    paragraph = c.get('paragraph', '')
                    # Kısa/boş/İngilizce paragrafsa yeniden üret; kriter dışıysa kaldır
                    words = paragraph.split()
                    mostly_english = sum(1 for w in words[:30]
                                        if w.isascii() and w.isalpha()) > 15
                    if len(words) < 50 or mostly_english:
                        p5_regenerate.append(rid)
                        regen_set.add(rid)
                        continue
                p5_remove.add(rid)

        if p5_remove:
            print(f"   🗑️  Kaldırılan: {sorted(p5_remove)}")
            top10_ids     = [i for i in top10_ids     if i not in p5_remove]
            remaining_ids = [i for i in remaining_ids if i not in p5_remove]
            top3_ids      = [i for i in top3_ids      if i not in p5_remove]
            # Pass 5 top3'ten haber çıkardıysa 3'e tamamla. Tamamlama da AYNI-OLAY
            # dedup'tan geçer (pick_distinct) — KRİTİK 3 garantisi backfill'de de
            # korunur; mevcut top3 ile mükerrer aday asla eklenmez.
            if len(top3_ids) < 3:
                # Kritik-3'e uygun (kategori bazlı) kalan adaylardan, puan sırasında
                # tamamla. Deterministik derive metodu aynı-olay + çapraz-gün dedup'ı
                # zaten uygular; başa mevcut top3'ü koyup uygun havuzu ekliyoruz.
                eligible_backfill = [
                    aid for aid in (list(top10_ids) + list(remaining_ids))
                    if aid not in p5_remove
                    and score_records.get(aid, {}).get('kat') not in KRITIK3_HARIC_KATEGORILER
                ]
                ordered_pool = []
                for aid in list(top3_ids) + eligible_backfill:
                    if aid not in ordered_pool:
                        ordered_pool.append(aid)
                top3_ids = self._derive_top3_by_score(
                    ordered_pool, score_records, content_by_id, articles_by_id)
                if len(top3_ids) < 3:
                    print(f"   ⚠️  Pass 5 sonrası top3 {len(top3_ids)}'e düştü, "
                          f"tamamlanacak ayrık haber bulunamadı")

        if p5_regenerate:
            print(f"   🔄 Yeniden üretilen (İngilizce içerik): {p5_regenerate}")
            for rid in p5_regenerate:
                if rid in content_by_id:
                    del content_by_id[rid]
            for i in range(0, len(p5_regenerate), 5):
                self._process_batch_with_split(
                    p5_regenerate[i:i + 5], articles_by_id, content_by_id,
                    label_prefix='P5-Yeniden',
                )

        if not p5_remove and not p5_regenerate:
            print("   ✅ Tüm içerikler kalite kontrolünden geçti")

        # ── DETERMİNİSTİK İNGİLİZCE-İÇERİK SÜPÜRMESİ (LLM'den bağımsız ağ) ──
        # Kalite-kontrol LLM'i bir haberi flag'lemezse, içerik filtresine takılıp
        # _make_fallback_content'e düşen HAM İNGİLİZCE başlık/paragraf rapora
        # sızabiliyordu (ör. "jailbreak/spyware" içerikli haberler). Render
        # edilecek TÜM haberleri LLM'den bağımsız tarayıp İngilizce kalanları
        # önce yeniden üret, hâlâ İngilizce ise yansız çeviriyle Türkçeye çevir.
        rendered_now = list(top10_ids) + list(remaining_ids)
        # İçeriği hiç gelmemiş (boş/None) haberler de _safe_content üzerinden ham
        # İngilizce render edilir → onları da yeniden üretim kapsamına al.
        english_leftovers = [aid for aid in rendered_now
                             if not content_by_id.get(aid)
                             or self._content_is_english(content_by_id.get(aid, {}))]
        if english_leftovers:
            print(f"   🔁 Deterministik İngilizce süpürme: {english_leftovers}")
            for rid in english_leftovers:
                content_by_id.pop(rid, None)
            for i in range(0, len(english_leftovers), 3):
                self._process_batch_with_split(
                    english_leftovers[i:i + 3], articles_by_id, content_by_id,
                    label_prefix='EN-Süpür',
                )
            # Yeniden üretim de İngilizce/fallback verdiyse (kalıcı içerik
            # filtresi) yansız çeviri çağrısıyla en azından Türkçe başlık+paragraf al.
            for rid in english_leftovers:
                if self._content_is_english(content_by_id.get(rid, {})):
                    rescued = self._rescue_translate(articles_by_id.get(rid, {}))
                    if rescued:
                        merged = dict(content_by_id.get(rid, {}))
                        merged.pop('_fallback', None)
                        merged.update(rescued)
                        content_by_id[rid] = merged
                        print(f"   🌐 Çeviriyle kurtarıldı: ID={rid}")
                    else:
                        print(f"   ⚠️  ID={rid} Türkçeleştirilemedi (içerik filtresi olası).")

        # ── PASS 5.5 — AUDITOR (rapor bittikten sonra son bütünlük denetimi) ──
        # ÜÇ görevi var: (1) MÜKERRER — deterministik same_event bag-of-words'tür;
        # 'aynı olay, farklı sözcükler' durumunu (ortak kod adı/CVE/aktör yoksa)
        # kaçırabilir (03.07 Kouloglou vakası). LLM TÜM rapor haberlerini görüp
        # aynı-olay gruplarını bulur; KRİTİK 3 korunur, mükerrer gövde kaldırılır.
        # (2) KESİK PARAGRAF — cümle ortasında kesilmiş paragrafları bulur;
        # kaynağı varsa yeniden üretir, hâlâ kesikse son tam cümleye kırpar.
        # (3) ANLATIM/RESMİ-DİL — laubali (-DI) basit geçmiş zaman ("oldu/yaptı/
        # etti") içeren paragrafları resmi register'a ("-mIştIr") yeniden yazdırır.
        print("\n🔁 Pass 5.5 — Auditor: (a) mükerrer denetimi...")
        dup_remove = self._dedup_review_llm(
            list(top3_ids) + list(top10_ids) + list(remaining_ids),
            content_by_id, articles_by_id, protected_ids=top3_ids)
        if dup_remove:
            print(f"   🗑️  LLM mükerrer olarak kaldırdı: {sorted(dup_remove)}")
            top10_ids     = [i for i in top10_ids     if i not in dup_remove]
            remaining_ids = [i for i in remaining_ids if i not in dup_remove]
        else:
            print("   ✅ LLM ek mükerrer bulmadı")

        # (b) Kesik paragraf denetimi — KRİTİK 3 korunur (kırpılır, silinmez);
        # düzeltilemeyen ve çok kısalan gövde haberi gövdeden düşürülür.
        print("   🔎 Auditor: (b) kesik paragraf denetimi...")
        trunc_drop = self._audit_truncated(
            list(top3_ids) + list(top10_ids) + list(remaining_ids),
            top3_ids, content_by_id, articles_by_id)
        if trunc_drop:
            top10_ids     = [i for i in top10_ids     if i not in trunc_drop]
            remaining_ids = [i for i in remaining_ids if i not in trunc_drop]

        # (c) Anlatım / resmi-dil denetimi — laubali (-DI) basit geçmiş zaman
        # ("oldu/yaptı/etti") içeren paragrafları resmi register'a ("-mIştIr")
        # yeniden yazdırır. Rapordaki TÜM haberlere uygulanır; haber silmez.
        print("   🔎 Auditor: (c) anlatım / resmi-dil denetimi...")
        self._audit_register(
            list(top3_ids) + list(top10_ids) + list(remaining_ids),
            content_by_id)

        # ── RAPOR GENELİ AYNI-OLAY DEDUP (gövde ↔ KRİTİK 3 + gövde içi) ──
        # KRİTİK 3'e alınan bir olay, gövdede (Önemli Gelişmeler / paragraflar /
        # Yönetici Özeti tablosu) İKİNCİ KEZ görünmemeli; ayrıca gövde içindeki
        # iki haber aynı olayı anlatmamalı. _build_html zaten top3 ID'lerini
        # gövdeden çıkarır ama AYNI olayın FARKLI ID'li kopyasını yakalamaz —
        # bu yüzden deterministik same_event ile burada eleriz.
        if top3_ids:
            view_fn_body = self._dedup_view_fn(content_by_id, articles_by_id)
            kept_body = _dedup.drop_duplicates_against(
                list(top10_ids) + list(remaining_ids), list(top3_ids), view_fn_body)
            kept_set = set(kept_body) | set(top3_ids)
            dropped_body = [aid for aid in (list(top10_ids) + list(remaining_ids))
                            if aid not in kept_set]
            if dropped_body:
                print(f"   🔁 Gövde aynı-olay dedup: KRİTİK 3/gövde mükerreri elendi "
                      f"{dropped_body}")
                top10_ids     = [i for i in top10_ids     if i in kept_set]
                remaining_ids = [i for i in remaining_ids if i in kept_set]

        # ── ÇAPRAZ-GÜN RAPOR-GENELİ DEDUP (gövde ↔ son 7 gün raporu) ──────
        # Yukarıdaki blok yalnızca AYNI RUN içinde (gövde ↔ bugünkü KRİTİK 3 +
        # gövde içi) tekilleştirir. Burada gövde adaylarını SON 7 GÜNDE
        # raporlanmış TÜM haberlere (KRİTİK 3 + gövde) karşı deterministik
        # same_event(cross_day=True) ile eleriz: bir olay son 7 günde
        # raporlandıysa FARKLI ID/URL/sözcüklerle gövdede tekrar görünemez.
        recent_report = self._load_recent_report_views()
        if recent_report:
            view_fn_x = self._dedup_view_fn(content_by_id, articles_by_id)
            top10_ids     = self._dedup_body_cross_day(top10_ids,     view_fn_x, recent_report)
            remaining_ids = self._dedup_body_cross_day(remaining_ids, view_fn_x, recent_report)
            # ── LLM SEMANTİK ÇAPRAZ-GÜN DEDUP (opsiyonel, güçlendirilmiş) ──────
            # İlk sürüm (0ad9a9c, 07-09; 7 gün + gevşek prompt) YÜZEYSEL benzeyen
            # GERÇEKTEN YENİ haberleri eledi (07-11→07-13 daralması). 07-13'te
            # devre dışı bırakıldı; güçlendirilmiş sürüm HAZIR ama VARSAYILAN
            # KAPALI (config.ENABLE_LLM_CROSS_DAY_DEDUP). Açıkken bile artık DAHA
            # DAR pencere (CROSS_DAY_DEDUP_WINDOW_DAYS) + SIKI prompt kullanır;
            # meşru tekrarlar zaten deterministik pas + skorlama `mukerrer`
            # sinyaliyle yakalanıyor.
            if ENABLE_LLM_CROSS_DAY_DEDUP:
                recent_narrow = self._load_recent_report_views(
                    days=CROSS_DAY_DEDUP_WINDOW_DAYS) or recent_report
                top10_ids     = self._dedup_body_cross_day_llm(top10_ids,     content_by_id, articles_by_id, recent_narrow)
                remaining_ids = self._dedup_body_cross_day_llm(remaining_ids, content_by_id, articles_by_id, recent_narrow)

        # NOT: Eski "az-haber guard" KALDIRILDI. Önceden az haber günlerinde
        # top3 dışında gövde haberi kalmayınca KRİTİK 3 kutusu boşaltılıyordu;
        # bu, ince/boş gövdeli rapor üretiyordu (istenmeyen). Artık az haber
        # günleri KAYNAKTA çözülüyor: _rank_by_score'daki "az-haber kurtarma"
        # barajı kontrollü düşürüp YENİ+siber düşük-puanlı haberleri havuza
        # alıyor → hem KRİTİK 3 hem gövde tam gövdeli haberlerle doluyor.

        # ════════════════════════════════════════════════════════════════
        # PASS 6 — YÖNETİCİ ÖZETİ (en önemli 9 haberin tek paragraf özeti)
        # ════════════════════════════════════════════════════════════════
        # En önemli 9 haber = top3 + Önemli Gelişmeler kutusundaki 6 haber
        # (top10 içinden, vuln olmayan ve top3 dışındaki ilk 6).
        print("\n📝 Pass 6 — Yönetici Özeti oluşturuluyor...")
        top3_set_p6 = set(top3_ids)
        top10_regular_p6 = [aid for aid in top10_ids
                            if score_records.get(aid, {}).get('kat') not in ZAFIYET_KATEGORILERI
                            and aid not in top3_set_p6]
        exec_ids = list(top3_ids) + top10_regular_p6[:6]

        # Giriş cümlesi için: son 24 saatte analiz edilen toplam haber ve kaynak sayısı
        es_news_count   = len(articles)
        # Taranan toplam kaynak (feed) sayısı — rapor kapsamını doğru yansıtır.
        # Önceki değer "sağ kalan haberlerin ait olduğu farklı kaynak sayısı" idi;
        # az-haber günlerinde (ör. 6) okuyucuya yalnızca 6 kaynak izlenmiş gibi
        # yanıltıcı görünüyordu. Gerçekte ~34 kaynak taranıyor.
        es_source_count = len(self.sources)

        exec_summary = ''
        es_lines = []
        for art_id in exec_ids:
            c = content_by_id.get(art_id, {})
            a = articles_by_id.get(art_id, {})
            tr_title  = c.get('tr_title') or a.get('title', '')
            paragraph = c.get('paragraph', '')
            snippet   = ' '.join(paragraph.split()[:90])
            es_lines.append(
                f"=== HABER {art_id} ===\n"
                f"Başlık: {tr_title}\n"
                f"Özet: {snippet}\n"
            )
        if es_lines:
            # Yönetici Özeti raporun en görünür bloğu; tek bir geçici LLM
            # hatasında TAMAMEN kaybolmaması için 3 kez dene.
            for es_attempt in range(3):
                es_data = self._gemini_call_json(
                    get_executive_summary_prompt(
                        '\n'.join(es_lines), es_source_count, es_news_count,
                        today=now.strftime('%Y-%m-%d')),
                    max_output_tokens=4096,  # thinking modelinde 1024 kesiliyordu
                    label=f'Pass6-YoneticiOzeti(d{es_attempt + 1})',
                )
                if (es_data and isinstance(es_data.get('ozet'), str)
                        and es_data['ozet'].strip()):
                    exec_summary = es_data['ozet'].strip()
                    print(f"   ✅ Yönetici Özeti üretildi ({len(exec_summary.split())} kelime)")
                    break
                print(f"   ⚠️  Yönetici Özeti denemesi {es_attempt + 1}/3 başarısız.")

            # 3 deneme de başarısızsa: blok ASLA kaybolmasın — en önemli
            # haberlerin Türkçe başlıklarından deterministik bir özet kur.
            if not exec_summary:
                titles = []
                for art_id in exec_ids:
                    c = content_by_id.get(art_id, {})
                    t = (c.get('tr_title') or '').strip().rstrip('.')
                    if t and not self._is_mostly_english(t):
                        titles.append(t)
                if titles:
                    lead = ('Son 48 saatin siber güvenlik gündeminde öne çıkan '
                            'başlıca gelişmeler şunlardır: ')
                    exec_summary = lead + '; '.join(titles[:8]) + '.'
                    print("   ↩️  Yönetici Özeti deterministik yedekle dolduruldu.")
                else:
                    print("   ⚠️  Yönetici Özeti üretilemedi — kutu atlanıyor.")

        # ════════════════════════════════════════════════════════════════
        # ASSEMBLY — Kod tarafı HTML oluşturma
        # ════════════════════════════════════════════════════════════════
        print("\n🔨 HTML assembly başlıyor...")

        # ── GÖVDE ZENGİNLEŞTİRME: zafiyet-ağırlıklı günlerde "Önemli Gelişmeler"
        # boş kalmasın. Gövdeye düşen (top3 dışı, zafiyet olmayan) haber sayısı
        # eşiğin altındaysa, EN YÜKSEK PUANLI güvenlik açıklarını ana akışa
        # terfi ettir. Terfi edenler Güvenlik Açıkları bölümünden çıkar (çift-
        # render yok). Zafiyet-dışı haber bolsa terfi olmaz (promote_ids boş).
        BODY_MIN = 5
        _top3_set = set(top3_ids)
        _body_pool = [aid for aid in (list(top10_ids) + list(remaining_ids))
                      if aid not in _top3_set]
        _regular = [aid for aid in _body_pool
                    if score_records.get(aid, {}).get('kat') not in ZAFIYET_KATEGORILERI]
        promote_ids = set()
        if len(_regular) < BODY_MIN:
            _vulns = [aid for aid in _body_pool
                      if score_records.get(aid, {}).get('kat') in ZAFIYET_KATEGORILERI]
            promote_ids = set(_vulns[:BODY_MIN - len(_regular)])
            if promote_ids:
                print(f"   🛡️→📰 Gövde ince ({len(_regular)}<{BODY_MIN}) — en güçlü "
                      f"{len(promote_ids)} güvenlik açığı 'Önemli Gelişmeler'e çıkarıldı.")

        html = self._build_html(
            articles    = articles,
            top10_ids   = top10_ids,
            remaining_ids = remaining_ids,
            content_by_id = content_by_id,
            today_str   = today_str,
            top3_ids    = top3_ids,
            exec_summary = exec_summary,
            category_by_id = category_by_id,
            promote_ids = promote_ids,
        )
        _rapor_haber_sayisi = len(top10_ids) + len(remaining_ids)
        print(f"✅ HTML oluşturuldu ({len(html)} karakter, "
              f"{_rapor_haber_sayisi} haber)")

        # ── TABAN GÜVENLİK AĞI ──────────────────────────────────────────────
        # Rapor beklenenden AZ haber içeriyorsa görünür uyarı bas (Actions
        # logunda ve rss_errors.txt'de iz bırakır). Üretimi ENGELLEMEZ — sadece
        # "2 haber rezaleti" gibi sessiz daralmaları erken görünür kılar ki
        # havuz açlığı (feed/dedup/pencere) fark edilmeden geçmesin.
        # REPORT_FLOOR artık src/config.py'den gelir — idempotency kontrolü
        # (_rapor_basarili) ile AYNI eşiği kullanmak zorunda; iki yerde ayrı
        # sabit tutmak sessizce ayrışma riski taşırdı.
        if _rapor_haber_sayisi < REPORT_FLOOR:
            uyari = (f"⚠️  TABAN UYARISI: rapor yalnızca {_rapor_haber_sayisi} haber "
                     f"içeriyor (beklenen ≥{REPORT_FLOOR}). Havuz açlığı olası — "
                     f"feed/dedup/tarih-penceresi kontrol edilmeli.")
            print(uyari)
            try:
                self.rss_errors.append(
                    f"TABAN UYARISI - rapor {_rapor_haber_sayisi} haber (<{REPORT_FLOOR})")
                self._save_rss_errors()
            except Exception:
                pass

        # ── Post-processing ───────────────────────────────────────────
        self._translate_social_signals()
        html = self._inject_social_box(html)
        html = self._remove_commentary_sentences(html)
        html = self._sanitize_html(html)

        html_index   = self._inject_manual_add(self._add_archive_links(html, is_archive=False), today_str)
        html_archive = self._add_archive_links(html, is_archive=True)

        os.makedirs("docs/raporlar", exist_ok=True)
        with open("docs/index.html", 'w', encoding='utf-8') as f:
            f.write(html_index)
        with open(f"docs/raporlar/{now.strftime('%Y-%m-%d')}.html", 'w', encoding='utf-8') as f:
            f.write(html_archive)

        print("✅ docs/index.html")
        print(f"✅ docs/raporlar/{now.strftime('%Y-%m-%d')}.html")

        # Çapraz-gün dedup referansı: bugünkü KRİTİK 3'ü parmak-izi deposuna yaz.
        self._save_kritik3_history(top3_ids, content_by_id, articles_by_id)
        # Çapraz-gün rapor-geneli dedup referansı: bugün RAPORA giren TÜM haberleri
        # (KRİTİK 3 + gövde) parmak-izi deposuna yaz.
        self._save_report_history(
            list(top3_ids) + list(top10_ids) + list(remaining_ids),
            content_by_id, articles_by_id)
        self.save_summary_to_archive(html)
        self._cleanup_old_reports()
        return html

    @staticmethod
    def _make_fallback_content(article):
        """
        Gemini'nin tamamen başarısız olduğu tek bir haber için fallback içerik üretir.
        İngilizce başlık + full_text'in ilk ~150 kelimesi Türkçeye dönüştürülmeden
        ham olarak yerleştirilir; en azından boş paragraf çıkmaz.
        '_fallback' bayrağı, deterministik İngilizce-süpürmenin bu haberi
        yeniden denemesi için işaret bırakır.
        """
        title = article.get('title', f"Haber #{article.get('id', '?')}")
        words = article.get('full_text', '').split()
        paragraph = ' '.join(words[:150]) if words else title
        return {'tr_title': title, 'paragraph': paragraph, '_fallback': True}

    @staticmethod
    def _is_mostly_english(text):
        """
        Bir metnin ağırlıklı İngilizce (Türkçeleştirilmemiş ham içerik) olup
        olmadığını sezgisel saptar: metinde Türkçe'ye özgü karakter YOKSA ve
        ASCII-Latin sözcükler baskınsa İngilizce sayılır. 110+ kelimelik gerçek
        bir Türkçe paragrafta çğıöşü karakterleri kaçınılmaz olarak bulunur,
        bu yüzden yanlış-pozitif riski çok düşüktür.
        """
        if not text or not text.strip():
            return False
        sample = text.split()[:40]
        if not sample:
            return False
        has_turkish = any(ch in text for ch in 'çğıöşüÇĞİÖŞÜ')
        if has_turkish:
            return False
        ascii_alpha = sum(1 for w in sample if w.isascii() and w.isalpha())
        return ascii_alpha >= max(8, int(len(sample) * 0.6))

    @classmethod
    def _content_is_english(cls, content):
        """Render edilecek bir haberin paragrafı ham İngilizce mi? Çok kısa
        paragraflar güvenilir sinyal vermediğinden İngilizce sayılmaz."""
        if not content:
            return False
        if content.get('_fallback'):
            return True
        para = content.get('paragraph', '')
        if len(para.split()) < 20:
            return False
        return cls._is_mostly_english(para)

    def _rescue_translate(self, article):
        """
        İçerik filtresine takılıp ham İngilizce kalan bir haberi, YANSIZ bir
        çeviri çağrısıyla Türkçeye dönüştürür (son çare). Başarısızlıkta None.
        """
        if not article:
            return None
        title = article.get('title', '')
        body = ' '.join(article.get('full_text', '').split()[:220])
        if not body:
            return None
        data = self._gemini_call_json(
            get_title_rescue_prompt(title, body),
            max_output_tokens=1500,
            label='EN-Çeviri',
        )
        if not data:
            return None
        tr_title = (data.get('tr_title') or '').strip()
        paragraph = (data.get('paragraph') or '').strip()
        if not tr_title or self._is_mostly_english(tr_title):
            return None
        out = {'tr_title': tr_title}
        if paragraph and not self._is_mostly_english(paragraph):
            out['paragraph'] = paragraph
        return out

    def _format_batch_for_prompt(self, batch, articles_by_id):
        """Bir batch makaleyi get_summary_batch_prompt için tam metin formatına dönüştürür."""
        lines = []
        for art_id in batch:
            a = articles_by_id.get(art_id)
            if not a:
                continue
            lines.append(
                f"=== HABER ID: {art_id} ===\n"
                f"Kaynak: {a['source']}\n"
                f"Başlık: {a['title']}\n"
                f"Tarih: {a['art_date']}\n"
                f"Link: {a['link']}\n\n"
                f"TAM METİN:\n{_cap_fulltext(a['full_text'])}\n"
            )
        return '\n'.join(lines)

    def _process_batch_with_split(self, batch, articles_by_id, content_by_id,
                                   label_prefix='Batch', _depth=0):
        """
        Bir batch makaleyi işler; başarısız olursa ikiye bölerek yinelemeli tekrar dener.
        Derinlik sınırı: batch boyutu 1'e düşünce (tek haber) artık bölünmez,
        o haber için _make_fallback_content çağrılır.

        Akış: batch(20) → hata → batch(10)+batch(10) → hata → batch(5)×4 → ...
              → batch(1) → hata → fallback içerik
        """
        if not batch:
            return

        max_tokens = min(4096 + len(batch) * 200, 8000)

        data = self._gemini_call_json(
            get_summary_batch_prompt(self._format_batch_for_prompt(batch, articles_by_id),
                                     today=_now_tr().strftime('%Y-%m-%d')),
            max_output_tokens=max_tokens,
            label=f'{label_prefix}(n={len(batch)})',
        )

        # Yalnızca BU batch'e ait dönen ID'leri yerleştir (model yanlış/uydurma
        # ID döndürürse o, batch dışı sayılır ve görmezden gelinir).
        norm = _normalize_id_content(data) if data else {}
        batch_set = set(batch)
        applied = 0
        for k, v in norm.items():
            try:
                aid = int(k)
            except (ValueError, TypeError):
                continue
            if aid in batch_set and aid not in content_by_id:
                content_by_id[aid] = v
                applied += 1

        # Bu batch içinde hâlâ içeriği gelmeyen ID'ler.
        # KRİTİK: model batch'in yalnızca bir kısmını döndürürse (ör. çıktı token
        # kesilmesi → kısmi JSON), eksik kalanlar eskiden sessizce atlanıp HAM
        # İNGİLİZCE kalıyordu. Artık eksikleri yeniden işliyoruz.
        missing = [aid for aid in batch if aid not in content_by_id]
        if not missing:
            return

        # Tek haber kaldı ve onu da getiremedik → ham fallback (son çare).
        if len(missing) == 1:
            art_id = missing[0]
            a = articles_by_id.get(art_id)
            if a:
                content_by_id[art_id] = self._make_fallback_content(a)
                print(f"   ⚠️  [{label_prefix}] Tek haber fallback: ID={art_id}")
            return

        # Eksikleri ikiye bölerek yeniden dene. Kısmi yanıt (applied > 0) da
        # buraya düşer; böylece kuyrukta kalan haberler tekrar denenir.
        if applied:
            print(f"   ⚠️  [{label_prefix}] Kısmi yanıt: {applied} geldi, "
                  f"{len(missing)} eksik → eksikler yeniden bölünüyor.")
        else:
            print(f"   🔀 [{label_prefix}] Batch bölünüyor: "
                  f"{len(missing)} → {len(missing) // 2} + {len(missing) - len(missing) // 2}")
        mid = len(missing) // 2
        self._process_batch_with_split(
            missing[:mid], articles_by_id, content_by_id,
            label_prefix=f'{label_prefix}L', _depth=_depth + 1,
        )
        self._process_batch_with_split(
            missing[mid:], articles_by_id, content_by_id,
            label_prefix=f'{label_prefix}R', _depth=_depth + 1,
        )

    def _create_html_legacy(self, txt_content):
        """
        Legacy fallback — Pass 1 başarısız olduğunda çalışır.
        Tek Gemini çağrısıyla sıralama + Türkçe özetleri JSON olarak alır,
        ardından _build_html() ile yeni formatta rapor üretir.
        """
        print("🔄 Legacy tek-çağrı yöntemi çalışıyor...")
        if not GEMINI_API_KEY and not is_openrouter_active():
            return self._create_fallback_html(
                txt_content, error_type="NoAPIKey",
                error_message="LLM API anahtarı mevcut değil"
            )

        now       = _now_tr()
        today_str = now.strftime('%d.%m.%Y')

        articles = self._parse_articles_from_txt(txt_content)
        if not articles:
            return self._create_fallback_html(
                txt_content, error_type="ParseError",
                error_message="txt_content'ten makale çıkarılamadı"
            )
        print(f"   {len(articles)} makale ayrıştırıldı.")

        # Brief hazırla
        brief_lines = []
        for a in articles:
            snippet = ' '.join(a['full_text'].split()[:200]).replace('\n', ' ')
            brief_lines.append(
                f"=== HABER ID: {a['id']} ===\n"
                f"Kaynak: {a['source']}\n"
                f"Başlık: {a['title']}\n"
                f"İçerik: {snippet}\n"
            )
        articles_brief = '\n'.join(brief_lines)

        data = self._gemini_call_json(
            get_legacy_json_prompt(articles_brief),
            max_output_tokens=65536,
            label='Legacy-JSON',
        )

        if data is None:
            return self._create_fallback_html(
                txt_content, error_type="LegacyJSONFailed",
                error_message="Legacy JSON çağrısı başarısız"
            )

        # Veriyi çözümle — LLM "N/A"/"12a" gibi sayısal olmayan id döndürürse
        # int() ValueError fırlatıp tüm legacy yolu düşürüyordu; ana yoldaki
        # isdigit korumasının aynısı uygulanır.
        def _valid_ids(seq):
            return [int(i) for i in seq
                    if str(i).lstrip('-').isdigit() and int(i) in all_ids]

        all_ids       = {a['id'] for a in articles}
        top10_ids     = _valid_ids(data.get('top10', []))
        filtered_ids  = set(_valid_ids(data.get('filtered', [])))
        remaining_ids = [i for i in _valid_ids(data.get('remaining', []))
                         if i not in set(top10_ids) and i not in filtered_ids]
        # Sıralamada yer almayan ama filtrelenmemiş makaleleri sona ekle
        ranked_set = set(top10_ids) | set(remaining_ids) | filtered_ids
        for a in articles:
            if a['id'] not in ranked_set:
                remaining_ids.append(a['id'])

        content_by_id = {}
        for s in data.get('summaries', []):
            try:
                aid = int(s['id'])
                content_by_id[aid] = {
                    'tr_title':  s.get('tr_title', ''),
                    'paragraph': s.get('paragraph', ''),
                }
            except (KeyError, ValueError, TypeError):
                pass

        # Özeti olmayan haberlere ham metin ekle
        id_to_article = {a['id']: a for a in articles}
        for aid in top10_ids + remaining_ids:
            if aid not in content_by_id:
                a = id_to_article.get(aid, {})
                snippet = ' '.join(a.get('full_text', '').split()[:120])
                content_by_id[aid] = {
                    'tr_title':  a.get('title', ''),
                    'paragraph': snippet,
                }

        print(f"   📊 Doğrulama: Özet={len(content_by_id)}, "
              f"Top10={len(top10_ids)}, Kalan={len(remaining_ids)}")

        # Pass 4 — Top 3 seçimi (CVE dışı)
        CVE_RE = re.compile(r'CVE-\d{4}-\d{4,7}', re.IGNORECASE)
        VULN_KEYWORDS = [
            'güvenlik açığı', 'vulnerability', 'patch', 'yama', 'zero-day',
            'exploit', 'zafiyet', 'güncelleme', 'update', 'advisory',
        ]
        non_vuln_ids = []
        for a in articles:
            aid = a['id']
            if aid in filtered_ids or aid not in set(top10_ids + remaining_ids):
                continue
            title = a.get('title', '').lower()
            text  = a.get('full_text', '').lower()
            is_vuln = bool(CVE_RE.search(a.get('title', '') + a.get('full_text', '')))
            if not is_vuln:
                is_vuln = any(kw in title or kw in text for kw in VULN_KEYWORDS)
            if not is_vuln:
                non_vuln_ids.append(aid)

        # Ana yol ile ORTAK helper: brief + LLM + siber-boyut guard.
        top3_ids = self._select_top3(
            non_vuln_ids, content_by_id, id_to_article,
            list(set(top10_ids) | set(remaining_ids)),
            label='Legacy-Top3',
        )

        print(f"   Seçilen Top 3 ID: {top3_ids}")

        # Pass 5.5 — adanmış LLM mükerrer denetimi (ana yolla aynı; semantik güvence).
        dup_remove = self._dedup_review_llm(
            list(top3_ids) + list(top10_ids) + list(remaining_ids),
            content_by_id, id_to_article, protected_ids=top3_ids, label='Legacy-MükerrerDenetimi')
        if dup_remove:
            print(f"   🗑️  LLM mükerrer olarak kaldırdı (legacy): {sorted(dup_remove)}")
            top10_ids     = [i for i in top10_ids     if i not in dup_remove]
            remaining_ids = [i for i in remaining_ids if i not in dup_remove]

        # Rapor geneli aynı-olay dedup (gövde ↔ KRİTİK 3 + gövde içi) — ana yolla aynı.
        if top3_ids:
            view_fn_body = self._dedup_view_fn(content_by_id, id_to_article)
            kept_body = _dedup.drop_duplicates_against(
                list(top10_ids) + list(remaining_ids), list(top3_ids), view_fn_body)
            kept_set = set(kept_body) | set(top3_ids)
            dropped_body = [aid for aid in (list(top10_ids) + list(remaining_ids))
                            if aid not in kept_set]
            if dropped_body:
                print(f"   🔁 Gövde aynı-olay dedup (legacy): mükerrer elendi {dropped_body}")
                top10_ids     = [i for i in top10_ids     if i in kept_set]
                remaining_ids = [i for i in remaining_ids if i in kept_set]

        # Çapraz-gün rapor-geneli dedup (gövde ↔ son gün raporu) — ana yolla aynı.
        recent_report = self._load_recent_report_views()
        if recent_report:
            view_fn_x = self._dedup_view_fn(content_by_id, id_to_article)
            top10_ids     = self._dedup_body_cross_day(top10_ids,     view_fn_x, recent_report, label='legacy')
            remaining_ids = self._dedup_body_cross_day(remaining_ids, view_fn_x, recent_report, label='legacy')
            # LLM semantik çapraz-gün dedup — VARSAYILAN KAPALI (ana yolla aynı;
            # bkz. config.ENABLE_LLM_CROSS_DAY_DEDUP). Açıkken dar pencere kullanır.
            if ENABLE_LLM_CROSS_DAY_DEDUP:
                recent_narrow = self._load_recent_report_views(
                    days=CROSS_DAY_DEDUP_WINDOW_DAYS) or recent_report
                top10_ids     = self._dedup_body_cross_day_llm(top10_ids,     content_by_id, id_to_article, recent_narrow, label='legacy')
                remaining_ids = self._dedup_body_cross_day_llm(remaining_ids, content_by_id, id_to_article, recent_narrow, label='legacy')

        # Auditor (c) — anlatım / resmi-dil denetimi (ana yolla aynı).
        self._audit_register(
            list(top3_ids) + list(top10_ids) + list(remaining_ids), content_by_id)

        # HTML oluştur
        html = self._build_html(
            articles      = articles,
            top10_ids     = top10_ids,
            remaining_ids = remaining_ids,
            content_by_id = content_by_id,
            today_str     = today_str,
            top3_ids      = top3_ids,
        )

        self._translate_social_signals()
        html = self._inject_social_box(html)
        html = self._remove_commentary_sentences(html)
        html = self._sanitize_html(html)

        html_index   = self._inject_manual_add(self._add_archive_links(html, is_archive=False), today_str)
        html_archive = self._add_archive_links(html, is_archive=True)

        os.makedirs("docs/raporlar", exist_ok=True)
        with open("docs/index.html", 'w', encoding='utf-8') as f:
            f.write(html_index)
        with open(f"docs/raporlar/{now.strftime('%Y-%m-%d')}.html", 'w', encoding='utf-8') as f:
            f.write(html_archive)

        print("✅ docs/index.html (legacy)")
        print(f"✅ docs/raporlar/{now.strftime('%Y-%m-%d')}.html (legacy)")
        # Çapraz-gün dedup referansı: bugünkü KRİTİK 3'ü parmak-izi deposuna yaz.
        self._save_kritik3_history(top3_ids, content_by_id, id_to_article)
        # Çapraz-gün rapor-geneli dedup referansı: bugün RAPORA giren TÜM haberler.
        self._save_report_history(
            list(top3_ids) + list(top10_ids) + list(remaining_ids),
            content_by_id, id_to_article)
        self.save_summary_to_archive(html)
        self._cleanup_old_reports()
        return html

    def _translate_social_signals(self):
        """
        Sosyal sinyal başlıklarını Gemini ile resmi Türkçe tek cümleye çevirir.
        Sonuç her öğenin 'title_tr' alanına yazılır.
        Hata durumunda orijinal başlık kullanılmaya devam eder.
        """
        import re as _re
        if not self.social_data:
            return

        lines = [f"[S{i}]: {p.get('title', '')}"
                 for i, p in enumerate(self.social_data, 1)]
        prompt = (
            "Aşağıdaki sosyal medya paylaşım başlıklarını değerlendir.\n\n"
            "KARAR KURALI — Her başlık için şunu sor: Bu başlık Türkçeye çevrildiğinde "
            "ANLAMLI ve AKICI bir Türkçe cümle oluşuyor mu?\n"
            "  • EVET → Resmi Türkçeye çevir (-mıştır, -edilmiştir, -tespit edilmiştir "
            "gibi resmi fiil çekimleri zorunludur).\n"
            "  • HAYIR → Orijinal metni AYNEN geri yaz, hiç dokunma.\n\n"
            "Anlamlı çeviri YAPILAMAYAN durumlar (orijinali koru):\n"
            "  - Başlık yalnızca CVE numarası, versiyon, hash, komut satırı vb. içeriyorsa\n"
            "  - Başlıktaki kelimelerin büyük çoğunluğu teknik kısaltma / özel isimse\n"
            "  - Çevrildiğinde Türkçe cümle kurulamıyorsa\n"
            "  - Anlam bütünlüğü bozulacaksa\n\n"
            "KRİTİK: Teknik terimler, yazılım/şirket/ürün/protokol adları, CVE numaraları "
            "(Windows, Apache, Kubernetes, LockBit, Fortinet, RCE, SQL Injection vb.) "
            "çevrilmez — orijinal halleriyle cümle içinde bırakılır.\n\n"
            "[S1]'den [S" + str(len(self.social_data)) + "]'e kadar HER satır için "
            "mutlaka bir çıktı ver. Sadece sonuçları yaz, açıklama ekleme.\n"
            "Format:\n[S1]: <çeviri veya orijinal metin>\n[S2]: <çeviri veya orijinal metin>\n\n"
            + '\n'.join(lines)
        )
        if is_openrouter_active():
            text = _llm.generate_text(
                prompt, max_output_tokens=2048, temperature=0.2,
                label='sosyal-ceviri',
            ) or ''
            matched = 0
            for match in _re.finditer(r'\[S(\d+)\]:\s*(.+)', text):
                idx = int(match.group(1)) - 1
                if 0 <= idx < len(self.social_data):
                    self.social_data[idx]['title_tr'] = match.group(2).strip()
                    matched += 1
            print(f"   Sosyal sinyal Türkçe özetler (OpenRouter): "
                  f"{matched}/{len(self.social_data)}")
            return

        for model_name in ['gemini-2.5-pro', 'gemini-2.5-flash', 'gemini-2.0-flash']:
            try:
                client = genai.Client(api_key=GEMINI_API_KEY)
                chunks = []
                for chunk in client.models.generate_content_stream(
                    model=model_name,
                    contents=prompt,
                    config=genai_types.GenerateContentConfig(
                        # Çıktı sosyal sinyal SAYISIYLA ölçeklenir; thinking payı
                        # da eklenince 2048 sondaki satırları kesebiliyordu.
                        max_output_tokens=4096,
                        temperature=0.2,
                    ),
                ):
                    if chunk.text:
                        chunks.append(chunk.text)
                text = ''.join(chunks).strip()
                matched = 0
                for match in _re.finditer(r'\[S(\d+)\]:\s*(.+)', text):
                    idx = int(match.group(1)) - 1
                    if 0 <= idx < len(self.social_data):
                        self.social_data[idx]['title_tr'] = match.group(2).strip()
                        matched += 1
                print(f"   Sosyal sinyal Türkçe özetler ({model_name}): "
                      f"{matched}/{len(self.social_data)}")
                if matched > 0:
                    break   # Başarılı, ikinci modeli deneme
            except Exception as e:
                print(f"   Sosyal sinyal çeviri hatası [{model_name}]: {e}")
                continue

    def _inject_social_box(self, html):
        """
        Sosyal medya sinyalleri kutusunu HTML'e enjekte eder.
        Konum: normal haberlerden sonra, güvenlik açıklarından önce.
        self.social_data listesini kullanır — Gemini'den bağımsız, programatik.
        """

        if not getattr(self, 'social_data', None):
            return html

        platform_css = {
            'reddit':            'reddit-item',
            'hackernews':        'hn-item',
            'github_advisories': 'github-item',
            'mastodon':          'mastodon-item',
        }
        platform_labels = {
            'reddit':            'Reddit',
            'hackernews':        'HackerNews',
            'github_advisories': 'GitHub Advisory',
            'mastodon':          'Mastodon',
        }

        import html as _h
        items_html = ''
        for post in self.social_data:
            platform    = post.get('platform', '')
            source      = platform_labels.get(platform, post.get('source', ''))
            # title_tr varsa (Gemini çevirisi) onu kullan, yoksa orijinal başlık
            raw_title   = post.get('title_tr') or post.get('title', '')
            title       = raw_title.replace('<', '&lt;').replace('>', '&gt;')
            # link href attribute'una gömülüyor: tırnak/şema enjeksiyonuna karşı
            # yalnızca http/https kabul et, tırnak dahil escape et.
            link = str(post.get('link') or '#').strip()
            if not re.match(r'^https?://', link, re.IGNORECASE):
                link = '#'
            link = _h.escape(link, quote=True)
            score       = post.get('score', 0)
            comments    = post.get('comments', 0)
            item_cls    = platform_css.get(platform, '')

            if platform == 'github_advisories':
                _sev_tr    = {'critical': 'KRİTİK', 'high': 'YÜKSEK',
                              'medium': 'ORTA', 'low': 'DÜŞÜK'}
                sev_raw    = post.get('severity', '').lower()
                sev_label  = _sev_tr.get(sev_raw, sev_raw.upper())
                cvss       = post.get('cvss', 0)
                engagement = f"ÖNEM: {sev_label}"
                if cvss:
                    engagement += f"  |  CVSS: {cvss}"
            elif platform == 'mastodon':
                favs    = post.get('favourites', 0)
                reblogs = post.get('reblogs', 0)
                engagement = f"★{favs}"
                if reblogs:
                    engagement += f"  |  ↺{reblogs}"
            elif platform == 'reddit':
                ups = post.get('score', 0)
                num_c = post.get('comments', 0)
                engagement = f"▲{ups}"
                if num_c:
                    engagement += f"  |  {num_c} yorum"
            else:
                # hackernews
                engagement = f"{score} puan"
                if comments > 0:
                    engagement += f"  |  {comments} yorum"

            items_html += (
                f'<div class="signal-item {item_cls}">'
                f'  <div class="signal-meta">'
                f'    <span class="signal-platform-label">{source}</span>'
                f'    <span class="signal-engagement">{engagement}</span>'
                f'  </div>'
                f'  <a href="{link}" target="_blank" rel="noopener noreferrer">{title}</a>'
                f'</div>\n'
            )

        box_html = (
            f'<div class="social-signals">'
            f'<h2>Sosyal Medya Sinyalleri</h2>'
            f'<div class="signal-list">{items_html}</div>'
            f'</div>'
        )

        # Konum: normal haberlerden sonra, güvenlik açıklarından önce
        if '<!-- SOCIAL_SIGNALS_HERE -->' in html:
            return html.replace('<!-- SOCIAL_SIGNALS_HERE -->', box_html, 1)

        # Fallback: BeautifulSoup ile executive-table'dan sonra ekle
        from bs4 import BeautifulSoup as _BS
        soup = _BS(html, 'html.parser')
        exec_table   = soup.find('table', class_='executive-table')
        news_section = soup.find('div', class_='news-section')
        if exec_table:
            exec_table.insert_after(_BS(box_html, 'html.parser'))
        elif news_section:
            news_section.insert_before(_BS(box_html, 'html.parser'))
        else:
            body = soup.find('body')
            if body:
                body.append(_BS(box_html, 'html.parser'))
        return str(soup)

    # Kritik CSS sınıfları — bunlar eksikse sayfa düzgün görünmez
    def _remove_commentary_sentences(self, html):
        """Gemini'nin paragrafların sonuna eklediği UYDURMA değerlendirme/yorum
        cümlelerini sil. Kaynak metinde olmayan "bu ne anlama geliyor / ne kadar
        önemli / neyi teyit ediyor" türü editoryal kapanış cümleleri kaldırılır.

        İki katmanlı temizlik:
          1) Öznesi ne olursa olsun (Bu olay…, Söz konusu…, Hükümetin bu…) bir
             değerlendirme FİİLİYLE biten cümleler — yani editoryal kapanışlar.
          2) Eski "Bu X … fiil" ve "… bir kez daha … fiil" kalıpları.
        Haber, top3 kart ve yönetici özeti paragraflarının HEPSİNDE çalışır.
        """
        import re

        # Editoryal/değerlendirme fiilleri — biçimsel haber dilinde neredeyse
        # yalnızca "yorum kapanışı" olarak kullanılırlar. Olgusal cümleler bunun
        # yerine açıklamıştır/bildirilmiştir/duyurmuştur/belirtilmiştir kullanır.
        # Belirsiz olabilen fiillerde (teyit/yansıt/vurgula) YALNIZCA şimdiki
        # zaman "-maktadır/-mektedir" biçimi alınır; bu biçim editoryal kapanışın
        # imzasıdır. Geçmiş "-miştir" biçimi çoğu kez olgusaldır (ör. "şirket
        # ihlali teyit etmiştir") ve KORUNUR.
        EVAL_VERBS = (
            r'(?:göstermektedir|göstermiştir|ortaya koymaktadır|ortaya koymuştur|'
            r'gözler önüne sermektedir|gözler önüne sermiştir|darbe vurmuştur|'
            r'vurgulamaktadır|kanıtlamaktadır|teyit etmektedir|yansıtmaktadır|'
            r'anlamına gelmektedir|işaret etmektedir|teşkil etmektedir|'
            r'önem arz etmektedir|önem taşımaktadır|dikkat çekmektedir|'
            r'açıkça ortaya çıkmaktadır|farkındalık yaratmaktadır)'
        )

        # Yalnızca paragrafın SON cümlesini hedefler: son cümle bir değerlendirme
        # fiiliyle bitiyorsa (özne ne olursa olsun) o cümle editoryal kapanıştır
        # ve silinir. Paragraf ortasındaki olgusal cümlelere DOKUNULMAZ — böylece
        # haberin bilgileri korunur, yalnızca uydurma kapanış yorumu kalkar.
        LAST_EVAL_RE = re.compile(
            r'[^.!?<]*' + EVAL_VERBS + r'\s*[.!?]?\s*$',
            re.IGNORECASE
        )

        def _strip_commentary(text):
            text = text.strip()
            # Arka arkaya birden fazla editoryal kapanış olabilir (ör. iki
            # değerlendirme cümlesi); son cümle olgusal olana dek soy.
            while True:
                m = LAST_EVAL_RE.search(text)
                # m.start() == 0 → paragrafın tamamı tek editoryal cümle;
                # boşaltma, en az bir cümle kalsın.
                if not m or m.start() == 0:
                    break
                candidate = text[:m.start()].rstrip()
                if not candidate:
                    break
                text = candidate
            return re.sub(r'\s{2,}', ' ', text).strip()

        cleaned_count = 0

        def make_processor(cls):
            def process_paragraph(m):
                nonlocal cleaned_count
                content = m.group(1)
                cleaned = _strip_commentary(content)
                if cleaned != content.strip():
                    cleaned_count += 1
                return f'<p class="{cls}">{cleaned}</p>'
            return process_paragraph

        fixed = html
        for cls in ('news-content', 'top3-card-paragraph', 'exec-brief-paragraph'):
            fixed = re.sub(
                r'<p class="' + cls + r'">(.*?)</p>',
                make_processor(cls),
                fixed,
                flags=re.DOTALL
            )

        if cleaned_count > 0:
            print(f"   ✅ {cleaned_count} paragraftan uydurma yorum cümlesi temizlendi")
        return fixed

    def _add_archive_links(self, html, is_archive=False):
        """HTML'e son 30 günün linklerini ekle"""

        reports = []
        for i in range(30):
            date = _now_tr() - timedelta(days=i)
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

    def _create_fallback_html(self, txt_content, error_type=None, error_message=None):
        """Gemini API başarısız olursa — yeni format layout'u, ham İngilizce içerikle"""
        now       = _now_tr()
        today_str = now.strftime('%d.%m.%Y')

        articles = self._parse_articles_from_txt(txt_content) if txt_content else []

        if articles:
            # Ham içerikle content_by_id oluştur — AI özeti yok, orijinal metin
            content_by_id = {}
            for a in articles:
                snippet = ' '.join(a.get('full_text', '').split()[:110])
                content_by_id[a['id']] = {
                    'tr_title':  a.get('title', ''),
                    'paragraph': snippet,
                }
            # Sıralama yok — olduğu gibi kullan
            top10_ids     = [a['id'] for a in articles[:10]]
            remaining_ids = [a['id'] for a in articles[10:]]
            top3_ids      = []

            html = self._build_html(
                articles      = articles,
                top10_ids     = top10_ids,
                remaining_ids = remaining_ids,
                content_by_id = content_by_id,
                today_str     = today_str,
                top3_ids      = top3_ids,
            )
            # Hata uyarı bandı ekle
            if error_type or error_message:
                import html as _html_escape
                safe_msg = _html_escape.escape(str(error_message or error_type or ''))[:200]
                warning_bar = (
                    f'<div style="background:#fff3cd;border-bottom:2px solid #ffc107;'
                    f'padding:10px 20px;font-size:13px;color:#856404;">'
                    f'⚠️ Gemini API yanıt vermedi — içerik çevrilmedi/özetlenmedi. '
                    f'<code style="font-size:12px">{safe_msg}</code></div>'
                )
                html = html.replace('<body>', '<body>' + warning_bar, 1)
            html = html.replace('[FALLBACK]', '')
        else:
            # Makale ayrıştırılamadıysa minimal sayfa
            html = (
                '<!DOCTYPE html><html lang="tr"><head><meta charset="UTF-8">'
                f'<title>Siber Güvenlik Raporu [FALLBACK]</title></head><body>'
                f'<h2>⚠️ Gemini API yanıt vermedi</h2>'
                f'<pre style="font-size:13px">'
                f'{_html_mod.escape(txt_content[:3000]) if txt_content else ""}</pre>'
                '</body></html>'
            )

        # YAPISAL fallback işareti: idempotency kontrolü (_rapor_basarili) daha
        # önce 'Gemini API yanıt vermedi' alt-dizesine bakıyordu — Gemini
        # hakkındaki meşru bir haber bu cümleyi içerirse İYİ rapor "başarısız"
        # sayılıp üzerine yazılabilirdi. Fallback sayfayı yalnızca bu yorum
        # işaretiyle kesin olarak damgalıyoruz.
        html = html.replace('</body>', '<!-- RAPOR_DURUM: FALLBACK -->\n</body>', 1)

        os.makedirs("docs/raporlar", exist_ok=True)
        with open("docs/index.html", 'w', encoding='utf-8') as f:
            f.write(html)
        with open(f"docs/raporlar/{now.strftime('%Y-%m-%d')}.html", 'w', encoding='utf-8') as f:
            f.write(html)

        print("✅ Fallback HTML oluşturuldu (yeni format, ham içerik)")
        return html

    def _cleanup_old_reports(self):
        """30 günden eski raporları sil"""
        import glob

        cutoff = _now_tr() - timedelta(days=30)
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
            except Exception:
                pass

        if deleted > 0:
            print(f"🗑️  {deleted} eski rapor silindi (30+ gün)")
        else:
            print("📁 Arşiv temiz (30 gün içinde)")


def _reset_today_state():
    """Bugünün durum dosyalarını CLAUDE.md 'Taze Rapor İçin Reset' prosedürüne
    göre CERRAHİ olarak sıfırlar; eski günlere / append-only geçmişe DOKUNMAZ.

    Tetikleme: workflow_dispatch input `reset_today=true` → env RESET_TODAY.
    Amaç: idempotency (Kontrol 1 rapor + Kontrol 2 ham) aynı gün taze üretimi
    atlarken, tek tıkla raporu SIFIRDAN ürettirmek. GitHub Actions 'Run workflow'
    kimlik doğrulamayı kendisi yapar — tarayıcıya token gömülmez.

    SİL (taze üretimi engelleyen durum): bugünün raporu, ham, cron başarı işareti.
    CERRAHİ (sadece bugünü çıkar): linkler'de bugünün satırları, arşivde bugünün
    bloğu. DOKUNMA: kritik3/rapor geçmişi (json), skorlama_log, rss_errors, index.
    """
    now = _now_tr()
    today_str = now.strftime('%Y-%m-%d')
    today_header = f"📅 {now.strftime('%d %B %Y').upper()} - EN ÖNEMLİ 43 HABER (SEÇİLMİŞ)"
    print("🧹 RESET_TODAY: bugünün durumu sıfırlanıyor (taze üretim zorlanıyor)...")

    # 1) SİL — taze üretimi engelleyen durum dosyaları
    for path in (f"docs/raporlar/{today_str}.html", "data/haberler_ham.txt",
                 "data/cron_basarili.txt"):
        try:
            if os.path.exists(path):
                os.remove(path)
                print(f"   🗑️  silindi: {path}")
        except OSError as e:
            print(f"   ⚠️  silinemedi {path}: {e}")

    # 2) CERRAHİ — haberler_linkler.txt: SADECE bugünün satırlarını çıkar
    #    (eski günler 7-günlük çapraz-gün dedup geçmişi için KORUNUR)
    links_path = "data/haberler_linkler.txt"
    if os.path.exists(links_path):
        try:
            with open(links_path, encoding='utf-8') as f:
                lines = f.readlines()
            kept = [ln for ln in lines if not ln.startswith(f"{today_str}\t")]
            with open(links_path, 'w', encoding='utf-8') as f:
                f.writelines(kept)
            print(f"   ✂️  linkler: {len(lines) - len(kept)} bugün satırı çıkarıldı "
                  f"(eski günler korundu)")
        except IOError as e:
            print(f"   ⚠️  linkler düzenlenemedi: {e}")

    # 3) CERRAHİ — haberler_arsiv.txt: SADECE bugünün bloğunu çıkar
    #    Blok = '\n' + 80×'=' + '\n' + today_header + ... → sonraki gün bloğuna/EOF'a kadar.
    if os.path.exists(ARCHIVE_FILE):
        try:
            with open(ARCHIVE_FILE, encoding='utf-8') as f:
                content = f.read()
            sep = '=' * 80
            marker = f"\n{sep}\n{today_header}\n"
            start = content.find(marker)
            if start != -1:
                nxt = content.find(f"\n{sep}\n📅 ", start + len(marker))
                end = nxt if nxt != -1 else len(content)
                content = content[:start] + content[end:]
                with open(ARCHIVE_FILE, 'w', encoding='utf-8') as f:
                    f.write(content)
                print(f"   ✂️  arşiv: bugünün bloğu çıkarıldı ({today_header})")
            else:
                print("   ℹ️  arşivde bugünün bloğu yok — atlandı")
        except IOError as e:
            print(f"   ⚠️  arşiv düzenlenemedi: {e}")

    print("   ✅ RESET_TODAY tamam — pipeline sıfırdan çalışacak.\n")


def main():
    print("\n" + "=" * 70)
    print("🔒 SİBER GÜVENLİK HABERLERİ")
    print("=" * 70)
    print(f"📅 {_now_tr().strftime('%d %B %Y %H:%M')}")
    print("=" * 70 + "\n")

    # ── RESET_TODAY: elle taze üretim (workflow_dispatch input) ─────────────
    # Idempotency'den ÖNCE çalışır; bugünün durumunu sıfırlayıp aşağıdaki
    # Kontrol 1/2'nin taze fetch + rapor üretmesini sağlar. Sadece env işareti
    # açıkça verildiğinde tetiklenir (otomatik cron/dispatch'te ASLA çalışmaz).
    if os.environ.get('RESET_TODAY', '').strip().lower() in ('1', 'true', 'yes', 'on'):
        _reset_today_state()

    now = _now_tr()
    today_str = now.strftime('%Y-%m-%d')
    today_report = f"docs/raporlar/{today_str}.html"
    ham_txt_path = "data/haberler_ham.txt"
    cron_marker_path = "data/cron_basarili.txt"

    # Bu çalıştırma CRON tarafından mı tetiklendi?
    # GitHub Actions her adıma GITHUB_EVENT_NAME ortam değişkenini otomatik koyar.
    #   • 'schedule'            → GitHub'ın kendi cron'u (güvenilmez: sabah slotlarını düşürür)
    #   • 'repository_dispatch' → harici zamanlayıcı (cron-job.org) tetiklemesi.
    #     GitHub cron'unun düşürdüğü sabah saatlerini güvenilir şekilde tetiklemek için
    #     kullanılır; cron'la AYNI idempotency davranışı istenir (marker yazar, aynı gün
    #     başarılı raporu olan sonraki slotları atlar). Bu yüzden 'schedule' gibi sayılır.
    # Manuel (workflow_dispatch) ve yerel çalıştırmalarda is_schedule FALSE kalır → marker
    # yazılmaz, elle çalıştırma her zaman raporu zorla yeniden üretir.
    is_schedule = os.environ.get('GITHUB_EVENT_NAME') in ('schedule', 'repository_dispatch')

    def _rapor_basarili(content: str) -> bool:
        """Rapor fallback/hata içermiyorsa (gerçekten başarılıysa) True döner.

        Birincil sinyal _create_fallback_html'in bastığı YAPISAL yorum
        işaretidir (RAPOR_DURUM: FALLBACK). Eski işaretler ([FALLBACK] başlığı,
        uyarı bandı cümlesi) yalnızca bu işaret eklenmeden önce üretilmiş eski
        raporlar için ikincil olarak korunur. ('error-box' kontrolü kaldırıldı:
        o class hiçbir yerde üretilmiyordu — ölü kontroldü.)

        İKİNCİ ÖLÇÜT — TABAN: fallback olmayan ama İÇİ BOŞ bir rapor da başarılı
        sayılmamalı. Eşik olmadan 2 haberlik bir rapor "başarılı" kabul edilip o
        günün sonraki cron slotlarını atlatıyor ve gün ince raporla kilitleniyordu
        (07-27 12:08). Artık REPORT_FLOOR altındaki rapor başarısız sayılır →
        sıradaki slot yeniden dener (o sırada feed'ler tazelenmiş olur)."""
        if ('RAPOR_DURUM: FALLBACK' in content
                or '[FALLBACK]' in content
                or 'Gemini API yanıt vermedi — içerik çevrilmedi' in content):
            return False
        # Haber sayısı = gövde haberleri + kritik-3 kartları. Üretimdeki sayaç
        # (_rapor_haber_sayisi = top10 + remaining) ile AYNI toplamı vermeli;
        # yalnızca news-item sayılırsa kritik-3 kartları dışarıda kalır ve
        # 10 haberlik SAĞLIKLI rapor 7 görünüp "başarısız" damgalanır — o zaman
        # her cron slotu tüm LLM hattını boş yere yeniden çalıştırır.
        # ('class="news-item' öneki 'news-item vuln-item' varyantını da yakalar.)
        haber_sayisi = content.count('class="news-item') + content.count('class="top3-card"')
        if haber_sayisi < REPORT_FLOOR:
            print(f"   ⚠️  Mevcut rapor yalnızca {haber_sayisi} haber içeriyor "
                  f"(<{REPORT_FLOOR}) — başarılı sayılmıyor, yeniden denenecek.")
            return False
        return True

    # ── Kontrol 1: Bugünün BAŞARILI raporu zaten var mı? (KURŞUNGEÇİRMEZ) ────
    # Idempotency sinyali RAPORUN KENDİSİdir: docs/raporlar/<bugün>.html dosyası
    # varsa VE fallback/hata içermiyorsa, o gün için başarılı rapor üretilmiş
    # demektir. Bu durumda OTOMATİK (schedule/dispatch) çalıştırma HEMEN çıkar —
    # başarılı raporun ÜZERİNE ASLA YAZILMAZ. (Dosya adı bugünün tarihini içerir;
    # git checkout mtime'ına güvenilmez, dosya-adı tarihi kesin sinyaldir.)
    #
    # Neden işaret dosyası (cron_basarili.txt) DEĞİL de rapor dosyası? İşaret
    # silinir/kaybolursa (07-01'de olduğu gibi) marker-tabanlı kontrol atlar ve
    # rapor yeniden üretilip BOZULABİLİR. Rapor dosyası tek doğruluk kaynağı
    # olduğundan bu zincir kesin olarak kırılır.
    #
    # ⚠️ MANUEL (workflow_dispatch) çalıştırma bu atlamaya TABİ DEĞİLDİR: elle
    # "Run workflow" her zaman TAZE rapor üretir.
    if is_schedule and os.path.exists(today_report):
        try:
            with open(today_report, encoding='utf-8') as f:
                report_content = f.read()
            if _rapor_basarili(report_content):
                print(f"✅ Bugünün başarılı raporu zaten var: {today_report}")
                print("   Otomatik çalıştırma atlanıyor — başarılı raporun üzerine yazılmaz.")
                return 0
            else:
                print("⚠️  Bugünün raporu fallback/hatalı — yeniden denenecek.")
        except Exception:
            pass  # Okuma hatası varsa devam et

    # ── Kontrol 2: Ham haber verisi bugüne ait mi? ─────────────────────────
    # Eğer bugün zaten haber çekildiyse (ama Gemini başarısız olduysa),
    # kaynaklara tekrar gitme — haberler_linkler.txt tüm haberleri
    # "görüldü" olarak işaretlediğinden sıfır haber döner.
    #
    # ÖNEMLİ: os.path.getmtime() kullanılmıyor — git checkout tüm dosyalara
    # o anki zamanı damgalar, dolayısıyla dünkü ham.txt de "bugün" gibi görünür.
    # Bunun yerine dosyanın içindeki SESSION_DATE satırı kontrol ediliyor.
    ham_exists_for_today = False
    if os.path.exists(ham_txt_path):
        try:
            with open(ham_txt_path, encoding='utf-8') as _f:
                first_line = _f.readline().strip()
            if first_line == f"SESSION_DATE: {today_str}":
                ham_exists_for_today = True
        except Exception:
            pass

    sistem = HaberSistemi()

    if ham_exists_for_today:
        print("📄 Bugünün ham haberleri mevcut — haber çekme atlanıyor, sadece Gemini çalıştırılıyor.")
        try:
            sistem.social_data = fetch_social_signals(SOCIAL_SIGNAL_CONFIG)
            print(f"📡 {len(sistem.social_data)} sosyal sinyal çekildi")
        except Exception as e:
            print(f"⚠️  Sosyal medya sinyalleri çekilemedi: {str(e)[:100]}")
            sistem.social_data = []
        try:
            with open(ham_txt_path, encoding='utf-8') as f:
                txt = f.read()
        except Exception as e:
            print(f"❌ Ham TXT okunamadı: {e}")
            return 1
    else:
        # 1. Topla
        haberler = sistem.topla()
        if not haberler:
            print("❌ Haber yok!")
            return 1

        # 2. TXT
        txt = sistem.save_txt(haberler)

    # 3. HTML (Gemini)
    sistem.create_html(txt)

    # ── CRON başarı işareti ────────────────────────────────────────────────
    # Günü "tamamlandı" sayan TEK koşul: bu çalıştırma CRON (schedule) ile
    # tetiklendi VE üretilen rapor başarılı (fallback değil). Bu durumda bugünün
    # tarihi data/cron_basarili.txt'ye yazılır; aynı gün sonraki cron slotları atlar.
    #   • Manuel tetiklemeler bu işareti YAZMAZ → cron saati gelince cron yine çalışır.
    #   • Rapor fallback ise işaret yazılmaz → sıradaki cron slotu yeniden dener.
    if is_schedule:
        try:
            basarili = False
            if os.path.exists(today_report):
                with open(today_report, encoding='utf-8') as f:
                    basarili = _rapor_basarili(f.read())
            if basarili:
                with open(cron_marker_path, 'w', encoding='utf-8') as f:
                    f.write(today_str)
                print(f"🔖 CRON başarı işareti güncellendi: {today_str}")
            else:
                print("⚠️  Rapor başarılı değil — CRON işareti yazılmadı, sıradaki cron denenecek.")
        except Exception as e:
            print(f"⚠️  CRON işareti yazılamadı: {str(e)[:100]}")

    print("\n" + "=" * 70)
    print("✨ TAMAMLANDI!")
    print("=" * 70)
    print("🌐 https://siberguvenlikhaberler.github.io/siberguvenlik/")
    print("=" * 70 + "\n")

    return 0


if __name__ == "__main__":
    exit(main())
