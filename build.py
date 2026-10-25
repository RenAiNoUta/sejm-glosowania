"""Buduje model w DuckDB i eksportuje dane dla strony (docs/data/*.json).

    python build.py            # SQL + eksport
    python build.py --report   # dodatkowo raport jakości klasyfikacji głosowań

Plik bazy: data/sejm.duckdb – możesz go otworzyć w DuckDB CLI albo DBeaver
i pisać własne zapytania na tabelach stg_*, fct_*, mart_*.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SQL_DIR = ROOT / "sql"
DB_PATH = ROOT / "data" / "sejm.duckdb"
OUT_DIR = ROOT / "docs" / "data"

STANCE_CHARS = {1: "+", -1: "-", 0: "0", None: "."}
NOT_IN_SEJM = "_"


# ---------------------------------------------------------------------------
# Czyste funkcje (bez DuckDB) – łatwe do testowania
# ---------------------------------------------------------------------------
def encode_stances(mp_ids: list[int], stance_rows) -> dict[str, str]:
    """[(bill_id, mp_id, stance)] -> {bill_id: "+-0._…"} w kolejności mp_ids.

    '+' poparł, '-' sprzeciw, '0' wstrzymał się, '.' nieobecny,
    '_' nie był wtedy posłem.
    """
    index = {mp: i for i, mp in enumerate(mp_ids)}
    out: dict[str, list[str]] = {}
    for bill_id, mp_id, stance in stance_rows:
        if mp_id not in index:
            continue
        row = out.setdefault(bill_id, [NOT_IN_SEJM] * len(mp_ids))
        row[index[mp_id]] = STANCE_CHARS[stance]
    return {k: "".join(v) for k, v in out.items()}


def pca_2d(encoded: dict[str, str], mp_ids: list[int], min_share: float = 0.3):
    """Mapa podobieństwa posłów: PCA na macierzy poseł × ustawa.

    Wartości: +1 / -1 / 0, brak głosu uzupełniany średnią z kolumny.
    Zwraca [{"id": mp_id, "x": .., "y": ..}] dla posłów, którzy oddali głos
    w co najmniej `min_share` ustaw.
    """
    import numpy as np

    if not encoded:
        return []
    bills = sorted(encoded)
    values = {"+": 1.0, "-": -1.0, "0": 0.0}
    m = np.full((len(mp_ids), len(bills)), np.nan)
    for j, b in enumerate(bills):
        for i, ch in enumerate(encoded[b]):
            if ch in values:
                m[i, j] = values[ch]

    voted = (~np.isnan(m)).sum(axis=1)
    keep = voted >= max(1, int(min_share * len(bills)))
    m = m[keep]
    if m.shape[0] < 3:
        return []

    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # kolumny bez żadnego głosu
        col_mean = np.nan_to_num(np.nanmean(m, axis=0))
    m = np.where(np.isnan(m), col_mean, m) - col_mean
    _, _, vt = np.linalg.svd(m, full_matrices=False)
    coords = m @ vt[:2].T
    if coords.shape[1] < 2:
        coords = np.hstack([coords, np.zeros((coords.shape[0], 1))])
    scale = np.abs(coords).max(axis=0)
    scale[scale == 0] = 1
    coords = coords / scale

    kept_ids = [mp for mp, k in zip(mp_ids, keep) if k]
    return [
        {"id": mp, "x": round(float(x), 4), "y": round(float(y), 4)}
        for mp, (x, y) in zip(kept_ids, coords)
    ]


def write(name: str, data) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / name).write_text(
        json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# DuckDB
# ---------------------------------------------------------------------------
def rows(con, sql: str) -> list[dict]:
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def run_sql(con) -> None:
    for path in sorted(SQL_DIR.glob("*.sql")):
        print(f"SQL: {path.name}")
        con.execute(path.read_text(encoding="utf-8"))


def export(con) -> None:
    mps = rows(con, """
        SELECT mp_id AS id, full_name AS name, sort_name AS sort, club, active,
               district_num AS district, district_name AS "districtName"
        FROM stg_mps ORDER BY sort_name
    """)
    mp_ids = [m["id"] for m in mps]

    clubs = rows(con, """
        SELECT club AS id, club_name AS name, members_count AS members
        FROM stg_clubs ORDER BY members_count DESC NULLS LAST
    """)

    bills = rows(con, """
        SELECT bill_id AS id, print_no AS print, title, author_type AS author,
               final_category AS cat, final_vote_at AS date, first_vote_at AS first,
               n_votings AS n, yes_votes AS yes, no_votes AS no, abstain_votes AS abst,
               outcome, margin
        FROM dim_bills ORDER BY final_vote_at DESC
    """)

    stance_rows = con.execute("SELECT bill_id, mp_id, stance FROM fct_bill_stance").fetchall()
    encoded = encode_stances(mp_ids, stance_rows)

    club_bill: dict[str, dict[str, list[int]]] = {}
    for r in rows(con, "SELECT * FROM mart_club_bill"):
        club_bill.setdefault(r["bill_id"], {})[r["club"]] = [
            r["n_support"], r["n_oppose"], r["n_abstain"], r["n_absent"]
        ]

    stats = {
        "attendance": rows(con, """
            SELECT mp_id AS id, n_votings AS n, n_present AS present,
                   round(attendance, 4) AS rate, n_key_votings AS "keyN", n_key_present AS "keyPresent"
            FROM mart_mp_attendance"""),
        "loyalty": rows(con, """
            SELECT mp_id AS id, n_votes AS n, n_against_club AS against,
                   round(against_club_rate, 4) AS rate,
                   n_key_votes AS "keyN", n_key_against_club AS "keyAgainst"
            FROM mart_mp_loyalty"""),
        "alignment": rows(con, """
            SELECT club_a AS a, club_b AS b, n_bills AS n, round(agreement, 4) AS agree
            FROM mart_club_alignment"""),
        "authors": rows(con, "SELECT * FROM mart_author_success ORDER BY n_bills DESC"),
        "hours": rows(con, "SELECT * FROM mart_vote_hours ORDER BY hour"),
        "pca": pca_2d(encoded, mp_ids),
    }

    meta = rows(con, """
        SELECT
            (SELECT count(*) FROM stg_votings)       AS votings,
            (SELECT count(*) FROM dim_bills)         AS bills,
            (SELECT count(*) FROM stg_mps)           AS mps,
            (SELECT max(voted_at) FROM stg_votings)  AS "lastVote",
            (SELECT max(sitting) FROM stg_votings)   AS "lastSitting"
    """)[0]
    meta["generatedAt"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    meta["categories"] = {r["category"]: r["n_votings"] for r in rows(con, "SELECT * FROM mart_category_summary")}

    write("meta.json", meta)
    write("mps.json", mps)
    write("clubs.json", clubs)
    write("bills.json", bills)
    write("stances.json", {"mpIds": mp_ids, "bills": encoded})
    write("club_bill.json", club_bill)
    write("stats.json", stats)
    print(f"Eksport: {len(bills)} ustaw, {len(mps)} posłów → {OUT_DIR.relative_to(ROOT)}/")


def report(con) -> None:
    print("\n=== Kategorie głosowań ===")
    for r in rows(con, "SELECT * FROM mart_category_summary ORDER BY n_votings DESC"):
        print(f"  {r['category']:<14}{r['n_votings']:>7}")
    print("\n=== Najczęstsze tematy w kategorii 'proceduralne' (sprawdź, czy nic nie ucieka) ===")
    for r in rows(con, """
        SELECT topic, count(*) AS n FROM stg_votings
        WHERE category = 'proceduralne' GROUP BY 1 ORDER BY 2 DESC LIMIT 25"""):
        print(f"  {r['n']:>5}  {r['topic'][:100]}")
    print("\n=== Głosowania merytoryczne bez numeru druku ===")
    for r in rows(con, """
        SELECT voting_id, title FROM stg_votings
        WHERE category IN ('calosc','odrzucenie','weto') AND print_no IS NULL LIMIT 15"""):
        print(f"  {r['voting_id']}  {r['title'][:100]}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true", help="pokaż raport jakości klasyfikacji")
    args = parser.parse_args()

    import duckdb

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    import os
    os.chdir(ROOT)  # ścieżki w SQL są względne do katalogu repo
    con = duckdb.connect(str(DB_PATH))
    try:
        run_sql(con)
        export(con)
        if args.report:
            report(con)
    finally:
        con.close()


if __name__ == "__main__":
    main()
