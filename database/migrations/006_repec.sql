CREATE TABLE repec_records (
    id SERIAL PRIMARY KEY,
    journal_id INTEGER REFERENCES journals(id),
    dataset_id INTEGER NOT NULL REFERENCES datasets(id),
    raw_row_id INTEGER REFERENCES raw_rows(id),
    source_row_hash VARCHAR(64) NOT NULL,
    source_snapshot_date DATE,        -- the date implied by the source file, if any (usually NULL)
    imported_at TIMESTAMPTZ DEFAULT NOW(),  -- kept separate from source_snapshot_date, see Day 7
    rank INTEGER,
    journal_name_raw VARCHAR(500) NOT NULL,   -- original, e.g. "Econometrica, Econometric Society"
    journal_name_clean VARCHAR(500) NOT NULL, -- publisher suffix stripped, when safe to do so
    publisher_from_name VARCHAR(255),         -- extracted from the suffix, when safe to do so
    publisher_split_confidence VARCHAR(10),   -- 'high','low','none' — see Day 7 splitting logic
    score NUMERIC(10,3),
    items_listed INTEGER,
    simple_if NUMERIC(10,3),
    recursive_if NUMERIC(10,3),
    discounted_if NUMERIC(10,3),
    recursive_discounted_if NUMERIC(10,3),
    h_index INTEGER,
    euclid NUMERIC(10,3),
    raw_json JSONB,
    UNIQUE(dataset_id, source_row_hash)
);

CREATE INDEX idx_repec_journal
ON repec_records(journal_id);