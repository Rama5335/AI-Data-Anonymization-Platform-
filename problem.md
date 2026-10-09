# Problem Tanımı: Test ve Geliştirme Ortamlarında Hassas Verilerin Korunması

## 1. Görüşme Yapılan Birim

**Birim:** Öğrenci İşleri

## 2. Problem

Test ve geliştirme ortamlarında gerçek öğrenci verilerinin kullanılması, kişisel ve hassas bilgilerin yetkisiz kişilerin eline geçmesi riskini artırabilir. Öğrenci numarası, telefon numarası, e-posta adresi ve kimlik bilgileri gibi veriler yeterince korunmadığında gizlilik ihlalleri yaşanabilir.

Veritabanlarında hangi alanların hassas veri içerdiğini manuel olarak belirlemek zaman alıcıdır ve insan hatasına açıktır. Özellikle büyük miktarda veriyle çalışırken hassas alanların belirlenmesi ve bu alanlardaki verilerin güvenli biçimde değiştirilmesi zorlaşabilir. Bu durum hem iş yükünü artırabilir hem de bazı kişisel verilerin gözden kaçmasına neden olabilir.

## 3. Görüşme Soruları ve Yanıtları

### Soru 1
**Gerçek verilerin test ve geliştirme ortamlarında kullanılması sizce hangi güvenlik ve gizlilik risklerini oluşturur?**

**Yanıt:**
Gerçek verilerin test ortamında kullanılması, kişisel verilerin yetkisiz kişilerin eline geçmesine neden olabilir. Özellikle öğrenci numarası, telefon numarası, e-posta adresi ve kimlik bilgileri gibi verilerin korunması gerekir.

### Soru 2
**Veritabanındaki hassas alanların otomatik olarak tespit edilmesi ve anonimleştirilmesi sizce hangi sorunları çözebilir?**

**Yanıt:**
Hassas alanları manuel olarak belirlemek zaman alabilir ve insan hatasına açık olabilir. Otomatik tespit ve anonimleştirme işlemleri bu süreci hızlandırabilir ve kişisel verilerin korunmasına yardımcı olabilir.

### Soru 3
**Bu süreçte karşılaştığınız en büyük problem nedir?**

**Yanıt:**
En büyük problemlerden biri, hangi alanların hassas veri içerdiğini belirlemek ve çok sayıda veriyi güvenli bir şekilde değiştirmektir. Manuel işlemlerde hata yapma ihtimali de bulunmaktadır.

### Soru 4
**Bu işlemin otomatik yapılması size göre faydalı olur mu?**

**Yanıt:**
Evet. Özellikle çok fazla veri olduğunda otomatik bir sistem işlemi kolaylaştırabilir, zaman kaybını azaltabilir ve kişisel verilerin korunmasına yardımcı olabilir.

## 4. Temel Problemler

- Gerçek öğrenci verilerinin test ve geliştirme ortamlarında kullanılması gizlilik ve güvenlik riskleri oluşturabilir.
- Hassas verilerin bulunduğu sütunları manuel olarak tespit etmek zaman alabilir.
- Manuel işlemler insan hatasına açıktır; bazı hassas alanlar gözden kaçabilir.
- Büyük miktarda verinin güvenli ve tutarlı bir şekilde anonimleştirilmesi zor olabilir.
- Anonimleştirme sürecinin manuel yürütülmesi iş yükünü ve işlem süresini artırabilir.

## 5. İhtiyaç ve Beklenen Çözüm

Öğrenci İşleri biriminin ihtiyaçları doğrultusunda, veritabanındaki hassas alanları otomatik olarak tespit etmeye yardımcı olan ve kullanıcı onayından sonra bu alanlardaki verileri güvenli biçimde anonimleştiren bir sisteme ihtiyaç vardır.

Bu sistemin;

- Hassas veri içerebilecek alanları tespit etmesi,
- Önerilen sınıflandırmaları kullanıcıya göstermesi ve işlem öncesinde onay alması,
- Gerçek kişisel bilgileri koruyacak uygun anonimleştirme yöntemlerini uygulaması,
- İşlem hatalarını ve insan kaynaklı riskleri azaltmaya yardımcı olması,
- Büyük veri kümelerinde süreci daha hızlı ve yönetilebilir hâle getirmesi

beklenmektedir.

## 6. Sonuç

Öğrenci İşleri biriminden alınan görüşler, gerçek öğrenci verilerinin test ortamlarında kullanılmasının gizlilik riski oluşturabileceğini ve hassas verilerin manuel olarak belirlenip değiştirilmesinin zaman alıcı, hata yapmaya açık bir süreç olduğunu göstermektedir. Bu nedenle otomatik hassas veri tespiti ve kontrollü anonimleştirme, kişisel verilerin korunmasını destekleyebilecek ve iş yükünü azaltabilecek bir çözüm olarak değerlendirilmektedir.
