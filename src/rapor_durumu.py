"""RAPOR DURUMU — raporun TEK doğruluk kaynağı ve değişmez (invariant) bekçisi.

NEDEN VAR — ölçülmüş kök neden
────────────────────────────────
Rapor, `main.create_html` içinde ~745 satır boyunca ÜÇ DÜZ LİSTENİN
(`top3_ids`, `top10_ids`, `remaining_ids`) elle mutasyona uğratılmasıyla
oluşuyordu: manşeti değiştiren 7, gövdeyi değiştiren 13 ayrı nokta. Bu
katmanların paylaştığı kurallar YALNIZCA YORUM SATIRLARINDA yazıyordu; 8268
satırlık `main.py`'de tek bir `assert` yoktu. Sonuç: her yeni katman
kurallardan birini SESSİZCE çiğneyebiliyordu ve tek dedektör raporu okuyan
insandı.

ÖLÇÜLDÜ — yayın yönetmeni katmanı eklendikten sonra ÜÇ GÜNDE ÜÇ FARKLI kural
çiğnendi ve üçü de ancak kullanıcı fark edince görüldü:
  • 2026-08-19 — aynı haber gövdede İKİ KEZ (28 girdi / 26 benzersiz).
  • 2026-08-20 — SilkParasite (94 puan) rapordan sessizce DÜŞTÜ.
  • 2026-08-21 — defterin manşete yasakladığı haber manşete ÇIKARILDI
    (üst üste iki gün aynı manşet).

ÖLÇÜLDÜ — mevcut denetimin kör noktası: `arz` muhasebesinde nedeni
kaydedilmemiş her eleme `diger` kovasına yazılıyordu, yani defter haber
kaybolduğu gün bile TUTUYORDU. Her gün 4-7 puanlı siber haber boru hattından
KAYITLI HİÇBİR NEDEN OLMADAN çıkıyor; çoğu doğru karar (çapraz-gün elemesi),
birkaçı gerçek kayıp — ve sistem ikisini AYIRT EDEMİYORDU.

NE YAPAR
────────
Katmanlar listeleri değiştirmeye devam eder; her katmandan sonra `senkronla()`
çağrılır. Bu metot ne değiştiğini ÇIKARIR, her ayrılan haber için bir NEDEN
arar ve dört değişmezi denetler:

  D1  Bir haber manşette en fazla bir kez, gövdede en fazla bir kez görünür.
  D2  Rapordan ayrılan her haberin KAYITLI bir nedeni vardır.
  D3  Manşet kapısının yasakladığı haber manşette olamaz.

⚠️ MANŞETİN GÖVDE LİSTESİNDE BULUNMASI İHLAL DEĞİLDİR. Bu kod tabanında
`top3_ids`, `top10_ids`in ALT KÜMESİDİR (`_derive_top3_by_score` havuzdan
seçer, havuzdan ÇIKARMAZ) ve gövdeyi çizen `_build_html` manşeti kendisi
dışlar. Bunu ihlal saymak her koşuda yanlış alarm üretir; asıl arıza,
2026-08-19'da olduğu gibi bir haberin GÖVDE LİSTESİNDE İKİ KEZ bulunmasıdır.

İhlal bulunca SESSİZ KALMAZ: onarılabilir olanı onarır (mükerrer girdi
tekilleştirilir, nedensiz düşen haber gövdeye geri alınır), hepsini
`ihlaller` listesine yazar ve ekrana basar. Liste `kalite_denetim.jsonl`'e
girer; testler boş olmasını bekler.

TASARIM KARARI — onarım, koşuyu düşürmekten iyidir. `raise` üretimde raporu
tamamen öldürürdü; oysa bu ihlallerin çoğu YEREL ve onarılabilir. Amaç
insan müdahalesini sıfırlamak: koşu kendi hatasını görür, düzeltir ve
bildirir.
"""


# Onarılabilir ihlal türleri (onarım DAVRANIŞI değiştirir — kayıt şart).
MUKERRER_GIRDI = 'mukerrer_girdi'
NEDENSIZ_KAYIP = 'nedensiz_kayip'
# Onarılamaz (yerine geçecek haberi seçmek bu katmanın işi değil) — bildirilir.
YASAKLI_MANSET = 'yasakli_manset'


class RaporDurumu:
    """Raporun manşet/gövde durumu + her haberin akıbet defteri.

    manset : KRİTİK 3 id listesi
    top10  : gövdenin üst bölümü (Önemli Gelişmeler)
    kalan  : gövdenin geri kalanı
    Üçü de main.py'deki aynı adlı listelerin AYNASIDIR; bölünme korunur çünkü
    bölünmeyi yeniden türetmek 2026-08-19'da mükerrer gövde üretmişti.
    """

    def __init__(self, manset, top10, kalan, manset_yasak=None, yazdir=None):
        self.manset = list(manset)
        self.top10 = list(top10)
        self.kalan = list(kalan)
        self.manset_yasak = set(manset_yasak or ())
        self.ihlaller = []
        # {id: {'akibet': 'manset'|'govde'|'elenen', 'katman': str, 'neden': str}}
        self.karar = {}
        self._yazdir = yazdir or (lambda s: print(s))
        for aid in self._tumu():
            self.karar[aid] = {'akibet': 'havuz', 'katman': 'siralama',
                               'neden': ''}

    # ── iç yardımcılar ───────────────────────────────────────────────────
    def _tumu(self):
        return list(self.manset) + list(self.top10) + list(self.kalan)

    def _kaydet(self, tur, katman, aid, ayrinti):
        kayit = {'tur': tur, 'katman': katman, 'id': aid, 'ayrinti': ayrinti}
        self.ihlaller.append(kayit)
        self._yazdir(f"   🚨 DEĞİŞMEZ İHLALİ [{katman}] {tur}: ID {aid} — {ayrinti}")
        return kayit

    @staticmethod
    def _tekille(ids, gorulen):
        """Sıra koruyarak, `gorulen` kümesinde olmayanları alır."""
        out = []
        for aid in ids:
            if aid in gorulen:
                continue
            gorulen.add(aid)
            out.append(aid)
        return out

    # ── ana giriş ────────────────────────────────────────────────────────
    def senkronla(self, katman, manset, top10, kalan, nedenler=None):
        """Bir katmandan SONRA çağrılır. Onarılmış (manset, top10, kalan) döner.

        katman   : değişikliği yapan katmanın adı (kayıt için)
        nedenler : {id: neden} — o katmanın elediği haberlerin gerekçeleri
                   (main.py'deki `eleme_nedeni` sözlüğü doğrudan verilebilir)
        """
        nedenler = nedenler or {}
        onceki = set(self._tumu())

        yeni_manset = list(manset)
        yeni_top10 = list(top10)
        yeni_kalan = list(kalan)

        # ── D1: tekillik. Manşet ve gövde AYRI AYRI tekilleştirilir; ikisinin
        #    kesişmesi ihlal DEĞİLDİR (bkz. modül başlığı).
        yeni_manset = self._tekille(yeni_manset, set())
        gorulen, temiz = set(), {'top10': [], 'kalan': []}
        for ad, dizi in (('top10', yeni_top10), ('kalan', yeni_kalan)):
            for aid in dizi:
                if aid in gorulen:
                    self._kaydet(MUKERRER_GIRDI, katman, aid,
                                 'gövde listesinde ikinci kez — '
                                 'tekilleştirildi')
                    continue
                gorulen.add(aid)
                temiz[ad].append(aid)
        yeni_top10, yeni_kalan = temiz['top10'], temiz['kalan']

        # ── D2: ayrılan her haberin kayıtlı bir nedeni olmalı.
        simdi = set(yeni_manset) | set(yeni_top10) | set(yeni_kalan)
        for aid in sorted(onceki - simdi):
            neden = (nedenler.get(aid) or '').strip()
            if neden:
                self.karar[aid] = {'akibet': 'elenen', 'katman': katman,
                                   'neden': neden}
                continue
            # NEDENSİZ DÜŞÜŞ — 2026-08-20'de SilkParasite böyle kayboldu.
            # Onarım: gövdenin BAŞINA geri alınır (manşetten düşen haber
            # puanca gövdenin en güçlüsüdür; sona atmak sıralamayı bozar).
            self._kaydet(NEDENSIZ_KAYIP, katman, aid,
                         'rapordan kayıtlı neden olmadan düştü — gövdeye '
                         'geri alındı')
            yeni_top10.insert(0, aid)
            simdi.add(aid)
            self.karar[aid] = {'akibet': 'govde', 'katman': katman,
                               'neden': 'nedensiz kayıp onarıldı'}

        # ── D3: kapının yasakladığı haber manşette olamaz.
        for aid in yeni_manset:
            if aid in self.manset_yasak:
                self._kaydet(YASAKLI_MANSET, katman, aid,
                             'manşet kapısı bu haberi düşürmüştü — katman '
                             'kararı EZDİ')

        for aid in yeni_manset:
            self.karar[aid] = {'akibet': 'manset', 'katman': katman,
                               'neden': self.karar.get(aid, {}).get('neden', '')}
        for aid in yeni_top10 + yeni_kalan:
            if self.karar.get(aid, {}).get('akibet') != 'govde':
                self.karar[aid] = {'akibet': 'govde', 'katman': katman,
                                   'neden': self.karar.get(aid, {}).get('neden', '')}

        self.manset, self.top10, self.kalan = yeni_manset, yeni_top10, yeni_kalan
        return list(self.manset), list(self.top10), list(self.kalan)

    # ── raporlama ────────────────────────────────────────────────────────
    def nedensiz_cikanlar(self, aday_ids):
        """Havuzda olup rapora girmeyen ve AKIBETİ KAYITLI OLMAYAN id'ler.

        `diger` kovasının yerini alır: eskiden nedeni bilinmeyen her eleme
        bu kovaya yazılıyor ve muhasebe TUTUYORDU — haber kaybolduğu gün bile.
        Artık nedensiz çıkış ayrı ve görünür bir alarmdır.
        """
        rapor = set(self._tumu())
        out = []
        for aid in aday_ids:
            if aid in rapor:
                continue
            k = self.karar.get(aid)
            if not k or k.get('akibet') != 'elenen' or not k.get('neden'):
                out.append(aid)
        return out

    def ozet(self):
        """kalite_denetim.jsonl'e yazılacak kayıt."""
        tur_sayisi = {}
        for i in self.ihlaller:
            tur_sayisi[i['tur']] = tur_sayisi.get(i['tur'], 0) + 1
        return {'ihlal_sayisi': len(self.ihlaller),
                'ihlal_turleri': tur_sayisi,
                'ihlaller': self.ihlaller[:20]}
