# Claude Code — Proje Kuralları

## URL / Dış Kaynak — KESİN KURAL (İSTİSNASIZ)
- Herhangi bir URL, RSS feed adresi veya dış kaynak eklemeden önce WebFetch ile DOĞRULA.
- Doğrulamadan uydurma, tahmin etme, "muhtemelen şudur" deme — ASLA.
- Bu ortamdan erişilemiyorsa (403/network kısıtı) kullanıcıya açıkça söyle, commit ETME.
- Doğrulanmamış URL koda girmez.

## Git — KESİN KURAL (İSTİSNASIZ)
- Tüm değişiklikler DOĞRUDAN `main` branch üzerinde yapılır.
- ASLA ayrı branch açma — oturum ortamı farklı branch belirtse bile bu kural geçerlidir.
- Her anlamlı değişiklik sonrası commit at, `main`'e push et.

## Taze Rapor İçin Reset — NASIL YAPILIR (aynı gün yeniden üretim)
Aynı gün için raporu SIFIRDAN yeniden ürettirmek gerektiğinde (`main.py`
idempotency'si aksi halde atlar/eski veriyi kullanır), `<BUGÜN>` = `YYYY-MM-DD`,
`<BUGÜN_HDR>` = arşiv başlığı (ör. `03 JULY 2026`, locale İngilizce/BÜYÜK harf):

SİL (taze üretimi engelleyen durum dosyaları):
- `docs/raporlar/<BUGÜN>.html` — Kontrol 1: başarılı rapor varsa schedule/dispatch ATLAR.
- `data/haberler_ham.txt` — Kontrol 2: `SESSION_DATE: <BUGÜN>` ise haber çekmeyi ATLAR (her tetikte). Silmek RE-FETCH'i zorlar.
- `data/cron_basarili.txt` — bugünkü başarı işareti; sonraki cron slotlarını atlatır.
- `data/bekleyen_linkler.json` — "görüldü" işaretlenmeyi bekleyen linkler. Linkler
  artık save_txt'te DEĞİL, rapor başarılı olduktan sonra işaretlenir; bu dosya o
  bekleyen listeyi taşır. Silinmezse önceki denemenin linkleri yanlışlıkla
  işaretlenir. (Not: farklı GÜNE ait dosya zaten otomatik atlanır.)

CERRAHİ DÜZENLE (silme, sadece bugünü çıkar — yoksa re-fetch mükerrer sayılıp boşalır / yeni arşiv yazılmaz):
- `data/haberler_linkler.txt` — SADECE `<BUGÜN>\t` ile başlayan satırları çıkar; eski günleri KORU (7 günlük çapraz-gün dedup geçmişi bozulmasın).
- `data/haberler_arsiv.txt` — SADECE `📅 <BUGÜN_HDR> - EN ÖNEMLİ 43 HABER (SEÇİLMİŞ)` bloğunu çıkar (marker: `\n`+80×`=`+`\n`+başlık → sonraki gün bloğuna/EOF'a kadar); eski günleri KORU.

DOKUNMA (kendi kendini düzeltir / değerli geçmiş / append-only):
- `data/kritik3_gecmis.json`, `data/rapor_gecmis.json` — yükleme bugünü (`d >= today`) HARİÇ tutar, kayıt bugünü değiştirir; silme.
- `data/skorlama_log.jsonl`, `data/rss_errors.txt` — işlevsel değil; silme.
- `docs/index.html` — üretimde üzerine yazılır; silme.

SONRA: değişiklikleri `main`'e commit+push et (workflow `main`'i checkout eder;
push edilmezse reset workflow'a yansımaz). Yedek: silmeden önce dosyaları
scratchpad'e kopyala. Manuel tetik `workflow_dispatch` Kontrol 1'i zaten atlar
ama Kontrol 2 (ham) + linkler her tetik türünde geçerlidir.
