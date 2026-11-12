# Library Management API

Proste REST API do zarządzania książkami w bibliotece: dodawanie, przeglądanie,
usuwanie, wypożyczanie i zwracanie książek. Stan wypożyczenia obejmuje numer karty
czytelnika i datę wypożyczenia.

## Stack i wymagania

- Python 3.12+, FastAPI i Uvicorn.
- SQLAlchemy 2, PostgreSQL 17 i sterownik psycopg 3.
- Pydantic 2 / pydantic-settings do walidacji i konfiguracji, Alembic do migracji.
- pytest i HTTPX do testów, Ruff do lintingu i formatowania.

Do uruchomienia w kontenerach wystarczy Docker z Docker Compose v2 i wolny port
8000. Praca lokalna wymaga dodatkowo Pythona 3.12+ oraz dostępnej bazy PostgreSQL.
Polecenia wykonuj w katalogu głównym repozytorium; przykłady używają powłoki sh/bash/zsh.

## Uruchomienie w Dockerze

```sh
docker compose up
```

Compose buduje brakujący obraz API, uruchamia PostgreSQL i czeka na jego healthcheck.
API wykonuje `alembic upgrade head`, a następnie uruchamia Uvicorn jako użytkownik
bez uprawnień roota. Błąd migracji przerywa start. Plik `.env` nie jest wymagany.

- Swagger UI: http://localhost:8000/docs
- OpenAPI: http://localhost:8000/openapi.json
- Healthcheck: http://localhost:8000/health — sprawdza działanie API, bez odpytywania bazy.

Domyślnie użytkownik, hasło i nazwa bazy to `library` (konfiguracja lokalna).
PostgreSQL działa pod adresem `db:5432` w sieci Compose, bez publikowania portu na
hoście. Dane są przechowywane w nazwanym wolumenie `postgres_data`.

Opcjonalnie skopiuj `.env.example` do `.env` i ustaw `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `POSTGRES_DB`. Compose tworzy z nich `DATABASE_URL`.
Jawne `DATABASE_URL` nadpisuje cały adres; musi wskazywać bazę dostępną z kontenera.
Przy znakach specjalnych w danych dostępowych podaj je w URL w postaci zakodowanej.
Zmienne powłoki mają pierwszeństwo przed `.env`. Zmiana `POSTGRES_*` nie zmienia
użytkownika ani hasła w już zainicjalizowanym wolumenie.

```sh
docker compose up --build     # przebudowanie po zmianach w kodzie lub zależnościach
docker compose logs -f api    # logi migracji, operacji zapisu i błędów
docker compose down          # zatrzymanie; dane bazy pozostają
```

`docker compose down --volumes` usuwa także dane lokalnej bazy.

Przykład dodania książki:

```sh
curl -i http://localhost:8000/api/v1/books \
  -H 'Content-Type: application/json' \
  -d '{"serial_number":"000123","title":"Solaris","author":"Stanisław Lem"}'
```

## Uruchomienie lokalne

Przygotuj bazę PostgreSQL i ustaw `DATABASE_URL` w powłoce lub w `.env`.
Baza z podstawowego Compose nie udostępnia portu na hoście.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
export DATABASE_URL='postgresql+psycopg://library:library@localhost:5432/library'
alembic upgrade head
uvicorn app.main:app --reload
```

Opcjonalne `APP_NAME` ustawia tytuł aplikacji (domyślnie `Library Management API`).
W Compose tę zmienną trzeba dodać do `api.environment`; sam wpis w `.env` jej nie
przekazuje. Konfiguracja jest odczytywana raz i wymaga restartu po zmianie.

## Endpointy

| Metoda | Ścieżka | Działanie | Sukces | Błędy |
| --- | --- | --- | --- | --- |
| GET | `/health` | Stan API | `200` | — |
| POST | `/api/v1/books` | Dodanie książki | `201` | `409`, `422` |
| GET | `/api/v1/books` | Lista książek | `200` | `422` |
| GET | `/api/v1/books/{serial_number}` | Szczegóły książki | `200` | `404`, `422` |
| DELETE | `/api/v1/books/{serial_number}` | Usunięcie dostępnej książki | `204` | `404`, `409`, `422` |
| POST | `/api/v1/books/{serial_number}/borrow` | Wypożyczenie | `200` | `404`, `409`, `422` |
| POST | `/api/v1/books/{serial_number}/return` | Zwrot | `200` | `404`, `409`, `422` |

Tworzenie przyjmuje `serial_number`, `title`, `author`. Numery seryjne i numery kart
czytelnika są tekstem z dokładnie sześcioma cyframi ASCII; zera wiodące są zachowywane.
Tytuł i autor mają 1–255 znaków po usunięciu białych znaków z początku i końca.
Dodatkowe pola w żądaniach tworzenia i wypożyczenia są odrzucane.

Wypożyczenie przyjmuje np. `{"borrower_card_number":"000456"}`. Zwrot nie wymaga
body i czyści numer karty oraz datę wypożyczenia. Odpowiedź książki zawiera
`serial_number`, `title`, `author`, `is_borrowed`, `borrower_card_number`, `borrowed_at`.
Data ma strefę czasową i jest zapisywana przez aplikację w UTC. Lista zwraca tablicę
książek posortowaną po numerze seryjnym: `limit=100` (1–100), `offset=0` (minimum 0).
Usunięcie zwraca pustą odpowiedź.

`404` oznacza brak książki, `409` — duplikat numeru albo niedozwoloną zmianę stanu:
ponowne wypożyczenie, zwrot dostępnej książki lub usunięcie wypożyczonej.
Błędy domenowe mają format `{"detail":"..."}`; walidacja zwraca `422` z listą
błędów w `detail`. Nieoczekiwane błędy zwracają `500` z ogólnym komunikatem,
bez szczegółów SQL i wyjątków; szczegóły trafiają do logów serwera.

## Architektura i decyzje techniczne

```text
app/
├── main.py               # konfiguracja FastAPI, logowania i routerów
├── api/                  # HTTP: endpointy i centralna obsługa wyjątków
├── core/                 # konfiguracja środowiska i wyjątki domenowe
├── db/                   # baza modeli i sesje SQLAlchemy
├── models/               # model tabeli i ograniczenia integralności
├── schemas/              # walidacja żądań i schematy odpowiedzi
└── services/             # reguły biznesowe, zapytania i transakcje
alembic/                  # konfiguracja i wersje migracji
tests/                    # testy jednostkowe, HTTP i integracyjne PostgreSQL
```

- Routery obsługują HTTP; serwis zawiera operacje na książkach, bez zależności od FastAPI.
- Synchroniczne SQLAlchemy i zwykłe funkcje endpointów upraszczają obsługę blokującego I/O.
- Każde żądanie otrzymuje osobną sesję, zamykaną przez zależność `get_db`.
  Zapisy używają `Session.begin()` w serwisie, z automatycznym commit/rollback;
  wymagają sesji bez aktywnej transakcji. `expire_on_commit=False` pozwala odczytać
  wynik bez dodatkowego zapytania po zatwierdzeniu. Zamknięcie sesji wycofuje
  pozostałą transakcję odczytu.
- Wypożyczenie, zwrot i usunięcie blokują wiersz przez `SELECT ... FOR UPDATE`.
  Sprawdzenie stanu i zapis odbywają się w jednej transakcji, co zapobiega wyścigom.
- Walidacja Pydantic daje czytelne błędy API, a ograniczenia bazy chronią integralność
  danych. Duplikat rozpoznawany jest po naruszeniu klucza głównego, bez osobnego
  sprawdzania istnienia przed zapisem. Pozostałe błędy bazy nie stają się `409`.

## Testy i jakość kodu

Po instalacji dodatku `dev` w środowisku lokalnym:

```sh
python -m pytest
ruff check .
ruff format --check .
```

Bez `TEST_DATABASE_URL` testy PostgreSQL są pomijane; pozostałe nie wymagają bazy.
Aby uruchomić cały zestaw, przygotuj osobną bazę z uprawnieniem tworzenia schematów,
np. w tymczasowym kontenerze (wolny port 55432):

```sh
docker run -d --name library-api-test-db -p 127.0.0.1:55432:5432 \
  -e POSTGRES_USER=library -e POSTGRES_PASSWORD=library \
  -e POSTGRES_DB=library_test postgres:17
# Poczekaj, aż polecenie zakończy się sukcesem:
docker exec library-api-test-db pg_isready -U library -d library_test
TEST_DATABASE_URL='postgresql+psycopg://library:library@localhost:55432/library_test' \
  python -m pytest
# Po testach usuń tymczasową bazę:
docker rm -fv library-api-test-db
```

Każdy test integracyjny tworzy osobny schemat, wykonuje migracje i usuwa schemat
po zakończeniu. Testy obejmują CRUD, walidację, błędy domenowe i wewnętrzne,
rollback, blokady i równoczesne wypożyczenia, a także OpenAPI i stronę Swagger UI.
Ruff sprawdza również adnotacje typów funkcji; `ruff format .` formatuje cały kod.

## Migracje

W Compose migracje wykonują się automatycznie przy każdym starcie API.
Dla uruchomienia lokalnego, z ustawionym `DATABASE_URL`:

```sh
alembic upgrade head                     # zastosuj migracje
alembic current                          # bieżąca wersja
alembic revision --autogenerate -m "opis" # wygeneruj nową migrację po zmianie modeli
```

Przejrzyj wygenerowaną migrację przed jej zastosowaniem. Schemat bazy jest zarządzany
przez Alembic; aplikacja nie tworzy tabel przez `create_all()` przy starcie.
