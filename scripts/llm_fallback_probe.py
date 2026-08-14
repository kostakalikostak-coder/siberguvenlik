"""
LLM sağlayıcı yedeği probu (teşhis).

Rapor üretmez, commit'lemez, hiçbir veri dosyasına dokunmaz. Tek yaptığı,
minik bir JSON istemini üç senaryoda çalıştırıp zincirin gerçekten kurulu
olduğunu KANITLAMAK:

  1) Gerçek OpenRouter anahtarıyla normal çağrı        → birincil yol sağlam mı?
  2) OPENROUTER_API_KEY kasten bozulur (401 alır)      → yedek Gemini devralıyor mu?
  3) Yalnızca Gemini (LLM_PROVIDER=gemini)             → AI Studio anahtarı geçerli mi?

2. senaryo asıl sorunun cevabıdır: "kredi biterse Gemini işi yapar mı?".
Bozuk anahtar 401 verir, kredi bitişi 402 verir; ikisi de llm_client'ta KALICI
hata sınıfındadır ve aynı kod yolundan yedeğe düşer.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ISTEM = ('Sadece şu JSON nesnesini döndür, başka hiçbir şey yazma: '
         '{"durum": "ok", "saglayici": "<seni çalıştıran modelin adı>"}')


def _sistem():
    """__init__ çalıştırmadan _gemini_call_json'ı kullanılabilir bir örnek."""
    import main
    return main.HaberSistemi.__new__(main.HaberSistemi)


def _cagir(etiket):
    try:
        return _sistem()._gemini_call_json(ISTEM, max_output_tokens=512, label=etiket)
    except Exception as e:
        print(f"   ‼️  İstisna [{type(e).__name__}]: {e}")
        return None


def main():
    import main as m
    from src import config

    or_key = os.getenv('OPENROUTER_API_KEY', '')
    g_key = os.getenv('GEMINI_API_KEY', '')
    print("=" * 70)
    print("LLM YEDEK PROBU")
    print(f"  OPENROUTER_API_KEY : {'VAR' if or_key else 'YOK'}")
    print(f"  GEMINI_API_KEY     : {'VAR' if g_key else 'YOK'}")
    print(f"  LLM_PROVIDER       : {config.LLM_PROVIDER}")
    print(f"  OpenRouter modeller: {config.OPENROUTER_MODEL} → {config.OPENROUTER_FALLBACK_MODELS}")
    print(f"  Gemini yedek sırası: {config.GEMINI_FALLBACK_MODELS}")
    print("=" * 70)

    # Bu anahtara AÇIK olan modelleri listele — model adlarını tahmin etmemek
    # için. (2.5-pro gibi kapanan modeller sessizce 404 verip deneme harcıyor.)
    if g_key:
        try:
            from google import genai
            # İstemci yerel değişkende TUTULUR: geçici nesne çöpe gidince
            # sayfalayıcı "client has been closed" hatası veriyor.
            _c = genai.Client(api_key=g_key)
            adlar = [
                mo.name.replace('models/', '')
                for mo in _c.models.list()
                if 'generateContent' in (getattr(mo, 'supported_actions', None) or [])
            ]
            print("\nAI Studio'da generateContent'e AÇIK modeller:")
            for a in sorted(adlar):
                print(f"   • {a}")
        except Exception as e:
            print(f"\n⚠️  Model listesi alınamadı: {type(e).__name__}: {e}")

    sonuc = {}

    # ── 1) Normal yol: OpenRouter birincil ───────────────────────────────────
    print("\n[1/3] Normal yol — OpenRouter birincil")
    sonuc['openrouter'] = _cagir('probe-normal') is not None
    print(f"   → {'BAŞARILI' if sonuc['openrouter'] else 'BAŞARISIZ'}")

    # ── 2) Asıl test: OpenRouter kasten bozuk → yedek devralmalı ─────────────
    print("\n[2/3] Kredi-bitti benzetimi — OpenRouter anahtarı kasten bozuk")
    from src import llm_client
    bozuk = 'sk-or-v1-' + '0' * 40      # geçerli biçim, geçersiz anahtar → 401
    eski = llm_client.OPENROUTER_API_KEY
    llm_client.OPENROUTER_API_KEY = bozuk
    try:
        veri = _cagir('probe-yedek')
    finally:
        llm_client.OPENROUTER_API_KEY = eski
    sonuc['yedek'] = veri is not None
    print(f"   → {'BAŞARILI (Gemini devraldı)' if sonuc['yedek'] else 'BAŞARISIZ'}  yanıt={veri}")

    # ── 3) Saf Gemini yolu: AI Studio anahtarı tek başına geçerli mi? ────────
    print("\n[3/3] Saf Gemini yolu — LLM_PROVIDER=gemini")
    eski_aktif = m.is_openrouter_active
    m.is_openrouter_active = lambda: False
    try:
        veri3 = _cagir('probe-gemini')
    finally:
        m.is_openrouter_active = eski_aktif
    sonuc['gemini'] = veri3 is not None
    print(f"   → {'BAŞARILI' if sonuc['gemini'] else 'BAŞARISIZ'}  yanıt={veri3}")

    print("\n" + "=" * 70)
    for k, v in sonuc.items():
        print(f"  {k:12s}: {'✅' if v else '❌'}")
    print("=" * 70)

    # Asıl kanıt 2. senaryodur; onsuz prob başarısız sayılır.
    if not sonuc['yedek']:
        print("❌ YEDEK ÇALIŞMIYOR — GEMINI_API_KEY geçersiz ya da kota kapalı olabilir.")
        return 1
    print("✅ YEDEK ÇALIŞIYOR: OpenRouter düştüğünde Gemini işi devralıyor.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
