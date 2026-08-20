CREATE TABLE sources (
    id SERIAL PRIMARY KEY,
    code VARCHAR(20) UNIQUE NOT NULL,   -- 'SCIMAGO','ABDC','ABS','REPEC','FT50'
    display_name VARCHAR(100) NOT NULL
);

INSERT INTO sources (code, display_name) VALUES
    ('SCIMAGO','SCImago Journal Rank'),
    ('ABDC','ABDC Journal Quality List'),
    ('ABS','Academic Journal Guide (ABS/AJG)'),
    ('REPEC','RePEc Aggregate Rankings'),
    ('FT50','Financial Times 50');

CREATE TABLE datasets (
    id SERIAL PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    dataset_year INTEGER,
    subject_area VARCHAR(150),          -- NULL for sources without area splits
    file_name VARCHAR(255) NOT NULL,
    file_hash VARCHAR(64) NOT NULL,     -- sha256, used to detect duplicate re-imports
    imported_at TIMESTAMPTZ DEFAULT NOW(),
    record_count INTEGER,
    -- status is a real state machine, not just pending/loaded:
    --   'pending' = import started, not finished (a prior run may have crashed mid-way)
    --   'loaded'  = import finished successfully and committed
    --   'failed'   = import was attempted and explicitly rolled back
    status VARCHAR(20) NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','loaded','failed')),
    UNIQUE(file_hash)
);

CREATE TABLE raw_files (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id),
    file_path VARCHAR(500) NOT NULL,
    sha256 VARCHAR(64) NOT NULL UNIQUE,
    file_size BIGINT,
    parser_version VARCHAR(20),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE raw_rows (
    id SERIAL PRIMARY KEY,
    raw_file_id INTEGER NOT NULL REFERENCES raw_files(id),
    row_number INTEGER NOT NULL,
    raw_data JSONB NOT NULL,
    UNIQUE(raw_file_id, row_number)
);

CREATE INDEX idx_raw_rows_file ON raw_rows(raw_file_id);

-- A structured, queryable home for rejected rows. CSV exports of this table
-- are still produced for convenience, but the database is the source of truth.
CREATE TABLE ingestion_rejections (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id),
    raw_file_id INTEGER REFERENCES raw_files(id),
    row_number INTEGER,
    reason VARCHAR(100) NOT NULL,
    details TEXT,
    raw_row_snapshot JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);