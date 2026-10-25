-- =====================================================================
-- Warstwa MARTS: tabele gotowe do analizy i eksportu na stronę.
-- =====================================================================

-- ---------------------------------------------------------------------
-- Fakty: głosy w głosowaniach elektronicznych
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE fct_votes AS
SELECT
    v.voting_id, v.mp_id, v.club, v.vote,
    g.voted_at, g.category, g.sitting
FROM stg_votes v
JOIN stg_votings g USING (voting_id)
WHERE g.kind = 'ELECTRONIC'
  AND v.vote <> 'VOTE_VALID';


-- ---------------------------------------------------------------------
-- Linia klubu w każdym głosowaniu (dominanta wśród głosujących)
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE int_club_line AS
WITH counts AS (
    SELECT
        voting_id, club,
        count(*) FILTER (WHERE vote = 'YES')     AS n_yes,
        count(*) FILTER (WHERE vote = 'NO')      AS n_no,
        count(*) FILTER (WHERE vote = 'ABSTAIN') AS n_abstain,
        count(*) FILTER (WHERE vote = 'ABSENT')  AS n_absent
    FROM fct_votes
    GROUP BY voting_id, club
)
SELECT
    *,
    n_yes + n_no + n_abstain AS n_voting,
    CASE
        WHEN n_yes > n_no AND n_yes > n_abstain     THEN 'YES'
        WHEN n_no > n_yes AND n_no > n_abstain      THEN 'NO'
        WHEN n_abstain > n_yes AND n_abstain > n_no THEN 'ABSTAIN'
    END AS club_line   -- NULL przy remisie
FROM counts;


-- ---------------------------------------------------------------------
-- Frekwencja posłów
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE mart_mp_attendance AS
SELECT
    mp_id,
    count(*)                                             AS n_votings,
    count(*) FILTER (WHERE vote <> 'ABSENT')             AS n_present,
    count(*) FILTER (WHERE vote <> 'ABSENT') / count(*)  AS attendance,
    count(*) FILTER (WHERE category IN ('calosc', 'odrzucenie', 'weto'))
                                                         AS n_key_votings,
    count(*) FILTER (WHERE category IN ('calosc', 'odrzucenie', 'weto') AND vote <> 'ABSENT')
                                                         AS n_key_present
FROM fct_votes
GROUP BY mp_id;


-- ---------------------------------------------------------------------
-- Lojalność wobec klubu: jak często poseł głosuje inaczej niż jego klub
-- (klub w dniu głosowania; pomijamy posłów niezrzeszonych i remisy)
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE mart_mp_loyalty AS
SELECT
    f.mp_id,
    count(*)                                         AS n_votes,
    count(*) FILTER (WHERE f.vote <> l.club_line)    AS n_against_club,
    count(*) FILTER (WHERE f.vote <> l.club_line) / count(*) AS against_club_rate,
    count(*) FILTER (WHERE f.category IN ('calosc', 'odrzucenie', 'weto'))
                                                     AS n_key_votes,
    count(*) FILTER (WHERE f.category IN ('calosc', 'odrzucenie', 'weto') AND f.vote <> l.club_line)
                                                     AS n_key_against_club
FROM fct_votes f
JOIN int_club_line l USING (voting_id, club)
WHERE f.vote IN ('YES', 'NO', 'ABSTAIN')
  AND l.club_line IS NOT NULL
  AND l.n_voting >= 3
  AND f.club NOT ILIKE 'niez%'
GROUP BY f.mp_id;


-- ---------------------------------------------------------------------
-- Ustawy: głosowania merytoryczne zgrupowane po numerze druku
-- ---------------------------------------------------------------------
-- Immunitety i wota zaufania/nieufności to pojedyncze decyzje – każda
-- z nich jest osobną pozycją (klucz = id głosowania), a nie ustawą.
CREATE OR REPLACE TABLE int_bill_votings AS
SELECT
    *,
    CASE
        WHEN category IN ('immunitet', 'wotum') THEN 'glos-' || voting_id
        ELSE coalesce('druk-' || print_no, 'tytul-' || md5(bill_title))
    END AS bill_id
FROM stg_votings
WHERE category IN ('calosc', 'odrzucenie', 'weto', 'immunitet', 'wotum');

CREATE OR REPLACE TABLE dim_bills AS
WITH ranked AS (
    SELECT
        *,
        row_number() OVER (PARTITION BY bill_id ORDER BY voted_at DESC, voting_number DESC) AS rn,
        count(*)     OVER (PARTITION BY bill_id)                                           AS n_votings,
        min(voted_at) OVER (PARTITION BY bill_id)                                          AS first_vote_at
    FROM int_bill_votings
)
SELECT
    bill_id,
    print_no,
    bill_title       AS title,
    CASE WHEN category IN ('immunitet', 'wotum') THEN NULL ELSE author_type END AS author_type,
    category         AS final_category,
    voting_id        AS final_voting_id,
    voted_at         AS final_vote_at,
    first_vote_at,
    n_votings,
    yes_votes, no_votes, abstain_votes, total_voted,
    CASE category
        WHEN 'calosc'     THEN CASE WHEN yes_votes > no_votes THEN 'przyjęto' ELSE 'odrzucono' END
        WHEN 'odrzucenie' THEN CASE WHEN yes_votes > no_votes THEN 'projekt odrzucony' ELSE 'w toku' END
        -- odrzucenie weta wymaga 3/5 oddanych głosów
        WHEN 'weto'       THEN CASE WHEN yes_votes * 5 >= total_voted * 3 THEN 'weto odrzucone' ELSE 'weto utrzymane' END
        -- zgoda na pociągnięcie posła i wotum nieufności: bezwzględna większość
        -- ustawowej liczby posłów (231); wotum zaufania: zwykła większość
        WHEN 'immunitet'  THEN CASE WHEN yes_votes >= 231 THEN 'zgoda wyrażona' ELSE 'brak zgody' END
        WHEN 'wotum'      THEN CASE
            WHEN lower(topic) LIKE '%nieufności%'
                THEN CASE WHEN yes_votes >= 231 THEN 'wotum nieufności przyjęte' ELSE 'wotum nieufności odrzucone' END
            ELSE CASE WHEN yes_votes > no_votes THEN 'wotum zaufania udzielone' ELSE 'wotum zaufania nieudzielone' END
        END
    END AS outcome,
    abs(yes_votes - no_votes) AS margin
FROM ranked
WHERE rn = 1;


-- ---------------------------------------------------------------------
-- Stanowisko posła wobec ustawy: ostatni oddany głos merytoryczny,
-- przeliczony na kierunek względem ustawy.
--   stance =  1  poparł ustawę
--   stance = -1  sprzeciwił się
--   stance =  0  wstrzymał się
--   stance NULL  nie głosował w żadnym głosowaniu nad tą ustawą
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE fct_bill_stance AS
WITH votes AS (
    SELECT
        b.bill_id, b.voted_at, b.voting_number, b.polarity,
        v.mp_id, v.club, v.vote
    FROM int_bill_votings b
    JOIN stg_votes v USING (voting_id)
    WHERE v.vote <> 'VOTE_VALID'
),
eligible AS (
    SELECT bill_id, mp_id, arg_max(club, voted_at) AS club
    FROM votes
    GROUP BY bill_id, mp_id
),
last_vote AS (
    SELECT
        bill_id, mp_id, vote, polarity,
        row_number() OVER (PARTITION BY bill_id, mp_id ORDER BY voted_at DESC, voting_number DESC) AS rn
    FROM votes
    WHERE vote IN ('YES', 'NO', 'ABSTAIN')
)
SELECT
    e.bill_id, e.mp_id, e.club,
    CASE l.vote
        WHEN 'YES'     THEN l.polarity
        WHEN 'NO'      THEN -l.polarity
        WHEN 'ABSTAIN' THEN 0
    END AS stance
FROM eligible e
LEFT JOIN last_vote l
       ON l.bill_id = e.bill_id AND l.mp_id = e.mp_id AND l.rn = 1;


-- ---------------------------------------------------------------------
-- Stanowisko klubów wobec ustaw (klub posła w dniu głosowania)
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE mart_club_bill AS
SELECT
    bill_id, club,
    count(*) FILTER (WHERE stance = 1)      AS n_support,
    count(*) FILTER (WHERE stance = -1)     AS n_oppose,
    count(*) FILTER (WHERE stance = 0)      AS n_abstain,
    count(*) FILTER (WHERE stance IS NULL)  AS n_absent
FROM fct_bill_stance
GROUP BY bill_id, club;


-- ---------------------------------------------------------------------
-- Zgodność klubów między sobą (jak często większość klubu A
-- zajmuje to samo stanowisko co większość klubu B)
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE mart_club_alignment AS
WITH club_stance AS (
    SELECT
        bill_id, club,
        CASE
            WHEN n_support > n_oppose AND n_support > n_abstain THEN 1
            WHEN n_oppose > n_support AND n_oppose > n_abstain  THEN -1
            WHEN n_abstain > n_support AND n_abstain > n_oppose THEN 0
        END AS stance
    FROM mart_club_bill
    WHERE n_support + n_oppose + n_abstain >= 3
      AND club NOT ILIKE 'niez%'
)
SELECT
    a.club AS club_a,
    b.club AS club_b,
    count(*) AS n_bills,
    avg(CASE WHEN a.stance = b.stance THEN 1.0 ELSE 0.0 END) AS agreement
FROM club_stance a
JOIN club_stance b ON a.bill_id = b.bill_id
WHERE a.stance IS NOT NULL AND b.stance IS NOT NULL
GROUP BY a.club, b.club;


-- ---------------------------------------------------------------------
-- Skuteczność wnioskodawców
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE mart_author_success AS
SELECT
    coalesce(author_type, 'nieustalony')                          AS author_type,
    count(*)                                                      AS n_bills,
    count(*) FILTER (WHERE outcome = 'przyjęto')                  AS n_passed,
    count(*) FILTER (WHERE outcome IN ('odrzucono', 'projekt odrzucony')) AS n_rejected,
    count(*) FILTER (WHERE outcome = 'w toku')                    AS n_pending
FROM dim_bills
WHERE final_category IN ('calosc', 'odrzucenie')
GROUP BY 1;


-- ---------------------------------------------------------------------
-- Kiedy się głosuje: liczba głosowań wg godziny
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE mart_vote_hours AS
SELECT
    hour(voted_at)                         AS hour,
    count(*)                               AS n_votings
FROM stg_votings
WHERE kind = 'ELECTRONIC'
GROUP BY 1;


-- ---------------------------------------------------------------------
-- Przegląd kategorii (kontrola jakości klasyfikacji)
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE mart_category_summary AS
SELECT category, count(*) AS n_votings
FROM stg_votings
GROUP BY 1;
