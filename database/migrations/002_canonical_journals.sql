CREATE TABLE journals (
    id SERIAL PRIMARY KEY,
    canonical_title VARCHAR(500) NOT NULL,
    normalized_title VARCHAR(500) NOT NULL,
    publisher VARCHAR(255),          -- policy for how this gets filled: see Day 5
    first_observed_year INTEGER,     -- earliest year ANY source recorded this journal;
                                      -- NOT a founding date, the data can't support that claim
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_journals_normalized_title
ON journals(normalized_title);

CREATE TABLE journal_identifiers (
    id SERIAL PRIMARY KEY,
    journal_id INTEGER NOT NULL REFERENCES journals(id),
    identifier_type VARCHAR(30) NOT NULL
        CHECK (identifier_type IN ('ISSN','EISSN','SCIMAGO_SOURCE_ID')),
        -- Controlled vocabulary. Add new values here deliberately, never by
        -- just typing a new string somewhere in ingestion code.
    identifier_value VARCHAR(100) NOT NULL,
    normalized_value VARCHAR(100) NOT NULL,
    source_id INTEGER REFERENCES sources(id),
    is_primary BOOLEAN DEFAULT FALSE,
    UNIQUE(identifier_type, normalized_value)
    -- This constraint alone prevents the same ISSN (or the same SCImago
    -- Sourceid) from ever being attached to two different journals — no
    -- separate partial index is needed for the Sourceid case specifically,
    -- since identifier_type filters by type within the general constraint.
);

CREATE INDEX idx_journal_identifiers_journal
ON journal_identifiers(journal_id);

-- At most one PRIMARY identifier per journal PER TYPE (e.g. one primary ISSN
-- and, separately, one primary EISSN — not one primary overall, since a
-- journal can reasonably have both a primary print and a primary electronic ID).
CREATE UNIQUE INDEX uq_one_primary_per_type
ON journal_identifiers(journal_id, identifier_type)
WHERE is_primary = TRUE;

CREATE TABLE journal_aliases (
    id SERIAL PRIMARY KEY,
    journal_id INTEGER NOT NULL REFERENCES journals(id),
    source_id INTEGER REFERENCES sources(id),
    alias_name VARCHAR(500) NOT NULL,
    normalized_alias VARCHAR(500) NOT NULL,
    UNIQUE(journal_id, normalized_alias)
);

CREATE INDEX idx_journal_aliases_normalized
ON journal_aliases(normalized_alias);

CREATE TABLE journal_source_mapping (
    id SERIAL PRIMARY KEY,
    journal_id INTEGER NOT NULL REFERENCES journals(id),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    source_record_table VARCHAR(50) NOT NULL, -- e.g. 'scimago_records'
    source_record_id INTEGER NOT NULL,
    match_method VARCHAR(30) NOT NULL,
    match_score NUMERIC(4,3),
    match_status VARCHAR(20) DEFAULT 'pending',
    UNIQUE(source_id, source_record_table, source_record_id)
    -- A single source record maps to at most one journal.
);

CREATE INDEX idx_jsm_journal
ON journal_source_mapping(journal_id);