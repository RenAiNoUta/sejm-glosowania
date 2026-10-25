-- =====================================================================
-- Warstwa STAGING: typowanie, nazwy kolumn, rozbicie zagnieżdżonych list.
-- W Snowflake: doc:title::STRING oraz LATERAL FLATTEN(input => doc:votes).
-- =====================================================================

-- ---------------------------------------------------------------------
-- Głosowania (jeden wiersz = jedno głosowanie) + klasyfikacja
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE stg_votings AS
WITH base AS (
    SELECT
        (doc ->> '$.term')::INTEGER              AS term,
        (doc ->> '$.sitting')::INTEGER           AS sitting,
        (doc ->> '$.sittingDay')::INTEGER        AS sitting_day,
        (doc ->> '$.votingNumber')::INTEGER      AS voting_number,
        (doc ->> '$.date')::TIMESTAMP            AS voted_at,
        doc ->> '$.kind'                         AS kind,
        coalesce(doc ->> '$.title', '')          AS title,
        coalesce(doc ->> '$.topic', '')          AS topic,
        doc ->> '$.description'                  AS description,
        (doc ->> '$.yes')::INTEGER               AS yes_votes,
        (doc ->> '$.no')::INTEGER                AS no_votes,
        (doc ->> '$.abstain')::INTEGER           AS abstain_votes,
        (doc ->> '$.notParticipating')::INTEGER  AS not_participating,
        (doc ->> '$.totalVoted')::INTEGER        AS total_voted
    FROM raw_votings
),
classified AS (
    SELECT
        *,
        lower(title) AS title_lc,
        lower(topic) AS topic_lc
    FROM base
)
SELECT
    printf('%03d-%04d', sitting, voting_number) AS voting_id,
    term, sitting, sitting_day, voting_number, voted_at, kind,
    title, topic, description,
    yes_votes, no_votes, abstain_votes, not_participating, total_voted,

    -- Rodzaj głosowania. Kolejność warunków ma znaczenie.
    CASE
        WHEN kind IS DISTINCT FROM 'ELECTRONIC'                     THEN 'inne'
        -- immunitet: tylko głosowania wprost nad wnioskiem o zgodę (ZA = zgoda);
        -- "przyjęcie wniosku z druku" komisji pomijamy – kierunek nieznany
        WHEN topic_lc LIKE '%wyrażenie zgody na pociągnięcie%'      THEN 'immunitet'
        WHEN topic_lc LIKE '%wotum nieufności%'
          OR topic_lc LIKE '%wotum zaufania%'                        THEN 'wotum'
        -- "o uchwale Senatu", "uchwała Senatu" – głosowania nad poprawkami Senatu
        WHEN title_lc LIKE '%uchwa% senatu%'
          OR topic_lc LIKE '%senat%'                                 THEN 'senat'
        -- weto: "ponowne uchwalenie ustawy", "ponownym uchwaleniem",
        -- "przyjęcie wniosku o ponowne rozpatrzenie" – we wszystkich ZA = za ustawą
        WHEN topic_lc LIKE '%ponown% uchwal%'
          OR topic_lc LIKE '%ponowne rozpatrzenie%'                  THEN 'weto'
        WHEN topic_lc LIKE '%nad całością%'                          THEN 'calosc'
        WHEN topic_lc LIKE '%odrzucenie projektu%'
          OR topic_lc LIKE '%odrzucenie w całości%'
          OR topic_lc LIKE '%odrzucenie w pierwszym czytaniu%'       THEN 'odrzucenie'
        WHEN topic_lc LIKE '%poprawk%'
          OR topic_lc LIKE '%wniosek mniejszości%'                   THEN 'poprawka'
        ELSE 'proceduralne'
    END AS category,

    -- Kierunek względem ustawy: +1 = głos ZA oznacza poparcie ustawy,
    -- -1 = głos ZA oznacza sprzeciw (wniosek o odrzucenie projektu).
    CASE
        WHEN topic_lc LIKE '%odrzucenie projektu%'
          OR topic_lc LIKE '%odrzucenie w całości%'
          OR topic_lc LIKE '%odrzucenie w pierwszym czytaniu%' THEN -1
        ELSE 1
    END AS polarity,

    -- Numer pierwszego druku w tytule = numer projektu (klucz ustawy).
    nullif(regexp_extract(title_lc, 'druk[^\s]*\s+nr\s+(\d+)', 1), '') AS print_no,

    -- Wnioskodawca projektu, jeśli da się go odczytać z tytułu.
    CASE regexp_extract(title_lc, '(rządow|poselsk|senack|obywatelsk|komisyjn|prezydenck)', 1)
        WHEN 'rządow'      THEN 'rządowy'
        WHEN 'poselsk'     THEN 'poselski'
        WHEN 'senack'      THEN 'senacki'
        WHEN 'obywatelsk'  THEN 'obywatelski'
        WHEN 'komisyjn'    THEN 'komisyjny'
        WHEN 'prezydenck'  THEN 'prezydencki'
    END AS author_type,

    -- Czytelny tytuł, np. "Projekt ustawy o …" albo "Weto Prezydenta do ustawy …":
    -- bez numeru punktu, prefiksu "Sprawozdanie Komisji … o", "(druki nr …)"
    -- i dopisków typu "- trzecie czytanie"; odmiana "projekcie/projektu" → "Projekt".
    CASE
        WHEN topic_lc LIKE '%wyrażenie zgody na pociągnięcie%'
            THEN 'Zgoda na ' || regexp_replace(topic, '^.*?wyrażenie zgody na ', '')
        WHEN topic_lc LIKE '%wotum zaufania%'
            THEN regexp_replace(topic, '\.$', '')
        ELSE upper(left(t5, 1)) || substr(t5, 2)
    END AS bill_title
FROM (
    SELECT
        *,
        regexp_replace(
            regexp_replace(
                regexp_replace(
                    regexp_replace(
                        coalesce(
                            nullif(regexp_extract(title, '([Pp]rojek(t|tu|cie)\s+(ustawy|uchwały).*)', 1), ''),
                            regexp_replace(
                                regexp_replace(title, '^Pkt\.?\s*\d+\.?\s*', ''),
                                '^Sprawozdanie Komisji[^(]*?\s+o\s+', '')
                        ),
                        '\s*\(druk[^)]*\).*$', ''),
                    '\s+-\s+(pierwsze|drugie|trzecie) czytanie.*$', ''),
                '^[Pp]rojek(t|tu|cie)\s', 'Projekt '),
            '^wniosku Prezydenta Rzeczypospolitej Polskiej o ponowne rozpatrzenie ustawy',
            'Weto Prezydenta do ustawy') AS t5
    FROM classified
) AS c;


-- ---------------------------------------------------------------------
-- Głosy posłów (jeden wiersz = jeden poseł w jednym głosowaniu)
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE stg_votes AS
WITH exploded AS (
    SELECT
        (doc ->> '$.sitting')::INTEGER                AS sitting,
        (doc ->> '$.votingNumber')::INTEGER           AS voting_number,
        unnest(json_extract(doc, '$.votes[*]'))       AS v
    FROM raw_votings
)
SELECT
    printf('%03d-%04d', sitting, voting_number) AS voting_id,
    (v ->> '$.MP')::INTEGER AS mp_id,
    v ->> '$.club'          AS club,      -- klub W DNIU głosowania
    CASE v ->> '$.vote'
        WHEN 'YES'        THEN 'YES'
        WHEN 'NO'         THEN 'NO'
        WHEN 'ABSTAIN'    THEN 'ABSTAIN'
        WHEN 'VOTE_VALID' THEN 'VOTE_VALID'
        ELSE 'ABSENT'
    END AS vote
FROM exploded;


-- ---------------------------------------------------------------------
-- Posłowie i kluby (stan bieżący)
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE stg_mps AS
SELECT
    (doc ->> '$.id')::INTEGER         AS mp_id,
    doc ->> '$.firstLastName'         AS full_name,
    doc ->> '$.lastFirstName'         AS sort_name,
    doc ->> '$.club'                  AS club,
    coalesce((doc ->> '$.active')::BOOLEAN, TRUE) AS active,
    (doc ->> '$.districtNum')::INTEGER AS district_num,
    doc ->> '$.districtName'          AS district_name,
    doc ->> '$.voivodeship'           AS voivodeship
FROM raw_mps;

CREATE OR REPLACE TABLE stg_clubs AS
SELECT
    doc ->> '$.id'                     AS club,
    doc ->> '$.name'                   AS club_name,
    (doc ->> '$.membersCount')::INTEGER AS members_count
FROM raw_clubs;
