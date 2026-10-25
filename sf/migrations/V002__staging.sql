-- Warstwa STAGING jako widoki. Porównaj z sql/10_staging.sql (DuckDB):
--   DuckDB:     doc ->> '$.title'            Snowflake:  DOC:title::STRING
--   DuckDB:     unnest(json_extract(...))    Snowflake:  LATERAL FLATTEN(input => DOC:votes)
--   DuckDB:     regexp_extract(s, p, 1)      Snowflake:  REGEXP_SUBSTR(s, p, 1, 1, 'e', 1)

-- Ten sam plik może zostać załadowany ponownie (PUT ... OVERWRITE),
-- więc bierzemy najnowszą wersję każdego głosowania.
CREATE OR REPLACE VIEW SEJM.STAGING.VOTINGS_LATEST AS
SELECT DOC
FROM SEJM.RAW.VOTINGS
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY DOC:sitting::INT, DOC:votingNumber::INT
  ORDER BY LOADED_AT DESC
) = 1;

CREATE OR REPLACE VIEW SEJM.STAGING.STG_VOTINGS AS
WITH base AS (
  SELECT
    DOC:term::INT               AS TERM,
    DOC:sitting::INT            AS SITTING,
    DOC:sittingDay::INT         AS SITTING_DAY,
    DOC:votingNumber::INT       AS VOTING_NUMBER,
    DOC:date::TIMESTAMP_NTZ     AS VOTED_AT,
    DOC:kind::STRING            AS KIND,
    COALESCE(DOC:title::STRING, '') AS TITLE,
    COALESCE(DOC:topic::STRING, '') AS TOPIC,
    DOC:yes::INT                AS YES_VOTES,
    DOC:no::INT                 AS NO_VOTES,
    DOC:abstain::INT            AS ABSTAIN_VOTES,
    DOC:notParticipating::INT   AS NOT_PARTICIPATING,
    DOC:totalVoted::INT         AS TOTAL_VOTED
  FROM SEJM.STAGING.VOTINGS_LATEST
)
SELECT
  LPAD(SITTING, 3, '0') || '-' || LPAD(VOTING_NUMBER, 4, '0') AS VOTING_ID,
  *,
  CASE
    WHEN KIND IS DISTINCT FROM 'ELECTRONIC'                          THEN 'inne'
    WHEN TITLE ILIKE '%uchwał% senatu%' OR TOPIC ILIKE '%senat%'     THEN 'senat'
    WHEN TOPIC ILIKE '%ponown% uchwal%'
      OR TITLE ILIKE '%ponowne rozpatrzenie%'                         THEN 'weto'
    WHEN TOPIC ILIKE '%nad całością%'                                 THEN 'calosc'
    WHEN TOPIC ILIKE '%odrzucenie projektu%'
      OR TOPIC ILIKE '%odrzucenie w pierwszym czytaniu%'              THEN 'odrzucenie'
    WHEN TOPIC ILIKE '%poprawk%' OR TOPIC ILIKE '%wniosek mniejszości%' THEN 'poprawka'
    ELSE 'proceduralne'
  END AS CATEGORY,
  IFF(TOPIC ILIKE '%odrzucenie projektu%' OR TOPIC ILIKE '%odrzucenie w pierwszym czytaniu%', -1, 1) AS POLARITY,
  REGEXP_SUBSTR(LOWER(TITLE), 'druk\\S*\\s+nr\\s+(\\d+)', 1, 1, 'e', 1) AS PRINT_NO,
  REGEXP_REPLACE(
    COALESCE(
      REGEXP_SUBSTR(TITLE, '([Pp]rojek\\S*\\s+(ustawy|uchwały).*)', 1, 1, 'e', 1),
      REGEXP_REPLACE(TITLE, '^Pkt\\.?\\s*\\d+\\.?\\s*', '')
    ),
    '\\s*\\(druk[^)]*\\)\\s*$', ''
  ) AS BILL_TITLE
FROM base;

CREATE OR REPLACE VIEW SEJM.STAGING.STG_VOTES AS
SELECT
  LPAD(v.DOC:sitting::INT, 3, '0') || '-' || LPAD(v.DOC:votingNumber::INT, 4, '0') AS VOTING_ID,
  f.value:MP::INT      AS MP_ID,
  f.value:club::STRING AS CLUB,
  CASE f.value:vote::STRING
    WHEN 'YES' THEN 'YES' WHEN 'NO' THEN 'NO' WHEN 'ABSTAIN' THEN 'ABSTAIN'
    WHEN 'VOTE_VALID' THEN 'VOTE_VALID' ELSE 'ABSENT'
  END AS VOTE
FROM SEJM.STAGING.VOTINGS_LATEST v,
     LATERAL FLATTEN(input => v.DOC:votes) f;

CREATE OR REPLACE VIEW SEJM.STAGING.STG_MPS AS
SELECT
  DOC:id::INT              AS MP_ID,
  DOC:firstLastName::STRING AS FULL_NAME,
  DOC:club::STRING         AS CLUB,
  COALESCE(DOC:active::BOOLEAN, TRUE) AS ACTIVE,
  DOC:districtNum::INT     AS DISTRICT_NUM,
  DOC:districtName::STRING AS DISTRICT_NAME
FROM SEJM.RAW.MPS;
