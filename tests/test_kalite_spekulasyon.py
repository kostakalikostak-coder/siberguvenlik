"""Pass 5 kalite filtresi — spekülasyon yazıları rapora girmemeli.

ÖLÇÜLDÜ (2026-09-01): "Is Someone Hacking DoD Refrigerators?" başlıklı yazı,
ABD Savunma Bakanlığı kantinlerindeki soğutma arızalarının siber saldırı
OLABİLECEĞİNİ tartışıyordu; doğrulanmış hiçbir kurban, fail ya da olay
bildirmiyordu. 55 puanla ve `stratejik_kurum_saldirisi` etiketiyle rapora
girdi — hem kalite filtresi hem yayın yönetmeni geçirmişti.

Kural DAR tutulur: soru biçimli başlık tek başına eleme sebebi değildir ve
faili bilinmeyen GERÇEK bir saldırı spekülasyon sayılmaz.
"""
from src.config import get_quality_review_prompt


def _prompt():
    return get_quality_review_prompt('=== HABER ID: 1 ===\n')


def test_spekulasyon_kurali_kriter_disi_kontrolunde():
    p = _prompt()
    i_k3 = p.index('KONTROL 3')
    i_k4 = p.index('KONTROL 4')
    assert 'SPEKÜLASYON' in p[i_k3:i_k4], \
        'spekülasyon kuralı KRİTER DIŞI kontrolünde değil'


def test_kural_kaldirma_yolunda():
    """KONTROL 3 kaldırma kontrolüdür — kural regenerate'e değil remove'a bağlı."""
    p = _prompt()
    i_k3 = p.index('KONTROL 3')
    assert '"remove"' in p[:i_k3 + 400], 'KONTROL 3 remove listesine bağlı değil'


def test_asiri_elemeye_karsi_karsi_ornek_var():
    """Kural, faili bilinmeyen gerçek saldırıları ve süren soruşturmaları
    elemeyecek şekilde sınırlandırılmış olmalı."""
    p = _prompt()
    assert 'başlığın soru biçiminde olması TEK BAŞINA yeterli değildir' in p, \
        'soru başlığı tek başına eleme sebebi sayılıyor'
    for parca in ('SÜREN soruşturması', 'failin bilinmemesi olayı spekülasyon yapmaz'):
        assert parca in p, f'karşı örnek eksik: {parca}'
