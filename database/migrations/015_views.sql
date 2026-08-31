BEGIN;

-- ============================================================================
-- JournalHub — Day 9
-- Migration 015: Website/API query views + journals.updated_at trigger
--
-- Migration 015 is used because migrations 009-014 already exist.
-- ============================================================================


-- ============================================================================
-- 1. Latest SCImago record per (journal, subject_area)
-- ============================================================================

CREATE OR REPLACE VIEW journal_scimago_latest_view AS
SELECT DISTINCT ON (journal_id, subject_area)
       journal_id,
       subject_area,
       year,
       sjr,
       sjr_best_quartile,
       h_index,
       id AS scimago_record_id
FROM scimago_records
WHERE journal_id IS NOT NULL
ORDER BY
    journal_id,
    subject_area,
    year DESC,
    sjr DESC NULLS LAST,
    id DESC;


-- ============================================================================
-- 2. Latest ABDC record per journal
-- ============================================================================

CREATE OR REPLACE VIEW journal_abdc_latest_view AS
SELECT DISTINCT ON (journal_id)
       journal_id,
       rating_year,
       rating,
       for_code,
       for_scheme,
       dataset_id
FROM abdc_records
WHERE journal_id IS NOT NULL
ORDER BY
    journal_id,
    rating_year DESC,
    dataset_id DESC,
    id DESC;


-- ============================================================================
-- 3. Source availability per canonical journal
-- ============================================================================

CREATE OR REPLACE VIEW journal_source_availability_view AS
SELECT
    j.id AS journal_id,
    j.canonical_title,

    EXISTS (
        SELECT 1
        FROM scimago_records s
        WHERE s.journal_id = j.id
    ) AS has_scimago,

    EXISTS (
        SELECT 1
        FROM abdc_records a
        WHERE a.journal_id = j.id
    ) AS has_abdc,

    EXISTS (
        SELECT 1
        FROM abs_records ab
        WHERE ab.journal_id = j.id
    ) AS has_abs,

    EXISTS (
        SELECT 1
        FROM repec_records r
        WHERE r.journal_id = j.id
    ) AS has_repec,

    EXISTS (
        SELECT 1
        FROM ft50_records f
        WHERE f.journal_id = j.id
    ) AS has_ft50

FROM journals j;


-- ============================================================================
-- 4. Journal summary view
-- ============================================================================

CREATE OR REPLACE VIEW journal_summary_view AS
SELECT
    j.id AS journal_id,
    j.canonical_title,
    j.publisher,

    best_sc.sjr AS scimago_current_sjr,
    best_sc.sjr_best_quartile AS scimago_current_quartile,

    ab.rating AS abdc_current_rating,

    EXISTS (
        SELECT 1
        FROM ft50_records f
        WHERE f.journal_id = j.id
    ) AS in_ft50

FROM journals j

LEFT JOIN LATERAL (
    SELECT
        sjr,
        sjr_best_quartile,
        subject_area
    FROM journal_scimago_latest_view v
    WHERE v.journal_id = j.id
    ORDER BY
        CASE v.sjr_best_quartile
            WHEN 'Q1' THEN 1
            WHEN 'Q2' THEN 2
            WHEN 'Q3' THEN 3
            WHEN 'Q4' THEN 4
            ELSE 5
        END,
        v.sjr DESC NULLS LAST,
        v.subject_area ASC
    LIMIT 1
) best_sc
    ON TRUE

LEFT JOIN journal_abdc_latest_view ab
    ON ab.journal_id = j.id;


-- ============================================================================
-- 5. journals.updated_at trigger
-- ============================================================================

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


DROP TRIGGER IF EXISTS trg_journals_updated_at
ON journals;


CREATE TRIGGER trg_journals_updated_at
BEFORE UPDATE ON journals
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();


-- ============================================================================
-- Migration completed successfully.
-- ============================================================================

COMMIT;