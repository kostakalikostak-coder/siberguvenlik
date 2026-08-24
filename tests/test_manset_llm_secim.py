"""KRİTİK 3'Ü LLM SEÇER — sözleşme ve güvenlik ağları.

NEDEN VAR: manşet bugüne dek DETERMİNİSTİK seçiliyordu (en yüksek puanlı üç
uygun haber) ve LLM yalnızca sonradan İTİRAZ ediyordu. Puan rubriği kaba bir
vekildir ve tekrar tekrar yanlış manşet üretti:
  • 2026-08-19 yamalanmış Apple açığı 94 puanla manşet oldu.
  • 2026-08-21 satıcının kapattığı Entra ID açığı (95), süregelen bir
    casusluk kampanyasının (94) yerine manşete çıkarıldı.
  • 2026-08-24 araştırmacıların NASA yazılımında bulduğu açık (91) manşet
    oldu — saldırı, saldırgan, kurban yok.

GÜVENLİK: kısa liste KODDA süzülür; LLM listeden seçer, liste dışına çıkamaz
ve geçersiz cevapta deterministik seçim korunur.
"""
import main


def _sistem(yanit):
    s = main.HaberSistemi.__new__(main.HaberSistemi)
    s._gemini_call_json = lambda *a, **k: yanit
    s._manset_izi = []
    return s


def _veri(n=6):
    records = {i: {'kat': 'nation_state_apt', 'toplam': 100 - i}
               for i in range(1, n + 1)}
    content = {i: {'tr_title': f'Haber {i}', 'paragraph': f'Paragraf {i}.'}
               for i in records}
    arts = {i: {'id': i, 'title': f'T{i}', 'full_text': ''} for i in records}
    return records, content, arts


def test_llm_secimi_uygulanir():
    records, content, arts = _veri()
    s = _sistem({'secim': [4, 5, 6],
                 'gerekce': [{'id': 4, 'neden': 'süren kampanya'}]})
    secim = s._manset_llm_sec([1, 2, 3, 4, 5, 6], [1, 2, 3],
                              records, content, arts, [])
    assert secim == [4, 5, 6], 'LLM seçimi uygulanmadı'
    assert any(iz['katman'] == 'manset_llm_secim' for iz in s._manset_izi), \
        'seçim karar izine yazılmadı'


def test_liste_disi_id_reddedilir():
    """LLM aday listesinde OLMAYAN bir id döndüremez — o id mükerrer,
    yanlış kategori ya da defterin yasakladığı bir haber olabilir."""
    records, content, arts = _veri()
    s = _sistem({'secim': [4, 5, 99]})
    secim = s._manset_llm_sec([1, 2, 3, 4, 5, 6], [1, 2, 3],
                              records, content, arts, [])
    assert secim == [1, 2, 3], 'liste dışı id kabul edildi'


def test_eksik_secim_deterministige_duser():
    records, content, arts = _veri()
    for yanit in ({'secim': [4, 5]}, {'secim': []}, {}, None, {'secim': 'x'}):
        s = _sistem(yanit)
        assert s._manset_llm_sec([1, 2, 3, 4, 5, 6], [1, 2, 3],
                                 records, content, arts, []) == [1, 2, 3], \
            f'geçersiz yanıtta deterministik seçim korunmadı: {yanit}'


def test_mukerrer_secim_reddedilir():
    """Aynı id iki kez → 3 farklı manşet yok → deterministiğe düş."""
    records, content, arts = _veri()
    s = _sistem({'secim': [4, 4, 5]})
    assert s._manset_llm_sec([1, 2, 3, 4, 5, 6], [1, 2, 3],
                             records, content, arts, []) == [1, 2, 3]


def test_aday_azsa_llm_cagrilmaz():
    """3 veya daha az aday varsa seçilecek bir şey yok — maliyet harcanmaz."""
    records, content, arts = _veri(3)
    cagri = []
    s = _sistem({'secim': [1, 2, 3]})
    s._gemini_call_json = lambda *a, **k: cagri.append(1) or {'secim': [1, 2, 3]}
    assert s._manset_llm_sec([1, 2, 3], [1, 2, 3],
                             records, content, arts, []) == [1, 2, 3]
    assert not cagri, 'gereksiz LLM çağrısı yapıldı'


def test_secici_kisa_listeyi_kodda_suzer():
    """LLM'e yalnızca manşete UYGUN adaylar gitmeli — kapı kararları
    seçicinin ÜSTÜNDEDİR."""
    import inspect
    kaynak = inspect.getsource(main.HaberSistemi._derive_top3_by_score)
    i_kapi = kaynak.index('manset_yasak')
    i_llm = kaynak.index('_manset_llm_sec(')
    assert i_kapi < i_llm, 'LLM seçimi manşet kapısından ÖNCE çalışıyor'
    assert 'eligible' in kaynak.split('kisa_liste')[0][-3000:], \
        'kısa liste süzülmüş havuzdan kurulmuyor'


def test_kisa_liste_capraz_gun_temiz():
    """LLM seçimi, SONRA elenecek adaylar arasından yapılmamalı.

    ÖLÇÜLDÜ (2026-08-24): LLM 19, 4 ve 18'i seçti; üçü de sonraki çapraz-gün
    katmanlarında mükerrer çıkıp mekanik yedeklerle değiştirildi ve manşet
    74-76 puanlık zayıf haberlere düştü. Seçimin bir anlamı olması için kısa
    liste elenmeyecek adaylardan kurulmalı.
    """
    import inspect
    kaynak = inspect.getsource(main.HaberSistemi._derive_top3_by_score)
    parca = kaynak.split('kisa_liste')[0][-1200:]
    assert 'mukerrer_karari(' in parca, \
        'kısa liste çapraz-gün mükerrerlerinden arındırılmıyor'
    assert '_aday or list(top3_ids)' in kaynak, \
        'tüm adaylar elenirse deterministik seçime düşülmüyor'


def test_yedek_bulucu_manset_yasagina_uyar():
    """Manşet yasağı YEDEK seçiminde de geçerli olmalı.

    ÖLÇÜLDÜ (2026-08-24): İran/Birleşik Krallık enerji santrali haberi
    manşet oldu — aynı olay 08-23'te de manşetti ve GELISME olarak yasak
    almıştı. Yasak yalnızca ana kapıda uygulanıyordu; bir çapraz-gün elemesi
    tetiklenince yasaklı haber YEDEK olarak manşete geri geldi.
    """
    import inspect
    kaynak = inspect.getsource(main.HaberSistemi._kritik3_yedek_bul)
    assert '_manset_yasak' in kaynak, 'yedek bulucu manşet yasağına bakmıyor'
    assert 'mukerrer_karari(' in kaynak, \
        'yedek bulucu geçmişi eski karşılaştırıcıyla ölçüyor'


def test_hakem_secimden_once_de_kosar():
    """Seçicinin göremediği bir eleyici seçimden SONRA çalışmamalı.

    ÖLÇÜLDÜ (2026-08-24): LLM Myanmar/CoolClient casusluk haberini manşet
    seçti; SON kapıdaki LLM HAKEMİ onu çapraz-gün mükerreri diye eledi ve
    manşet, yerine geçen CISA günlükleme kılavuzuna düştü. Deterministik
    temizlik yetmiyor — hakem de kısa listeye seçimden ÖNCE bakmalı.
    """
    import inspect
    kaynak = inspect.getsource(main.HaberSistemi._derive_top3_by_score)
    on = kaynak.split('kisa_liste = ')[0]
    assert '_mukerrer_llm_hakem(' in on, \
        'hakem manşet seçiminden önce çalışmıyor'
