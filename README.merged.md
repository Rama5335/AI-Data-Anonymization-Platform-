# AI Data Anonymization Platform (Yapay Zekâ Destekli Veri Anonimleştirme Platformu)

> **Not:** Bu dosya (`README.merged.md`) bir **birleştirme önerisidir**. Repodaki mevcut
> `README.md` dosyası korunmuştur ve otomatik olarak değiştirilmemiştir. İçerik onaylandığında
> bu dosya `README.md` olarak kullanılabilir.

Ağ içinde kalan (offline-first), MSSQL veritabanlarındaki hassas verileri **keşfeden**,
**sınıflandıran**, **insan onayına sunan**, **yedekleyen** ve **güvenli biçimde anonimleştiren**
bir platformdur. Tüm yapay zekâ çıkarımı yerel olarak **Ollama** üzerinden çalışır; hedef
veritabanı hiçbir zaman ağ dışına çıkmaz.

> **Durum:** Faz 1–17 tamamlandı. Ayrıntılar için `docs/PHASES.md` ve `docs/phaseN_validation_report.md`
> dosyalarına bakın.

---

## İçindekiler

- [Ne Yapar?](#ne-yapar)
- [Mimari](#mimari)
- [Teknoloji Yığını](#teknoloji-yığını)
- [Veritabanı Tasarımı](#veritabanı-tasarımı)
- [Backend](#backend)
- [Frontend](#frontend)
- [Kimlik Doğrulama ve Yetkilendirme](#kimlik-doğrulama-ve-yetkilendirme)
- [Kurulum ve Çalıştırma](#kurulum-ve-çalıştırma)
- [Test ve Doğrulama](#test-ve-doğrulama)
- [Güvenlik Yaklaşımı](#güvenlik-yaklaşımı)
- [Proje Yapısı](#proje-yapısı)
- [Lisans](#lisans)

---

## Ne Yapar?

Platform uçtan uca şu akışı uygular:

```
keşfet (discover) -> şema (schema) -> örnekle (sample) -> sınıflandır (classify)
-> insan onayı (approval) -> yedekle (backup) -> maskele (mask) -> güncelle (update) -> denetle (audit)
```

Her adım uygulanmış, test edilmiş ve tek kullanımlık bir Docker MSSQL üzerinde canlı olarak
doğrulanmıştır.

## Mimari

Platform **iki ayrı veritabanı** ile çalışır:

- **Uygulama meta veritabanı** — platformun kendi durumu (kullanıcılar, işler, keşifler, onaylar,
  yedekler, denetim kayıtları). `DATABASE_URL` üzerinden Alembic ile oluşturulur; uygulamanın
  başlangıçta açtığı tek veritabanıdır.
- **Hedef MSSQL veritabanları** — incelenen ve anonimleştirilen sistemler. Yalnızca istek başına,
  kısa ömürlü `pyodbc` bağlantıları ile erişilir ve hiçbir zaman meta veritabanı ile karıştırılmaz.

Uygulama katmanları:

- **Backend** — FastAPI + SQLAlchemy + Alembic (Python 3.11).
- **Frontend** — Vite + React + TypeScript + Tailwind.
- **LLM** — Yerel Ollama (varsayılan `qwen2.5:7b-instruct`), ayrıca `openai` ve deterministik CI
  için `mock` sağlayıcıları.
- **Orkestrasyon** — Faz 15'te iş akışı durum makinesi LangGraph tabanlı hâle getirildi.

## Teknoloji Yığını

| Katman | Teknoloji |
|---|---|
| Backend | Python 3.11, FastAPI, SQLAlchemy 2.x, Alembic, Pydantic |
| Kimlik doğrulama | Argon2 (`pwdlib`), HS256 JWT |
| Veritabanı erişimi | `pyodbc` (ODBC Driver 18 for SQL Server) |
| LLM | Ollama (yerel), OpenAI uyumlu uç nokta, mock |
| Frontend | Vite, React, TypeScript, Tailwind CSS |
| Altyapı | Docker, docker compose, GitHub Actions (CI) |
| Test | pytest, ruff, black, Puppeteer (arayüz doğrulama) |

## Veritabanı Tasarımı

Meta veritabanı şeması **17 tablo** ve **8 Alembic revizyonundan** oluşur (head: `20261015_0008`).
Tam sütun/ilişki/kısıt/index referansı ve **Mermaid ER diyagramı** `docs/DATABASE.md` içindedir.

Öne çıkan ilişkiler: `servers → connections → jobs`; bir `job` kendi
`classification_results`, `discovered_*`, `backup_records`, `execution_history` ve `masking_jobs`
kayıtlarına sahiptir; her `classification_result` en fazla bir `approval` ile eşleşir;
`audit_logs` ilgili `user` ve `job` kayıtlarına referans verir. Tüm birincil anahtarlar uygulama
tarafında üretilen UUID dizeleridir ve DB seviyesinde `ON DELETE CASCADE` kuralı yoktur (silme
işlemleri açıkça yapılır).

```bash
cd backend
alembic upgrade head      # meta veritabanı şemasını oluşturur/günceller
alembic current           # uygulanan revizyonu gösterir
```

## Backend

```
backend/
  app/
    agents/            iş akışı ajanları (keşif, örnekleme, sınıflandırma, yedek, maskeleme)
    api/               HTTP rota modülleri
    classification/    LLM istemcisi + prompt + ayrıştırıcı
    database/          meta motoru + hedefe salt-okunur katalog/örnekleyici
    models/            SQLAlchemy ORM varlıkları
    schemas/           Pydantic istek/yanıt modelleri
    security/          kimlik şifreleme, JWT, parola karma, redaksiyon
  migrations/          Alembic sürümleri
  tests/               pytest test paketi
  tools/               verify_item*.py, verify_auth_smoke.py, validate_e2e.py
  scripts/             validate_phase*.ps1 + dev_up.ps1
```

## Frontend

```
frontend/
  src/
    auth/              AuthContext + LoginPage
    components/        her arayüz yüzeyi için bir dosya
    lib/               API istemcisi + tipler
  tools/               Puppeteer doğrulama/ekran görüntüsü betikleri
```

Arayüz; bağlantı formu, şema/örnekleme, onay merkezi, yedek merkezi, yürütme izleyici, denetim
kaydı, iş akışı, sunucular ve kullanıcılar sekmelerini içerir.

## Kimlik Doğrulama ve Yetkilendirme

- **Parola karma** — `pwdlib` ile Argon2; düz metin asla saklanmaz veya loglanmaz.
- **Giriş** — `POST /auth/login` parola karmasını doğrular ve `sub`, `iat`, `exp` içeren
  HS256 JWT üretir. Token ömrü `JWT_ACCESS_TOKEN_MINUTES` (varsayılan 30).
- **Token doğrulama** — `get_current_user`, `Authorization: Bearer <token>` başlığını okur;
  eksik/bozuk/süresi geçmiş/bilinmeyen token'ı **401**, pasif kullanıcıyı **403** ile reddeder.
  Bu kontrol **tüm** korumalı rotalarda uygulanır; yetkilendirme frontend'den bağımsız olarak
  backend tarafından zorlanır.
- **Roller** — `user` ve `admin` (`require_role`). Yedekleme, uygulama/geri alma, denetim okuma,
  kullanıcı oluşturma ve sunucu oluşturma/güncelleme/silme için **admin** gerekir.
  `/health` ve `/auth/login` tek herkese açık rotalardır.
- **Açık kayıt yoktur** — herkese açık bir kayıt uç noktası bulunmaz; `POST /users` yalnızca
  admin içindir. İlk çalıştırmada `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD`
  ortam değişkenlerinden bir yönetici oluşturulur; **ilk başarılı girişten sonra bu değişkenler
  kaldırılmalıdır.**
- **Frontend durumu** — `AuthContext` JWT'yi yalnızca bellekte tutar (`localStorage` kullanmaz),
  bu nedenle sayfa yenilendiğinde oturum kapanır. Herhangi bir API çağrısından dönen 401 de
  oturumu sonlandırır.

## Kurulum ve Çalıştırma

### Ön koşullar

- Python 3.11, Node.js (LTS) ve Docker (canlı doğrulayıcılar için).
- Yerel LLM için: [Ollama](https://ollama.com).

### 1. Ortam değişkenleri (zorunlu)

Gerçek sır değerleri **repoya yazılmaz**. Önce aşağıdaki değişkenleri kendi ortamınızda tanımlayın
(örnek şablon: `.env.example`):

```bash
# JWT imzalama anahtarı (32+ karakter):
python -c "import secrets; print(secrets.token_urlsafe(48))"
# Kimlik şifreleme anahtarı (32 baytlık urlsafe-base64, Fernet):
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Ardından `backend/.env` dosyanızı `.env.example` dosyasından türetin ve en azından
`JWT_SECRET_KEY`, `CREDENTIAL_ENCRYPTION_KEY` ve `DATABASE_URL` değerlerini doldurun.

### 2. Yerel çalıştırma

```bash
# Backend
cd backend
python -m venv .cpython311-venv
source .cpython311-venv/bin/activate        # Windows: .\.cpython311-venv\Scripts\activate
pip install -e ".[dev]"
cp ../.env.example .env                      # sırları kendi değerlerinizle düzenleyin
alembic upgrade head
uvicorn app.main:app --reload                # http://127.0.0.1:8765

# Frontend
cd ../frontend
npm install
npm run dev                                  # http://127.0.0.1:5174

# Yerel LLM (tek seferlik)
ollama serve
ollama pull qwen2.5:7b-instruct
```

Ardından arayüzde: bağlantı formu → şema/örnekleme/sınıflandırma → onay → yedek → yürütme
(`MASK AND UPDATE` yazarak onay) → denetim.

> `run.bat` (Windows) betiği de kullanılabilir; ancak `JWT_SECRET_KEY` ve
> `CREDENTIAL_ENCRYPTION_KEY` ortam değişkenlerinin önceden tanımlanması gerekir.

### 3. Docker Compose ile çalıştırma

Compose **zorunlu** üç ortam değişkeni bekler (varsayılan değer verilmez):

```bash
export JWT_SECRET_KEY="..."             # PowerShell: $env:JWT_SECRET_KEY = "..."
export CREDENTIAL_ENCRYPTION_KEY="..."
export MSSQL_SA_PASSWORD="..."

docker compose -f docker/docker-compose.yml up -d --build
docker exec -it ai-anonymizer-ollama ollama pull qwen2.5:7b-instruct
# arayüz: http://127.0.0.1:5173   (docker/nginx)  — backend: http://127.0.0.1:8765
```

> Portlar moda göre değişir: yerel **Vite** geliştirme sunucusu `5174`, docker **nginx**
> arayüzü `5173`; backend her zaman `8765`.

## Test ve Doğrulama

Backend (birim + sınır + güvenlik), `backend/` içinden:

```bash
python -m pytest -q
python -m ruff check .
python -m black --check .
```

Frontend (tip kontrolü + üretim derlemesi), `frontend/` içinden:

```bash
npm run build
```

Uçtan uca doğrulama:

```bash
# HTTP seviyesi: health, Swagger, giriş, 401/403, RBAC, bootstrap admin.
cd backend
python tools/verify_auth_smoke.py

# Tarayıcı seviyesi: giriş sayfası -> giriş -> Bearer başlığı -> çıkış -> 401.
cd frontend
node tools/verify_item2.cjs
```

Canlı doğrulayıcılar (Docker MSSQL + Ollama gerektirir):

```bash
pwsh scripts/validate_phase3.ps1
pwsh scripts/validate_phase5.ps1
pwsh scripts/validate_phase6.ps1
pwsh scripts/validate_phase9.ps1
```

## Güvenlik Yaklaşımı

Tam model için `docs/SECURITY.md`. Özetle:

- **AuthN/AuthZ** — Argon2 parola karması, HS256 JWT, iki rol (`user`, `admin`).
- **Bulut YZ yok** — Ollama yereldir; veri ağ dışına çıkmaz.
- **LLM üretimi SQL yok** — prompt şablonu bunu iki kez yasaklar; güvenli güncelleme yolu
  parametreli SQL'i kendisi kurar ve güvensiz tanımlayıcıları reddeder.
- **Diskte kimlik bilgisi yok** — saklanan bağlantı dizeleri `CREDENTIAL_ENCRYPTION_KEY` ile
  şifrelenir; istek başına kimlik bilgileri yalnızca HTTP çağrısı boyunca kullanılır.
- **Güncellemeden önce doğrulanmış yedek** — gerçek `POST /updates/apply`, `confirm: true` ve
  `BACKUP_DIR` altında var olan, doğrulanmış (`RESTORE VERIFYONLY`) bir `BackupRecord`'a
  çözümlenen `expected_backup_path` gerektirir.
- **Yürütme sonrası doğrulama + geri alma** — her partiden sonra etkilenen satırlar yeniden
  okunur; maskelenmiş değeri almayan bir satır tüm işlemi geri alır.
- **Yalnızca ekleme denetimi** — `AuditLog` kayıtları uygulama tarafından güncellenmez/silinmez;
  okuma yalnızca admin içindir ve ilgili tüm işlemler aktörün kimliğiyle denetlenir.

## Proje Yapısı

```
backend/                 FastAPI + SQLAlchemy + Alembic
frontend/                Vite + React + Tailwind
docker/                  compose + Dockerfile'lar
.github/workflows/       CI + haftalık entegrasyon
docs/                    DATABASE.md, SECURITY.md, ARCHITECTURE.md, RUNBOOK.md, PHASES.md
```

## Katkıda Bulunanlar

<!-- Üniversite teslimi için ekip üyeleri buraya eklenmelidir. -->

## Lisans

Şu an için dahili / lisanssız.
