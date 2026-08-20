# JournalHub

JournalHub is a unified journal intelligence platform that consolidates journal metadata, rankings, ratings, identifiers, and historical records from multiple academic journal-ranking sources into a single normalized system.

## Project Status

**Current phase:** Day 1 — Environment Setup + Full Data Inventory

The project is currently establishing the reproducible environment and verifying all raw source datasets before any data is parsed into the database.

## Data Sources

JournalHub currently works with the following sources:

- SCImago Journal & Country Rank
- ABDC — Australian Business Deans Council Journal Quality List
- ABS/AJG — Academic Journal Guide
- RePEc
- Financial Times 50 (FT50)

## Raw Data Policy

The directory:

```text
data/raw/

## Database Schema

The PostgreSQL database schema is implemented through ordered migrations in
`database/migrations/`.

Day 2 creates the complete database schema with 18 tables covering:

- source and dataset tracking
- raw file and raw-row lineage
- ingestion rejections
- canonical journals
- journal identifiers and aliases
- source-to-journal mappings
- SCImago records, categories, and areas
- ABDC records
- ABS records
- RePEc records
- FT50 records
- entity-resolution candidates and decisions

### Raw file SHA-256 uniqueness

`raw_files.sha256` is globally unique across all sources.

This is intentional: if two different source folders contain byte-identical
files, the system treats them as the same raw artifact. This is a deliberate
simplicity tradeoff, not an oversight.

The database also stores `raw_rows.raw_data` as a structured raw-row snapshot.
It is **not** a byte-level lossless copy of the original file. Parsing through
`pandas.read_csv()` can normalize representations before the row is stored.
The untouched files under `data/raw/` remain the true byte-level source of
truth.