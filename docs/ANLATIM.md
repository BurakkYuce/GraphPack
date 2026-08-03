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

**Zorlanan: belgeyi yapay zekâya okutup pano kurdurmak.**

*Pahalı — ama nerede koşturduğuna bağlı.* Bir dizüstü bilgisayarda 200 belgeyi
okutmak **10,5 saat** sürdü. Aynı 200 belge, bulut üzerindeki bir modelde
**4 dakika** ve yaklaşık 60 sent. Yani "yavaş" dediğimiz şey tasarımın değil,
dizüstü bilgisayarın özelliğiymiş. Kıyas için: okutmadan, sadece aranabilir hale
getirmek 609 makale için 6 dakika.

*Söz dinlemiyordu, dinletildi.* Modele "sadece şu tür şeyleri, şu kurallara göre
çıkar" diyoruz. Dizüstündeki modelde çıkardıklarının **sadece %18'i** kurallara
uyuyordu; model kendi kafasına göre tür icat ediyordu.

Buradaki asıl bulgu şu: **kimse bunu kontrol etmiyordu.** Asıl sistem kuralları
alıyor ama uygulamıyor — üstelik altındaki kütüphane, kendi örnek kurallarına
göre denetleyip her şeyi çöpe atıyor ve "başarıyla tamamlandı" diyordu. Biz hem
kuralları gerçekten uygulattık hem de sonucu denetleyen katmanı ekledik.
Uyum **%18'den %100'e** çıktı. O katman olmadan, kurallarının %82'sini çiğneyen
bir pano düzgün bir panodan ayırt edilemiyor.

*Ve panonun üçte biri bizim kendi notumuzmuş.* Belgeye iliştirdiğimiz künye
bilgileri — bağlantı adresi, tarih, durum — modele belge metniymiş gibi
gidiyormuş. Çıkarılan varlıkların **%31'i** aslında bizim eklediğimiz bağlantı
adresiydi, ve hepsi "geçerli tür" taşıdığı için uyum denetimi bunu göremiyordu.
Künyeyi gizleyince oran %0,7'ye düştü — ve pano küçülmedi, **iyileşti**: gerçek
yapı için yer açıldı, depo bağlantıları 10'dan 85'e çıktı.

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

**Üç kez teşhis koyduk, ikisi yanlış çıktı.** Yazılım paketleri tarafı uzun süre
ölçülemedi: not verilebilecek yalnızca 20-24 örnek çıkıyordu, ki 20 örnekle
"başarı %22" demek 20 kişiye sorup seçim tahmini yapmak gibidir.

Önce "model ve kurallar yüzünden" dedik — kontrollü bir koşum yanlış çıkardı.
Sonra "pano çok dar" dedik — panoyu 8 katına çıkardık, 2 örnek kazandık.
Üçüncüsü tuttu: sorun **sorduğumuz sorunun şekliydi.** Belgelerin %69'u tek
paketten bahsediyor, bizim ölçüm yöntemimiz ise aynı belgede *iki* ilişkili
paket arıyordu. Başlıkları da panoya birer düğüm olarak ekleyince — kod değil,
ayar — örnek sayısı **24'ten 135'e** çıktı ve belirsizlik payı ±13 puandan
±6'ya indi.

Bunun dersi "ilk iki tahmin kötüydü" değil. Bir teşhisi **yazmak**, onu
birinin koşabileceği bir iddiaya çevirir; koşmak bir dolar ve yirmi dakika,
yanlış inançla devam etmek ise ondan sonraki her kararı şekillendirirdi.

**Yeni ölçüm daha kolay bir soru soruyor, ve bunu da yazıyoruz.** "Bu başlığın
kendi paketi metinde anılıyor mu" sorusu, "bağımlılık ilişkisi bulundu mu"
sorusundan kolay. Üstelik cevabın bir kısmı künyede yazılı: künyeyi de
gizleyince başarı %86'dan %75'e düşüyor — yani ~10 puanı künyeyi tekrarlamak,
~75 puanı gerçekten metni okumak. Bu sayıyı tahmin etmedik, ölçtük.

**Aynı ayarla iki kez koşunca sonuç değişiyor.** Bunu daha önce hiç kontrol
etmemiştik. Yeni ölçüm iki koşumda da aynı 94 örneği üretti; eski ölçüm 66 ve 37
üretti — neredeyse yarı yarıya. Yani eski ölçümün belirsizliği, yazdığımız
belirsizlik payından bile fazla.

**Halka açık sınavdaki sayımız, makalenin sayısından yüksek görünüyor ama
karşılaştırılabilir değil.** Makale 0,586 diyor, biz 0,759. Sebep bizim daha iyi
olmamız değil: makale *parça* düzeyinde puanlıyor, biz *makale* düzeyinde; ve
makalenin "isabet" tanımı kanıtların ne kadarını bulduğun, bizimki en az birini
bulup bulmadığın. İkisi de bizim lehimize kolaylık. Bunu makaleyi okuyunca fark
ettik ve kendi kodumuzdaki ters iddiayı düzelttik.

**Tek makine, tek yerel model.** Dizüstündeki bütün süre ölçümleri o makineye
ait. Bulut modeliyle koşanlar tamamen başka bir rejimde.

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
5. **Neyi ölçtüğünü sormak, iyileştirmeye çalışmadan önce** — bu projenin en
   pahalı dersi. "Sistem kötü" diye okunabilecek bir sayı, aslında yanlış soru
   sorulduğu için düşüktü. Model değişmedi, veri değişmedi, tek bir belge bile
   yeniden okutulmadı; soru değişti ve ölçülebilir örnek sayısı beşe katlandı.

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
> %63, ilk onda %98. Yapay zekâya okutup pano kurdurma tarafı zor: model
> verdiğimiz kuralların ancak %18'ine uyuyordu.
>
> En kıymetli iki bulgu da orada. Birincisi: kimse bu uyumu ölçmüyordu — asıl
> sistem kuralları alıp uygulamıyordu bile. Ölçen ve uygulatan katmanı biz
> koyduk, uyum %18'den %100'e çıktı.
>
> İkincisi daha genel: bir ölçüm düşük çıktığında önce "sistem kötü" demeyip
> "acaba yanlış soru mu soruyorum" diye sormak. Bizde cevabı evetti — soruyu
> değiştirince, tek bir belgeyi yeniden okutmadan, ölçülebilir örnek sayısı
> beşe katlandı.
