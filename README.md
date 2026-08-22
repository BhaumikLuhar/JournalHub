# JournalHub

JournalHub is a unified journal intelligence platform that consolidates journal metadata, rankings, ratings, identifiers, and historical records from multiple academic journal-ranking sources into a single normalized system.

## Project Status

**Current phase:** Day 4 complete — SCImago raw + staging ingestion

Days 1–4 are complete.

* **Day 1:** Environment setup and full raw-data inventory completed.
* **Day 2:** Complete PostgreSQL database schema implemented through ordered migrations, covering 18 tables, controlled vocabularies, uniqueness constraints, and foreign-key relationships.
* **Day 3:** Shared normalization utilities, CSV/Excel loaders, validation helpers, transactional dataset-import helpers, canonical-journal resolution, and entity-match candidate helpers completed and tested in isolation.
* **Day 4:** SCImago source-specific ingestion completed from raw CSV files into raw/staging storage, including filename parsing, source-row transformation, validation, transactional ingestion, rejection handling, idempotent retries, and crash-recovery testing.

### Day 3 Verification
The following were verified successfully:

* All shared ingestion and entity-resolution modules import successfully.
* All 14 normalization/manual-test assertions pass.
* Dataset creation, retry, and skip behavior is idempotent and non-destructive.
* Transactional dataset imports correctly commit on success and roll back on failure.
* Canonical-journal creation is sequentially idempotent.
* Conflicting identifiers do not abort new-journal creation and are logged for review.
* Entity-match candidates merge repeated evidence without creating duplicate rows.
* Accepting a candidate correctly closes all pending sibling candidates.
* Scratch test data has been removed from the database.
* No concurrency guarantee is claimed for the sequential entity-resolution implementation.

### Day 4 Verification

The following were verified successfully:

* 49 SCImago CSV files discovered and processed.
* All 49 SCImago datasets have `status='loaded'`.
* 139,491 SCImago records were loaded into `scimago_records`.
* `raw_rows` contains 139,491 SCImago source-row snapshots.
* Dataset `record_count` matches the actual `scimago_records` count for every dataset.
* `scimago_categories` and `scimago_areas` were populated without orphaned rows.
* `journal_id` remains NULL for all SCImago records, as required before entity resolution.
* A second ingestion run skips all already-loaded datasets without creating duplicates.
* Deliberate transaction failure was tested; failed imports rolled back their raw and normalized rows completely.
* Failed datasets can be retried successfully.
* SCImago `-` quartile values are normalized to NULL.
* SJR values are parsed as decimals and validated against the 0–100 range.
* No SCImago ingestion rejections were present after successful ingestion.
* Common normalization, SCImago parser, transformer, and validator manual tests all pass.

**Next phase:** Day 5 — Entity resolution

Day 5 will resolve source-specific journal records to canonical journals and populate the appropriate `journal_id` relationships using the entity-resolution helpers completed on Day 3.

## Current Ingestion Status

| Source | Status | Records |
|---|---|---:|
| SCImago | Loaded | 139,491 |
| ABDC | Pending | — |
| ABS/AJG | Pending | — |
| RePEc | Pending | — |
| FT50 | Pending | — |

## Entity Resolution Status

SCImago ingestion currently preserves all source records independently of canonical journal resolution.

At the end of Day 4:

* `scimago_records.journal_id` is NULL for all 139,491 records.
* Canonical-journal resolution has not yet been applied to SCImago records.
* Entity resolution is scheduled for Day 5.

## Data Sources
JournalHub currently works with the following sources:

* SCImago Journal & Country Rank
* ABDC — Australian Business Deans Council Journal Quality List
* ABS/AJG — Academic Journal Guide
* RePEc
* Financial Times 50 (FT50)

## Raw Data Policy
The directory:
`data/raw/`  
contains the untouched source files used as the byte-level source of truth.

Normalized ingestion records must never modify the original source representation before it is stored in raw-data lineage fields. Normalization utilities are used only when constructing normalized records.

The untouched files under `data/raw/` remain the authoritative byte-level source of truth.

## Database Schema
The PostgreSQL database schema is implemented through ordered migrations in:  
`database/migrations/`

Day 2 creates the complete database schema with 18 tables covering:

* source and dataset tracking
* raw file and raw-row lineage
* ingestion rejections
* canonical journals
* journal identifiers and aliases
* source-to-journal mappings
* SCImago records, categories, and areas
* ABDC records
* ABS records
* RePEc records
* FT50 records
* entity-resolution candidates and decisions

### Raw File SHA-256 Uniqueness
`raw_files.sha256` is globally unique across all sources.

This is intentional: if two different source folders contain byte-identical files, the system treats them as the same raw artifact. This is a deliberate simplicity tradeoff, not an oversight.

The database also stores `raw_rows.raw_data` as a structured raw-row snapshot. It is not a byte-level lossless copy of the original file. Parsing through `pandas.read_csv()` can normalize representations before the row is stored.

The untouched files under `data/raw/` remain the true byte-level source of truth.