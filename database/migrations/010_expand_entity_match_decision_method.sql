-- Day 6:
-- Allow compound entity-resolution methods such as
-- "exact_title_ambiguous+fuzzy_title" to be persisted.

ALTER TABLE entity_match_decisions
ALTER COLUMN match_method TYPE VARCHAR(100);