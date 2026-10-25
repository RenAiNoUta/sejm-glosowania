"""Przyrostowe pobieranie danych z API Sejmu do data/raw/.

Układ plików (to jest warstwa "raw" – zapisujemy odpowiedzi API prawie bez zmian):

    data/raw/mps.json            posłowie (nadpisywane przy każdym uruchomieniu)
    data/raw/clubs.json          kluby i koła (nadpisywane)
    data/raw/proceedings.json    posiedzenia (nadpisywane)
    data/raw/votings/NNN.json    wszystkie głosowania posiedzenia NNN z głosami posłów
    data/raw/processes.json      procesy legislacyjne projektów, nad którymi głosowano
                                 merytorycznie (krótki opis, rodzaj dokumentu, status)

Jedyna zmiana względem API: z listy głosów usuwamy imiona i nazwiska
(są w mps.json), co zmniejsza pliki mniej więcej o połowę.

Przyrostowość: dla każdego posiedzenia pobieramy tylko listę głosowań
(jedno lekkie zapytanie) i dociągamy szczegóły wyłącznie tych głosowań,
których jeszcze nie mamy. Wynik raz zakończonego głosowania się nie zmienia.

Szczegóły głosowań pobieramy równolegle (domyślnie 4 zapytania naraz).

Uruchomienie:  python -m ingest.extract [--term 10] [--full] [--workers 4]
"""

from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path

from ingest.sejm_api import SejmApi

RAW_DIR = Path("data/raw")
VOTE_KEYS_TO_KEEP = ("MP", "club", "vote", "listVotes")
PROGRESS_EVERY = 50  # co ile pobranych głosowań wypisać postęp

# Głosowania, dla których warto pobrać opis projektu (to samo, co model
# uznaje za merytoryczne – zob. sql/10_staging.sql).
SUBSTANTIVE_TOPIC = re.compile(r"nad całością|odrzucenie|ponown|wotum", re.IGNORECASE)
PRINT_NO = re.compile(r"druk\S*\s+nr\s+(\d+)", re.IGNORECASE)
PROCESS_KEYS = ("number", "title", "titleFinal", "description", "documentType",
                "passed", "processStartDate", "closureDate", "UE", "urgencyStatus")
PROCESS_REFRESH_DAYS = 7  # trwające procesy i brakujące numery sprawdzamy co tydzień


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def slim_voting(voting: dict) -> dict:
    voting = dict(voting)
    voting["votes"] = [
        {k: v[k] for k in VOTE_KEYS_TO_KEEP if k in v} for v in voting.get("votes") or []
    ]
    return voting


def sitting_numbers(proceedings: list[dict]) -> list[int]:
    nums = set()
    for p in proceedings:
        n = p.get("number")
        if isinstance(n, int) and n > 0:
            nums.add(n)
    return sorted(nums)


def load_existing(path: Path) -> list[dict] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def run(term: int, full: bool = False, api: SejmApi | None = None, workers: int = 4) -> dict:
    api = api or SejmApi(term=term)
    stats = {"sittings_checked": 0, "sittings_fetched": 0, "votings_fetched": 0}

    print("Pobieram słowniki: posłowie, kluby, posiedzenia…")
    write_json(RAW_DIR / "mps.json", api.mps())
    write_json(RAW_DIR / "clubs.json", api.clubs())
    proceedings = api.proceedings()
    write_json(RAW_DIR / "proceedings.json", proceedings)

    sittings = sitting_numbers(proceedings)
    print(f"Posiedzeń do sprawdzenia: {len(sittings)}")

    for pos, sitting in enumerate(sittings, start=1):
        stats["sittings_checked"] += 1
        path = RAW_DIR / "votings" / f"{sitting:03d}.json"
        listing = api.votings_of_sitting(sitting)
        if not listing:
            continue  # posiedzenie bez głosowań albo zaplanowane w przyszłości

        existing = None if full else load_existing(path)
        if existing is not None and len(existing) == len(listing):
            continue

        have = {v["votingNumber"]: v for v in existing or []}
        numbers = sorted(item["votingNumber"] for item in listing)
        missing = [n for n in numbers if n not in have]

        def fetch(num: int) -> dict:
            return slim_voting(api.voting(sitting, num))

        # map() zachowuje kolejność, a wyniki przychodzą w miarę pobierania.
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for num, voting in zip(missing, pool.map(fetch, missing)):
                have[num] = voting
                stats["votings_fetched"] += 1
                if stats["votings_fetched"] % PROGRESS_EVERY == 0:
                    print(
                        f"  … pobrano {stats['votings_fetched']} głosowań "
                        f"(posiedzenie {sitting}, {pos}/{len(sittings)})"
                    )

        result = [have[n] for n in numbers]
        write_json(path, result)
        stats["sittings_fetched"] += 1
        print(f"  posiedzenie {sitting} ({pos}/{len(sittings)}): {len(result)} głosowań, "
              f"łącznie pobrano {stats['votings_fetched']}")

    print(
        f"Gotowe. Sprawdzono {stats['sittings_checked']} posiedzeń, "
        f"zaktualizowano {stats['sittings_fetched']}, pobrano {stats['votings_fetched']} głosowań."
    )
    stats["processes_fetched"] = fetch_processes(api, workers, full)
    return stats


def wanted_print_numbers() -> set[str]:
    """Numery druków z tytułów głosowań merytorycznych."""
    numbers = set()
    for path in sorted((RAW_DIR / "votings").glob("*.json")):
        for v in load_existing(path) or []:
            if SUBSTANTIVE_TOPIC.search(v.get("topic") or ""):
                m = PRINT_NO.search(v.get("title") or "")
                if m:
                    numbers.add(m.group(1))
    return numbers


def fetch_processes(api, workers: int = 4, full: bool = False, today: date | None = None) -> int:
    """Dociąga opisy procesów legislacyjnych do data/raw/processes.json.

    Pobieramy nowe numery, a co PROCESS_REFRESH_DAYS dni ponownie te, które
    jeszcze trwają albo których API wcześniej nie znało.
    """
    today = today or date.today()
    path = RAW_DIR / "processes.json"
    known = {p["number"]: p for p in (None if full else load_existing(path)) or []}
    stale_before = (today - timedelta(days=PROCESS_REFRESH_DAYS)).isoformat()

    def needs_fetch(num: str) -> bool:
        p = known.get(num)
        if p is None:
            return True
        finished = p.get("found") and p.get("closureDate")
        return not finished and p.get("fetchedAt", "") < stale_before

    todo = sorted((n for n in wanted_print_numbers() if needs_fetch(n)), key=int)
    if not todo:
        if not path.exists():
            write_json(path, [])  # model w DuckDB oczekuje pliku, nawet pustego
        print("Procesy legislacyjne: nic nowego.")
        return 0
    print(f"Procesy legislacyjne do pobrania: {len(todo)}")

    def fetch(num: str) -> dict:
        data = api.process(num)
        if not data:
            return {"number": num, "found": False, "fetchedAt": today.isoformat()}
        slim = {k: data.get(k) for k in PROCESS_KEYS}
        slim.update(number=num, found=True, fetchedAt=today.isoformat())
        return slim

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        for proc in pool.map(fetch, todo):
            known[proc["number"]] = proc
            done += 1
            if done % PROGRESS_EVERY == 0:
                print(f"  … procesy: {done}/{len(todo)}")
                write_json(path, sorted(known.values(), key=lambda p: int(p["number"])))
    write_json(path, sorted(known.values(), key=lambda p: int(p["number"])))
    found = sum(1 for p in known.values() if p.get("found"))
    print(f"Procesy legislacyjne: pobrano {done}, w pliku {len(known)} (z opisem w API: {found}).")
    return done


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--term", type=int, default=10, help="kadencja Sejmu (domyślnie 10)")
    parser.add_argument("--full", action="store_true", help="pobierz wszystko od nowa")
    parser.add_argument("--workers", type=int, default=4, help="ile zapytań równolegle (domyślnie 4)")
    args = parser.parse_args()
    run(args.term, args.full, workers=args.workers)


if __name__ == "__main__":
    main()
