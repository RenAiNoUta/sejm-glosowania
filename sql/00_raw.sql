-- =====================================================================
-- Warstwa RAW: surowe dokumenty JSON z API, jeden wiersz = jeden obiekt.
-- Odpowiednik w Snowflake: tabela z kolumną VARIANT ładowana przez
-- COPY INTO ze stage'a (zob. snowflake/migrations/V1__raw.sql).
-- =====================================================================

CREATE OR REPLACE TABLE raw_votings AS
SELECT json AS doc
FROM read_json_objects('data/raw/votings/*.json', format = 'array');

CREATE OR REPLACE TABLE raw_mps AS
SELECT json AS doc
FROM read_json_objects('data/raw/mps.json', format = 'array');

CREATE OR REPLACE TABLE raw_clubs AS
SELECT json AS doc
FROM read_json_objects('data/raw/clubs.json', format = 'array');

CREATE OR REPLACE TABLE raw_processes AS
SELECT json AS doc
FROM read_json_objects('data/raw/processes.json', format = 'array');
