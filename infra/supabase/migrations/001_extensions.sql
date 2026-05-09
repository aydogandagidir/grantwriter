-- 001 — Extensions
-- Source: docs/03-database-schema.md §1
-- Run order: this is the first migration; everything else depends on these.

create extension if not exists "uuid-ossp";
create extension if not exists "pgcrypto";
create extension if not exists "vector";
create extension if not exists "pg_trgm";
create extension if not exists "btree_gin";
