ALTER TABLE entity_match_candidates
ALTER COLUMN match_method TYPE VARCHAR(100);

ALTER TABLE entity_match_decisions
ALTER COLUMN match_method TYPE VARCHAR(100);

ALTER TABLE journal_source_mapping
ALTER COLUMN match_method TYPE VARCHAR(100);