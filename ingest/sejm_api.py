"""Minimalny klient API Sejmu (https://api.sejm.gov.pl).

API jest publiczne i nie wymaga klucza. Klient dodaje:
- ponawianie zapytań przy błędach 429/5xx z rosnącym odstępem,
- mały odstęp między zapytaniami, żeby nie obciążać serwera Sejmu.
"""

from __future__ import annotations

import time

import requests

BASE_URL = "https://api.sejm.gov.pl/sejm"
USER_AGENT = "sejm-glosowania (projekt edukacyjny; github.com/RenAiNoUta/sejm-glosowania)"


class SejmApi:
    def __init__(self, term: int = 10, delay: float = 0.15, max_retries: int = 5, session=None):
        self.term = term
        self.delay = delay
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})

    def _get(self, path: str, allow_404: bool = False):
        url = f"{BASE_URL}/term{self.term}{path}"
        for attempt in range(1, self.max_retries + 1):
            resp = self.session.get(url, timeout=30)
            if resp.status_code == 404 and allow_404:
                return None
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = min(60, 2**attempt)
                print(f"  {resp.status_code} dla {url}, ponawiam za {wait}s (próba {attempt})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            time.sleep(self.delay)
            return resp.json()
        raise RuntimeError(f"Nie udało się pobrać {url} po {self.max_retries} próbach")

    # --- słowniki ---------------------------------------------------------
    def mps(self):
        return self._get("/MP")

    def clubs(self):
        return self._get("/clubs")

    def proceedings(self):
        return self._get("/proceedings")

    # --- głosowania -------------------------------------------------------
    def votings_of_sitting(self, sitting: int):
        """Lista głosowań na posiedzeniu (bez głosów posłów)."""
        return self._get(f"/votings/{sitting}", allow_404=True) or []

    def voting(self, sitting: int, number: int):
        """Szczegóły głosowania razem z listą głosów posłów."""
        return self._get(f"/votings/{sitting}/{number}")
