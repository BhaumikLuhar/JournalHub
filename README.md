# JournalHub

JournalHub is a unified journal intelligence platform that consolidates journal metadata, rankings, ratings, identifiers, and historical records from multiple academic journal-ranking sources into a single normalized system.

## Project Status

**Current phase:** Day 7 complete — ABS/AJG wide-to-long ingestion + entity resolution and RePEc title-only ingestion + entity resolution.

Days 1–7 are complete.

* **Day 1:** Environment setup and full raw-data inventory completed.
* **Day 2:** Complete PostgreSQL database schema implemented through ordered migrations, covering 18 tables, controlled vocabularies, uniqueness constraints, and foreign-key relationships.
* **Day 3:** Shared normalization utilities, CSV/Excel loaders, validation helpers, transactional dataset-import helpers, canonical-journal resolution, confidence-scored RePEc publisher-suffix splitting, and entity-match candidate helpers completed and tested in isolation.
* **Day 4:** SCImago source-specific ingestion completed from raw CSV files into raw/staging storage, including filename parsing, source-row transformation, validation, transactional ingestion, rejection handling, idempotent retries, and crash-recovery testing.
* **Day 5:** SCImago canonical-journal seeding completed. Every distinct SCImago `sourceid` now resolves to exactly one canonical journal, with source identifiers, source mappings, entity-match decisions, SCImago record links, deterministic representative title/publisher selection, and idempotent reruns verified.
* **Day 6:** ABDC ingestion and entity resolution completed. All six historical ABDC sheets were parsed, transformed, validated, ingested transactionally, and resolved against the canonical journal layer using the ISSN → exact-title → fuzzy-title hierarchy. Rating normalization, FoR scheme mapping, candidate evidence merging, sibling-closing on auto-accept, new-journal ISSN registration, conflict reporting, retry behavior, and resolver fixed-point idempotency were verified.
* **Day 7:** ABS/AJG and RePEc ingestion and entity resolution completed. ABS was transformed from wide format into year-level normalized records using dynamic `AJG\d{4}` detection, while preserving one raw source row per original wide row and reusing its source-row hash across emitted rating-year records. RePEc was ingested without identifiers and resolved using title-only matching with confidence-scored publisher-suffix splitting, safe raw-title fallback for low/none split confidence, exact-title handling, fuzzy candidate generation, evidence merging, automatic acceptance, pending review, and new-journal creation. Real source-data inspection also required two publisher-column width migrations.

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
* RePEc publisher-suffix splitting uses confidence levels and safely declines ambiguous cases instead of blindly splitting every comma-containing title.

### Day 4 Verification

The following were verified successfully:

* 49 SCImago CSV files discovered and processed.
* All 49 SCImago datasets have `status='loaded'`.
* 139,491 SCImago records were loaded into `scimago_records`.
* `raw_rows` contains 139,491 SCImago source-row snapshots.
* Dataset `record_count` matches the actual `scimago_records` count for every dataset.
* `scimago_categories` and `scimago_areas` were populated without orphaned rows.
* `journal_id` remained NULL for all SCImago records before Day 5 entity resolution, as required.
* A second ingestion run skips all already-loaded datasets without creating duplicates.
* Deliberate transaction failure was tested; failed imports rolled back their raw and normalized rows completely.
* Failed datasets can be retried successfully.
* SCImago `-` quartile values are normalized to NULL.
* SJR values are parsed as decimals and validated against the 0–100 range.
* No SCImago ingestion rejections were present after successful ingestion.
* Common normalization, SCImago parser, transformer, and validator manual tests all pass.

### Day 5 Verification

The following were verified successfully:

* 139,491 SCImago records were available as the input dataset for canonical seeding.
* 17,148 distinct SCImago `sourceid` values were identified.
* 17,148 canonical journal rows exist in `journals`.
* Every distinct SCImago `sourceid` resolves to exactly one canonical journal.
* Every SCImago record has a non-NULL `journal_id`.
* 17,148 `SCIMAGO_SOURCE_ID` identifiers are present in `journal_identifiers`.
* 139,491 SCImago records have corresponding entries in `journal_source_mapping`.
* 139,491 SCImago records have corresponding entries in `entity_match_decisions`.
* All SCImago source mappings use `match_method='source_id'`, `match_score=1.0`, and `match_status='accepted'`.
* All SCImago entity-match decisions use `match_method='source_id'`, `confidence=1.0`, and `decision='accepted'`.
* No SCImago `sourceid` is linked to more than one canonical journal.
* Strong source identifiers take precedence over normalized-title matching during SCImago canonical seeding.
* The canonical helper was corrected so that a supplied strong source identifier does not fall through to ISSN/title matching when that source identifier has not yet been registered.
* 19 Sourceids that were initially collapsed through normalized-title matching were identified and repaired into separate canonical journals.
* The repaired 19 Sourceids now each have their own canonical journal and `SCIMAGO_SOURCE_ID` identifier.
* The repair was performed transactionally and preserved the 139,491 SCImago source mappings and entity-match decisions.
* The full SCImago canonical build was executed a second time after repair.
* The second canonical-build run created 0 new journals and reused all 17,148 existing canonical journals.
* The canonical journal count remained 17,148 after the second run, confirming idempotency.
* `first_observed_year` is populated for all 17,148 canonical journals.
* The deterministic multi-area representative-row rule was spot-checked against a real SCImago `sourceid`.
* For a multi-area journal with multiple rows in its latest year, the alphabetically-first subject area among rows with a non-null publisher was selected.
* Canonical publisher selection was verified against the deterministic representative-row rule.
* 2,144 journals currently have no canonical publisher, but none of those journals had a usable publisher in their latest-year SCImago rows; therefore no publisher backfill was required or performed.
* No duplicate `journal_identifiers` rows were found for the inspected repaired journal.
* The apparent duplicate identifier observed during joined inspection was confirmed to be caused by multiple SCImago records joining to the same canonical journal, not duplicate identifier rows.
* The SCImago canonical build and repair leave source-specific raw publisher values intact in `scimago_records.publisher_raw`.

## Current Ingestion Status

| Source | Status | Records |
|---|---|---:|
| SCImago | Loaded + canonicalized | 139,491 |
| ABDC | Loaded + entity resolution | 16,214 |
| ABS/AJG | Loaded + entity resolution | 4,749 |
| RePEc | Loaded + entity resolution | 3,459 |
| FT50 | Pending | — |

## Entity Resolution Status

SCImago ingestion records are now linked to canonical journals.

At the end of Day 5:

* `scimago_records.journal_id` is populated for all 139,491 SCImago records.
* 17,148 distinct SCImago `sourceid` values map one-to-one to 17,148 canonical journals.
* Each SCImago Sourceid has a corresponding `SCIMAGO_SOURCE_ID` entry in `journal_identifiers`.
* Each SCImago source record has a corresponding accepted entry in `journal_source_mapping`.
* Each SCImago source record has a corresponding accepted entry in `entity_match_decisions`.
* SCImago canonical seeding is idempotent: rerunning the canonical-build pipeline does not create additional canonical journals.

### ABDC Entity Resolution Status

ABDC records are now linked to canonical journals where the resolver has sufficient evidence.

At the end of Day 6:

* 16,214 ABDC records are loaded for dataset 54.
* 16,002 ABDC records have a non-NULL `journal_id`.
* 212 ABDC records remain unresolved and are intentionally retained for review.
* 16,002 ABDC entity-match decisions exist for the ABDC source.
* 391 ABDC candidate rows exist across accepted, pending, and rejected review states.
* 16,001 ABDC source-to-journal mappings exist.
* 204 of the 212 unresolved records have an ISSN or online ISSN; 8 have neither.
* All 212 unresolved records currently have pending candidate evidence.
* Accepted candidates never coexist with pending sibling candidates for the same source record.
* Repeated execution of the resolver is stable at the current unresolved fixed point.

### ABS/AJG Entity Resolution Status

ABS/AJG records are now linked to canonical journals where the resolver has sufficient evidence.

At the end of Day 7:

* 4,749 ABS records are loaded for dataset 55.
* 4,743 ABS records have a non-NULL `journal_id`.
* 6 ABS records remain unresolved and are retained for review.
* 4,743 ABS entity-match decisions exist for dataset 55:
  * 4,192 `exact_issn`
  * 486 `exact_title`
  * 65 `new_journal`
* 1,635 original ABS source rows are retained in `raw_rows`.
* Each original ABS wide source row can emit multiple normalized rating-year records while retaining the same `source_row_hash`.
* ABS rating-year totals are:
  * 2018 = 1,479
  * 2021 = 1,635
  * 2024 = 1,635
* The real ABS rating scale was inspected directly and confirmed as `1`, `2`, `3`, `4`, and `4*`.
* ABS raw-row lineage is intact: no normalized ABS record is missing its referenced raw row.

### RePEc Entity Resolution Status

RePEc has no journal identifier, so resolution is title-only.

At the end of Day 7:

* 3,459 RePEc records are loaded for dataset 56.
* 3,284 RePEc records have a non-NULL `journal_id`.
* 175 RePEc records remain unresolved and are intentionally retained for review.
* 3,284 RePEc entity-match decisions exist:
  * 1,173 `exact_title`
  * 2 `exact_title_ambiguous+fuzzy_title`
  * 24 `fuzzy_title`
  * 2,085 `new_journal`
* 211 pending RePEc candidate rows remain for Day 8 manual review.
* RePEc publisher-suffix split confidence is:
  * 3,014 `high`
  * 445 `low`
* No RePEc records have a non-NULL `source_snapshot_date`, because the source file provides no source date.
* `imported_at` is populated for all 3,459 RePEc records.
* RePEc has 3,459 distinct raw-row references and 3,459 distinct source-row hashes.
* No RePEc record has lost raw-row lineage.
* The RePEc dataset is `status='loaded'` with `record_count=3,459`.
* The resolver was rerun after correcting a publisher-width constraint and completed all 176 initially unresolved records without runtime failures.
* The remaining 175 unresolved records are intentional review cases rather than ingestion/resolution crashes.

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

Normalized ingestion records must never modify the original source representation before it is stored in raw-data lineage fields.

Normalization utilities are used only when constructing normalized records.

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

### Day 7 Schema Corrections

Two real source-data cases exceeded the original 255-character publisher-column width.

Migration `013_expand_repec_publisher.sql` expands:

`repec_records.publisher_from_name` from `VARCHAR(255)` to `VARCHAR(500)`.

Migration `014_expand_journal_publisher.sql` expands:

`journals.publisher` from `VARCHAR(255)` to `VARCHAR(500)`.

These changes were made after inspecting the actual RePEc source data. The longest observed `publisher_from_name` value was 279 characters, and the longest canonical journal publisher value encountered during resolution was therefore required to fit within the expanded canonical publisher field. Source values are preserved rather than truncated.

## Raw File SHA-256 Uniqueness

`raw_files.sha256` is globally unique across all sources.

This is intentional: if two different source folders contain byte-identical files, the system treats them as the same raw artifact.

This is a deliberate simplicity tradeoff, not an oversight.

The database also stores `raw_rows.raw_data` as a structured raw-row snapshot. It is not a byte-level lossless copy of the original file.

Parsing through `pandas.read_csv()` can normalize representations before the row is stored.

The untouched files under `data/raw/` remain the true byte-level source of truth.

## Day 5 — SCImago Canonical Journal Seeding

SCImago is used as the initial canonical-journal seeding source because its records provide strong source identifiers and rich journal metadata.

Canonical journals are seeded through the idempotent `get_or_create_canonical_journal` helper.

For SCImago canonical seeding, `SCIMAGO_SOURCE_ID` is treated as the strongest source-specific identifier. When a SCImago Sourceid already has an associated canonical identifier, the existing journal is reused.

When the Sourceid is not yet registered, a new canonical journal is created rather than allowing normalized-title matching to collapse it into an existing journal.

This ensures that every distinct SCImago `sourceid` has exactly one canonical journal during SCImago seeding.

### Deterministic Representative Row

A SCImago `sourceid` may have multiple rows for the same year because a journal can be classified into multiple subject areas.

The representative row is selected deterministically:

1. Find the maximum year present for the `sourceid`.
2. Among rows from that year, prefer rows with a non-null, non-empty `publisher_raw`.
3. Among those rows, select the alphabetically-first `subject_area`.
4. If none of the latest-year rows has a publisher, select the alphabetically-first `subject_area` regardless of publisher availability.

This is an arbitrary but deterministic tie-break. The purpose is reproducibility and stable reruns rather than claiming that one subject-area row is inherently more authoritative than another.

### Canonical Publisher Policy

When a canonical journal is created from SCImago, `journals.publisher` is populated from the `publisher_raw` value of the deterministically selected representative SCImago row.

This value represents the best known current publisher at canonical journal creation time. It is not treated as a verified authoritative publisher fact and may later be overwritten when a more authoritative source is incorporated.

The original source-specific publisher value remains in `scimago_records.publisher_raw` and in the corresponding publisher columns of other source tables. Source provenance is therefore preserved even if the canonical publisher is later changed.

If none of the applicable representative candidate rows contains a usable publisher, the canonical publisher may remain NULL.

### `first_observed_year` Policy

`journals.first_observed_year` is the earliest year in which the journal was observed for its SCImago `sourceid` across the currently loaded SCImago data.

It is not the journal's founding year, launch year, or publication-history start date.

As additional historical SCImago data or other year-bearing sources are loaded in the future, this value may become earlier.

### Source-Specific Provenance

Canonicalization does not replace or destroy source-specific metadata.

The original SCImago records remain in `scimago_records`, including:

* `sourceid`
* `title`
* `issn_raw`
* `publisher_raw`
* `subject_area`
* `year`
* other source-specific SCImago fields

Canonical fields in `journals` represent the normalized entity layer, while source tables retain source-specific observations and provenance.

### Day 5 Idempotency

The SCImago canonical-build pipeline is designed to be safely rerunnable.

A second execution against an already-canonicalized SCImago dataset must:

* reuse existing canonical journals through their `SCIMAGO_SOURCE_ID`;
* create no additional canonical journals;
* preserve the existing `journal_id` relationships;
* preserve source mappings and entity-match decisions;
* leave the canonical journal count unchanged.

The Day 5 pipeline was executed twice successfully. The second execution created 0 new journals and reused all 17,148 existing canonical journals.

## Day 6 — ABDC Ingestion + Entity Resolution

ABDC is the first non-SCImago source resolved against the canonical journal layer seeded on Day 5.

The real ABDC workbook contains six historical rating sheets. Because the workbook uses different header positions and column names across years, parsing uses a two-signal header detector rather than a fixed row number.

### Supported ABDC Years and FoR Schemes

2025 — ANZSRC2020

2022 — ANZSRC2008

2019 — ANZSRC2008

2016 — ANZSRC2008

2013 — ANZSRC2008

2010 — ANZSRC2008

### ABDC Rating Policy

Ratings are normalized to the controlled values A*, A, B, and C.

Case and surrounding whitespace are normalized, while blank source ratings become NULL.

The real workbook contains one lowercase `c` in the 2010 sheet. It is normalized to `C`. The real 2016 sheet also contains one blank rating, which is preserved as NULL. Neither case is treated as an ingestion rejection.

### ABDC Publisher-Length Correction

A real 2016 ABDC publisher value is 302 characters long.

`abdc_records.publisher` therefore uses `VARCHAR(500)` so the source observation is preserved without truncation.

### Entity-Resolution Hierarchy

For each unresolved ABDC record, resolution proceeds through:

Exact ISSN/EISSN matching.

Exact normalized-title matching.

Fuzzy normalized-title candidate generation using RapidFuzz.

When an exact title is ambiguous, candidate evidence is stored and fuzzy matching continues. If fuzzy evidence refers to the same candidate journal, candidate upsert logic merges the evidence into one row, producing a combined method such as `exact_title_ambiguous+fuzzy_title`.

High-confidence fuzzy matches are automatically accepted and pending siblings are explicitly rejected. Medium-confidence candidates remain pending for later review. Low-confidence/no-match cases can create a new canonical journal, with usable ISSN/EISSN values registered immediately.

### Candidate Review Semantics

`entity_match_candidates` is an evidence/review table rather than a one-to-one mirror of `entity_match_decisions`. A source record can therefore have multiple candidate rows while no final decision exists.

Once one candidate is accepted, all pending sibling candidates for that source record are closed as rejected. This keeps already-resolved records out of the future manual-review queue.

The candidate identity uniqueness constraint prevents duplicate `(source_id, source_record_table, source_record_id, candidate_journal_id)` rows.

### ABDC Operational Results

The completed dataset contains:

16,214 ABDC records.

16,002 resolved records.

212 unresolved records.

16,002 entity-match decisions.

391 candidate rows.

16,001 source mappings.

0 ingestion rejections.

0 duplicate `(dataset_id, source_row_hash, rating_year)` groups.

The six year totals are 2010 = 2,662; 2013 = 2,765; 2016 = 2,777; 2019 = 2,679; 2022 = 2,680; and 2025 = 2,651.

The current unresolved population is intentionally retained for review rather than being forced into low-confidence automatic merges.

### ABDC Idempotency and Fixed Point

A second ingestion run skips the already-loaded workbook. Re-running entity resolution processes the remaining unresolved records safely.

After the unresolved population reached 212 records, another full resolver run processed all 212 successfully but changed none of the database counts. The fixed point is currently:

18,202 canonical journals.

16,002 ABDC decisions.

391 ABDC candidates.

16,001 ABDC mappings.

212 unresolved ABDC records.

One resolved record (`abdc_records.id = 8082`, 4OR) has the known manual-test artifact of an accepted decision without a corresponding source mapping. This is preserved as a documented test artifact rather than being altered blindly.

### Day 6 Verification

The following were verified:

* All six ABDC sheets and their real-world header structures.
* Parser and transformer behavior across all six sheets.
* Rating normalization, including the real lowercase `c` and blank 2016 rating.
* FoR scheme mapping for all six years.
* Transactional ingestion and retry behavior.
* SHA-256 lineage for the loaded workbook.
* Zero ingestion rejections.
* Zero duplicate source-row/rating-year keys.
* ISSN exact matching and conflict-safe behavior.
* Exact-title matching and ambiguous-title handling.
* Fuzzy-title matching and RapidFuzz candidate generation.
* Candidate evidence merging.
* Automatic acceptance with sibling closure.
* The six explicit `exact_title_ambiguous+fuzzy_title` cases.
* New-journal creation with immediate ISSN registration.
* Stable repeated ingestion and resolution at the 212-record unresolved fixed point.

## Day 7 — ABS/AJG Ingestion + Entity Resolution

ABS/AJG is a wide-format source: one source row contains journal metadata plus multiple rating-year columns.

### ABS Parser

The ABS parser uses the shared CSV loader with a standard comma delimiter.

The real file loaded successfully from:

`data/raw/abs/abs_ajg_2024.csv`

The file contains 1,635 source rows and the columns:

* `ISSN`
* `FIELD`
* `TITLE`
* `PUBLISHER`
* `AJG2024`
* `AJG2021`
* `AJG2018`

### ABS Dynamic Wide-to-Long Transformation

Rating columns are detected dynamically using:

`AJG\d{4}`

The implementation does not hardcode the currently observed years. This allows future files containing columns such as `AJG2027` to be handled without changing the transformer.

For every non-empty rating cell, one normalized ABS record is emitted with:

* normalized journal title
* cleaned field
* normalized ISSN
* cleaned publisher
* four-digit rating year
* cleaned rating value
* source-row hash

The original wide-row hash is deliberately reused across every rating-year record emitted from the same source row.

This is valid because uniqueness includes the rating year:

`UNIQUE(dataset_id, source_row_hash, rating_year)`

### ABS Real Rating-Scale Verification

The real source file was inspected directly before validation rules were finalized.

Observed values in each of `AJG2024`, `AJG2021`, and `AJG2018` were:

`1`, `2`, `3`, `4`, `4*`

No assumed rating scale was used in place of inspecting the source data.

### ABS Operational Results

The source file contains:

1,635 original wide source rows.

The unpivot produced:

4,749 normalized ABS records.

The year distribution is:

* 2018 = 1,479
* 2021 = 1,635
* 2024 = 1,635

The 2018 total is lower because 156 source rows have no 2018 rating value.

The source-row hash fanout was verified:

* 1,635 unique source-row hashes.
* 1,479 hashes emit 3 rating-year records.
* 156 hashes emit 2 rating-year records.
* Every source row therefore emits exactly 2 or 3 normalized records.
* The same hash is reused across all rating-year records from each source row.

The original 1,635 wide rows are stored in `raw_rows`, preserving one raw source-row snapshot per original ABS row.

### ABS Entity Resolution

ABS uses the shared entity-resolution hierarchy from Day 6.

ISSN matching is attempted before title matching.

At the end of Day 7:

* 4,749 total ABS records.
* 4,743 resolved.
* 6 unresolved.
* 4,192 accepted through `exact_issn`.
* 486 accepted through `exact_title`.
* 65 created as `new_journal`.
* 27 pending ABS candidate rows.
* 0 ABS records have lost raw-row lineage.

The ABS resolver completed all 4,749 processing attempts without runtime failures.

## Day 7 — RePEc Ingestion + Title-Only Entity Resolution

RePEc has no ISSN or other journal identifier in the source file. Resolution therefore depends entirely on title evidence.

The real source file is:

`data/raw/repec/repec_journals_aggregate.csv`

It contains 3,459 source rows and 10 columns:

* `rank`
* `journals`
* `score`
* `items_listed`
* `simple_if`
* `recursive_if`
* `discounted_if`
* `recursive_discounted_if`
* `h_index`
* `euclid`

### RePEc Transformation

Each source row produces exactly one normalized `repec_records` row.

The source journal string is retained as `journal_name_raw`.

A confidence-scored publisher-suffix splitter produces:

* `journal_name_clean`
* `publisher_from_name`
* `publisher_split_confidence`

The splitter is intentionally conservative. Ambiguous titles are allowed to remain unsplit instead of assuming that the text after a comma is always a publisher.

Real transformation results:

* 3,459 normalized records.
* 3,459 unique source-row hashes.
* 3,459 distinct source rows.
* 3,014 `high` publisher split confidence.
* 445 `low` publisher split confidence.
* No `none` confidence rows in the loaded dataset.
* `source_snapshot_date = NULL` for all 3,459 rows.

RePEc numeric fields use standard `.` decimal parsing. The source does not use the SCImago comma-decimal representation, so the SCImago-specific decimal parser is not used.

### RePEc Date Policy

The RePEc source file has no source snapshot date.

Therefore:

`source_snapshot_date = NULL`

for every RePEc record.

The database's:

`imported_at`

field is populated automatically by the schema and records the actual ingestion time.

The implementation does not invent today's date and place it into a field that implies a source-provided date.

### RePEc Ingestion

The initial ingestion attempt exposed two real publisher-length cases exceeding the original 255-character schema width.

The longest observed `publisher_from_name` was 279 characters.

Migration `013_expand_repec_publisher.sql` expanded `repec_records.publisher_from_name` to `VARCHAR(500)`.

After that correction, the full RePEc file loaded successfully:

* raw rows = 3,459
* normalized records = 3,459
* rejected = 0
* duplicates = 0
* dataset id = 56
* dataset status = `loaded`
* dataset record count = 3,459

During entity resolution, the same real publisher-length condition was encountered when a new canonical journal was created. The canonical `journals.publisher` field was therefore expanded from `VARCHAR(255)` to `VARCHAR(500)` through migration `014_expand_journal_publisher.sql`.

The failed resolver transaction rolled back cleanly, leaving the affected RePEc record unresolved. After the schema correction, only the unresolved population was retried and completed without failures.

### RePEc Entity-Resolution Strategy

ISSN matching is deliberately skipped because RePEc provides no ISSN identifier in this dataset.

Resolution proceeds through title evidence:

1. Existing accepted/manual decision is reapplied when present.
2. Exact normalized-title matching is attempted against the publisher-cleaned title.
3. One exact match is accepted directly.
4. Multiple exact matches are stored as `exact_title_ambiguous` candidate evidence and fuzzy matching continues.
5. Fuzzy normalized-title matching produces up to five candidates.
6. For low/none publisher-split confidence, the original raw title is also considered so that an unsafe publisher split cannot force a bad match.
7. Candidate evidence is merged through the shared candidate upsert helper.
8. The strongest merged candidate is automatically accepted at the high-confidence threshold.
9. Medium-confidence candidates remain pending for Day 8 review.
10. Records without a sufficiently strong candidate can create a new canonical journal.

Because RePEc has no ISSN safety net, a larger manual-review population is expected than for ABDC or ABS.

### RePEc Operational Results

The final dataset contains:

3,459 RePEc records.

3,284 resolved records.

175 unresolved records.

3,284 entity-match decisions.

The decision distribution is:

* 1,173 `accepted / exact_title`
* 2 `accepted / exact_title_ambiguous+fuzzy_title`
* 24 `accepted / fuzzy_title`
* 2,085 `new_journal`

There are 211 pending RePEc candidate rows.

The 211 pending candidate rows are the explicit Day 8 RePEc review workload.

The final raw-lineage checks show:

* 3,459 RePEc records.
* 3,459 distinct `raw_row_id` values.
* 3,459 distinct `source_row_hash` values.
* 0 records with missing raw-row lineage.

The final dataset state is:

* dataset id = 56
* source = REPEC
* dataset year = NULL
* file = `repec_journals_aggregate.csv`
* status = `loaded`
* record count = 3,459

### Day 7 Verification

The following were verified:

* ABS parser loads the real CSV successfully.
* ABS rating columns are detected dynamically using `AJG\d{4}`.
* Real ABS rating values were inspected directly.
* ABS wide-to-long transformation produces 4,749 records from 1,635 source rows.
* ABS source-row hashes are reused correctly across emitted rating-year records.
* ABS rating-year counts are 2018 = 1,479, 2021 = 1,635, 2024 = 1,635.
* ABS raw-row lineage is intact.
* ABS entity resolution completed with 4,743 resolved and 6 unresolved records.
* RePEc parser loads the real CSV successfully.
* RePEc transformation produces exactly one normalized record per source row.
* RePEc numeric decimal values are parsed using standard `.` decimal syntax.
* RePEc publisher-split confidence was verified as 3,014 high and 445 low.
* RePEc source snapshot date is NULL for all 3,459 records.
* RePEc `imported_at` is populated for all 3,459 records.
* RePEc raw-row and source-row-hash cardinalities are both 3,459.
* RePEc ingestion completed with 3,459 records and 0 rejections.
* The two publisher-width migrations were applied after inspecting actual source values.
* RePEc entity resolution completed without runtime failures after the schema correction.
* 3,284 RePEc records have final journal assignments.
* 175 RePEc records remain intentionally unresolved.
* 211 pending RePEc candidate rows were recorded as the Day 8 review workload.
* RePEc raw lineage has 0 missing references.
* Dataset 56 is loaded with `record_count=3,459`.

## Project Conventions

* Raw source files are never modified by ingestion.
* Source-specific tables preserve source-level observations and provenance.
* Canonical tables represent normalized cross-source entities.
* Strong source identifiers take precedence over weaker entity-resolution signals when explicitly supplied.
* Normalized title matching must not override a supplied authoritative source identifier during source-specific canonical seeding.
* Database writes that belong to one logical ingestion or canonicalization operation should be transactional.
* Idempotency is required for repeatable ingestion and canonicalization operations.
* Deterministic ordering and tie-breaking must be used whenever multiple equivalent source records could otherwise produce non-reproducible canonical values.
* Source-specific wide-to-long transformations must retain raw-row lineage to the original source row.
* Source-provided dates must not be invented when the source does not supply them.
* Publisher-suffix parsing must be confidence-aware and must safely decline ambiguous splits.
* Changes discovered during implementation that materially affect entity identity, provenance, idempotency, schema compatibility, or downstream correctness should be incorporated into the project plan and documented in the repository.

Day 6 completion is based on the ABDC parser/transformer/resolver manual tests, database integrity checks, ingestion verification, and repeated ingest/resolve fixed-point checks performed during implementation. Day 7 completion is based on the ABS and RePEc parser/transformer checks, real source-value inspection, transactional ingestion results, entity-resolution checks, lineage validation, and the final database spot-checks described above.

A repository-wide pytest result should only be recorded here after that suite is explicitly run and passed.

### Title fuzzy-matching candidate blocking

Title fuzzy matching currently uses first-four-character blocking on `journals.normalized_title` before applying RapidFuzz `token_sort_ratio`.

This is acceptable for the current dataset size. If the canonical journal population grows past roughly 50,000 rows, revisit this implementation and consider PostgreSQL `pg_trgm` trigram indexing, since first-four-character blocking degrades for very common title prefixes such as "journal of".
