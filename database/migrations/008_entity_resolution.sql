CREATE TABLE entity_match_candidates (
    id SERIAL PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    source_record_table VARCHAR(50) NOT NULL,
    source_record_id INTEGER NOT NULL,
    candidate_journal_id INTEGER NOT NULL REFERENCES journals(id),
    similarity NUMERIC(5,4),
    issn_match BOOLEAN,
    publisher_match BOOLEAN,
    match_method VARCHAR(30) NOT NULL,   -- may become a combined value like 'exact_title_ambiguous+fuzzy_title'
    rank_among_candidates SMALLINT NOT NULL DEFAULT 1,  -- 1 = best candidate, 2 = second-best, etc.
    review_status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (review_status IN ('pending','accepted','rejected')),
    -- This column is the persistent workflow state for manual review, and the
    -- single source of truth for what's been reviewed — never a CSV file.
    reviewed_by VARCHAR(100),
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_id, source_record_table, source_record_id, candidate_journal_id)
);

CREATE INDEX idx_emc_pending
ON entity_match_candidates(review_status)
WHERE review_status = 'pending';

-- Database-level guarantee that at most one candidate can be 'accepted' per
-- source record.
CREATE UNIQUE INDEX uq_one_accepted_candidate
ON entity_match_candidates(source_id, source_record_table, source_record_id)
WHERE review_status = 'accepted';

CREATE TABLE entity_match_decisions (
    id SERIAL PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    source_record_table VARCHAR(50) NOT NULL,
    source_record_id INTEGER NOT NULL,
    journal_id INTEGER REFERENCES journals(id),  -- NULL when decision = 'rejected_no_match'
    match_method VARCHAR(100) NOT NULL,
    confidence NUMERIC(5,4),
    -- Explicit taxonomy so "intentionally unmatched" is distinguishable from
    -- "still needs review":
    decision VARCHAR(20) NOT NULL
        CHECK (decision IN ('accepted','manually_confirmed','rejected_no_match','new_journal')),
    reviewed_by VARCHAR(100),
    reviewed_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_id, source_record_table, source_record_id)
);