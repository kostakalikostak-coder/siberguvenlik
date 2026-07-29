"""
OpenRouter LLM istemcisi — Gemini 3 Flash için PASİF altyapı.

Bu modül, sistemin LLM çağrılarını Gemini (google-genai) yerine OpenRouter
üzerinden yapabilmesi için hazır bekler. OpenRouter OpenAI uyumlu bir API
sunduğundan, requirements.txt'te zaten bulunan `openai` paketi yalnızca
base_url değiştirilerek kullanılır.

AKTİFLEŞME KOŞULU (config.is_openrouter_active):
    LLM_PROVIDER=openrouter  VE  OPENROUTER_API_KEY tanımlı.
Anahtar gelene kadar bu modüldeki fonksiyonlar çağrılmaz; sistem Gemini ile
çalışmaya devam eder. Yani altyapı tamamen pasiftir.

Gemini 3 Flash notları (OpenRouter):
    model     : google/gemini-3-flash-preview
    bağlam    : 1M token, çoklu-mod (metin/görsel/PDF/ses/video) giriş, metin çıkış
    reasoning : "thinking" modeli — reasoning.effort = minimal|low|medium|high|xhigh
                NOT: bu dosya bir zamanlar "effort ve max_tokens AYNI ANDA
                gönderilemez" diyordu; kod ikisini birlikte gönderiyor ve
                üretimde her gün sorunsuz çalışıyor (raporlar üretiliyor).
                Not bayattı, kaldırıldı — yanlış kısıt sonraki değişikliklerde
                yanıltıcı olurdu.
    json      : response_format={"type":"json_object"} ile JSON modu desteklenir.

OpenAI SDK'sında OpenRouter'a özel alanlar (reasoning, vb.) standart şemada
olmadığı için `extra_body` ile gönderilir.
"""
import re
import json
import time

from src.config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL,
    OPENROUTER_FALLBACK_MODELS, OPENROUTER_REASONING_EFFORT,
    OPENROUTER_REASONING_EXCLUDE, OPENROUTER_TEMPERATURE, OPENROUTER_TIMEOUT,
    OPENROUTER_HTTP_REFERER, OPENROUTER_APP_TITLE,
)

# Geçerli reasoning effort seviyeleri (OpenRouter birleşik reasoning şeması)
_VALID_EFFORTS = {'minimal', 'low', 'medium', 'high', 'xhigh'}

# Kesilme (truncation) güvenliği: Gemini 3 Flash bir "thinking" modeli ve
# OpenRouter'da reasoning token'ları da max_tokens bütçesinden harcanır. Bütçe
# yetmezse çıktı finish_reason='length' ile YARIDA kesilir (yarım JSON / kesik
# cümle). Böyle bir durumda bütçe iki katına çıkarılıp yeniden denenir; üst
# sınır _TRUNC_BUDGET_CAP'tir (sonsuz büyümeyi ve maliyet patlamasını önler).
_TRUNC_BUDGET_CAP = 32000


def _extract_json_from_text(text):
    """AI yanıtından JSON nesnesini güvenli biçimde çıkarır.

    main.py'deki aynı adlı yardımcının kopyasıdır (modül bağımsız kalsın diye).
    """
    text = (text or '').strip()
    # Olası thinking bloklarını temizle (<think>...</think>)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
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


def _build_client():
    """OpenRouter'a yönlendirilmiş OpenAI istemcisi oluşturur.

    İçe aktarma fonksiyon içinde yapılır: modül import edilse bile, OpenRouter
    aktif değilken `openai` paketinin mevcut olmasına gerek kalmaz (pasiflik).
    """
    from openai import OpenAI
    return OpenAI(
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
        timeout=OPENROUTER_TIMEOUT,
        default_headers={
            # OpenRouter sıralama panosu için opsiyonel kimlik başlıkları
            'HTTP-Referer': OPENROUTER_HTTP_REFERER,
            'X-Title': OPENROUTER_APP_TITLE,
        },
    )


def _reasoning_config():
    """reasoning.effort yapılandırmasını döndürür; kapalıysa None."""
    effort = OPENROUTER_REASONING_EFFORT
    if effort in ('', 'none', 'off', '0'):
        return None
    if effort not in _VALID_EFFORTS:
        effort = 'low'
    cfg = {'effort': effort}
    if OPENROUTER_REASONING_EXCLUDE:
        cfg['exclude'] = True
    return cfg


def _models_to_try():
    """Birincil model + yedekleri tekrarsız sırayla döndürür."""
    seq = [OPENROUTER_MODEL] + list(OPENROUTER_FALLBACK_MODELS)
    seen, ordered = set(), []
    for m in seq:
        if m and m not in seen:
            seen.add(m)
            ordered.append(m)
    return ordered


def generate_text(prompt, max_output_tokens=4096, temperature=None,
                  json_mode=False, label=''):
    """OpenRouter (Gemini 3 Flash) ile metin üretir, ham metni döndürür.

    Başarısızlıkta None döner. Model sırası: birincil → yedekler.

    GEÇİCİ hatalarda (429 rate-limit, 5xx, ağ/timeout) AYNI model bir kez daha
    denenir; kalıcı hatalarda (400/401/403/404 — kötü istek, yetkisiz anahtar,
    bilinmeyen model) beklemeden sonraki modele geçilir. Eskiden her hata tipi
    aynı sayılıyordu: tek bir anlık 429, birincil modeli "yanmış" sayıp isteği
    daha zayıf bir yedeğe düşürüyordu; kalıcı bir 404 ise boşuna 15s bekletiyordu.
    """
    # Geçici sayılan HTTP kodları — aynı model üzerinde yeniden denemeye değer.
    _TRANSIENT = (408, 409, 425, 429, 500, 502, 503, 504, 529)
    if not OPENROUTER_API_KEY:
        print(f"   ⚠️  [{label}] OPENROUTER_API_KEY yok, atlanıyor.")
        return None

    client = _build_client()
    reasoning = _reasoning_config()
    temp = OPENROUTER_TEMPERATURE if temperature is None else temperature
    models = _models_to_try()

    extra_body = {}
    if reasoning is not None:
        extra_body['reasoning'] = reasoning

    kwargs = {}
    if json_mode:
        # OpenRouter, OpenAI uyumlu JSON modunu destekler
        kwargs['response_format'] = {'type': 'json_object'}

    def _status_of(exc):
        """İstisnadan HTTP durum kodunu çıkarır (OpenAI SDK'sı .status_code taşır)."""
        for attr in ('status_code', 'http_status', 'code'):
            v = getattr(exc, attr, None)
            if isinstance(v, int):
                return v
        resp = getattr(exc, 'response', None)
        v = getattr(resp, 'status_code', None)
        return v if isinstance(v, int) else None

    for attempt, model in enumerate(models):
        # Aynı model üzerinde geçici hata için tek ek deneme (toplam 2).
        for same_model_try in range(2):
            try:
                print(f"   [{label}] OpenRouter deneme {attempt + 1}/{len(models)} "
                      f"[{model}]{' (tekrar)' if same_model_try else ''}...")
                # Kesilme olursa AYNI model üzerinde bütçeyi katlayarak yeniden dene.
                budget = max_output_tokens
                text = ''
                while True:
                    resp = client.chat.completions.create(
                        model=model,
                        messages=[{'role': 'user', 'content': prompt}],
                        max_tokens=budget,
                        temperature=temp,
                        extra_body=extra_body or None,
                        **kwargs,
                    )
                    choice = resp.choices[0]
                    text = choice.message.content or ''
                    finish = getattr(choice, 'finish_reason', None)
                    # finish_reason='length' → çıktı bütçeye takılıp kesildi (çıktı
                    # boş da olabilir: tüm bütçe reasoning'e gitmiş). Bütçeyi büyüt.
                    if finish == 'length' and budget < _TRUNC_BUDGET_CAP:
                        new_budget = min(budget * 2, _TRUNC_BUDGET_CAP)
                        print(f"   [{label}] ✂️  Çıktı kesildi (length, bütçe={budget}); "
                              f"bütçe {new_budget}'e çıkarılıp yeniden deneniyor.")
                        budget = new_budget
                        continue
                    break
                if not text.strip():
                    print(f"   [{label}] ⚠️  Boş yanıt — sonraki model deneniyor.")
                    break          # aynı modeli tekrarlamak boş yanıtı düzeltmez
                print(f"   [{label}] ✅ OpenRouter başarılı [{model}].")
                return text
            except Exception as e:
                status = _status_of(e)
                gecici = (status in _TRANSIENT) if status is not None else True
                print(f"   [{label}] ⚠️  OpenRouter hata "
                      f"[{type(e).__name__}{f'/{status}' if status else ''}]: {e}")
                if gecici and same_model_try == 0:
                    print(f"   [{label}] ⏳ Geçici hata — 15s sonra AYNI model tekrar.")
                    time.sleep(15)
                    continue
                if not gecici:
                    print(f"   [{label}] ⏭️  Kalıcı hata — beklemeden sonraki model.")
                elif attempt < len(models) - 1:
                    print(f"   [{label}] ⏳ 15s bekleniyor...")
                    time.sleep(15)
                break

    print(f"   [{label}] ❌ OpenRouter {len(models)} deneme başarısız.")
    return None


def generate_json(prompt, max_output_tokens=4096, temperature=None, label=''):
    """OpenRouter ile JSON yanıt üretir ve ayrıştırılmış nesneyi döndürür.

    _gemini_call_json'ın OpenRouter karşılığıdır. Başarısızlıkta None döner.
    """
    text = generate_text(
        prompt, max_output_tokens=max_output_tokens, temperature=temperature,
        json_mode=True, label=label,
    )
    if text is None:
        return None
    try:
        return _extract_json_from_text(text)
    except Exception as e:
        print(f"   [{label}] ⚠️  JSON ayrıştırma hatası: {e}")
        return None
