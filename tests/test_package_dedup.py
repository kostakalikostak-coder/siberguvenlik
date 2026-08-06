"""Açık kaynak paket adı sinyali — çapraz-gün KRİTİK 3 mükerrer koruması.

2026-08-06 vakası: keyv/cacheable npm solucanı 05-08'de KRİTİK 3 manşetiydi ve
06-08'de YENİDEN manşet oldu. Koruma (pick_distinct + exclude_views=recent_k3)
bağlıydı, ama same_event(cross_day=True) False döndü: codename/actor/entity
kümelerinin ÜÇÜ DE boştu.

Sebep yapısal ve bir haber sınıfının tamamını kapsıyordu: extract_codenames
CamelCase ya da TÜMÜ-BÜYÜK (≥5 harf) arar; paket ekosistemlerinde ad kuralı
KÜÇÜK HARF ve çoğu kez kısadır ('keyv' 4 harf). Yani tedarik zinciri
olaylarının en ayırt edici kimliği dedup'a tümüyle görünmezdi — tam da
günlerce sürüp tekrar tekrar manşet olan sınıf.

İlk denemede kural gürültülüydü: [a-z] sınıfı Türkçe harfleri kapsamadığı için
"solucanı"dan 'olucan', "saldırı"dan 'sald' gibi sahte token'lar çıkıyordu ve
rapor geçmişinde 9 eşleşmenin 8'i yanlış pozitifti. Türkçe harf sınırı +
ayrı konu eşiği (0.22) eklendikten sonra ölçüm: 252 kritik3 çifti ve 8008
rapor çiftinde YALNIZCA doğru eşleşme kaldı, 0 yanlış pozitif.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src import dedup


# ── Gerçek vaka: data/kritik3_gecmis.json ve rapor_gecmis.json kayıtlarının
# BİREBİR kopyası (tests/fixtures/keyv_npm_kritik3.json).
# Kısaltılmış/elle yazılmış metin kullanmak testi yanıltır: konu örtüşmesi
# paragraph VE full_text üzerinden ölçülür, üretimde full_text 1500 karakter
# saklanır. Kısa fixture eşiğin altında kalıp yanlış "geçti/kaldı" verir.
_FIX = json.loads(
    (Path(__file__).parent / 'fixtures' / 'keyv_npm_kritik3.json')
    .read_text(encoding='utf-8'))

KEYV_0805 = _FIX['keyv_0805']   # 05-08 KRİTİK 3 manşeti
KEYV_0806 = _FIX['keyv_0806']   # 06-08 KRİTİK 3 manşeti — aynı olay
ARCH_LINUX = _FIX['arch_linux']  # aynı ekosistem, FARKLI olay


def test_kucuk_harfli_paket_adi_cikarilir():
    got = dedup.extract_package_names(
        'npm paketlerinde keyv ve cacheable solucanı saldırısı yayıldı')
    assert 'keyv' in got and 'cacheable' in got


def test_turkce_sozcuk_ortasindan_sahte_token_cikmaz():
    """Regresyon: "solucanı"→'olucan', "saldırı"→'sald' üretiliyordu."""
    got = dedup.extract_package_names(
        'npm paketlerine yönelik solucanı saldırısı geliştiricileri hedefledi')
    for sahte in ('olucan', 'sald', 'geli', 'ararl', 'rganlar'):
        assert sahte not in got, f'Türkçe sözcük parçası token oldu: {sahte}'


def test_ekosistem_baglami_yoksa_calismaz():
    """Bağlam kapısı: paket ekosistemi geçmiyorsa kural hiç devreye girmez."""
    assert dedup.extract_package_names(
        'Fidye yazılımı bir hastanenin sunucularını şifreledi') == set()


def test_capraz_gun_ayni_paket_olayi_yakalanir():
    """Asıl vaka: 05-08 manşeti ile 06-08 manşeti aynı olaydır."""
    ok, why = dedup.same_event(KEYV_0806, KEYV_0805, explain=True, cross_day=True)
    assert ok, f'çapraz-gün mükerrer yakalanmadı ({why})'
    assert 'keyv' in why


def test_ayni_ekosistem_farkli_olay_birlesmez():
    """Yanlış-pozitif koruması: ikisi de paket ekosistemi haberi ama farklı olay."""
    assert not dedup.same_event(KEYV_0806, ARCH_LINUX, cross_day=True)


def test_paket_kurali_konu_kapisina_tabi():
    """Ortak paket adı TEK BAŞINA yetmez — konu örtüşmesi de gerekir."""
    a = {'tr_title': 'npm paketi lodash için yeni sürüm yayımlandı',
         'title': '', 'paragraph': 'npm paketi lodash guncellendi.', 'full_text': ''}
    b = {'tr_title': 'Fidye yazılımı saldırısı bir limanı durdurdu',
         'title': '', 'paragraph': 'Liman operasyonlari durdu.', 'full_text': ''}
    assert not dedup.same_event(a, b, cross_day=True)
