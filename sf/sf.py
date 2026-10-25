"""Snowflake: migracje schematu i ładowanie danych z data/raw/.

    python sf/sf.py migrate     # stosuje nowe pliki sf/migrations/V*.sql
    python sf/sf.py load        # PUT plików JSON na stage + COPY INTO
    python sf/sf.py all         # jedno i drugie

Połączenie (zmienne środowiskowe, w GitHub Actions – sekrety):
    SNOWFLAKE_ACCOUNT            np. abc12345.eu-central-1  (lub org-konto)
    SNOWFLAKE_USER               np. SEJM_CI
    SNOWFLAKE_PRIVATE_KEY_PATH   ścieżka do klucza prywatnego (.p8)
    SNOWFLAKE_PRIVATE_KEY_PASSPHRASE  (opcjonalnie)
    SNOWFLAKE_ROLE               domyślnie SEJM_PIPELINE
    SNOWFLAKE_WAREHOUSE          domyślnie SEJM_WH

Migracje: prosty, własny mechanizm (wzorowany na schemachange/Flyway).
Zastosowane wersje są zapisywane w SEJM.PUBLIC.SCHEMA_MIGRATIONS
wraz z sumą kontrolną – zmiana już wdrożonego pliku kończy się błędem.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "sf" / "migrations"
RAW = ROOT / "data" / "raw"
DB = "SEJM"


def connect():
    import snowflake.connector

    params = dict(
        account=os.environ["SNOWFLAKE_ACCOUNT"],
        user=os.environ["SNOWFLAKE_USER"],
        role=os.environ.get("SNOWFLAKE_ROLE", "SEJM_PIPELINE"),
        warehouse=os.environ.get("SNOWFLAKE_WAREHOUSE", "SEJM_WH"),
        authenticator="SNOWFLAKE_JWT",
        private_key_file=os.environ["SNOWFLAKE_PRIVATE_KEY_PATH"],
    )
    if os.environ.get("SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"):
        params["private_key_file_pwd"] = os.environ["SNOWFLAKE_PRIVATE_KEY_PASSPHRASE"]
    return snowflake.connector.connect(**params)


def migrate(con) -> None:
    cur = con.cursor()
    cur.execute(f"CREATE DATABASE IF NOT EXISTS {DB}")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {DB}.PUBLIC.SCHEMA_MIGRATIONS (
            VERSION STRING, FILE_NAME STRING, CHECKSUM STRING,
            APPLIED_AT TIMESTAMP_LTZ DEFAULT CURRENT_TIMESTAMP()
        )""")
    applied = {r[0]: r[1] for r in cur.execute(f"SELECT VERSION, CHECKSUM FROM {DB}.PUBLIC.SCHEMA_MIGRATIONS")}

    files = sorted(MIGRATIONS.glob("V*__*.sql"))
    for path in files:
        version = re.match(r"V(\d+)__", path.name).group(1)
        sql = path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
        if version in applied:
            if applied[version] != checksum:
                sys.exit(f"Migracja {path.name} zmieniła się po wdrożeniu. Dodaj nowy plik V… zamiast edytować stary.")
            continue
        print(f"Stosuję {path.name}")
        for _ in con.execute_string(sql):
            pass
        cur.execute(
            f"INSERT INTO {DB}.PUBLIC.SCHEMA_MIGRATIONS (VERSION, FILE_NAME, CHECKSUM) VALUES (%s, %s, %s)",
            (version, path.name, checksum),
        )
    print("Migracje aktualne.")


def load(con) -> None:
    cur = con.cursor()
    stage = f"@{DB}.RAW.SEJM_STAGE"
    votings = (RAW / "votings").as_posix()
    if not any((RAW / "votings").glob("*.json")):
        sys.exit("Brak danych w data/raw/votings – uruchom najpierw: python -m ingest.extract")

    print("PUT głosowań na stage…")
    cur.execute(f"PUT 'file://{votings}/*.json' {stage}/votings/ AUTO_COMPRESS=TRUE OVERWRITE=TRUE PARALLEL=8")
    for name in ("mps", "clubs"):
        cur.execute(f"PUT 'file://{(RAW / (name + '.json')).as_posix()}' {stage}/{name}/ AUTO_COMPRESS=TRUE OVERWRITE=TRUE")

    # COPY pamięta, które pliki już załadował – nowe i zmienione wejdą,
    # niezmienione zostaną pominięte. Duplikaty usuwa widok VOTINGS_LATEST.
    print("COPY INTO RAW.VOTINGS…")
    cur.execute(f"""
        COPY INTO {DB}.RAW.VOTINGS (DOC, SRC_FILE)
        FROM (SELECT $1, METADATA$FILENAME FROM {stage}/votings/)
        ON_ERROR = 'ABORT_STATEMENT'
    """)
    # Słowniki to bieżący stan – podmieniamy w całości.
    for name, table in (("mps", "MPS"), ("clubs", "CLUBS")):
        cur.execute(f"TRUNCATE TABLE {DB}.RAW.{table}")
        cur.execute(f"COPY INTO {DB}.RAW.{table} (DOC) FROM (SELECT $1 FROM {stage}/{name}/) FORCE = TRUE")

    n = cur.execute(f"SELECT COUNT(*) FROM {DB}.STAGING.VOTINGS_LATEST").fetchone()[0]
    print(f"Załadowano. Głosowań w modelu: {n}")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "all"
    if cmd not in ("migrate", "load", "all"):
        sys.exit(__doc__)
    con = connect()
    try:
        if cmd in ("migrate", "all"):
            migrate(con)
        if cmd in ("load", "all"):
            load(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
