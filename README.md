# JournalHub

JournalHub is a unified journal intelligence platform that consolidates journal metadata, rankings, ratings, identifiers, and historical records from multiple academic journal-ranking sources into a single normalized system.

## Project Status

**Current phase:** Day 9 complete — validation suite, source-aware lineage validation, coverage reporting, deterministic database views, and `updated_at` trigger completed and verified. The Day 8 manual-review queue remains intentionally deferred.

Days 1–9 implementation work is complete. The Day 8 manual-review decisions themselves remain intentionally deferred and must be completed before the final whole-project resolution-state closure.

* **Day 1:** Environment setup and full raw-data inventory completed.
* **Day 2:** Complete PostgreSQL database schema implemented through ordered migrations, covering 18 tables, controlled vocabularies, uniqueness constraints, and foreign-key relationships.
* **Day 3:** Shared normalization utilities, CSV/Excel loaders, validation helpers, transactional dataset-import helpers, canonical-journal resolution, confidence-scored RePEc publisher-suffix splitting, and entity-match candidate helpers completed and tested in isolation.
* **Day 4:** SCImago source-specific ingestion completed from raw CSV files into raw/staging storage, including filename parsing, source-row transformation, validation, transactional ingestion, rejection handling, idempotent retries, and crash-recovery testing.
* **Day 5:** SCImago canonical-journal seeding completed. Every distinct SCImago `sourceid` now resolves to exactly one canonical journal, with source identifiers, source mappings, entity-match decisions, SCImago record links, deterministic representative title/publisher selection, and idempotent reruns verified.
* **Day 6:** ABDC ingestion and entity resolution completed. All six historical ABDC sheets were parsed, transformed, validated, ingested transactionally, and resolved against the canonical journal layer using the ISSN → exact-title → fuzzy-title hierarchy. Rating normalization, FoR scheme mapping, candidate evidence merging, sibling-closing on auto-accept, new-journal ISSN registration, conflict reporting, retry behavior, and resolver fixed-point idempotency were verified.
* **Day 7:** ABS/AJG and RePEc ingestion and entity resolution completed. ABS was transformed from wide format into year-level normalized records while preserving one raw source row per original wide row and reusing its source-row hash across emitted rating-year records. RePEc was ingested without identifiers and resolved using title-only matching with confidence-scored publisher-suffix splitting, safe raw-title fallback for low/none split confidence, exact-title handling, fuzzy candidate generation, evidence merging, automatic acceptance, pending review, and new-journal creation. Real source-data inspection also required two publisher-column width migrations.
* **Day 8:** FT50 ingestion and title-only entity resolution completed. All 50 FT50 records loaded successfully and resolved: 49 were accepted against existing canonical journals and 1 resulted in a new canonical journal. The database-backed manual-review queue and stale-CSV protection were implemented and tested. Manual review of the remaining candidate queue was intentionally deferred.
* **Day 9:** Automated duplicate/integrity, history, entity-resolution, source-aware lineage, and coverage validation completed. Four deterministic database views and the `journals.updated_at` trigger were created and verified. ABS lineage was explicitly validated using the one-to-many `DISTINCT source_row_hash` formula. All 53 loaded datasets passed lineage validation, no dangling source references were detected in the deterministic entity-check sample, all four required views exist, and the `updated_at` trigger was proven to fire.

## Current Ingestion Status

| Source  | Status                     | Records |
| ------- | -------------------------- | ------: |
| SCImago | Loaded + canonicalized     | 139,491 |
| ABDC    | Loaded + entity resolution |  16,214 |
| ABS/AJG | Loaded + entity resolution |   4,749 |
| RePEc   | Loaded + entity resolution |   3,459 |
| FT50    | Loaded + entity resolution |      50 |

All five sources currently have loaded datasets:

| Source  | Dataset count | Loaded | Not loaded |
| ------- | ------------: | -----: | ---------: |
| ABDC    |             1 |      1 |          0 |
| ABS     |             1 |      1 |          0 |
| FT50    |             1 |      1 |          0 |
| REPEC   |             1 |      1 |          0 |
| SCIMAGO |            49 |     49 |          0 |

## Day 9 — Validation Suite, Coverage Report, and Database Views

Day 9 added automated checks intended to catch integrity, lineage, entity-resolution, and coverage problems during future re-imports.

The validation modules are:

```text
validation/duplicate_checks.py
validation/history_checks.py
validation/entity_checks.py
validation/lineage_checks.py
validation/coverage_report.py
```

All five scripts completed successfully without runtime errors.

### Day 9 Duplicate / Integrity Validation

`validation/duplicate_checks.py` verifies:

* `journal_identifiers` uniqueness.
* `journal_source_mapping` uniqueness.
* Existing ISSN conflict-report state.

Verified database constraints:

* `journal_identifiers UNIQUE(identifier_type, normalized_value)` is active.
* `journal_source_mapping UNIQUE(source_id, source_record_table, source_record_id)` is active.

Both uniqueness checks passed.

The existing `reports/issn_conflicts.csv` contains 11 conflict rows. None currently has pending-candidate evidence that allows the validation script to prove its manual resolution state.

These are therefore reported as **unresolved/unverifiable from the current legacy conflict-report format**, not automatically marked as resolved.

This does not mean that 11 duplicate identifiers currently exist in the database. The structural uniqueness constraint is active and the direct uniqueness check passes.

The conflict-report limitation should remain documented until the conflict-report format itself contains sufficient resolution linkage.

### Day 9 SCImago History Validation

`validation/history_checks.py` scans for a journal/subject-area observation in year `Y` and `Y+2` with no observation in `Y+1`.

The final run detected:

**403 historical gaps**

These are written to:

```text
reports/history_gaps.csv
```

The gaps are **informational only**.

A journal can legitimately be absent from a SCImago subject-area file for a year. The project therefore does not treat these gaps as ingestion or data-integrity failures.

### Day 9 Entity-Resolution Validation

`validation/entity_checks.py` performs two checks.

#### Accepted fuzzy-match score recomputation

A deterministic sample of:

**44 accepted fuzzy matches**

was selected.

Results:

* recomputable = 44
* not recomputable = 0
* flagged = 0
* score-drop threshold = 0.050

Therefore:

**PASS — no sampled accepted fuzzy match had a score drop >= 0.050.**

Results are written to:

```text
reports/entity_check_flags.csv
```

The report currently contains only its header because no matches were flagged.

#### Polymorphic source-reference validation

Because `journal_source_mapping` stores the referenced table name and record ID polymorphically, PostgreSQL cannot enforce the referenced source-record table through a normal foreign key.

The validation therefore checks sampled mappings against their literal source tables.

Final deterministic sample:

* mappings selected = 500
* references checked = 500
* dangling references = 0

Results are written to:

```text
reports/dangling_source_references.csv
```

The report contains only its header.

Therefore:

**PASS — no dangling source references were detected in the deterministic sample.**

### Day 9 Source-Aware Lineage Validation

`validation/lineage_checks.py` implements source-specific lineage formulas.

For SCImago, ABDC, RePEc, and FT50:

```text
raw_rows
=
normalized_records
+
ingestion_rejections
```

For ABS:

```text
raw_rows
=
COUNT(DISTINCT source_row_hash)
+
ingestion_rejections
```

The ABS-specific formula is required because one original wide ABS row legitimately produces multiple normalized rating-year records.

### Lineage Results

All 53 loaded datasets passed.

#### SCImago

All 49 SCImago datasets passed with:

```text
raw rows = normalized records
rejections = 0
difference = 0
```

Total SCImago source records:

```text
139,491
```

All 139,491 are represented by normalized SCImago records.

#### ABDC

Dataset 54:

```text
raw rows              = 16,214
normalized records    = 16,214
distinct source rows  = 16,214
rejections            = 0
difference            = 0
```

#### ABS

Dataset 55:

```text
raw rows                     = 1,635
normalized records           = 4,749
distinct source_row_hash     = 1,635
ingestion rejections         = 0
difference                   = 0
```

This confirms that the 4,749 normalized ABS records are a valid one-to-many expansion of the 1,635 original source rows.

The explicit ABS source-aware verification also returned:

```text
distinct source_row_hash = 1,635
ingestion_rejections     = 0
difference               = 0
PASS
```

#### RePEc

Dataset 56:

```text
raw rows              = 3,459
normalized records    = 3,459
distinct source rows  = 3,459
rejections            = 0
difference            = 0
```

#### FT50

Dataset 57:

```text
raw rows              = 50
normalized records    = 50
distinct source rows  = 50
rejections            = 0
difference            = 0
```

Overall:

**53 datasets checked, 0 lineage failures.**

The generated report is:

```text
reports/lineage_checks.csv
```

### Day 9 Coverage Report

`validation/coverage_report.py` generates:

```text
reports/coverage_report.txt
```

Final canonical-journal count:

**20,353**

SCImago seed coverage:

```text
source records = 139,491
linked         = 139,491
unlinked       = 0
```

Downstream source outcomes:

| Source | Records | Matched | New journal | Rejected no match | Pending |
| ------ | ------: | ------: | ----------: | ----------------: | ------: |
| ABDC   |  16,214 |  14,948 |       1,054 |                 0 |     212 |
| ABS    |   4,749 |   4,678 |          65 |                 0 |       6 |
| REPEC  |   3,459 |   1,199 |       2,085 |                 0 |     175 |
| FT50   |      50 |      49 |           1 |                 0 |       0 |

Total downstream records still pending in the Day 9 coverage taxonomy:

**393**

These pending records are intentional because the Day 8 manual-review queue has been deliberately deferred. The coverage report explicitly records this state rather than treating it as ingestion loss.

The numbers should not be confused with the Day 8 review-export counts:

* **578** = pending candidate rows.
* **411** = source records represented by those candidate rows.
* **393** = pending downstream records counted by the Day 9 coverage taxonomy.

These are different levels of aggregation.

No downstream source has a `rejected_no_match` outcome in the current state.

### Day 9 Deterministic Database Views

The database migration for the views is:

```text
database/migrations/015_views.sql
```

The migration creates four views intended for direct use by the future API/website layer:

```text
journal_scimago_latest_view
journal_abdc_latest_view
journal_source_availability_view
journal_summary_view
```

All four views were successfully created and verified.

#### `journal_scimago_latest_view`

Provides one row per:

```text
(journal_id, subject_area)
```

using the latest observed year for each journal/subject-area pair.

The deterministic ordering is:

```text
year DESC,
sjr DESC NULLS LAST,
id DESC
```

This is intentionally not one arbitrary row per journal because a journal can belong to multiple SCImago subject areas.

A multi-area journal was tested:

```text
journal_id = 3970
```

It returned five subject-area rows for 2025:

* Arts and Humanities
* Business, Management and Accounting
* Decision Sciences
* Psychology
* Social Sciences

The exact same query was executed twice and returned identical results.

This verifies the deterministic tie-breaking behavior.

#### `journal_abdc_latest_view`

Returns the latest ABDC rating per journal.

The deterministic ordering is:

```text
rating_year DESC,
dataset_id DESC,
id DESC
```

The dataset and record IDs provide stable tie-breaking if multiple datasets happen to contain the same rating year.

#### `journal_source_availability_view`

Provides one row per canonical journal with boolean availability flags:

```text
has_scimago
has_abdc
has_abs
has_repec
has_ft50
```

This is intended to allow the future website/API layer to answer source-availability questions without reconstructing the relationships independently.

#### `journal_summary_view`

Provides a website/API-oriented summary per canonical journal, including:

```text
journal_id
canonical_title
publisher
scimago_current_sjr
scimago_current_quartile
abdc_current_rating
in_ft50
```

The headline SCImago selection prefers the numerically best quartile:

```text
Q1 → Q2 → Q3 → Q4
```

followed by:

```text
sjr DESC
subject_area ASC
```

This makes selection deterministic when a journal has multiple equally ranked Q1/Q2/etc. subject-area observations.

### Day 9 `updated_at` Trigger

Migration `015_views.sql` also creates:

```text
set_updated_at()
```

and:

```text
trg_journals_updated_at
```

The trigger fires:

```text
BEFORE UPDATE ON journals
```

and sets:

```text
NEW.updated_at = NOW()
```

The trigger was verified directly.

Test:

```sql
UPDATE journals
SET canonical_title = canonical_title
WHERE id = 31;
```

The row's `updated_at` changed from:

```text
2026-08-23 11:04:05.792854+05:30
```

to:

```text
2026-08-31 22:29:43.011925+05:30
```

Therefore the trigger is confirmed operational.

The final trigger definition is:

```text
CREATE TRIGGER trg_journals_updated_at
BEFORE UPDATE ON public.journals
FOR EACH ROW
EXECUTE FUNCTION set_updated_at()
```

### Day 9 Verification Summary

The final Day 9 validation sequence was:

```bash
python -m validation.duplicate_checks
python -m validation.history_checks
python -m validation.entity_checks
python -m validation.lineage_checks
python -m validation.coverage_report
```

All five completed successfully.

Final validation state:

* Duplicate/integrity uniqueness checks = PASS.
* SCImago history gaps = 403 informational records.
* Accepted fuzzy-match sample = 44 checked, 0 flagged.
* Polymorphic source-reference sample = 500 checked, 0 dangling.
* Loaded datasets checked for lineage = 53.
* Lineage failures = 0.
* ABS source-aware lineage = PASS.
* Coverage report generated successfully.
* Canonical journals = 20,353.
* SCImago linked = 139,491 / 139,491.
* Four required database views = present.
* Multi-area SCImago deterministic query = verified identical across repeated execution.
* `updated_at` trigger = present and functionally verified.

## Current Entity Resolution State

SCImago is fully linked.

Downstream source resolution currently remains:

| Source | Total records | Resolved/decided | Pending |
| ------ | ------------: | ---------------: | ------: |
| ABDC   |        16,214 |           16,002 |     212 |
| ABS    |         4,749 |            4,743 |       6 |
| RePEc  |         3,459 |            3,284 |     175 |
| FT50   |            50 |               50 |       0 |

The Day 8 review-export queue remains:

* ABDC = 340 pending candidate rows.
* ABS = 27 pending candidate rows.
* RePEc = 211 pending candidate rows.
* FT50 = 0 pending candidate rows.
* Total = 578 pending candidate rows.
* Source records represented = 411.

These values are candidate-level review quantities and should not be substituted for the Day 9 coverage-report pending count of 393 downstream records.

## Deferred Manual Review

The manual review of the pending candidate queue remains intentionally deferred.

This is a deliberate project-state decision, not an ingestion or lineage failure.

Before the final resolution-state closure, the following must still be completed:

1. Review all pending source-record groups.
2. Enter `accepted`, `new_journal`, or `rejected_no_match`.
3. For `accepted`, select the specific candidate.
4. Before `new_journal`, search the canonical journal table for a possible existing entity.
5. Apply decisions using `entity_resolution/apply_review_decisions.py`.
6. Inspect `reports/stale_review_rows.csv`.
7. Confirm no stale decisions were incorrectly applied.
8. Confirm the pending candidate population reaches 0.
9. Run the whole-project unresolved-without-decision integrity check.
10. Run the sibling-closing invariant.
11. Confirm legitimate `rejected_no_match` outcomes are closed decisions.
12. Complete final resolution-state validation.
13. Update the README to record manual-review completion.
14. Make the final resolution-state completion commit.

The Day 9 validation suite must continue to treat the deferred review state honestly. Pending records must not be artificially converted to accepted or rejected outcomes merely to make the coverage report show zero pending.

## Data Sources

JournalHub currently works with:

* SCImago Journal & Country Rank
* ABDC — Australian Business Deans Council Journal Quality List
* ABS/AJG — Academic Journal Guide
* RePEc
* Financial Times 50 (FT50)

## Raw Data Policy

The directory:

```text
data/raw/
```

contains the untouched source files used as the byte-level source of truth.

Normalized ingestion records must never modify the original source representation before it is stored in raw-data lineage fields.

Normalization utilities are used only when constructing normalized records.

The untouched files under `data/raw/` remain the authoritative byte-level source of truth.

## Database Schema

The PostgreSQL database schema is implemented through ordered migrations in:

```text
database/migrations/
```

The schema covers:

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

Current migrations include:

```text
001_sources_and_datasets.sql
002_canonical_journals.sql
003_scimago.sql
004_abdc.sql
005_abs.sql
006_repec.sql
007_ft50.sql
008_entity_resolution.sql
009_expand_scimago_coverage.sql
010_expand_entity_match_decision_method.sql
011_expand_abdc_publisher.sql
012_expand_entity_resolution_match_methods.sql
013_expand_repec_publisher.sql
014_expand_journal_publisher.sql
015_views.sql
```

Migration `015_views.sql` contains the Day 9 database views and `updated_at` trigger.

## Important Schema Corrections

Two real source-data cases exceeded the original 255-character publisher-column width.

Migration `013_expand_repec_publisher.sql` expands:

```text
repec_records.publisher_from_name
```

from `VARCHAR(255)` to `VARCHAR(500)`.

Migration `014_expand_journal_publisher.sql` expands:

```text
journals.publisher
```

from `VARCHAR(255)` to `VARCHAR(500)`.

These changes were made after inspecting actual source data. Source values are preserved rather than truncated.

ABDC also required its publisher field to support values up to 500 characters.

## Raw File SHA-256 Uniqueness

`raw_files.sha256` is globally unique across all sources.

This is intentional: if two different source folders contain byte-identical files, the system treats them as the same raw artifact.

This is a deliberate simplicity tradeoff, not an oversight.

The database also stores `raw_rows.raw_data` as a structured raw-row snapshot. It is not a byte-level lossless copy of the original file.

Parsing through `pandas.read_csv()` can normalize representations before the row is stored.

The untouched files under `data/raw/` remain the true byte-level source of truth.

## Project Conventions

* Raw source files are never modified by ingestion.
* Source-specific tables preserve source-level observations and provenance.
* Canonical tables represent normalized cross-source entities.
* Strong source identifiers take precedence over weaker entity-resolution signals when explicitly supplied.
* Normalized title matching must not override a supplied authoritative source identifier during source-specific canonical seeding.
* Database writes belonging to one logical ingestion or canonicalization operation should be transactional.
* Idempotency is required for repeatable ingestion and canonicalization operations.
* Deterministic ordering and tie-breaking must be used whenever multiple equivalent source records could otherwise produce non-reproducible canonical values.
* Source-specific wide-to-long transformations must retain raw-row lineage to the original source row.
* Source-provided dates must not be invented when the source does not supply them.
* Publisher-suffix parsing must be confidence-aware and must safely decline ambiguous splits.
* Database-backed review state is authoritative; exported review CSVs are snapshots only.
* A stale review CSV must never overwrite newer database state.
* Manual entity-resolution decisions must distinguish `accepted`, `new_journal`, and `rejected_no_match`.
* `new_journal` must not be selected merely because candidate similarity is low; the canonical table should first be checked for an existing differently named entity.
* `rejected_no_match` is a legitimate closed resolution outcome and must not be treated as an unresolved error.
* Validation checks should be deterministic wherever sampling is involved.
* ABS lineage must use the source-aware `DISTINCT source_row_hash` formula because one raw row can produce multiple normalized records.
* Database views used by future API/website code must have deterministic tie-breaking.
* Changes discovered during implementation that materially affect entity identity, provenance, idempotency, schema compatibility, validation correctness, or downstream behavior should be incorporated into the project plan and documented in the repository.

## Fuzzy-Matching Candidate Blocking

Title fuzzy matching currently uses first-four-character blocking on `journals.normalized_title` before applying RapidFuzz `token_sort_ratio`.

This is acceptable for the current dataset size.

If the canonical journal population grows past roughly 50,000 rows, revisit this implementation and consider PostgreSQL `pg_trgm` trigram indexing, since first-four-character blocking degrades for very common title prefixes such as `journal of`.

## Repository Reports Generated During Day 9

The validation suite generates:

```text
reports/issn_conflicts.csv
reports/history_gaps.csv
reports/entity_check_flags.csv
reports/dangling_source_references.csv
reports/lineage_checks.csv
reports/coverage_report.txt
```

The existing review snapshot remains:

```text
reports/ambiguous_matches.csv
```

and remains a snapshot of the database-backed pending queue rather than authoritative state.

## End-of-Day 9 State

**Day 9 implementation is complete.**

The project now has:

* complete ingestion of all five source families;
* canonical journal resolution infrastructure;
* database-backed manual review infrastructure;
* stale-review protection;
* duplicate/integrity validation;
* informational historical-gap detection;
* fuzzy-match regression checks;
* polymorphic source-reference validation;
* source-aware lineage validation;
* ABS-specific one-to-many lineage handling;
* coverage reporting;
* deterministic SCImago latest-data view;
* deterministic ABDC latest-data view;
* source-availability view;
* journal summary view;
* functional `updated_at` trigger.

The remaining unresolved work is the intentionally deferred manual entity-resolution review and the final whole-project resolution-state closure.

The Day 9 completion commit should be:

```text
Day 9: source-aware lineage validation, coverage report, deterministic views, updated_at trigger
```

A repository-wide pytest result should only be recorded here after that suite is explicitly run and passed.
