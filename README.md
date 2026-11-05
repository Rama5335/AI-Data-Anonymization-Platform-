# AI Data Anonymization Platform

Yapay zekâ destekli, **offline-first** veri anonimleştirme platformu. MSSQL
veritabanlarındaki hassas verileri keşfeder, sınıflandırır, insan onayına sunar,
yedekler ve parametreli/geri alınabilir UPDATE'lerle anonimleştirir.

> Ayrıntılı Türkçe doküman: [`README.merged.md`](README.merged.md).
> Mimari: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · Güvenlik:
> [`docs/SECURITY.md`](docs/SECURITY.md) · Operasyon:
> [`docs/RUNBOOK.md`](docs/RUNBOOK.md) · Veritabanı:
> [`docs/DATABASE.md`](docs/DATABASE.md).

## Öne çıkanlar

- **Keşif ve sınıflandırma** — şema/örnek okuma, yerel Ollama modeliyle PII tespiti.
- **İnsan onayı** — onay/ret/override akışı; yalnızca onaylanan sütunlar güncellenir.
- **Zorunlu yedek kapısı** — gerçek güncelleme, `BACKUP_DIR` altındaki **doğrulanmış**
  bir `BackupRecord` olmadan çalışmaz.
- **Güvenli güncelleme** — parametreli SQL, kimlik doğrulamalı tanımlayıcılar,
  parti bazlı doğrulama ve hata durumunda rollback.
- **Denetim** — yalnızca ekleme (append-only) audit kaydı; okuma admin'e özel.
- **Yerel YZ** — yalnızca Ollama (`qwen2.5:7b-instruct`) veya deterministik `mock`.
  Bulut YZ sağlayıcıları ve genel `OLLAMA_BASE_URL` değerleri reddedilir.
- **RBAC** — `user` / `admin`; yedekleme, audit, kullanıcı ve sunucu mutasyonları
  admin gerektirir. Arayüz de bu sekmeleri/eylemleri admin olmayanlardan gizler.

## Hızlı başlangıç

```bash
# 1) Backend
cd backend
python -m venv .cpython311-venv
# Windows: .\.cpython311-venv\Scripts\activate
pip install -e ".[dev]"
cp ../.env.example .env          # JWT_SECRET_KEY, CREDENTIAL_ENCRYPTION_KEY, DATABASE_URL
alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8765

# 2) Yerel LLM
ollama serve
ollama pull qwen2.5:7b-instruct

# 3) Frontend
cd ../frontend
npm ci
npm run dev                      # http://127.0.0.1:5174
```

Windows'ta `run.bat`, Docker ile `docker compose -f docker/docker-compose.yml up -d --build`
kullanılabilir. Sağlık uçları: `GET /health` (liveness) ve `GET /health/ready`
(meta veritabanı hazır mı; değilse 503).

## Test ve kalite kapısı

```bash
cd backend
python -m pytest -q
python -m ruff check .
python -m compileall -q app tests tools

cd ../frontend
npm ci
npm run lint
npm run build
```

Hafta 5 doğrulama sonuçları ve test edilmeyen kalemler:
[`docs/WEEK5_VERIFICATION_REPORT.md`](docs/WEEK5_VERIFICATION_REPORT.md).

## Yapılandırma

Tüm ayarlar ortam değişkenlerinden okunur; şablon: [`.env.example`](.env.example).
Önemli değişkenler: `DATABASE_URL`, `JWT_SECRET_KEY`,
`CREDENTIAL_ENCRYPTION_KEY`, `CORS_ORIGINS`, `LLM_PROVIDER`,
`OLLAMA_BASE_URL`, `OLLAMA_MODEL`, `BACKUP_DIR`, `BACKUP_KEEP`.

Gerçek sırlar repoya **yazılmaz**; `.env`, `*.db`, `backups/` ve `dist/`
`.gitignore` ile hariç tutulur. İlk kurulumdan sonra `BOOTSTRAP_ADMIN_*`
değişkenlerini kaldırın.

## Bilinen sınırlamalar

Tek onaylayıcı modeli, `/auth/login` için hız sınırı/kilit bulunmaması,
`RESTORE VERIFYONLY`'nin test-restore olmaması ve frontend bağımlılıklarındaki
`npm audit` uyarıları. Ayrıntılar: [`docs/SECURITY.md`](docs/SECURITY.md).
