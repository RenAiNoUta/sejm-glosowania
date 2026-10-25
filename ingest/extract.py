"""Przyrostowe pobieranie danych z API Sejmu do data/raw/.

Układ plików (to jest warstwa "raw" – zapisujemy odpowiedzi API prawie bez zmian):

    data/raw/mps.json            posłowie (nadpisywane przy każdym uruchomieniu)
    data/raw/clubs.json          kluby i koła (nadpisywane)
    data/raw/proceedings.json    posiedzenia (nadpisywane)
    data/raw/votings/NNN.json    wszystkie głosowania posiedzenia NNN z głosami posłów

Jedyna zmiana względem API: z listy głosów usuwamy imiona i nazwiska
(są w mps.json), co zmniejsza pliki mniej więcej o połowę.

Przyrostowość: dla każdego posiedzenia pobieramy tylko listę głosowań
(jedno lekkie zapytanie) i dociągamy szczegóły wyłącznie tych głosowań,
których jeszcze nie mamy. Wynik raz zakończonego głosowania się nie zmienia.

Uruchomienie:  python -m ingest.extract [--term 10] [--full]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ingest.sejm_api import SejmApi

RAW_DIR = Path("data/raw")
VOTE_KEYS_TO_KEEP = ("MP", "club", "vote", "listVotes")
PROGRESS_EVERY = 50  # co ile pobranych głosowań wypisać postęp


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


def run(term: int, full: bool = False, api: SejmApi | None = None) -> dict:
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
        result = []
        for item in sorted(listing, key=lambda v: v["votingNumber"]):
            num = item["votingNumber"]
            if num in have:
                result.append(have[num])
                continue
            result.append(slim_voting(api.voting(sitting, num)))
            stats["votings_fetched"] += 1
            if stats["votings_fetched"] % PROGRESS_EVERY == 0:
                print(
                    f"  … pobrano {stats['votings_fetched']} głosowań "
                    f"(posiedzenie {sitting}, {pos}/{len(sittings)})"
                )

        write_json(path, result)
        stats["sittings_fetched"] += 1
        print(f"  posiedzenie {sitting} ({pos}/{len(sittings)}): {len(result)} głosowań, "
              f"łącznie pobrano {stats['votings_fetched']}")

    print(
        f"Gotowe. Sprawdzono {stats['sittings_checked']} posiedzeń, "
        f"zaktualizowano {stats['sittings_fetched']}, pobrano {stats['votings_fetched']} głosowań."
    )
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--term", type=int, default=10, help="kadencja Sejmu (domyślnie 10)")
    parser.add_argument("--full", action="store_true", help="pobierz wszystko od nowa")
    args = parser.parse_args()
    run(args.term, args.full)


if __name__ == "__main__":
    main()
