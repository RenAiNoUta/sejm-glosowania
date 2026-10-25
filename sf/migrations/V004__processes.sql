-- Procesy legislacyjne: oficjalny krótki opis projektu ("projekt dotyczy …").
CREATE TABLE IF NOT EXISTS SEJM.RAW.PROCESSES (
  DOC        VARIANT,
  LOADED_AT  TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
);

CREATE OR REPLACE VIEW SEJM.STAGING.STG_PROCESSES AS
SELECT
  DOC:number::STRING                          AS PRINT_NO,
  COALESCE(DOC:found::BOOLEAN, FALSE)         AS FOUND,
  DOC:title::STRING                           AS PROCESS_TITLE,
  DOC:titleFinal::STRING                      AS TITLE_FINAL,
  NULLIF(TRIM(DOC:description::STRING), '')   AS DESCRIPTION,
  DOC:documentType::STRING                    AS DOCUMENT_TYPE,
  DOC:passed::BOOLEAN                         AS PASSED
FROM SEJM.RAW.PROCESSES;
