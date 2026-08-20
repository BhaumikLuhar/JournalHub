CREATE TABLE abs_records (
    id SERIAL PRIMARY KEY,
    journal_id INTEGER REFERENCES journals(id),
    dataset_id INTEGER NOT NULL REFERENCES datasets(id),
    raw_row_id INTEGER REFERENCES raw_rows(id),
    source_row_hash VARCHAR(64) NOT NULL,
    rating_year INTEGER NOT NULL,      -- one row per (journal, rating_year) after unpivoting
    journal_name VARCHAR(500) NOT NULL,
    field VARCHAR(100),
    issn VARCHAR(20),
    publisher VARCHAR(255),
    rating VARCHAR(5) NOT NULL,
    raw_json JSONB,
    UNIQUE(dataset_id, source_row_hash, rating_year)
    -- NULL ISSN values don't enforce uniqueness in Postgres, and the real
    -- source record is "one wide row + one rating-year column" — the row
    -- hash + year is the correct key, not ISSN or title.
);

CREATE INDEX idx_abs_journal
ON abs_records(journal_id);