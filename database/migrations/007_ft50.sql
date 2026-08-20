CREATE TABLE ft50_records (
    id SERIAL PRIMARY KEY,
    journal_id INTEGER REFERENCES journals(id),
    dataset_id INTEGER NOT NULL REFERENCES datasets(id),
    raw_row_id INTEGER REFERENCES raw_rows(id),
    source_row_hash VARCHAR(64) NOT NULL,
    ft50_year INTEGER NOT NULL,
    rank INTEGER,
    journal_name VARCHAR(500) NOT NULL,
    included BOOLEAN DEFAULT TRUE,
    raw_json JSONB,
    UNIQUE(dataset_id, source_row_hash, ft50_year)
);

CREATE INDEX idx_ft50_journal
ON ft50_records(journal_id);