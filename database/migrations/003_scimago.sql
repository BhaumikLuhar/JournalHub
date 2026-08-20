CREATE TABLE scimago_records (
    id SERIAL PRIMARY KEY,
    journal_id INTEGER REFERENCES journals(id),   -- nullable until entity resolution runs
    dataset_id INTEGER NOT NULL REFERENCES datasets(id),
    raw_row_id INTEGER REFERENCES raw_rows(id),   -- explicit lineage back to the exact raw row
    year INTEGER NOT NULL,
    subject_area VARCHAR(150) NOT NULL,
    rank INTEGER,
    sourceid VARCHAR(30) NOT NULL,
    title VARCHAR(500) NOT NULL,
    type VARCHAR(30),
    issn_raw VARCHAR(100),
    publisher_raw VARCHAR(255),
    open_access BOOLEAN,
    open_access_diamond BOOLEAN,
    sjr NUMERIC(10,3),
    sjr_best_quartile VARCHAR(5),     -- NULL when source value was '-'
    h_index INTEGER,
    total_docs INTEGER,
    total_docs_3years INTEGER,
    total_refs INTEGER,
    total_citations_3years INTEGER,
    citable_docs_3years INTEGER,
    citations_per_doc_2years NUMERIC(10,3),
    refs_per_doc NUMERIC(10,3),
    female_percentage NUMERIC(6,3),
    overton INTEGER,
    country VARCHAR(100),
    region VARCHAR(100),
    coverage VARCHAR(255),
    raw_json JSONB,
    CHECK (
        sjr_best_quartile IS NULL
        OR sjr_best_quartile IN ('Q1','Q2','Q3','Q4')
    ),
    UNIQUE(sourceid, year, subject_area, dataset_id)
);

CREATE INDEX idx_scimago_journal
ON scimago_records(journal_id);

CREATE INDEX idx_scimago_sourceid
ON scimago_records(sourceid);

CREATE TABLE scimago_categories (
    id SERIAL PRIMARY KEY,
    scimago_record_id INTEGER NOT NULL REFERENCES scimago_records(id),
    category_name VARCHAR(255) NOT NULL,
    quartile VARCHAR(5)
);

CREATE TABLE scimago_areas (
    id SERIAL PRIMARY KEY,
    scimago_record_id INTEGER NOT NULL REFERENCES scimago_records(id),
    area_name VARCHAR(255) NOT NULL
);