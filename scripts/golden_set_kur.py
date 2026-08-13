#!/usr/bin/env python3
"""GOLDEN SET KURUCU — data/dedup_golden.json'u gerçek üretim verisinden üretir.

NEDEN VAR:
Dedup eşikleri 2026 Temmuz-Ağustos boyunca vaka-vaka elle ayarlandı (bkz.
src/dedup.py Kural 5 yorumundaki eşik seçim notları). Her ayar bir önceki
vakayı düzeltirken yeni bir sınıfı bozdu: 08-06'da güvenlik tabanı yanlış
tetikledi, 08-07'de 8 günde 81 adet ≥85 puanlı haber mükerrer bayrağıyla
gitti, 08-12'de günün 2. ve 3. en yüksek puanlı haberi manşetten düştü.
Sebep tek: DEĞİŞİKLİĞİN ETKİSİNİ ÖLÇEN SABİT BİR REFERANS YOKTU.

Bu script o referansı kurar: etiketli haber ÇİFTLERİ. Her çift, gerçek
üretim metniyle (repo'daki geçmiş + ham dosya) doldurulur ve ELLE etiketlenir.
Etiketler dört ilişki türünden biridir (bkz. src/olay_iliski.py):

    AYNI_GELISME            — aynı olayın aynı gelişmesi; GERÇEK mükerrer
    YENI_GELISME            — aynı olay, YENİ gelişme; rapora girmeli
    AYNI_AKTOR_FARKLI_OLAY  — aynı aktör/tema, farklı olay; tamamen serbest
    ILISKISIZ               — alakasız

Çıktı data/dedup_golden.json KENDİ KENDİNE YETERLİDİR (metinleri gömer), çünkü
data/haberler_ham.txt her gün üzerine yazılır. Bir kez üretilir, commit'lenir;
sonrasında bu script yalnızca YENİ vaka eklemek için tekrar çalıştırılır.

Ağ/LLM/API anahtarı GEREKTİRMEZ.

Çalıştır:  python scripts/golden_set_kur.py
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

HAM = 'data/haberler_ham.txt'
RAPOR_GECMIS = 'data/rapor_gecmis.json'
KRITIK3_GECMIS = 'data/kritik3_gecmis.json'
CIKTI = 'data/dedup_golden.json'

# full_text'in kaç karakteri gömülecek. _mukerrer_dogrulandi üretimde 2500
# karakterle çalışıyor (bkz. main.py); golden set aynı bütçeyi kullanmalı,
# aksi halde ölçüm üretimden farklı bir girdiyle yapılır.
METIN_BUTCE = 2500

# ─────────────────────────────────────────────────────────────────────────────
# ETİKET TABLOSU — ELLE DOLDURULUR
#
# Her satır bir ÇİFT: (a) bugünün adayı, (b) geçmişteki referans.
# 'a' kaynağı:  ('ham', <id>)          → data/haberler_ham.txt içindeki [id]
#               ('rapor', <tarih>, <başlık parçası>) → rapor_gecmis.json
#               ('kritik3', <tarih>, <başlık parçası>) → kritik3_gecmis.json
# 'b' aynı biçimde.
#
# 'iliski': beklenen doğru cevap.
# 'ayni_gun': True ise gün-içi kümeleme vakası (çapraz-gün değil).
# 'not': vakanın NEDEN bu etikete sahip olduğunun tek cümlelik gerekçesi.
# ─────────────────────────────────────────────────────────────────────────────
VAKALAR = [
    # ══ 2026-08-12 ÜRETİM HATALARI (bu golden set'in kurulma sebebi) ══════
    {
        'ad': '2026-08-12 İran su altyapısı ↔ 08-10 CISA Minnesota',
        'a': ('ham', 31),
        'b': ('kritik3', '2026-08-10', 'CISA'),
        'iliski': 'YENI_GELISME',
        'ayni_gun': False,
        'not': 'Aynı süregelen su altyapısı kampanyası ama YENİ eyaletler '
               '(New Jersey, Alabama) ve yeni saldırılar. 96 puanla günün 2. '
               'haberiydi; mukerrer=1 yiyip manşetten düştü.',
    },
    {
        'ad': '2026-08-12 Sandworm UAC-0145 ↔ 08-11 Sandworm Polonya',
        'a': ('ham', 8),
        'b': ('kritik3', '2026-08-11', 'Sandworm'),
        'iliski': 'AYNI_AKTOR_FARKLI_OLAY',
        'ayni_gun': False,
        'not': 'Aynı aktör (Sandworm), tamamen farklı olay: Polonya enerji '
               'sabotajı ↔ Ukraynalı IT uzmanlarına sahte mülakat/VPN. '
               '92 puanla mukerrer=1 yiyip manşetten düştü.',
    },
    {
        'ad': '2026-08-12 4 eyalet yerel yönetim ↔ 08-11 Suisun City',
        'a': ('ham', 72),
        'b': ('rapor', '2026-08-11', 'Suisun'),
        'iliski': 'YENI_GELISME',
        'ayni_gun': False,
        'not': 'Dünkü Suisun City haberini İÇERİYOR ama 3 yeni kurban ekliyor '
               '(Coweta OK, Mitchell SD, Teksas/Wisconsin ilçeleri). Rapora '
               'girmeli AMA paragraf yeni kurbanlarla açmalı — üretimde dünkü '
               'Suisun kısmıyla açtığı için mükerrer hissi verdi.',
    },
    {
        'ad': '2026-08-12 Patch Tuesday 421 ↔ Windows 10 KB5120249 ESU',
        'a': ('ham', 40),
        'b': ('ham', 21),
        'iliski': 'AYNI_GELISME',
        'ayni_gun': True,
        'not': 'İkisi de Ağustos 2026 Yama Salısı. ESU bülteni, 421-CVE '
               'toplamının bir alt kümesi. mukerrer=0 ile ikisi de rapora '
               'girdi (biri KRİTİK 3, biri gövde).',
    },
    {
        'ad': '2026-08-12 ShieldBreak Defender bypass ↔ Patch Tuesday 421',
        'a': ('ham', 3),
        'b': ('ham', 40),
        'iliski': 'ILISKISIZ',
        'ayni_gun': True,
        'not': 'Defender yama atlatma PoC iddiası, Yama Salısı toplamından '
               'ayrı bir olay. Üretimde govde_ayni_olay ile YANLIŞ elendi.',
    },

    # ══ DOĞRU YAKALANAN GÜN-İÇİ MÜKERRERLER (regresyon koruması) ══════════
    {
        'ad': '2026-08-12 Kimwolf v7 (Unit42) ↔ Kimwolf v7 (Chrome parmak izi)',
        'a': ('ham', 6),
        'b': ('ham', 29),
        'iliski': 'AYNI_GELISME',
        'ayni_gun': True,
        'not': 'Aynı Unit 42 araştırması, iki farklı kaynak. Doğru elendi.',
    },
    {
        'ad': '2026-08-12 Delta Wi-Fi (BleepingComputer) ↔ Delta Wi-Fi (Register)',
        'a': ('ham', 20),
        'b': ('ham', 41),
        'iliski': 'AYNI_GELISME',
        'ayni_gun': True,
        'not': 'Aynı DEF CON dönüşü uçuş olayı. Doğru elendi.',
    },
    {
        'ad': '2026-08-12 Cisco ASA/FTD (THN) ↔ Cisco ASA/FTD (Bleeping)',
        'a': ('ham', 4),
        'b': ('ham', 19),
        'iliski': 'AYNI_GELISME',
        'ayni_gun': True,
        'not': 'Aynı CVE-2026-20349 istismarı. Doğru elendi.',
    },
    {
        'ad': '2026-08-12 Kötü amaçlı SIM (Register) ↔ Kötü amaçlı SIM (Bleeping)',
        'a': ('ham', 12),
        'b': ('ham', 44),
        'iliski': 'AYNI_GELISME',
        'ayni_gun': True,
        'not': 'Aynı Birmingham Üniversitesi / Fuzzware araştırması.',
    },
    {
        'ad': '2026-08-12 Deepfake sertifika (Register) ↔ (HelpNetSecurity)',
        'a': ('ham', 43),
        'b': ('ham', 63),
        'iliski': 'AYNI_GELISME',
        'ayni_gun': True,
        'not': 'Aynı İspanya Ulusal Polisi operasyonu.',
    },

    # ══ DOĞRU YAKALANAN ÇAPRAZ-GÜN MÜKERRERLER (regresyon koruması) ═══════
    {
        'ad': '2026-08-12 Mozilla GPG anahtarı ↔ 08-11 Mozilla GPG anahtarı',
        'a': ('ham', 13),
        'b': ('rapor', '2026-08-11', 'Firefox GPG'),
        'iliski': 'AYNI_GELISME',
        'ayni_gun': False,
        'not': 'Dün raporlanan anahtar yenileme olayının aynısı.',
    },
    {
        'ad': '2026-08-12 Gunra fidye ↔ 08-11 Gunra fidye',
        'a': ('ham', 16),
        'b': ('rapor', '2026-08-11', 'Gunra'),
        'iliski': 'AYNI_GELISME',
        'ayni_gun': False,
        'not': 'Dün raporlanan Gunra operasyon genişlemesinin aynısı.',
    },
    {
        'ad': '2026-08-12 Head Mare TrueConf ↔ 08-09 Head Mare TrueConf',
        'a': ('ham', 78),
        'b': ('kritik3', '2026-08-09', 'Head Mare'),
        'iliski': 'AYNI_GELISME',
        'ayni_gun': False,
        'not': '3 gün önce KRİTİK 3 manşeti olan olayın aynısı.',
    },
    {
        'ad': '2026-08-12 CEVA Logistics ↔ 08-11 CEVA Logistics',
        'a': ('ham', 71),
        'b': ('rapor', '2026-08-11', 'CEVA'),
        'iliski': 'AYNI_GELISME',
        'ayni_gun': False,
        'not': 'Dün raporlanan ihlalin aynısı.',
    },
    {
        'ad': '2026-08-12 DeadLock fidye ↔ 08-11 DeadLock fidye',
        'a': ('ham', 10),
        'b': ('rapor', '2026-08-11', 'DeadLock'),
        'iliski': 'AYNI_GELISME',
        'ayni_gun': False,
        'not': 'Dün raporlanan merkeziyetsiz altyapı analizinin aynısı.',
    },

    # ══ YANLIŞ-POZİTİF OLMAMASI GEREKENLER (birleştirme riski) ════════════
    {
        'ad': '2026-08-12 Kuzey Koreli sahte kripto şirketi ↔ FBI federal kurum',
        'a': ('ham', 14),
        'b': ('ham', 47),
        'iliski': 'AYNI_AKTOR_FARKLI_OLAY',
        'ayni_gun': True,
        'not': 'İkisi de Kuzey Koreli uzaktan IT çalışanı teması ama farklı '
               'olay: DEF CON araştırması ↔ FBI soruşturması. İkisi de '
               'rapora girdi — doğru davranış.',
    },
    {
        'ad': '2026-08-12 SAP Commerce Cloud ↔ Adobe ColdFusion',
        'a': ('ham', 2),
        'b': ('ham', 56),
        'iliski': 'ILISKISIZ',
        'ayni_gun': True,
        'not': 'İki ayrı satıcının ayrı yama bültenleri; "kritik açık '
               'yamalandı" kalıbı benzer diye birleşmemeli.',
    },
    {
        'ad': '2026-08-12 SharePoint fidye istismarı ↔ Cisco ASA DoS',
        'a': ('ham', 27),
        'b': ('ham', 4),
        'iliski': 'ILISKISIZ',
        'ayni_gun': True,
        'not': 'İkisi de "CISA KEV + aktif istismar" kalıbı; farklı ürün, '
               'farklı CVE. src/dedup.py Kural 5 yorumundaki jenerik başlık '
               'yanlış-pozitif sınıfı.',
    },
    {
        'ad': '2026-08-12 Ivanti EPM ↔ SonicWall GMS',
        'a': ('ham', 52),
        'b': ('ham', 54),
        'iliski': 'ILISKISIZ',
        'ayni_gun': True,
        'not': 'İki ayrı satıcı yaması; jenerik başlık kalıbı benzerliği.',
    },
    # ══ 2026-08-13 ÜRETİM YANLIŞ-POZİTİFLERİ (jenerik kurum sözcükleri) ═══
    {
        'ad': '2026-08-13 Kolombiya Adalet Bakanlığı ↔ 08-06 Ransom Cartel hapis',
        'a': ('rapor', '2026-08-13', 'Kolombiya Adalet'),
        'b': ('rapor', '2026-08-06', 'Ransom Cartel'),
        'iliski': 'ILISKISIZ',
        'ayni_gun': False,
        'not': 'Kolombiya Adalet Bakanlığına fidye saldırısı ile Ransom Cartel '
               'kurucusunun ABD\'de hapis cezası tamamen farklı olaylar. '
               'Üretimde {ad:adalet, ad:bakanlığ} ortaklığıyla AYNI_GELISME '
               'sayıldı — "Adalet Bakanlığı" kurum TÜRÜ adıdır, olay kimliği '
               'değil. Manşette olduğu için elenmedi; gövdede olsa ölürdü.',
    },
    {
        'ad': '2026-08-13 İngiltere Adli Sicil Ofisi ↔ 08-07 İsviçre SharePoint',
        'a': ('rapor', '2026-08-13', 'Adli Sicil'),
        'b': ('rapor', '2026-08-07', 'SharePoint'),
        'iliski': 'ILISKISIZ',
        'ayni_gun': False,
        'not': 'İngiltere adli sicil ofisindeki ihlal ile İsviçre hükümetinin '
               'SharePoint sunucuları farklı olaylar. Üretimde {ad:informat, '
               'ad:office, ad:ofisi} ortaklığıyla AYNI_GELISME sayıldı — '
               '"Information Commissioner\'s Office" jenerik kurum tamlamasıdır.',
    },
    {
        'ad': '2026-08-12 LiteLLM/TeamPCP ↔ Project CAV3RN',
        'a': ('ham', 1),
        'b': ('ham', 79),
        'iliski': 'ILISKISIZ',
        'ayni_gun': True,
        'not': 'İkisi de tedarik zinciri/C2 temalı ama alakasız kampanyalar.',
    },
]


def _ham_gunu():
    """data/haberler_ham.txt'in SESSION_DATE'i (yoksa None).

    ham ID'leri GÜNE ÖZGÜDÜR: dosya her gün üzerine yazılır ve [31] bir gün
    İran su altyapısı, ertesi gün bambaşka bir haberdir. Bu yüzden ham kaynaklı
    vakalar YALNIZCA kendi günlerinde yeniden üretilebilir."""
    if not os.path.exists(HAM):
        return None
    with open(HAM, encoding='utf-8') as f:
        bas = f.read(200)
    m = re.search(r'SESSION_DATE:\s*(\d{4}-\d{2}-\d{2})', bas)
    return m.group(1) if m else None


def _ham_yukle():
    """data/haberler_ham.txt → {id: {'title','full_text'}}"""
    if not os.path.exists(HAM):
        print(f'   ⚠️  {HAM} yok — ham vakalar atlanacak.')
        return {}
    with open(HAM, encoding='utf-8') as f:
        s = f.read()
    pat = re.compile(
        r'(?m)^\[\s*(\d+)\]\s*(?:[^\n]*?)\s-\s(.+)\n─{80}\n'
        r'Tarih: [^\n]*\nLink: [^\n]*\n\n\[TAM METİN - \d+ kelime[^\]]*\]\n')
    out = {}
    ms = list(pat.finditer(s))
    for i, m in enumerate(ms):
        son = ms[i + 1].start() if i + 1 < len(ms) else len(s)
        govde = s[m.end():son]
        # Sonraki makale ayıracını temizle
        govde = re.split(r'\n={80}\n', govde)[0].strip()
        out[int(m.group(1))] = {
            'title': m.group(2).strip(),
            'full_text': govde[:METIN_BUTCE],
        }
    return out


def _gecmis_yukle(path):
    """rapor_gecmis/kritik3_gecmis → {tarih: [view, ...]}"""
    if not os.path.exists(path):
        print(f'   ⚠️  {path} yok.')
        return {}
    with open(path, encoding='utf-8') as f:
        kayitlar = json.load(f)
    out = {}
    for rec in kayitlar:
        if isinstance(rec, dict) and rec.get('views'):
            out.setdefault(rec.get('date', '?'), []).extend(rec['views'])
    return out


def _view_bul(kaynak, ham, rapor, kritik3):
    tur = kaynak[0]
    if tur == 'ham':
        v = ham.get(kaynak[1])
        if not v:
            return None, f'ham[{kaynak[1]}] bulunamadı'
        return {'tr_title': '', 'paragraph': '',
                'title': v['title'], 'full_text': v['full_text']}, ''
    depo = rapor if tur == 'rapor' else kritik3
    _, tarih, parca = kaynak
    for v in depo.get(tarih, []):
        metin = (v.get('tr_title', '') + ' ' + v.get('title', ''))
        if parca.lower() in metin.lower():
            return {
                'tr_title': v.get('tr_title', ''),
                'paragraph': v.get('paragraph', ''),
                'title': v.get('title', ''),
                'full_text': (v.get('full_text', '') or '')[:METIN_BUTCE],
            }, ''
    return None, f'{tur}[{tarih}] içinde "{parca}" bulunamadı'


def _vaka_gunu(vaka):
    """Vakanın ait olduğu gün — 'ad' alanının başındaki tarihten okunur."""
    m = re.match(r'(\d{4}-\d{2}-\d{2})', vaka['ad'])
    return m.group(1) if m else None


def main():
    ham = _ham_yukle()
    ham_gun = _ham_gunu()
    rapor = _gecmis_yukle(RAPOR_GECMIS)
    kritik3 = _gecmis_yukle(KRITIK3_GECMIS)

    ciftler, eksik, atlanan = [], [], []
    for vaka in VAKALAR:
        # GÜN GÜVENLİĞİ (kritik): ham ID'leri güne özgüdür ve dosya her gün
        # üzerine yazılır. Bu kontrol olmadan script, farklı bir günde
        # çalıştırıldığında ham kaynaklı vakaları SESSİZCE yanlış makalelerle
        # doldurur. ÖLÇÜLDÜ (2026-08-13): 22 vakanın 16'sı böyle bozuldu —
        # 'İran su altyapısı' vakasının a tarafı 'UK criminal records office'
        # oldu ve golden set puanı 15/20 → 8/22'ye düştü. Kalite kapısının
        # kendisi sessizce çürüdüğü için fark edilmesi de zordu.
        ham_kaynakli = 'ham' in (vaka['a'][0], vaka['b'][0])
        if ham_kaynakli and _vaka_gunu(vaka) != ham_gun:
            atlanan.append((vaka['ad'], f'ham dosyası {ham_gun} gününe ait'))
            continue
        va, ea = _view_bul(vaka['a'], ham, rapor, kritik3)
        vb, eb = _view_bul(vaka['b'], ham, rapor, kritik3)
        if not va or not vb:
            eksik.append((vaka['ad'], ea or eb))
            continue
        ciftler.append({
            'ad': vaka['ad'],
            'iliski': vaka['iliski'],
            'ayni_gun': vaka['ayni_gun'],
            'not': vaka['not'],
            'a': va,
            'b': vb,
        })

    # Mevcut dosyadaki vakalar KORUNUR — bu script yeni vaka EKLER, silmez.
    # (Ham dosya her gün değiştiği için eski vakaların kaynağı artık yoktur;
    # gömülü metinleriyle dosyada yaşamaya devam etmeleri gerekir.)
    mevcut = []
    if os.path.exists(CIKTI):
        with open(CIKTI, encoding='utf-8') as f:
            mevcut = json.load(f).get('ciftler', [])
    adlar = {c['ad'] for c in ciftler}
    korunan = [c for c in mevcut if c['ad'] not in adlar]
    tum = korunan + ciftler

    with open(CIKTI, 'w', encoding='utf-8') as f:
        json.dump({
            'aciklama': 'Dedup/olay-ilişkisi golden set — ELLE etiketli gerçek '
                        'üretim çiftleri. Değişiklikler bu sete karşı ölçülür '
                        '(scripts/dedup_olc.py, tests/test_dedup_golden.py).',
            'etiketler': ['AYNI_GELISME', 'YENI_GELISME',
                          'AYNI_AKTOR_FARKLI_OLAY', 'ILISKISIZ'],
            'ciftler': tum,
        }, f, ensure_ascii=False, indent=2)

    print(f'✅ {CIKTI}: {len(tum)} çift '
          f'({len(ciftler)} yenilendi, {len(korunan)} korundu)')
    dagitim = {}
    for c in tum:
        dagitim[c['iliski']] = dagitim.get(c['iliski'], 0) + 1
    for k, v in sorted(dagitim.items()):
        print(f'   {k:<24} {v}')
    if atlanan:
        print(f'\nℹ️  {len(atlanan)} ham kaynaklı vaka BUGÜN üretilemez '
              f'(mevcut kayıtları korundu):')
        for ad, neden in atlanan:
            print(f'   • {ad} → {neden}')
    if eksik:
        print(f'\n⚠️  {len(eksik)} vaka doldurulamadı:')
        for ad, neden in eksik:
            print(f'   • {ad} → {neden}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
