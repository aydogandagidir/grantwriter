# Supabase Migrations

Sprint 1 / Day 2 migrasyonları. Şema kaynağı: [`docs/03-database-schema.md`](../../docs/03-database-schema.md).

## Migrasyon Listesi

| # | Dosya | İçerik |
|---|---|---|
| 001 | `20260508120100_extensions.sql` | uuid-ossp, pgcrypto, vector, pg_trgm, btree_gin |
| 002 | `20260508120200_tenants_and_users.sql` | `tenants`, `public.users`, `tenant_invitations`, `tenant_llm_config` |
| 003 | `20260508120300_programmes.sql` | `programmes` + 5 program seed (TÜBİTAK 1501/1507, KOSGEB, HE RIA/IA, Cascade) |
| 004 | `20260508120400_calls.sql` | `calls`, `call_chunks` (HNSW embedding index) |
| 005 | `20260508120500_proposals.sql` | `proposals`, `proposal_provenance` |
| 006 | `20260508120600_citations_and_collaboration.sql` | `citations`, `proposal_versions`, `proposal_comments` |
| 007 | `20260508120700_rag_corpus.sql` | `successful_proposals_corpus` + `_chunks`, `funder_guidelines`, `cordis_funded_projects` |
| 008 | `20260508120800_usage_billing_audit.sql` | `tenant_usage_log`, `billing_events`, `audit_log` |

RLS politikaları (009) ve trigger/fonksiyonlar (010) ayrı görevlerde gelir (S1.D2.T2 + sonrası).

## Çalıştırma

### Supabase CLI ile (önerilen, production-paritetik)

```bash
# Tek seferlik kurulum:
# Windows: scoop install supabase    (https://supabase.com/docs/guides/cli)
# macOS:   brew install supabase/tap/supabase

cd infra
supabase init                # config.toml üretir, ilk çalıştırma sonrası commit edilir
supabase db reset            # tüm migrasyonları temiz veritabanına uygular
supabase status              # kullanılan portlar
psql -h 127.0.0.1 -p 54322 -U postgres -d postgres -c '\dt'
```

### Vanilla pgvector container (CLI yokken hızlı doğrulama)

```bash
docker run -d --name bd-pg-test -e POSTGRES_PASSWORD=test \
  -p 55432:5432 pgvector/pgvector:pg16

# auth.users stub'ı önce uygula (Supabase Auth'un yokluğunu telafi eder)
docker exec -i bd-pg-test psql -U postgres -d postgres < infra/supabase/auth_stub.sql

# Migrasyonları sırayla uygula
for f in infra/supabase/migrations/*.sql; do
  docker exec -i bd-pg-test psql -U postgres -d postgres < "$f"
done

# Tablo listesi
docker exec bd-pg-test psql -U postgres -d postgres -c '\dt public.*'

docker rm -f bd-pg-test
```

## Notlar

- **HNSW + 3072-dim:** `text-embedding-3-large` 3072 boyutlu vektör üretir; pgvector'ın `vector` tipi için HNSW limiti 2000. `halfvec(3072)` HNSW limit'i 4000 olduğundan, embedding sütunlarını `vector(3072)` olarak saklayıp HNSW indeksini `(embedding::halfvec(3072)) halfvec_cosine_ops` ifadesi üzerinden kuruyoruz. Saklamada hassasiyet kaybı yok, sadece indeks lookup'ında küçük (kabul edilebilir) recall kaybı var.
- **`auth.users`:** Supabase Cloud / `supabase db reset` ortamında otomatik gelir; raw container kullanırken `auth_stub.sql` ile sahte tablo oluşturulur.
- **İdempotenslik:** Tüm `CREATE TABLE`, `CREATE INDEX`, `CREATE EXTENSION` çağrıları `IF NOT EXISTS` ile, programme seed'i `ON CONFLICT (id) DO NOTHING` ile yazıldı. Aynı migrasyon iki kez çalıştırılabilir.
- **RLS yok:** Bu sette politika yok — `S1.D2.T2` (kritik güvenlik görevi) bunu yeşil RLS test suite'iyle ekleyecek.
