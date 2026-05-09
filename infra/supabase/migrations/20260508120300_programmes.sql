-- Migration 003: Grant programme catalog + Phase 1 seed (5 programmes)
-- Source of truth: docs/03-database-schema.md §2.2

create table if not exists programmes (
  id text primary key,
  name_tr text not null,
  name_en text not null,
  funder text not null,
  language text not null check (language in ('tr','en','both')),
  description_tr text,
  description_en text,
  active boolean default true,
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- Seed: Phase 1 programmes. ON CONFLICT DO NOTHING so re-running is safe.
insert into programmes (id, name_tr, name_en, funder, language) values
  ('tubitak_1501', 'TÜBİTAK 1501 Sanayi AR-GE',
   'TÜBİTAK 1501 Industrial R&D', 'TÜBİTAK', 'tr'),
  ('tubitak_1507', 'TÜBİTAK 1507 KOBİ AR-GE Başlangıç',
   'TÜBİTAK 1507 SME R&D Start', 'TÜBİTAK', 'tr'),
  ('kosgeb_arge', 'KOSGEB AR-GE ve Yenilik',
   'KOSGEB R&D and Innovation', 'KOSGEB', 'tr'),
  ('horizon_eu_ria', 'Horizon Europe RIA/IA',
   'Horizon Europe RIA/IA', 'European Commission', 'en'),
  ('cascade_funding', 'Cascade Funding & NLnet',
   'Cascade Funding & NLnet', 'NGI / FSTP', 'en')
on conflict (id) do nothing;
