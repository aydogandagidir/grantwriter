-- Migration: seed remaining TÜBİTAK programmes referenced by the scraper.
--
-- The Faz 1.3 TÜBİTAK scraper recognises codes 1501, 1507, 1505, 1601,
-- 1512, 1071 and 2244. Rows for 1501 / 1507 already exist (migration 003);
-- the other five are seeded here as metadata-only so the scraper's
-- INSERT into calls(programme_id=...) doesn't fail the foreign-key
-- constraint before Faz 5 ships the BaseProgramModule implementations.
--
-- Per docs/programs/tubitak.md.

insert into programmes (id, name_tr, name_en, funder, language, description_tr, description_en, metadata) values
  ('tubitak_1505',
   'TÜBİTAK 1505 Üniversite-Sanayi İşbirliği',
   'TÜBİTAK 1505 University-Industry Collaboration',
   'TÜBİTAK',
   'tr',
   'Müşteri kuruluş + yürütücü üniversite ortak başvurusu. Max 1.000.000 TL + %5 kurum hissesi. Sürekli açık.',
   'Joint application by an industry customer and a university executor. Max 1M TL + 5% institutional share. Rolling.',
   '{"max_budget_tl": 1000000, "max_duration_months": 24, "consortium_required": true, "call_pattern": "rolling"}'::jsonb),
  ('tubitak_1601',
   'TÜBİTAK 1601 Yenilik ve Girişimcilik Kapasitesi',
   'TÜBİTAK 1601 Innovation & Entrepreneurship Capacity',
   'TÜBİTAK',
   'tr',
   'Şirketler, üniversiteler, kamu, vakıflar, TSO/OSB/ihracatçı birlikleri için tematik kapasite çağrıları. %100 hibe.',
   'Thematic capacity-building calls for companies, universities, public bodies, foundations, chambers, exporters. 100% grant.',
   '{"funding_rate_pct": 100, "max_duration_months": 36, "call_pattern": "thematic"}'::jsonb),
  ('tubitak_1512',
   'TÜBİTAK 1512 BiGG Bireysel Genç Girişim',
   'TÜBİTAK 1512 BiGG Individual Young Entrepreneur',
   'TÜBİTAK',
   'tr',
   'Bireysel girişimciler için iki aşamalı destek. Aşama 2 max 900.000 TL (2024-1''den itibaren %3 hisse karşılığı yatırım modeli).',
   'Two-stage support for individual entrepreneurs. Stage 2 max 900K TL (since 2024-1 funded as a 3% equity investment).',
   '{"max_funding_tl": 900000, "individual_eligible": true, "stages": 2, "funding_model_since_2024": "equity_3pct"}'::jsonb),
  ('tubitak_1071',
   'TÜBİTAK 1071 Uluslararası Araştırma Fonları',
   'TÜBİTAK 1071 International Research Funds',
   'TÜBİTAK',
   'tr',
   'EuroHPC, PRIMA, COST, ikili işbirliği fonları gibi uluslararası çağrı-bazlı destekler. Çağrı bazlı bütçe/süre.',
   'International call-based support routed via EuroHPC, PRIMA, COST, bilateral funds. Budget and duration vary per call.',
   '{"submission_portal": "uidb-pbs.tubitak.gov.tr", "call_pattern": "international", "international_collaboration_required": true}'::jsonb),
  ('tubitak_2244',
   'TÜBİTAK 2244 Sanayi Doktora Programı',
   'TÜBİTAK 2244 Industrial Doctorate Program',
   'TÜBİTAK',
   'tr',
   'Üniversite + özel sektör kurumsal ortak başvuru. Burs + sanayi istihdam taahhüdü içerir.',
   'Joint application by a university and a private-sector partner. Includes doctoral scholarships and industry employment commitment.',
   '{"submission_portal": "e-bideb.tubitak.gov.tr", "partnership_required": true, "scholarship_based": true}'::jsonb)
on conflict (id) do nothing;
