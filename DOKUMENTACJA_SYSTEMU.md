# Dokumentacja systemu PSH QC / Profit

Dokument opisuje architekturę, moduły, API oraz stan integracji z systemem ERP Streamsoft aplikacji znajdującej się w katalogu `profit/`. Uzupełnia (nie duplikuje) istniejące dokumenty:

- [`CLAUDE.md`](CLAUDE.md) — zasady pracy, workflow commitów, deploy
- [`KOSZTORYSY_DOKUMENTACJA.md`](KOSZTORYSY_DOKUMENTACJA.md) — szczegółowa formuła kalkulacji kosztorysów
- [`DESIGN.md`](DESIGN.md) — design system (kolory, typografia) strony firmowej Kubiak

---

## 1. Przegląd systemu

**PSH QC** to wewnętrzny system webowy (Flask) do zarządzania produkcją rozdzielnic elektrycznych: przyjmowanie zamówień, kontrola jakości (QC/QA), montaż, kosztorysowanie ofert, rejestr NCR (niezgodności jakościowych) oraz ewidencja produkcji na spawalni/giętarce/wycinarce.

**Stack technologiczny:**
- Python 3.14 (produkcja: Python 3.11 pod Phusion Passenger), Flask 3.0
- Flask-SQLAlchemy (ORM) + SQLite (`instance/psh_qc.db`)
- Flask-Login (sesje/autoryzacja), Flask-WTF/WTForms
- ReportLab (generowanie PDF), openpyxl (eksport Excel), Pillow (walidacja obrazów)
- Frontend: Jinja2 + JS/CSS własne (bez frameworka SPA), PWA (`manifest.json`, `sw.js`)
- Testy: pytest (46 testów, `tests/` — w tym `tests/test_api_v1.py` dla REST API)

**Uruchomienie lokalne:** `.\venv\Scripts\python.exe app.py` → http://127.0.0.1:5000
**Produkcja:** `host82388.iqhs.pl`, Phusion Passenger, wejście przez `wsgi.py` → `app.py`.

### Role użytkowników (`models.User.role`)

| Rola | Uprawnienia |
|---|---|
| `admin` | pełny dostęp do wszystkich modułów i panelu administracyjnego |
| `kontroler` | kontrola jakości (QA), spawalnia, QAR |
| `monter` | pula zadań montażowych (`/monter/pool`) |
| `order` | zakładanie i zarządzanie zamówieniami klientów |
| `spawacz` | wprowadzanie rekordów produkcyjnych na spawalni |

---

## 2. Architektura kodu

```
profit/
├── app.py            — rdzeń aplikacji: auth, checklisty/raporty QC, zamówienia,
│                        panel admina, alerty, PWA, REST API (/api/v1, /api/desktop)
├── models.py          — wszystkie modele SQLAlchemy (wspólna baza danych)
├── config.py          — konfiguracja (klucz sesji, ścieżki uploadów, API_KEY)
├── pdf_generator.py   — generator PDF „Wytycznych importu CSV"
├── kosztorys/         — blueprint `/kosztorys` — moduł ofertowania/kosztorysów
│   ├── routes.py, calculator.py, excel_export.py, pdf_export.py
├── qar/                — blueprint `/qar` — rejestr niezgodności jakościowych (NCR)
│   ├── routes.py, pdf_export.py
├── spawalnia/          — blueprint `/spawalnia` — ewidencja spawalni/giętarki/wycinarki
│   ├── routes.py, excel_export.py, pdf_export.py
├── templates/           — szablony Jinja2, pogrupowane per moduł (admin, orders, qar, spawalnia, kosztorys, monter, alerts, errors)
├── static/              — CSS/JS/ikony/uploads
└── tests/               — pytest
```

Główna aplikacja rejestruje trzy blueprinty (`app.py:38-45`):

```python
app.register_blueprint(kosztorys_bp)   # url_prefix='/kosztorys'
app.register_blueprint(spawalnia_bp)   # url_prefix='/spawalnia'
app.register_blueprint(qar_bp)         # url_prefix='/qar'
```

Baza danych jest **jedna wspólna** (`models.py`, `db = SQLAlchemy()`) — wszystkie blueprinty operują na tych samych tabelach/silniku, nie ma osobnych baz per moduł. Migracje schematu są ręczne, realizowane przez `_migrate_schema()` w `app.py` (kolejne `ALTER TABLE`), bez Alembic.

---

## 3. Moduły funkcjonalne

### 3.1 Rdzeń — Kontrola jakości (QC) i raporty (`app.py`)

Centralny mechanizm systemu: **szablony kontrolne** (`ChecklistTemplate` → `Category` → `Task`) generują **raporty** (`Report` → `ReportItem` → `Photo`) wypełniane przez kontrolerów/monterów.

- Tworzenie raportu ręcznie (`/checklist/new`) lub przez skan QR (`/checklist/from-qr`)
- Wypełnianie checklisty: wynik OK/NG/N/A/DW, wartość liczbowa z zakresem (`value_min/max`), notatki, zdjęcia (walidacja MIME przez Pillow)
- Blokada edycji (`locked_by_id`, `lock_active` — 30 min) żeby dwóch kontrolerów nie edytowało tego samego raportu równolegle
- Sesje pracy (`ChecklistSession`) — czas rozpoczęcia/zakończenia pracy nad raportem
- Ocena raportu: `score` (skala 1–6) na podstawie % OK wśród ocenionych pozycji
- Eksport PDF raportu (`/reports/<id>/pdf`), eksport CSV listy raportów (`/reports/export.csv`)
- Pula montażu (`/monter/pool`) — monterzy „biorą” wolne zadania montażowe (`report_type='monter'`)

### 3.2 Zamówienia (`/orders`)

Zamówienia klientów (`Order`) — status: `active → in_control → ready_to_ship → shipped`.

- Ręczne dodawanie (`/orders/new`) z opcjonalnym załącznikiem PDF (np. specyfikacja/zamówienie od klienta)
- **Import masowy z CSV** (`/orders/import-csv`) — patrz sekcja 5 (integracja ERP)
- Po utworzeniu zamówienia system automatycznie:
  - dobiera pasujący szablon QA i montażu po dopasowaniu tokenów nazwy produktu (`_find_matching_template`)
  - generuje po jednym raporcie na sztukę (`quantity`), grupując wielosztukowe partie przez `batch_id`
  - wysyła alerty do kontrolerów/monterów (`Alert`)
- Status zmienia się automatycznie na `ready_to_ship`, gdy wszystkie raporty danego zamówienia są zakończone bez NG (`_check_order_complete`)
- Wysyłka (`/orders/<id>/ship`), regeneracja list kontrolnych (`/orders/<id>/regenerate`)

### 3.3 Alerty (`/alerts`)

Prosty system powiadomień w aplikacji (`Alert`) — polling (`/alerts/poll`), oznaczanie jako przeczytane, typy `info/urgent/success`.

### 3.4 Panel administracyjny (`/admin/*`)

Zarządzanie: szablonami kontrolnymi i ich kategoriami/zadaniami (w tym eksport/import szablonów jako JSON), użytkownikami, log audytu (`AuditLog`), statystyki (`/admin/stats`, w tym raporty NG per zadanie i czas trwania per szablon).

### 3.5 Kosztorysy (`/kosztorys`) — ofertowanie

Moduł do automatycznego liczenia kosztorysów szaf/rozdzielnic (4 rodziny: **PSH IP65**, **Compact**, **INOX** 304/316, **Modular**). Pełna formuła w [`KOSZTORYSY_DOKUMENTACJA.md`](KOSZTORYSY_DOKUMENTACJA.md); skrót:

1. Waga blachy (pole × grubość × gęstość 8.0) → koszt materiału + malowania
2. Odpad 15% na blasze DC01/INOX
3. Osprzęt: zawiasy, uszczelki (z obwodu), zamki, śruby M6/M8, wpusty kablowe
4. Robocizna wg tabeli `LaborRate` (laser/gięcie/spawanie/szlifowanie/montaż/pakowanie) w 3 progach objętościowych; INOX mnoży spawanie/szlifowanie ×1.4 (304) / ×1.6 (316)
5. Marża (domyślnie ×2.15) → rabat % → bonus %
6. Zyskowność = (cena po rabacie i bonusie − koszt) / cena × 100%

Kreator ofert (`/kosztorys/kreator`) pozwala budować wycenę z katalogu gotowych produktów. Eksport do PDF i Excel, panel admina do zarządzania cenami materiałów, stawkami robocizny, typami szaf i katalogiem produktów.

### 3.6 QAR — rejestr niezgodności (`/qar`)

Rejestr zgłoszeń jakościowych (NCR-podobny) — kategorie: Spawanie, Montaż, Materiał, Malowanie, Dokumentacja, Inne. Zgłoszenie (`QARReport`) ma opis, ustalenia (findings), sposób rozwiązania (resolution), zdjęcia (`QARPhoto`), status `open → in_progress → closed` z polem „zweryfikowane przez”. Eksport do PDF.

### 3.7 Spawalnia (`/spawalnia`)

Ewidencja produkcji na spawalni/giętarce/wycinarce — rekordy (`SpawalniaRecord`) przypisane do numeru ZO (zlecenia produkcyjnego), z operatorami spawania, gięcia i cięcia (osobne słowniki operatorów), pomiarami (przekątne, odchyłki, 3 pomiary kontrolne) i wynikiem OK/NG per etap. Obsługuje partie (batch) przy wielosztukowych ZO. Eksport do PDF i Excel dla admina/kontrolera.

---

## 4. API

System udostępnia trzy różne „warstwy" API o różnym przeznaczeniu i różnym poziomie zabezpieczeń:

### 4.1 Wewnętrzne AJAX API (`/api/item/*`, `/api/categories/*`, `/api/tasks/*`, `/api/report/*`, `/api/photo/*`)

Używane wyłącznie przez JS własnego frontendu do zapisywania wyników checklisty bez przeładowania strony (wynik zadania, wartość liczbową, notatki, upload zdjęcia, reorder zadań/kategorii, heartbeat blokady raportu). Wymaga zalogowania (`login_required`) + tokenu CSRF w nagłówku `X-CSRF-Token` lub polu formularza. **Nie nadaje się i nie jest przeznaczone do integracji zewnętrznych.**

### 4.2 `/api/desktop/*` — API dla aplikacji desktopowej

Osobny zestaw endpointów logowania sesyjnego (`/api/desktop/login`, `/api/desktop/logout`) i danych (dashboard, lista szablonów, tworzenie/pobieranie/kończenie raportów) przeznaczony dla towarzyszącej aplikacji desktopowej używającej tego samego mechanizmu sesji/cookie co przeglądarka (nie tokenów). Chronione `@login_required`, logowanie ma dodatkowo rate-limiting (5 nieudanych prób / 5 min / IP).

### 4.3 `/api/v1/*` — REST API do integracji zewnętrznych

To jedyna część API zaprojektowana pod integracje z systemami zewnętrznymi (np. ERP). **Od 2026-07 zabezpieczona kluczem API** — patrz niżej.

| Obszar | Endpoint | Metoda | Opis |
|---|---|---|---|
| Szablony | `/api/v1/templates` | GET | lista aktywnych szablonów kontrolnych |
| Checklisty | `/api/v1/checklists` | GET | lista raportów z filtrami (status, szablon, nr zamówienia, zakres dat), paginacja |
| Checklisty | `/api/v1/checklists` | POST | utworzenie nowego raportu dla szablonu (opcjonalnie powiązanego z zamówieniem po numerze) |
| Checklisty | `/api/v1/checklists/<id>` | GET | szczegóły raportu — kategorie, zadania, wyniki, zdjęcia, **czas rozpoczęcia/zakończenia, ocena (`score`), zgodność (`compliant`)** |
| Checklisty | `/api/v1/checklists/<id>/start` | POST | oznacza rozpoczęcie pracy nad raportem (`started_at`, nowa `ChecklistSession`) |
| Checklisty | `/api/v1/checklists/<id>/complete` | POST | zamyka raport, liczy `duration_seconds`, zwraca pełne podsumowanie (stats/score/compliant) |
| Checklisty | `/api/v1/report/<id>` | GET | alias `checklists/<id>` |
| Zamówienia | `/api/v1/orders` | GET | lista zamówień (filtr `?external_number=`, domyślnie bez `shipped`) |
| Zamówienia | `/api/v1/orders/<id>` | GET | szczegóły jednego zamówienia |
| Zamówienia | `/api/v1/orders` | POST | tworzenie zamówienia z ERP (`number`, `product_name`, `client`, `quantity`, `due_date`, `external_number`) + automatyczne dopasowanie szablonu QA/montażu |
| Kosztorysy | `/api/v1/prices` | GET | aktualne ceny materiałów (`MaterialPrice`) |
| Kosztorysy | `/api/v1/labor-rates` | GET | stawki robocizny wg progu objętościowego (`LaborRate`) |
| Kosztorysy | `/api/v1/quotes`, `/api/v1/quotes/<id>` | GET | lista/szczegóły wycen (status, wymiary, snapshot kalkulacji) |
| Spawalnia | `/api/v1/spawalnia/<zo_number>` | GET | rekordy produkcyjne dla numeru ZO (wyniki OK/NG, pomiary, operatorzy) |
| Spawalnia | `/api/v1/spawalnia/<zo_number>` | POST | utworzenie N list kontrolnych dla ZO (`quantity`) |
| QAR | `/api/v1/qar`, `/api/v1/qar/<id>` | GET | lista/szczegóły zgłoszeń niezgodności |
| QAR | `/api/v1/qar` | POST | utworzenie zgłoszenia NCR (`title`, `description`, opcjonalnie `category`, `location`) |

**Zabezpieczenie kluczem API:** każdy endpoint `/api/v1/*` wymaga nagłówka `X-API-Key` zgodnego z `Config.API_KEY` (dekorator `api_key_required`, `app.py`) — brak/zły klucz zwraca `401`. Ponieważ autoryzacja odbywa się kluczem, a nie sesją przeglądarki, `/api/v1/*` jest zwolnione z sesyjnej ochrony CSRF (`csrf_protect()` w `app.py`) — dzięki temu `POST`-y działają poprawnie z zewnętrznego skryptu/ERP bez logowania przez przeglądarkę. Klucz ustawia się zmienną środowiskową `API_KEY` w `.env` (patrz `CLAUDE.md`, sekcja „Zmienne środowiskowe”) — **nie zostawiać wartości domyślnej `change-this-api-key` na produkcji**.

Pełna specyfikacja z przykładami żądań/odpowiedzi: [`API_STREAMSOFT.md`](API_STREAMSOFT.md).

---

## 5. Integracja z ERP Streamsoft — stan obecny

**Nie istnieje żywe (API-to-API) połączenie ze Streamsoft.** Wymiana danych odbywa się wyłącznie plikowo (CSV), ręcznie, przez interfejs webowy:

### 5.1 Import zamówień z CSV (`/orders/import-csv`, `app.py:2195-2405`)

- Format: CSV, separator `;`, kodowanie UTF-8 / UTF-8-BOM / CP1250, pierwszy wiersz to nagłówki
- Wymagane kolumny: `Numer wew.`, `Identyfikator` (nazwa produktu), `Kontrahent`
- Opcjonalne: `Lp` (rozróżnia duplikaty numeru), `Ilość`, `Termin dostawy` (3 formaty daty), **`Numer zew.`** — opisany wprost jako „zewnętrzny numer zamówienia (np. z systemu klienta / ERP)", zapisywany jako notatka do zamówienia
- Przepływ: upload → parsowanie → zapis tymczasowy na dysku (sesja cookie ma limit ~4 KB, więc podgląd nie mieści się w sesji) → podgląd wierszy z możliwością odznaczenia → zatwierdzenie → utworzenie zamówień + automatyczne dopasowanie szablonów QA/montażu
- Duplikaty (istniejący numer wewnętrzny) i wiersze z brakującymi wymaganymi polami są pomijane i oznaczane jako błędne
- `generate_csv_guide.py` generuje PDF „Wytyczne importu CSV" (dokument dla działu handlowego/ERP z dokładną specyfikacją formatu — plik `wytyczne_import_csv.pdf`)

### 5.2 Eksport CSV (`/reports/export.csv`, `app.py:773-816`)

Eksport listy raportów QC (ID, tytuł, operator, szablon, status, daty, % postępu, liczby OK/NG/N-A) do CSV — może służyć jako dane wejściowe do dalszego przetwarzania w Streamsoft lub arkuszach kalkulacyjnych, ale nie jest to zautomatyzowane połączenie (brak harmonogramu/webhooka, plik trzeba pobrać ręcznie).

### 5.3 `static/css/erp-design.css`

To wyłącznie **warstwa wizualna** — zestaw zmiennych CSS (kolory, typografia) stylizujący interfejs modułu zamówień/importu na wygląd przypominający ERP Streamsoft PCBiznes. **Nie zawiera żadnej logiki integracyjnej ani wywołań API.**

### 5.4 Podsumowanie stanu integracji

| Kierunek | Mechanizm | Automatyzacja |
|---|---|---|
| Streamsoft → PSH QC (zamówienia) | Eksport CSV ze Streamsoft → ręczny upload w `/orders/import-csv` **lub** `POST /api/v1/orders` (kluczem API) | Ręczne (CSV) albo w pełni automatyczne (REST) |
| PSH QC → Streamsoft (raporty/wyniki) | Eksport CSV z `/reports/export.csv` **lub** odczyt `GET /api/v1/checklists` / `GET /api/v1/orders` | Ręczne (CSV) albo w pełni automatyczne (REST, polling) |
| Zewnętrzny numer ERP | Kolumna `Order.external_number` (dedykowane pole, filtrowalne przez `GET /api/v1/orders?external_number=`) | Ustrukturyzowane — wcześniej trafiało do `notes`, od 2026-07 osobna kolumna |

Import CSV pozostaje wygodny dla pracy ręcznej (obsługa handlowa wgrywa plik z podglądem przed zatwierdzeniem). REST `/api/v1` jest teraz właściwą ścieżką dla w pełni zautomatyzowanej integracji maszyna-maszyna — oba kanały mogą działać równolegle.

---

## 6. Możliwości rozwoju

Zrealizowane (2026-07): uwierzytelnianie kluczem API, wyjątek CSRF dla `/api/v1/*`, `POST /api/v1/orders`, kolumna `Order.external_number`, rozszerzenie checklisty o start/complete/score. Pozostałe kierunki rozwoju:

1. **Webhook zwrotny do Streamsoft** — po zmianie statusu zamówienia (np. `ready_to_ship` w `orders_ship`) lub zamknięciu raportu wywołać HTTP POST do adresu skonfigurowanego w `Config` (np. `STREAMSOFT_WEBHOOK_URL`), żeby ERP nie musiał odpytywać (`polling`) `GET /api/v1/orders`.
2. **Klucze per-integracja** — obecnie jeden wspólny `API_KEY` (świadoma decyzja na start integracji ze Streamsoft). Przy kolejnych integracjach zewnętrznych warto przejść na tabelę kluczy w bazie (nazwa klienta, status aktywności, log użycia), żeby móc odwołać dostęp jednej integracji bez wpływu na inne.
3. **Wersjonowanie API** — obecny `/api/v1` nie ma mechanizmu wersjonowania odpowiedzi/kompatybilności wstecznej; przy realnej integracji z ERP warto ustalić kontrakt (schema JSON/OpenAPI) i pilnować go testami kontraktowymi, a zmiany niekompatybilne wstecz wprowadzać jako `/api/v2`.
4. **Aktualizacja zamówienia przez API** — dziś `POST /api/v1/orders` tylko tworzy; brakuje `PATCH /api/v1/orders/<id>` do zmiany ilości/terminu z poziomu ERP po stronie zamówienia już istniejącego w PSH QC.
5. **Rate limiting dla `/api/v1/*`** — obecnie tylko klucz API chroni endpointy; przy wielu wywołaniach na sekundę (błąd po stronie integracji) nie ma ograniczenia liczby żądań, w przeciwieństwie do `/login`.

---

## 7. Dodawanie nowych modułów

System jest już zorganizowany modułowo przez blueprinty Flask (`kosztorys`, `qar`, `spawalnia`). Aby dodać kolejny moduł (np. „logistyka", „reklamacje"):

1. Utwórz katalog `profit/<nazwa_modulu>/` z plikami:
   - `__init__.py` — definicja blueprintu: `<nazwa>_bp = Blueprint('<nazwa>', __name__, url_prefix='/<nazwa>')` + `from . import routes`
   - `routes.py` — widoki/endpointy modułu
   - opcjonalnie `pdf_export.py`, `excel_export.py`, `calculator.py` — logika specyficzna dla modułu
2. Dodaj modele danych w **wspólnym** `models.py` (system celowo trzyma jedną bazę — nie twórz osobnej bazy danych dla modułu), z prefiksem nazw tabel czytelnym dla modułu.
3. Zarejestruj blueprint w `app.py` obok istniejących (`app.register_blueprint(<nazwa>_bp)`), tuż po `db.init_app(app)`.
4. Dodaj katalog szablonów `templates/<nazwa_modulu>/` — zachowaj konwencję istniejących modułów (`list.html`, `new.html`, `edit.html`, `_form.html` itp.).
5. Jeśli moduł wymaga migracji schematu, dopisz odpowiednie `ALTER TABLE ...` w `_migrate_schema()` w `app.py` (system nie używa Alembic).
6. Jeśli moduł ma mieć własne role dostępu, dodaj dekorator `<nazwa>_required` analogiczny do `admin_required`/`kontroler_required` w `app.py`, oraz właściwość `is_<rola>` w `models.User`.
7. Dopisz testy w `tests/` i uruchom pełny zestaw (`pytest tests/ -v`) przed commitem — zgodnie z workflow opisanym w `CLAUDE.md`.

---

## 8. Bezpieczeństwo — stan obecny (istotne dla integracji)

- CSRF: token sesyjny wymagany dla wszystkich `POST/PUT/PATCH/DELETE` poza wyjątkami (`static`, `sw.js`, `manifest.json`, `api_desktop_login`, `/api/v1/*`)
- `/api/v1/*` chronione kluczem API (nagłówek `X-API-Key`, dekorator `api_key_required`) — klucz w zmiennej środowiskowej `API_KEY` (`.env`, nie w repo)
- Sesje wygasają po 1h nieaktywności (`SESSION_TIMEOUT`)
- Brute-force: blokada logowania po 5 nieudanych próbach / 5 min / IP (dotyczy `/login` i `/api/desktop/login`) — **`/api/v1/*` nie ma jeszcze rate-limitingu**, patrz sekcja 6, pkt 5
- Wszystkie zmiany audytowane w `AuditLog` (kto, co, kiedy, z jakiego IP) — w tym akcje wykonane przez API (`api_order_create`, `api_checklist_complete`, `api_spawalnia_create`, `api_qar_create` itd.)
- Upload zdjęć weryfikowany przez Pillow (`Image.verify()`), nie tylko po rozszerzeniu pliku
