"""TEK TANIM — `olay_iliski.ayni_olay` sözleşmesi.

Sistemde "aynı olay"ın ÜÇ ayrı tanımı vardı (same_event, dört değerli
iliski_belirle, LLM) ve farklı katmanlar farklı tanımı kullanıyordu; bu
yüzden "denetim kaçak buldu ama politika bulmadı" mümkündü. Bu dosya tek
tanımın davranışını kilitler.

Politika (kullanıcı kararı, 2026-08-24): aynı olay bir kez yayımlanır.
"Yeni gelişme" olması onu mükerrer olmaktan ÇIKARMAZ.
"""
import json
import os

import pytest

from src import dedup, olay_iliski as O

GOLDEN = 'data/mukerrer_golden.json'
GECMIS = 'data/rapor_gecmis.json'


def _v(tr, para=''):
    return {'tr_title': tr, 'title': '', 'paragraph': para, 'full_text': ''}


def test_kurum_ve_arastirmaci_adlari_kod_adi_degildir():
    """ANSSI, watchTowr gibi adlar olayı DUYURAN taraftır, olayın öznesi değil.

    ÖLÇÜLDÜ: 38 etiketli çiftin 7'si tam olarak buradan sahte eşleşiyordu —
    'kod:anssi' dört ayrı CERT-FR ürün bültenini (Adobe, Zimbra, Microsoft,
    Oracle) birbirine bağladı.
    """
    for ad in ('anssi', 'watchtowr', 'kaspersky', 'mandiant', 'europol'):
        assert ad in dedup.CODENAME_DENYLIST, f'{ad} kod adı sayılıyor'


def test_farkli_cert_bultenleri_ayni_olay_degil():
    a = _v('Adobe Ürünlerinde Tespit Edilen Güvenlik Açığının Giderilmesi',
           'ANSSI, Adobe ürünlerinde çok sayıda güvenlik açığı bildirmiştir.')
    b = _v('Oracle MySQL Ürünlerinde Çok Sayıda Güvenlik Açığının Belirlenmesi',
           'ANSSI, Oracle MySQL ürünlerinde güvenlik açıkları bildirmiştir.')
    assert not O.ayni_olay(a, b), 'iki ayrı ürün bülteni aynı olay sayıldı'


def test_jenerik_ad_tek_basina_ayni_olay_yapmaz():
    a = _v("GitLab'daki Kritik GraphQL Zafiyetinin Acil Yamayla Giderilmesi",
           'GitLab, GraphQL bileşenindeki CVSS 9.8 puanlı açığı yamalamıştır.')
    b = _v('Citrix NetScaler Cihazlarında Kritik Kimlik Doğrulama Atlatma Açığı',
           'Citrix, NetScaler ürününde CVSS 9.2 puanlı açığı duyurmuştur.')
    assert not O.ayni_olay(a, b), "'cvss' ortaklığı aynı olay sayıldı"


def test_sirket_adi_kod_adi_olarak_iki_olayi_birlestirmez():
    """Kod adı çıkarıcı şirket/platform adlarını da yakalıyor; konu kapısı
    olmadan TikTok senatör baskısı ile TikTok gizlilik davası birleşiyordu."""
    a = _v("ABD'li Senatörlerin TikTok'a Güvenlik Özellikleri Hakkında Baskı "
           'Yapması',
           'Senatörler TikTok yönetiminden ebeveyn denetimi özellikleri '
           'talep etmiştir.')
    b = _v("TikTok'un Çocuk Gizliliği Davasında 400 Milyon Dolar Ödemesi",
           'TikTok, çocuk gizliliği ihlali davasında uzlaşma ödemesi '
           'yapacaktır.')
    assert not O.ayni_olay(a, b), 'iki ayrı TikTok olayı birleştirildi'


def test_baslik_benzerligi_tek_basina_yetmez():
    a = _v('Stripe API Anahtarlarının Halka Açık Kodlarda İfşa Olması',
           'Araştırmacılar halka açık depolarda Stripe API anahtarları '
           'bulmuştur.')
    b = _v('Binlerce AWS Erişim Anahtarının Halka Açık Kaynaklarda İfşa Olması',
           'Araştırmacılar halka açık kaynaklarda AWS erişim anahtarları '
           'bulmuştur.')
    assert not O.ayni_olay(a, b), 'benzer başlık kalıbı aynı olay sayıldı'


def test_yeni_gelisme_de_mukerrerdir():
    """Politika: aynı olay bir kez yayımlanır."""
    a = _v('Dahua Cihazlarına Yönelik Operation CameraSwarm Siber Saldırı '
           'Kampanyası',
           'Operation CameraSwarm kapsamında Dahua kameraları hedef '
           'alınmıştır.')
    b = _v('Operation CameraSwarm Kapsamında 14.000 IP Kameranın Ele '
           'Geçirilmesi',
           'Operation CameraSwarm kampanyasında 14.000 Dahua kamerası ele '
           'geçirilmiştir.')
    assert O.ayni_olay(a, b), 'aynı kampanyanın yeni gelişmesi mükerrer sayılmadı'


@pytest.mark.skipif(not (os.path.exists(GOLDEN) and os.path.exists(GECMIS)),
                    reason='referans veya geçmiş dosyası yok')
def test_etiketli_referansta_gerileme_yok():
    """data/mukerrer_golden.json — 38 elle etiketli çift.

    TABAN 38: ayni_olay bu referansta kaçan=0, sahte=0 ile ölçüldü. Düşerse
    bir değişiklik mükerrer politikasını GERİLETMİŞTİR.
    """
    TABAN = 38
    with open(GECMIS, encoding='utf-8') as f:
        rapor = {r['date']: r.get('views', []) or [] for r in json.load(f)}
    with open(GOLDEN, encoding='utf-8') as f:
        altin = json.load(f)['ciftler']
    indeks = {(g, (v.get('tr_title') or '')): v
              for g, vs in rapor.items() for v in vs}
    sz = O.OlaySozlugu([v for vs in rapor.values() for v in vs])
    dogru = degerlendirilen = 0
    for c in altin:
        a = indeks.get((c['gun_a'], c['baslik_a']))
        b = indeks.get((c['gun_b'], c['baslik_b']))
        if a is None or b is None:
            continue          # geçmiş penceresinden düşmüş çift
        degerlendirilen += 1
        if bool(O.ayni_olay(b, a, sozluk=sz)) == c['mukerrer']:
            dogru += 1
    if degerlendirilen < TABAN:
        pytest.skip(f'referans çiftlerinin yalnızca {degerlendirilen}/{TABAN} '
                    f'tanesi geçmişte duruyor')
    assert dogru >= TABAN, f'gerileme: {dogru}/{degerlendirilen} (taban {TABAN})'


def test_ayni_aktor_farkli_operasyon_mukerrer_degildir():
    """Aktör olayın FAİLİDİR, kimliği değil — aynı grup farklı olaylar yapar.

    Bellek 30 güne çıkınca risk büyüdü: aynı APT 30 gün içinde birçok ayrı
    operasyonla görünüyor. ÖLÇÜLDÜ (2026-08-24): "Mustang Panda'nın
    CoolClient'ı çekirdek rootkit'le güncellemesi" ile "Mustang Panda'nın
    QuickFox üzerinden tedarik zinciri saldırısı" 'actor:mustangpanda+
    topic=0.19' ile eşleşti — apayrı iki operasyon.
    """
    a = _v("Mustang Panda'nın CoolClient Arka Kapısını Çekirdek Rootkit'iyle "
           'Güncellemesi',
           'Mustang Panda, CoolClient arka kapısını imzalı bir çekirdek '
           'sürücüsüyle güncellemiş, Pakistan ve Moğolistan kamu kurumlarını '
           'hedef almıştır.')
    b = _v("Mustang Panda'nın QuickFox Üzerinden Tedarik Zinciri Saldırısı "
           'Düzenlemesi',
           'Mustang Panda, QuickFox VPN uygulamasının Windows yükleyicisini '
           'değiştirerek FDMTP arka kapısını yerleştirmiştir.')
    assert not O.ayni_olay(a, b), 'aynı aktörün farklı operasyonu birleştirildi'


def test_ayni_kod_adi_farkli_takma_adla_da_yakalanir():
    """HoneyMyte = Mustang Panda. Aktör adı değişse de OLAY aynıdır.

    Bu çift 10 gün arayla yayımlandı; 7 günlük bellekte hiç görülemiyordu.
    """
    a = _v('HoneyMyte Grubunun CoolClient Arka Kapısını Rootkit İle '
           'Güncellemesi',
           'HoneyMyte, CoolClient arka kapısını Windows çekirdek düzeyinde '
           'çalışan bir rootkit ile güncelleyerek casusluk operasyonlarını '
           'genişletmiştir.')
    b = _v("Mustang Panda'nın CoolClient Arka Kapısını Çekirdek Rootkit'iyle "
           'Güncellemesi',
           'Mustang Panda, CoolClient arka kapısını süreçleri gizleyen imzalı '
           'bir çekirdek sürücüsüyle güncellemiştir.')
    assert O.ayni_olay(a, b), 'takma ad değişince aynı olay kaçırıldı'


def test_cve_eslesmesi_aktor_kapisindan_muaf():
    """'actor:cve...' bir aktör değil, yapısal zafiyet kimliğidir."""
    from src import olay_iliski as _O
    assert _O.AKTOR_KONU_MIN > 0
    import inspect
    kaynak = inspect.getsource(_O.ayni_olay)
    assert "not gerekce.startswith('actor:cve')" in kaynak, \
        'CVE eşleşmesi aktör konu kapısına takılıyor'
