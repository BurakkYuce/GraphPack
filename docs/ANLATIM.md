# Bu proje ne yapıyor? (teknik olmayan anlatım)

## Tek cümle

Yapay zekâyla belge okuyup "kim kiminle bağlantılı" haritası çıkaran hazır bir
sistem var; biz onu **her yeni alana kod yazmadan, sadece ayar dosyasıyla**
uyarlanabilir hale getirdik — ve bunun gerçekten olduğunu ölçtük.

---

## Çözülen problem

Bir dedektif panosu düşün: duvarda fotoğraflar, aralarında iplerle çizilmiş
bağlantılar. "Bu kişi şu şirkette çalışıyor", "bu karar şu kanuna dayanıyor",
"bu program şu kütüphaneye muhtaç". Bilgisayarda bunun adı **bilgi grafiği**.

Böyle bir pano kurmanın faydası şu: normal arama motoru "şu kelimeyi içeren
belgeler" bulur. Pano ise **zincir kurabilir** — "A, B'ye dayanıyor; B de C'ye;
o halde C bozulursa A'yı da etkiler." Tek bir belgede yazmayan cevaplar.

Bu panoyu otomatik kuran açık kaynaklı bir sistem zaten var. Sorun şu: **her yeni
alan için sistemi yeniden yazmak gerekiyor.** Hukukçu kendi kopyasını çatallıyor,
finansçı kendi kopyasını, sağlıkçı kendi kopyasını. Üç ay sonra ortada birleşmesi
imkânsız üç ayrı sistem oluyor ve hiçbiri diğerinin düzeltmelerinden faydalanamıyor.

## İddiamız

**Alanlar arasındaki fark koddan değil, ayardan ibaret.**

Yani hukuk ile yazılım paketleri arasındaki fark, "hangi tür şeyler var, nasıl
adlandırılıyorlar, hangi sorular sorulur" — bunların hepsi bir klasöre yazılabilir
ve o klasörde tek satır program olmaz.

Buna **pack** diyoruz. Bir pack, doldurulmuş bir form gibi:
- *hangi tür şeyler var?* (kanun, karar, madde)
- *veriyi nereden alacağız?*
- *"İş Kanunu" ile "4857 sayılı Kanun" aynı şey mi?*
- *bu alana hangi sorular sorulur?*
- *cevabın doğru olup olmadığını nasıl anlarız?*

## İddiayı nasıl test ettik

Birbirine mümkün olduğunca **benzemeyen** üç alan seçtik:

| alan | ne | dil |
|---|---|---|
| `oss` | Python yazılım paketleri ve bağımlılıkları | İngilizce |
| `tr-law` | Yargıtay 9. Hukuk Dairesi kararları | Türkçe |
| `bench-wiki` | 49 yayından haber makaleleri | İngilizce |

Kritik nokta: **üçüncüsünü, ilk ikisi bitip ölçüldükten sonra ekledik.** Sonradan
eklemek dürüst testtir — sistemi ona göre şekillendirme şansımız olmadı.

Maliyeti:

```
297 satır ayar dosyası        (9 dosya, hiç program yok)
  8 satır bizim kodumuzda
  0 satır asıl sistemde
```

Sıfırı iddia etmek kolay, kanıtlamak zor. Biz her seferinde otomatik kontrol
ediyoruz: asıl sistemin dosyalarında tek harf değişmişse, kontrol kırmızı yanıyor.

O 8 satır da anlatmaya değer. Üçüncü alanın grafiği tamamen hazır kayıtlardan
geliyordu — makalenin *metnini* yapay zekâya okutmaya gerek yoktu. Sistemde bunu
söyleyecek bir düğme yoktu, biz ekledik. Yani "hiç kod yazılmaz" demiyoruz;
**"yazılan kod o alana özel değil, herkese yarayan genel bir yetenek olur"** diyoruz.

## Neyi ölçtük

İşin iki yarısı var, ve dürüst olmak gerekirse **biri iyi çalışıyor, diğeri
zorlanıyor.**

**İyi çalışan: doğru belgeyi bulmak.** Herkesin kullandığı halka açık bir sınav
var (MultiHop-RAG): 609 haber makalesi, 2.556 soru. Sorular tek makalede
cevaplanmıyor, birkaçını birleştirmek gerekiyor.

- İlk denemede doğru makale: **%63**
- İlk 10 arasında: **%98**

Bunlar 2.255 soru üzerinde ölçüldü, yani sağlam sayılar.

**Zorlanan: belgeyi yapay zekâya okutup pano kurdurmak.** İki sorun:

*Pahalı.* Bir dizüstü bilgisayarda 200 belgeyi okumak **10,5 saat** sürdü.
Karşılaştırma için: 609 makaleyi okutmadan, sadece aranabilir hale getirmek
**6 dakika**. Yani maliyetin neredeyse tamamı yapay zekânın metni okuması.

*Söz dinlemiyor.* Modele "sadece şu tür şeyleri, şu kurallara göre çıkar"
diyoruz. Çıkardıklarının **sadece %18'i** verdiğimiz kurallara uyuyor. Model
kendi kafasına göre yeni türler icat ediyor.

Buradaki asıl bulgu şu: **kimse bunu kontrol etmiyordu.** Asıl sistem kuralları
alıyor ama uygulamıyor. Biz kontrol katmanını ekledik, ve o katman olmadan
kurallarının %82'sini çiğneyen bir pano, düzgün bir panodan ayırt edilemiyor.

## Cevap anahtarı sorunu ve çözümü

Yapay zekânın işini not vermek için normalde **elle hazırlanmış cevap anahtarı**
gerekir. Binlerce belgeyi insan okuyup "bu doğru, bu yanlış" diye işaretler.
Pahalı ve yavaş.

Bizim numaramız: **belgeler zaten kendi cevap anahtarını taşıyor.**

- Yazılım paketlerinde: hangi paketin neye muhtaç olduğu resmî kayıtlarda yazıyor.
- Mahkeme kararlarında: karar hangi kanuna dayandığını kendi metninde söylüyor.
- Haber sınavında: sınavı hazırlayanlar hangi makalenin cevap olduğunu vermiş.

Yani yapay zekânın bulduklarını, zaten elimizde olan kesin bilgiyle
karşılaştırıyoruz. **Kimse tek bir etiket yazmadı.**

## Dürüstlük bölümü

Bu kısım bilerek burada, "gelecek çalışmalar"a süpürülmedi.

**Ölçümün yarısı eksik.** İddiamız "iki farklı alanda ölçtüm" ama şu an elimizde
bir alan var, o da zayıf ölçülmüş. Türk hukuku tarafının yapay zekâ okuması hiç
koşmadı — 16 saatlik bir iş ve şimdilik durduruldu.

**Elimizdeki ölçüm zayıf.** Yazılım paketleri tarafında not verilebilecek sadece
20 örnek çıktı. 20 örnekle "başarı %22" demek, 20 kişiye sorup seçim tahmini
yapmak gibi. Sayı var ama bir şey söylemiyor. Neden az çıktığını bulduk: pano
en popüler 1.000 paketi tanıyor, belgeler ise çok daha geniş bir dünyadan
konuşuyor. Çözümü de belli — panoyu genişletmek, ki bedava.

**Kanıtlanmayan bir şey.** "Pano kurmak düz aramadan daha iyi" diyoruz ama bunu
ölçmedik. Ölçmek zor değil, sadece yapılmadı. En büyük eksik bu.

**Tek makine, tek model.** Bütün süre ölçümleri bir dizüstü bilgisayara ait.
Başka donanımda başka çıkar.

## Bu işten geriye ne kalıyor

Paketlerin kendisi değil, yöntemler:

1. **Kendi kendini notlandıran değerlendirme** — belgeler cevap anahtarını zaten
   taşıyorsa, insan etiketlemesi gerekmez.
2. **Ayrı çalışan eşleştirme adımı** — "İş Kanunu" ile "4857" aynı şey mi kararını
   okuma işinden ayırdık. Kural değişince 10 saati baştan koşmak yerine saniyeler
   içinde tekrar çalıştırılıyor.
3. **Kuralları kendin denetle** — asıl sistem kuralları alıp uygulamıyor. Aynı
   altyapıyı kullanan herkes aynı durumda, ve muhtemelen bilmiyor.
4. **Her sayının yanına belirsizlik payı** — küçük örneklem, kendinden emin
   görünen sayı üretir. "%22" değil "%22, artı eksi 13" yazmak, o sayıyı sonuç
   diye sunmamızı engelledi.

---

## 30 saniyelik sözlü versiyon

> Belgeleri yapay zekâya okutup aralarındaki bağlantı haritasını çıkaran bir
> sistem var. Sorun, her yeni alana uyarlamak için sistemi baştan yazmak gerekmesi.
>
> Biz bunu ayar dosyasına indirdik. Üçüncü alanı sonradan ekledik: 297 satır ayar,
> 8 satır kod, asıl sistemde sıfır değişiklik — ve sıfır olduğunu otomatik
> kontrol ediyoruz.
>
> Doğru belgeyi bulma tarafı iyi çalışıyor: halka açık bir sınavda ilk denemede
> %63, ilk onda %98. Yapay zekâya okutup pano kurdurma tarafı zorlanıyor —
> dizüstünde 200 belge 10 saat sürüyor ve model verdiğimiz kuralların ancak
> %18'ine uyuyor.
>
> En kıymetli bulgu da o: kimse bu uyumu ölçmüyordu. Ölçen katmanı biz koyduk.
