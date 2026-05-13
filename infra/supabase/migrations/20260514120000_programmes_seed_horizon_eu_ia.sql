-- Migration: seed horizon_eu_ia row in programmes table.
--
-- The Horizon Europe Innovation Action programme module lands in Faz 5
-- (apps/api/src/programs/horizon_eu_ia/), but the EU F&T Portal scraper
-- (Faz 1.2) already discovers IA topics today. Without this row, the
-- scraper's INSERT into calls(programme_id='horizon_eu_ia') fails the
-- foreign-key constraint.
--
-- Seeding metadata-only here is safe — calls reference programmes(id)
-- but the programme's BaseProgramModule implementation (brief schema,
-- DOCX template, prompts) is needed only when a user actually drafts
-- a proposal against the programme, which happens after Faz 5 ships.
--
-- Also corrects the misleading existing label on horizon_eu_ria, which
-- previously read "Horizon Europe RIA/IA" — IA gets its own row now.

insert into programmes (id, name_tr, name_en, funder, language, description_tr, description_en, metadata) values
  ('horizon_eu_ia',
   'Horizon Europe IA (İnovasyon Eylemi)',
   'Horizon Europe Innovation Action',
   'European Commission',
   'en',
   'Pazar yakın teknoloji geliştirme — TRL 5-8, %70 destek (kar amacı güden), %100 destek (kar amacı gütmeyen).',
   'Close-to-market innovation development — TRL 5-8, 70% funding for-profit, 100% non-profit.',
   '{"type_of_action": "HORIZON-IA", "framework_programme_id": "43108390", "page_limit_part_b": 45, "shared_template_with": "horizon_eu_ria"}'::jsonb)
on conflict (id) do nothing;

-- Tidy up the now-stale label on horizon_eu_ria (was "RIA/IA"). Keeps
-- name_en/name_tr accurate now that IA is its own row.
update programmes
   set name_tr = 'Horizon Europe RIA (Araştırma ve Yenilik Eylemi)',
       name_en = 'Horizon Europe Research and Innovation Action'
 where id = 'horizon_eu_ria'
   and name_en = 'Horizon Europe RIA/IA';
