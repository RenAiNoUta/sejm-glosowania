# Jak głosował Sejm

Porównaj swoje stanowisko w sprawie ustaw z tym, jak faktycznie głosowali posłowie i kluby Sejmu X kadencji. Dane pochodzą z oficjalnego [API Sejmu](https://api.sejm.gov.pl/) i są odświeżane codziennie przez GitHub Actions.

Projekt edukacyjny: ten sam model danych zbudowany lokalnie w **DuckDB** i (opcjonalnie) w **Snowflake**.

```
API Sejmu ──► ingest/ (Python) ──► data/raw/*.json
                                        │
                 ┌──────────────────────┴───────────────────────┐
                 ▼                                              ▼
        DuckDB: sql/00_raw → 10_staging → 20_marts     Snowflake: sf/migrations
                 │                                     (stage, VARIANT, FLATTEN,
                 ▼                                      Dynamic Tables)
        build.py → docs/data/*.json
                 │
                 ▼
        docs/index.html  (GitHub Pages)
```

## Szybki start (lokalnie)

Wymagania: Python 3.10+, opcjonalnie Node (tylko do testów).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python -m ingest.extract           # pierwsze uruchomienie: kilka tysięcy zapytań, ok. 15–30 min
python build.py --report           # model DuckDB + eksport + raport klasyfikacji
python -m http.server 8000 -d docs # otwórz http://localhost:8000
```

Kolejne uruchomienia `ingest.extract` dociągają tylko nowe głosowania.

Baza ląduje w `data/sejm.duckdb`. Możesz ją otworzyć w DuckDB CLI (`duckdb data/sejm.duckdb`) albo w DBeaverze i pisać własne zapytania:

```sql
SELECT category, count(*) FROM stg_votings GROUP BY ALL ORDER BY 2 DESC;
SELECT * FROM dim_bills ORDER BY margin LIMIT 20;      -- najbardziej sporne
```

## Struktura

| Ścieżka | Co robi |
|---|---|
| `ingest/sejm_api.py` | klient API z ponawianiem i limitem tempa |
| `ingest/extract.py` | przyrostowe pobieranie do `data/raw/` |
| `sql/00_raw.sql` | surowe dokumenty JSON (`read_json_objects`) |
| `sql/10_staging.sql` | typowanie, rozbicie listy głosów, **klasyfikacja głosowań** |
| `sql/20_marts.sql` | ustawy, stanowiska posłów, frekwencja, lojalność, zgodność klubów |
| `build.py` | uruchamia SQL, liczy mapę podobieństwa (PCA), eksportuje JSON |
| `docs/` | strona (statyczna, bez backendu) + `score.js` z logiką dopasowania |
| `sf/` | wersja Snowflake: konfiguracja konta, migracje, ładowanie |
| `tests/` | testy bez sieci i bez DuckDB |

### Model danych

| Tabela | Ziarno |
|---|---|
| `stg_votings` | głosowanie (+ `category`, `polarity`, `print_no`) |
| `stg_votes` | poseł × głosowanie, klub **z dnia głosowania** |
| `dim_bills` | ustawa / uchwała (grupowanie po numerze druku) |
| `fct_bill_stance` | poseł × ustawa: +1 / −1 / 0 / NULL |
| `int_club_line` | klub × głosowanie: dominanta klubu |
| `mart_mp_loyalty`, `mart_mp_attendance` | poseł |
| `mart_club_bill`, `mart_club_alignment` | klub × ustawa, klub × klub |

### Klasyfikacja głosowań

Najważniejsza i najbardziej krucha część to `CASE` w `sql/10_staging.sql`, który na podstawie tytułu rozpoznaje głosowania nad całością, wnioski o odrzucenie (z odwróconym kierunkiem), weto, poprawki i resztę. Po każdej zmianie uruchom `python build.py --report`: pokazuje liczności kategorii, najczęstsze tematy w „proceduralnych” (żeby wyłapać, co ucieka) i głosowania merytoryczne bez numeru druku.

## GitHub Pages i automatyczne odświeżanie

1. Utwórz repozytorium (np. `sejm-glosowania`) i wypchnij kod.
2. **Settings → Pages → Build and deployment → Source: GitHub Actions.**
3. **Actions → Dane i strona → Run workflow.** Pierwszy przebieg pobiera wszystko (do ok. 30 min), kolejne codziennie rano zajmują kilka minut.

Surowe dane nie trafiają do repo – workflow trzyma je w cache GitHub Actions. Codzienny przebieg utrzymuje cache przy życiu, a jeśli zniknie, ingest pobierze dane od nowa.

## Snowflake (gdy założysz trial)

Najpierw zbuduj i przetestuj wszystko w DuckDB, trial zakładaj dopiero wtedy – liczy się 30 dni.

1. W Snowsight uruchom jako ACCOUNTADMIN `sf/setup_account.sql`: warehouse XS z auto-suspend, **resource monitor z limitem kredytów**, rola i użytkownik techniczny.
2. Wygeneruj parę kluczy i przypisz klucz publiczny użytkownikowi:
   ```bash
   openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
   openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
   ```
   ```sql
   ALTER USER SEJM_CI SET RSA_PUBLIC_KEY = '<zawartość rsa_key.pub bez linii BEGIN/END>';
   ```
3. Lokalnie:
   ```bash
   pip install snowflake-connector-python
   export SNOWFLAKE_ACCOUNT=... SNOWFLAKE_USER=SEJM_CI SNOWFLAKE_PRIVATE_KEY_PATH=./rsa_key.p8
   python sf/sf.py all
   ```
4. W GitHub: **Settings → Secrets and variables → Actions** dodaj `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_PRIVATE_KEY` (cała zawartość `rsa_key.p8`). Potem **Actions → Snowflake → Run workflow**.

Nigdy nie commituj `rsa_key.p8` (jest w `.gitignore`).

Co przećwiczysz: stage i `PUT`, `COPY INTO` z `METADATA$FILENAME`, `VARIANT` i `LATERAL FLATTEN`, `QUALIFY`, Dynamic Tables, wersjonowane migracje w CI, uwierzytelnianie kluczem. W `V003__marts.sql` są tylko rdzeniowe tabele – przeniesienie reszty martów z `sql/20_marts.sql` zostawiłem jako ćwiczenie. Strona dalej czyta pliki z DuckDB, więc działa niezależnie od tego, czy trial jest aktywny.

## Testy

```bash
python -m unittest discover -s tests   # ingest (sztuczne API), kodowanie stanowisk, PCA
node tests/test_score.js               # logika dopasowania na stronie
```

## Metodologia i neutralność

Opis liczenia zgodności jest na stronie w zakładce „Jak to liczymy”. Zasady projektu: lista obejmuje wszystkie projekty z głosowaniem merytorycznym (bez ręcznego wybierania), tytuły są oficjalne, a ustawy nie są ważone. Wynik pokazuje zgodność z dotychczasowymi głosowaniami w tej kadencji, a nie rekomendację wyborczą.

## Pomysły na rozwój

- poprawki jako osobne pozycje do wyboru (dokładniejsze niż całe ustawy),
- kategoryzacja tematyczna druków (np. Snowflake Cortex AI),
- historia przejść między klubami i zmiana zachowania przed przejściem,
- głosowania „na włosku” – kto był nieobecny, gdy decydowało kilka głosów.
