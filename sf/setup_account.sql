-- =====================================================================
-- Jednorazowa konfiguracja konta (uruchom ręcznie w Snowsight jako
-- ACCOUNTADMIN). Nie jest migracją – wymaga uprawnień administratora.
-- =====================================================================
USE ROLE ACCOUNTADMIN;

-- Mały warehouse, który sam się usypia po minucie bezczynności.
CREATE WAREHOUSE IF NOT EXISTS SEJM_WH
  WAREHOUSE_SIZE = 'XSMALL'
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;

-- Twardy limit kredytów: po przekroczeniu warehouse zostaje zawieszony.
CREATE RESOURCE MONITOR IF NOT EXISTS SEJM_LIMIT
  WITH CREDIT_QUOTA = 20
  FREQUENCY = MONTHLY
  START_TIMESTAMP = IMMEDIATELY
  TRIGGERS
    ON 75 PERCENT DO NOTIFY
    ON 100 PERCENT DO SUSPEND_IMMEDIATE;
ALTER WAREHOUSE SEJM_WH SET RESOURCE_MONITOR = SEJM_LIMIT;

-- Rola i użytkownik techniczny dla GitHub Actions (logowanie kluczem).
CREATE ROLE IF NOT EXISTS SEJM_PIPELINE;
GRANT USAGE ON WAREHOUSE SEJM_WH TO ROLE SEJM_PIPELINE;
GRANT CREATE DATABASE ON ACCOUNT TO ROLE SEJM_PIPELINE;

CREATE USER IF NOT EXISTS SEJM_CI
  TYPE = SERVICE
  DEFAULT_ROLE = SEJM_PIPELINE
  DEFAULT_WAREHOUSE = SEJM_WH
  COMMENT = 'GitHub Actions – sejm-glosowania';
GRANT ROLE SEJM_PIPELINE TO USER SEJM_CI;

-- Klucz publiczny: wygeneruj parę kluczy lokalnie (zob. README) i wklej
-- zawartość pliku rsa_key.pub bez linii BEGIN/END:
-- ALTER USER SEJM_CI SET RSA_PUBLIC_KEY = 'MIIBIjANBgkq...';
