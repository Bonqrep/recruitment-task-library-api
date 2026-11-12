# Library Management API

Proste REST API do zarządzania książkami w bibliotece: dodawanie, przeglądanie,
usuwanie, wypożyczanie i zwracanie książek. Stan wypożyczenia obejmuje numer karty
czytelnika i datę wypożyczenia. Osobny serwer MCP udostępnia sześć narzędzi
bibliotecznych przez Streamable HTTP, bez użycia modelu LLM.
Opcjonalny agent LangChain korzysta z tego serwera przez lokalne Chat API,
pamięta rozmowę i wymaga zatwierdzenia operacji zapisujących.

## Stack i wymagania

- Python 3.12+, FastAPI i Uvicorn.
- SQLAlchemy 2, PostgreSQL 17 i sterownik psycopg 3.
- Pydantic 2 / pydantic-settings do walidacji i konfiguracji, Alembic do migracji.
- pytest i HTTPX do testów, Ruff do lintingu i formatowania.
- Oficjalny MCP Python SDK 1.30.0 z FastMCP.
- LangChain 1.5 (`create_agent`), langchain-openai, langchain-mcp-adapters i LangGraph.

Do uruchomienia w kontenerach wystarczy Docker z Docker Compose v2 i wolny port
8000. Praca lokalna wymaga dodatkowo Pythona 3.12+ oraz dostępnej bazy PostgreSQL.
Polecenia wykonuj w katalogu głównym repozytorium; przykłady używają powłoki sh/bash/zsh.

## Uruchomienie w Dockerze

```sh
docker compose up --build --wait
```

Compose uruchamia trzy serwisy: `db`, `api` i `mcp`. API oraz MCP korzystają z tego
samego obrazu Python i kodu. Po healthchecku PostgreSQL **tylko API** wykonuje
`alembic upgrade head`, a następnie uruchamia Uvicorn. MCP czeka na zdrowe API,
co gwarantuje zakończenie migracji przed jego startem. Błąd migracji przerywa start.
Oba procesy działają jako użytkownik bez uprawnień roota. Plik `.env` jest opcjonalny.

Port API jest publikowany tylko na `127.0.0.1:8000`. MCP (`mcp:8001`) i PostgreSQL
(`db:5432`) są dostępne wyłącznie w sieci Compose, bez `ports`. Healthcheck API
sprawdza proces HTTP, a healthcheck MCP sprawdza również połączenie z PostgreSQL.
API nie czeka na MCP przy starcie: awaria MCP nie blokuje podstawowego REST API.

- Chat UI: http://localhost:8000/chat
- Swagger UI: http://localhost:8000/docs
- OpenAPI: http://localhost:8000/openapi.json
- Healthcheck: http://localhost:8000/health — sprawdza działanie API, bez odpytywania bazy.

Domyślnie użytkownik, hasło i nazwa bazy to `library` (konfiguracja lokalna).
PostgreSQL działa pod adresem `db:5432` w sieci Compose, bez publikowania portu na
hoście. Dane są przechowywane w nazwanym wolumenie `postgres_data`.

Opcjonalnie skopiuj `.env.example` do `.env` i ustaw `POSTGRES_USER`,
`POSTGRES_PASSWORD`, `POSTGRES_DB`. Compose tworzy z nich `DATABASE_URL`.
Jawne `DATABASE_URL` nadpisuje adres dla API i MCP; musi wskazywać bazę dostępną z kontenera.
Przy znakach specjalnych w danych dostępowych podaj je w URL w postaci zakodowanej.
Zmienne powłoki mają pierwszeństwo przed `.env`. Zmiana `POSTGRES_*` nie zmienia
użytkownika ani hasła w już zainicjalizowanym wolumenie.

```sh
docker compose up --build     # przebudowanie po zmianach w kodzie lub zależnościach
docker compose logs -f api mcp # logi migracji, narzędzi i błędów
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
Compose przekazuje tę zmienną do API. Konfiguracja jest odczytywana raz;
po zmianie `.env` wykonaj `docker compose up -d`, aby odtworzyć kontenery ze
zmienionym środowiskiem.

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
├── agent/                # LangChain, prompt i powiązanie zgody HITL z MCP
├── core/                 # konfiguracja środowiska i wyjątki domenowe
├── db/                   # baza modeli i sesje SQLAlchemy
├── models/               # model tabeli i ograniczenia integralności
├── mcp/                  # osobny serwer Streamable HTTP i adaptery narzędzi
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

## AI Library Assistant

Asystent wyszukuje książki, sprawdza dostępność, dodaje i usuwa pozycje oraz
obsługuje wypożyczenia i zwroty. Wykonuje rzeczywiste tool calling, korzystając
z katalogu PostgreSQL przez MCP. Dopytuje o brakujące dane i pozwala kontynuować
rozmowę o wcześniej wybranej książce.

Technologie: LangChain `create_agent`, ChatOpenAI, LangGraph, oficjalny MCP Python
SDK z FastMCP i `langchain-mcp-adapters`. Prosty frontend HTML/CSS/JavaScript
jest serwowany przez FastAPI, bez Node.js i osobnego procesu frontendu.

```text
Chat UI → FastAPI → LangChain → MCP (Streamable HTTP) → BookService → PostgreSQL
REST API ───────────────────────────────────────────→ BookService → PostgreSQL
```

`BookService` oznacza istniejący moduł `app/services/book_service.py`. REST i MCP
współdzielą jego transakcje, walidację, modele i wyjątki domenowe. Agent nie ma
dostępu do sesji SQL ani do dowolnych zapytań. Odkrywa sześć narzędzi MCP;
lista dozwolonych nazw w kodzie blokuje automatyczne dodawanie nowych narzędzi.

| Narzędzie | Parametry | Działanie |
| --- | --- | --- |
| `search_books` | opcjonalne `title`, `author`, `is_borrowed`, `limit=20`, `offset=0` | Wyszukiwanie i lista w obiekcie `books`. |
| `get_book` | `serial_number` | Szczegóły książki. |
| `add_book` | `serial_number`, `title`, `author` | Dodanie książki po zatwierdzeniu. |
| `borrow_book` | `serial_number`, `borrower_card_number` | Wypożyczenie po zatwierdzeniu. |
| `return_book` | `serial_number` | Zwrot po zatwierdzeniu. |
| `delete_book` | `serial_number` | Usunięcie dostępnej książki po zatwierdzeniu. |

Wyszukiwanie łączy filtry przez AND, dopasowuje fragmenty tekstu bez rozróżniania
wielkości liter i traktuje `%`, `_`, `\` dosłownie. Wyniki są sortowane po numerze
seryjnym; `limit` wynosi 1–100, `offset` jest nieujemny. Wszystkie identyfikatory
mają sześć cyfr ASCII i zachowują zera wiodące. MCP otwiera osobną sesję na każde
wywołanie, wykonuje blokujące operacje bazy w wątku i zwraca bezpieczne błędy.

### Konfiguracja i uruchomienie

```sh
cp .env.example .env
# W edytorze ustaw OPENAI_API_KEY w .env, jeśli chcesz korzystać z agenta.
docker compose config --quiet
docker compose up --build --wait
```

Otwórz **http://localhost:8000/chat**. Klucz pozostaje na backendzie; przeglądarka
komunikuje się wyłącznie z FastAPI. `.env` jest ignorowany przez Git i wykluczony
z kontekstu budowania obrazu. Nie wpisuj klucza do kodu ani README.

| Zmienna | Domyślnie lokalnie | Znaczenie |
| --- | --- | --- |
| `OPENAI_API_KEY` | brak | Wymagana wyłącznie przy użyciu czatu. |
| `OPENAI_MODEL` | `gpt-4.1-mini` | Model obsługujący tool calling. |
| `OPENAI_TIMEOUT` | `60` | Timeout pojedynczego żądania LLM w sekundach. |
| `CHAT_TIMEOUT` | `90` | Timeout inicjalizacji albo wywołania grafu. |
| `MCP_SERVER_URL` | `http://127.0.0.1:8001/mcp` | Compose ustawia `http://mcp:8001/mcp`. |
| `MCP_HOST`, `MCP_PORT` | `127.0.0.1`, `8001` | Compose ustawia host `0.0.0.0` wewnątrz kontenera. |

Klient OpenAI i agent powstają dopiero przy użyciu czatu. **Bez klucza działają
REST API, `/health`, `/docs` i Chat UI**; wysłanie wiadomości zwraca `503` z
komunikatem „Czat wymaga konfiguracji OPENAI_API_KEY.”.

Przy uruchomieniu lokalnym przygotuj PostgreSQL i migracje według wcześniejszej
instrukcji. W dwóch terminalach, z tym samym `DATABASE_URL`, uruchom:

```sh
python -m app.mcp.server
uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --no-proxy-headers
```

Przykładowe polecenia:

- „Znajdź książki Stanisława Lema”.
- „Czy Solaris jest dostępna?”.
- „Chcę ją wypożyczyć” → podaj numer karty, np. `000456`, gdy agent dopyta.
- „Zwróć książkę o numerze 000123”.
- „Dodaj książkę 000124, Hobbit, J. R. R. Tolkien”.
- „Usuń książkę o numerze 000124”.

Katalog początkowo jest pusty. Dodaj książkę przez REST lub zatwierdzone narzędzie
agenta; przykłady nie oznaczają, że wymienione tytuły istnieją w bazie.

### Rozmowy i zatwierdzanie operacji

Jeden agent i `InMemorySaver` przechowują historię oraz checkpointy w pamięci
procesu. `thread_id` rozdziela rozmowy. Chat UI pamięta go wyłącznie w pamięci
strony; „Nowa rozmowa” lub odświeżenie rozpoczynają nowy wątek. Restart API usuwa
historie i oczekujące zgody. Aplikacja wymaga jednego workera.

`HumanInTheLoopMiddleware` **zatrzymuje wszystkie cztery narzędzia zapisujące
przed ich wykonaniem**. UI pokazuje nazwę operacji i dokładne parametry oraz tytuł
książki odczytany z REST API. Brak odczytu tytułu blokuje zgodę, pozostawiając
anulowanie i ponowienie odczytu. Użytkownik zatwierdza lub odrzuca całą pokazaną
listę operacji. Zgoda w zwykłej wiadomości nie zatwierdza zapisu.

`POST /api/v1/chat` przyjmuje `{"message":"Czy macie Solaris?","thread_id":null}`.
W kolejnych wiadomościach przekazuj UUID otrzymany w odpowiedzi. Status odpowiedzi
to `completed` lub `requires_confirmation`. W drugim przypadku odpowiedź zawiera
`pending_confirmation` z `confirmation_id` i listą `operations` (`name`, `args`).
Decyzję wyślij do `POST /api/v1/chat/confirm`:

```json
{
  "thread_id": "fe8b3b2d-11b5-45bb-b546-3ec9786f7630",
  "confirmation_id": "identyfikator-z-odpowiedzi",
  "approved": true
}
```

`false` odrzuca operacje bez zapisu. Backend odczytuje parametry z checkpointu,
nie przyjmuje ich w żądaniu decyzji. Powtórzona lub nieaktualna zgoda zwraca `409`;
nieistniejąca rozmowa `404`. Kolejna propozycja zapisu wymaga nowej zgody.
Równoległe żądania tej samej rozmowy są odrzucane kodem `409`.

MCP dodatkowo wymaga form elicitation. Interceptor wiąże zgodę z dokładną parą
nazwa/argumenty z checkpointu i pozwala zaakceptować odpowiadający jej formularz
jednokrotnie. Bez takiego zakresu zgody zapis jest blokowany. Inny klient MCP musi
pokazać formularz użytkownikowi i przekazać jego decyzję, nigdy zatwierdzać go sam.

Błędy narzędzi są przekazywane agentowi jako `ToolMessage(status="error")`.
Chat API nie potwierdza wtedy sukcesu nawet przy błędnej odpowiedzi modelu.
Timeout zwraca `504`, niedostępny agent/MCP `503`, błędne dane `422`.
`completed` oznacza zakończenie obsługi, nie gwarancję sukcesu operacji.
Po awarii grafu backend usuwa checkpoint rozmowy, aby nie odtworzyć zapisu.

### Ograniczenia demonstracji

- Brak logowania i uprawnień; UUID rozmowy nie jest dowodem tożsamości. REST API
  pozostaje bezpośrednim interfejsem CRUD; zatwierdzanie dotyczy operacji agenta/MCP.
- Udostępniaj aplikację wyłącznie lokalnie. Compose publikuje API na loopback.
  Chat API sprawdza lokalny Host i ten sam Origin. `CHAT_ALLOWED_NETWORKS`
  (domyślnie puste) w Compose dopuszcza adresy prywatne RFC1918 używane przez NAT
  Dockera. Możesz nadpisać tę listę w `.env` jako tablicę JSON, np. zawężając ją
  do faktycznego adresu bramy Dockera. Nie publikuj portu na innych interfejsach
  ani przez proxy/tunel.
  Nagłówki przekazywane przez proxy nie służą do przyznawania dostępu.
- Pamięć nie jest trwała i nie ma limitu liczby przechowywanych rozmów; brak
  skalowania na wiele workerów, uwierzytelniania MCP i produkcyjnego magazynu zgód.
- Limity grafu i wywołań ograniczają pętle agenta. Model może się mylić; prompt
  nakazuje korzystać z wyników katalogu i dopytywać przy niejednoznacznych tytułach,
  ale nie stanowi formalnej gwarancji braku halucynacji.
- Koszt i dostępność czatu zależą od OpenAI. Zapisy nie są automatycznie ponawiane.
  Utrata odpowiedzi po zatwierdzeniu może nastąpić po commit: przed nową próbą
  sprawdź stan katalogu. Potwierdzona lista operacji nie jest jedną transakcją.
- Healthcheck API jest kontrolą procesu HTTP; zdrowe API nie oznacza dostępnego
  OpenAI. Healthcheck MCP nie wykonuje płatnego zapytania do modelu.
- SDK MCP 1.30.0 może logować `ClosedResourceError` podczas zamykania strumienia
  SSE klienta. Zaobserwowano to po zakończonych wywołaniach; nie blokuje narzędzi,
  zatwierdzania ani ponownego połączenia. To ograniczenie logowania SDK.

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
Testy agenta używają mockowanego LLM, prawdziwego grafu, HITL i MCP. Sprawdzają
parametry, pamięć, dopytywanie, niejednoznaczność, błędy i jednokrotne zgody.
Testy PostgreSQL uruchamiają MCP jako osobny proces przez Streamable HTTP oraz
sprawdzają wszystkie zapisy po zgodzie i brak zmian po odmowie. Mock modelu
weryfikuje orkiestrację, nie jakość rozpoznawania intencji przez OpenAI.
`ruff format .` formatuje kod; projekt nie zawiera osobnego analizatora typów.

Opcjonalne, płatne testy prawdziwego OpenAI wymagają jawnego włączenia:

```sh
RUN_OPENAI_TESTS=1 TEST_DATABASE_URL='postgresql+psycopg://library:library@localhost:55432/library_test' \
  python -m pytest tests/test_agent_postgresql.py -m openai -v -s
```

Ustaw wcześniej `OPENAI_API_KEY` w środowisku; testy są izolowane od lokalnego
`.env`. Domyślny zestaw nie wykonuje płatnych zapytań.

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
