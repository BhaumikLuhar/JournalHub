CREATE TABLE abdc_records (
    id SERIAL PRIMARY KEY,
    journal_id INTEGER REFERENCES journals(id),
    dataset_id INTEGER NOT NULL REFERENCES datasets(id),
    raw_row_id INTEGER REFERENCES raw_rows(id),
    source_row_hash VARCHAR(64) NOT NULL,  -- sha256 of the raw row; real uniqueness key
    rating_year INTEGER NOT NULL,
    journal_name VARCHAR(500) NOT NULL,
    publisher VARCHAR(255),
    issn VARCHAR(20),
    issn_online VARCHAR(20),
    year_inception INTEGER,
    for_code VARCHAR(20),
    for_scheme VARCHAR(20) NOT NULL,   -- 'ANZSRC2008' or 'ANZSRC2020'
    rating VARCHAR(5),        -- nullable: a blank source rating is valid data, not corruption
    raw_json JSONB,
    CHECK (rating IS NULL OR rating IN ('A*','A','B','C')),
    UNIQUE(dataset_id, source_row_hash, rating_year)
    -- Title is NOT a safe uniqueness key (two different journals can share a
    -- title). A hash of the original row within its dataset is a much safer
    -- "this is the same source record" key, and doubles as a lineage/audit aid.
);

CREATE INDEX idx_abdc_journal
ON abdc_records(journal_id);