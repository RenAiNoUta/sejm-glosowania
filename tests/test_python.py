"""Testy części Pythona, które nie wymagają sieci ani DuckDB.

    python -m unittest discover -s tests
"""

import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build  # noqa: E402
from ingest import extract  # noqa: E402


class FakeApi:
    """Udaje API Sejmu: 2 posiedzenia, w drugim przybywa głosowań."""

    def __init__(self):
        self.calls = []
        self.sitting2_count = 2

    def mps(self):
        return [{"id": 1, "firstLastName": "Jan Kowalski", "club": "A"}]

    def clubs(self):
        return [{"id": "A", "name": "Klub A", "membersCount": 1}]

    def proceedings(self):
        return [{"number": 1}, {"number": 2}, {"number": 0}, {"number": 3}]

    def votings_of_sitting(self, s):
        self.calls.append(("list", s))
        return {1: [{"votingNumber": 1}], 2: [{"votingNumber": n} for n in range(1, self.sitting2_count + 1)]}.get(s, [])

    def process(self, num):
        self.calls.append(("process", num))
        if num == "404":
            return None
        return {"number": int(num), "title": "Projekt", "description": "projekt dotyczy czegoś",
                "documentType": "projekt ustawy", "closureDate": "2026-01-01" if num == "100" else None,
                "stages": [{"big": "nieistotne"}]}

    def voting(self, s, n):
        self.calls.append(("detail", s, n))
        title = {1: "Pkt 1. Sprawozdanie o projekcie ustawy (druki nr 100 i 101)",
                 2: "Pkt 2. Pierwsze czytanie projektu (druk nr 200)",
                 3: "Pkt 3. Informacja (druk nr 404)"}.get(n, "t")
        topic = "głosowanie nad całością projektu." if n != 2 else "wniosek o odrzucenie projektu w pierwszym czytaniu."
        return {
            "sitting": s, "votingNumber": n, "title": title, "topic": topic,
            "votes": [{"MP": 1, "club": "A", "vote": "YES", "firstName": "Jan", "lastName": "Kowalski"}],
        }


class ExtractTest(unittest.TestCase):
    def test_incremental(self):
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                api = FakeApi()
                stats = extract.run(10, api=api)
                self.assertEqual(stats["votings_fetched"], 3)
                saved = json.loads(Path("data/raw/votings/002.json").read_text(encoding="utf-8"))
                self.assertEqual(saved[0]["votes"][0], {"MP": 1, "club": "A", "vote": "YES"})

                # drugie uruchomienie: nic nowego
                api.calls.clear()
                stats = extract.run(10, api=api)
                self.assertEqual(stats["votings_fetched"], 0)

                # pojawia się nowe głosowanie na posiedzeniu 2 – pobieramy tylko je
                api.sitting2_count = 3
                api.calls.clear()
                stats = extract.run(10, api=api)
                self.assertEqual([c for c in api.calls if c[0] == "detail"], [("detail", 2, 3)])
                self.assertEqual(len(json.loads(Path("data/raw/votings/002.json").read_text(encoding="utf-8"))), 3)
            finally:
                os.chdir(cwd)


class ProcessesTest(unittest.TestCase):
    def test_fetch_and_refresh(self):
        from datetime import date
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                api = FakeApi()
                api.sitting2_count = 3
                extract.run(10, api=api)
                procs = {p["number"]: p for p in json.loads(Path("data/raw/processes.json").read_text(encoding="utf-8"))}
                self.assertEqual(set(procs), {"100", "200", "404"})
                self.assertEqual(procs["100"]["description"], "projekt dotyczy czegoś")
                self.assertNotIn("stages", procs["100"])
                self.assertFalse(procs["404"]["found"])

                # ten sam dzień: nic do pobrania
                api.calls.clear()
                self.assertEqual(extract.fetch_processes(api), 0)
                # po tygodniu: odświeżamy trwające (200) i nieznalezione (404), zamknięty (100) nie
                api.calls.clear()
                extract.fetch_processes(api, today=date.today().replace(year=date.today().year + 1))
                self.assertEqual(sorted(c[1] for c in api.calls if c[0] == "process"), ["200", "404"])
            finally:
                os.chdir(cwd)


class BuildFunctionsTest(unittest.TestCase):
    def test_encode(self):
        enc = build.encode_stances([10, 20, 30], [("b1", 10, 1), ("b1", 20, None), ("b2", 30, -1), ("b2", 99, 1)])
        self.assertEqual(enc, {"b1": "+._", "b2": "__-"})

    def test_pca_separates_blocs(self):
        random.seed(0)
        mp_ids = list(range(40))
        rows = []
        for b in range(30):
            for mp in mp_ids:
                bloc = 1 if mp < 20 else -1
                rows.append((f"b{b}", mp, bloc if random.random() < 0.9 else -bloc))
        pts = build.pca_2d(build.encode_stances(mp_ids, rows), mp_ids)
        left = [p["x"] for p in pts if p["id"] < 20]
        right = [p["x"] for p in pts if p["id"] >= 20]
        self.assertEqual(len(pts), 40)
        self.assertTrue((sum(left) / 20) * (sum(right) / 20) < 0)  # bloki po przeciwnych stronach


if __name__ == "__main__":
    unittest.main()
