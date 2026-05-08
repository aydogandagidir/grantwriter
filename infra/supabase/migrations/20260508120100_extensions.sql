-- Migration 001: Extensions
-- Source of truth: docs/03-database-schema.md §1
--
-- Idempotent: every CREATE EXTENSION uses IF NOT EXISTS so this migration
-- can re-run on a partially-applied database without erroring.

create extension if not exists "uuid-ossp";
create extension if not exists "pgcrypto";
create extension if not exists "vector";       -- pgvector for RAG embeddings
create extension if not exists "pg_trgm";      -- fuzzy text search on titles, citations
create extension if not exists "btree_gin";    -- composite GIN indexes
