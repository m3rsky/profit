# Instrukcje dla Claude Code – projekt profit

Ten plik jest śledzony przez git i wypychany na GitHub (m3rsky/profit), żeby
praca nad projektem mogła być kontynuowana z dowolnego komputera z tym samym
kontekstem. Aktualizuj go, gdy pojawiają się nowe trwałe ustalenia dotyczące
sposobu pracy, architektury projektu lub deployu.

## Styl pracy

### 1. Rozpoznanie
Przed jakąkolwiek zmianą przeanalizuj odpowiednie pliki projektu.
Zrozum strukturę, zależności i istniejący kod.

### 2. Propozycje rozwiązania
Przedstaw wypunktowaną listę propozycji z krótkim opisem każdej.
Poczekaj na wybór użytkownika (chyba że zadanie jest jednoznaczne).

### 3. Po każdej zmianie
Wypisz:
- **Listę zmian** - co zostało zmienione i dlaczego
- **Listę zmienionych plików** - pełne ścieżki do każdego zmodyfikowanego pliku

### 4. Preferencje komunikacji
- Nie używać myślnika "—" (em dash) w żadnych tekstach dla użytkownika (czacie,
  dokumentacji, komentarzach do commitów). Zamiast tego przecinek, nawias albo
  osobne zdanie.

---

## Workflow commitów

Każda zmiana w kodzie przechodzi przez następujące kroki:

1. Wprowadź zmiany w kodzie
2. Uruchom testy lokalnie: `.\venv\Scripts\python.exe -m pytest tests/ -v`
3. Przedstaw wyniki testów i poczekaj na **akceptację użytkownika**
4. Po akceptacji: `git add` + `git commit`
5. `git push origin main`
6. Poczekaj na **potwierdzenie użytkownika**
7. Dopiero wtedy uznaj zadanie za zakończone

Nigdy nie commituj ani nie pushuj bez akceptacji użytkownika.

---

## Zakończenie pracy

Gdy użytkownik napisze **ZAKOŃCZENIE PRACY**, wygeneruj krótki komentarz (1-3 zdania) do ewidencji czasu pracy.

Komentarz ma:
- być po polsku
- opisywać konkretnie co było robione w tej sesji
- nadawać się do wklejenia w pole "Opis wykonanej pracy" w aplikacji ewidencji
- zaczynać się od czasownika (np. "Dodano", "Naprawiono", "Zaimplementowano", "Skonfigurowano")
- nie zawierać szczegółów technicznych - tylko zrozumiały opis dla pracodawcy

Przykład formatu:
> Dodano obsługę drugiego gatunku stali nierdzewnej INOX 316 w module wycen. Zaimplementowano widoczność selektora gatunku tylko dla szaf PSH INOX. Wdrożono zmiany na serwer produkcyjny.

---

## Informacje o projekcie

- **Stack:** Python 3.14 (lokalnie, `venv`), Flask 3.0.3, Flask-SQLAlchemy 3.1.1, Flask-Login, Flask-WTF, SQLite. Pełna lista w `requirements.txt`.
- **Testy:** `.\venv\Scripts\python.exe -m pytest tests/ -v` - 155 testów w `tests/` (m.in. `test_app.py`, `test_api_v1.py`, `test_briefing.py`, `test_dokumentacja.py`, `test_installer_stats.py`, `test_marszruta.py`, `test_qar_employee.py`, `test_qar_from_checklist.py`, `test_zadania_qa.py`), wszystkie muszą przejść. Współdzielone fixture'y są w `tests/conftest.py`.
- **Uruchomienie lokalne:** `.\venv\Scripts\python.exe app.py` → http://127.0.0.1:5000
- **Pre-commit hook:** automatycznie uruchamia testy przed każdym commitem
- **Migracje schematu:** wszystkie realizowane przez `_migrate_schema()` w `app.py` - bez Alembic. Nowe kolumny dodawane jako `ALTER TABLE ... ADD COLUMN ...`.
- **Dokumentacja techniczna w repo:** `API_STREAMSOFT.md`, `DOKUMENTACJA_SYSTEMU.md`, `KOSZTORYSY_DOKUMENTACJA.md`, `DESIGN.md`, `DOKUMENTACJA_KONTROLA_JAKOSCI.md`, `PROCEDURA_NIEZGODNOSCI_NG.md`, `tools/README_bulk_checklist_entry.md`.

---

## Moduł KOSZTORYSY - zrealizowane funkcje

### Materiały i ceny
- `DC01` - blacha z malowaniem
- `DX51` - blacha ocynkowana, bez malowania
- `INOX304` - stal nierdzewna 304, mnożnik robocizny ×1.4 (`inox_labor_factor`)
- `INOX316` - stal nierdzewna 316, mnożnik robocizny ×1.6 (`inox316_labor_factor`), cena 25 PLN/kg (`inox316`)
- Ceny materiałów i stawki robocizny: tabela `kosztorys_prices` (seeded przez `_seed_kosztorys()`)

### Wybór gatunku INOX
- Pole `inox_grade` w modelu `QuoteConfig` (`'304'` lub `'316'`, domyślnie `'304'`)
- Selektor 304/316 widoczny **tylko** gdy wybrany typ szafy zawiera "INOX" w nazwie
- Obsługa w: `new.html`, `edit.html`, `kreator.html` (JS show/hide po zmianie selecta)
- Migracja schematu: `ALTER TABLE quote_configs ADD COLUMN inox_grade VARCHAR(4) DEFAULT '304'`

### Osprzęt - ilości ręczne
Wcześniej hardkodowane ilości zastąpiono polami formularza. Pozycja pojawia się w wycenie tylko gdy qty > 0.

| Pole modelu     | Opis                        |
|-----------------|-----------------------------|
| `stud_m6_qty`   | Trzpień wstrzeliwany M6     |
| `nut_m8_qty`    | Nakrętka z podkładką M8     |
| `hinge_qty`     | Zawiasy                     |
| `screw_cap_qty` | Komplet śrub do kap         |
| `plug_qty`      | Zaślepki otworów            |

### Formuła zyskowności
```
zyskowność = (price_bonus - cost_total) / price_bonus * 100
```
gdzie `price_bonus` = cena po rabacie powiększona o bonus procentowy.

---

## Integracja ERP (Streamsoft) - `/api/v1`

Stan na 2026-07-01: `/api/v1/*` jest zabezpieczone i rozszerzone.

- Każdy request do `/api/v1/*` wymaga nagłówka `X-API-Key` zgodnego z `Config.API_KEY` (dekorator `api_key_required` w `app.py`). `/api/v1/*` jest wyłączone z sesyjnej ochrony CSRF (`csrf_protect()`), bo klucz API ją zastępuje.
- Endpointy: szablony (GET), zamówienia (GET lista/szczegóły, POST tworzenie - auto-dopasowuje szablon QA/monter tak jak UI), listy kontrolne (GET lista/szczegóły, POST tworzenie, POST `/start`, POST `/complete` - odpowiedź zawiera `score`/`compliant`/`duration_seconds`), kosztorysy - ceny/stawki/wyceny (GET, tylko odczyt), spawalnia po numerze ZO (GET/POST), QAR - raporty niezgodności (GET lista/szczegóły, POST tworzenie).
- `Order.external_number` to osobna kolumna (migrowana przez `_migrate_schema()`), filtrowalna: `GET /api/v1/orders?external_number=`.
- Rozszerzenie o zbiorczy wpis list kontrolnych (patrz sekcja "Narzędzie: Zbiorczy wpis list kontrolnych"): `POST /api/v1/checklists` przyjmuje dodatkowo `quantity` (seria, `batch_id`/`batch_index`/`batch_total`), `operator`/`operator_id`, `completed`, `performed_at`, `template_name`, `installers` (przypisanie monterów do punktów typu `installer`). Nowe endpointy pomocnicze: `GET /api/v1/users`, `GET /api/v1/installers`.
- Testy: `tests/test_api_v1.py`.
- Znane, celowo odłożone braki: brak outbound webhooka do Streamsoft przy zmianie statusu, jeden wspólny klucz API (nie per-klient), brak `PATCH /api/v1/orders/<id>`, brak rate limitingu na `/api/v1/*`.
- Pełna dokumentacja: `API_STREAMSOFT.md` oraz `DOKUMENTACJA_SYSTEMU.md` sekcja 4.3.

---

## Moduł "Poranny Briefing" (AI, admin-only)

Wdrożony 2026-08-03. Panel wewnątrz `/admin/stats`, gdzie admin wybiera zakres dat i generuje na żądanie analizę AI (Claude Sonnet 5, model id `claude-sonnet-5`) danych QAR (niezgodności) + list kontrolnych QC dla tego zakresu.

Kluczowe decyzje projektowe:
- V1 korzysta tylko z QAR + list kontrolnych QC (bez zamówień/spawalni/kosztorysów/alertów).
- Do Claude wysyłane są wyłącznie zagregowane dane (liczby, wskaźniki, czasy trwania) - pola tekstowe (opisy QAR, notatki checklist) nigdy nie są wysyłane, żeby uniknąć ryzyka PII w tekście dowolnym.
- Dane osobowe (User/DepartmentEmployee) są zastępowane pseudonimami per-run typu "Monter #3", budowanymi od nowa przy każdej generacji (nie stały globalny mapping).
- Generacja tylko na żądanie (przycisk), bez schedulera/crona.
- Model `DailyBriefing` przechowuje pełną historię (treść, zużycie tokenów, zanonimizowany payload do audytu) - kolumna `raw_aggregated_data` zawiera tylko wersję zanonimizowaną.

Implementacja: `briefing_service.py` (agregacja + anonimizacja + wywołanie Claude), route'y `POST /admin/briefing/generate` i `GET /admin/briefing/<id>` w `app.py`, panel w `templates/admin/stats.html`. Testy: `tests/test_briefing.py` (mockują Claude, brak realnych wywołań sieciowych w testach).

Wymaga `ANTHROPIC_API_KEY` w `.env` (lokalnie i na serwerze produkcyjnym) - bez niego moduł zwraca czytelny błąd "nieskonfigurowane" zamiast się wysypać.

---

## Narzędzie: Zbiorczy wpis list kontrolnych (`tools/bulk_checklist_entry.py`)

Desktopowy program (Python + tkinter, bez dodatkowych zależności) do masowego wpisywania serii list kontrolnych (np. 30 sztuk tego samego produktu dla klienta) do bazy profit, tak jakby kontrola faktycznie się odbyła (status `completed`, punkty OK, wybrana data/godzina). Łączy się przez REST `/api/v1` (klucz API), nigdy bezpośrednio z bazą - dzięki temu przechodzi tę samą walidację i trafia do `AuditLog`, i może działać z dowolnego komputera z dostępem do adresu serwera.

Stan na 2026-09-11 (wdrożone na produkcję):
- `POST /api/v1/checklists` rozszerzone o `quantity`, `operator`/`operator_id`, `completed`, `performed_at`, `template_name`.
- Pole `installers` (nazwa lub `{name/installer_id, role, is_at_fault}`) jest przypisywane do **każdego** punktu checklisty typu `installer` w **każdym** raporcie serii - samo zaznaczenie OK nie wystarcza dla punktów "kto montował".
- Nowe endpointy: `GET /api/v1/users`, `GET /api/v1/installers`.
- Konfiguracja (adres serwera + API_KEY) zapisywana lokalnie na komputerze użytkownika w `C:\Users\Marek Palczowski\.profit_bulk_checklist_config.json` (poza repo, nie w git, zawiera klucz API w czystym tekście - nie kopiować między komputerami bez potrzeby).
- Pełna dokumentacja: `tools/README_bulk_checklist_entry.md`.

Ten program jest w trakcie dalszej rozbudowy - przed kolejnymi zmianami sprawdź README i historię commitów gałęzi `feature/bulk-checklist-entry-api` i `feature/checklist-installer-assignment` (już zmergowane do `main`) dla kontekstu decyzji projektowych (np. dlaczego `installers` stosowane do WSZYSTKICH punktów typu `installer` w raporcie, nie per-punkt).

---

## Deploy na serwer

### Skrypt jednym kliknięciem
`D:\Dane\Desktop\deploy-profit.ps1` - wykonuje kolejno:
1. `git push` na GitHub (token PAT w zmiennej `$GH_TOKEN` - aktualizować gdy wygaśnie)
2. SSH `git pull origin main` na serwerze `host82388@host82388.iqhs.pl`
3. `touch ~/profit/tmp/restart.txt` - restart aplikacji Passenger

**Ważne:** ten skrypt NIE uruchamia `pip install`. Jeśli zmiana dodaje nową zależność do `requirements.txt`, trzeba ją doinstalować ręcznie w venv serwera PRZED restartem (patrz niżej) - inaczej restart wysypuje CAŁĄ aplikację (nie tylko nową funkcję), bo import w `app.py` się wywala.

### Dane serwera
- **Host:** `host82388.iqhs.pl`, użytkownik `host82388`
- **Panel:** własny panel hostingu "EVO" (`host82388.iqhs.pl:2222/evo/`), NIE standardowy cPanel - mimo że komponenty (CloudLinux, konwencje `.htaccess`) wyglądają jak cPanel. Brak przycisku do czyszczenia cache w "Funkcjach zaawansowanych".
- **Klucz SSH:** `C:\Users\Marek Palczowski\.ssh\profitsys_rsa`
- **Serwer aplikacji:** nginx + Phusion Passenger, Python 3.11
- **Venv aplikacji:** `~/virtualenv/profit/3.11` (Python 3.11.15), aktywacja: `source ~/virtualenv/profit/3.11/bin/activate`. Tu trzeba `pip install` każdą nową zależność przed restartem.
- **Restart:** `touch ~/profit/tmp/restart.txt`
- **Ścieżka projektu:** `~/profit`
- `wsgi.py` na serwerze wywołuje `init_db()`/`db.create_all()` przy każdym imporcie - nowe tabele (nowe `db.Model`) tworzą się same przy restarcie, nie trzeba ręcznej migracji przez SSH. Da się to samo zweryfikować z wyprzedzeniem: `cd ~/profit && source ~/virtualenv/profit/3.11/bin/activate && python -c "from app import app, init_db; init_db()"` - bezpieczne, `create_all()` nie rusza istniejących tabel, i łapie błędy importu nowej zależności zanim dotknie się `restart.txt`.

### Ręczny deploy (bez skryptu)
```powershell
git push https://<TOKEN>@github.com/m3rsky/profit.git main
ssh -i "C:\Users\Marek Palczowski\.ssh\profitsys_rsa" host82388@host82388.iqhs.pl "cd ~/profit && git pull origin main && touch tmp/restart.txt"
```

### Zmienne środowiskowe (`.env`, niecommitowany)

Plik `.env` w katalogu projektu (lokalnie i na serwerze) musi zawierać:
```
SECRET_KEY=...         # klucz sesji Flask
API_KEY=...            # klucz do autoryzacji /api/v1/* (integracje zewnętrzne, np. Streamsoft, narzędzie bulk_checklist_entry.py)
ANTHROPIC_API_KEY=...  # klucz do Claude API (moduł "Poranny Briefing", /admin/briefing/*)
```

- Bez `API_KEY` w `.env` na serwerze `Config.API_KEY` spada na wartość domyślną `change-this-api-key` - upewnij się, że serwer produkcyjny ma ustawiony własny, wygenerowany klucz.
- Bez `ANTHROPIC_API_KEY` moduł "Poranny Briefing" zwraca czytelny błąd zamiast 500, ale nie wygeneruje briefingu.
- **Preferuj prawdziwy plik `.env`** na serwerze nad panelem EVO/cPanel do ustawiania zmiennych środowiskowych - patrz pułapka niżej. Weryfikacja bez ujawniania wartości: `awk -F= '{print $1": len="length($2)}' ~/profit/.env`.

---

## Znane pułapki (deploy i infrastruktura serwera)

- **Panel EVO/cPanel "Setup Python App" do zmiennych środowiskowych jest niewiarygodny na tym serwerze.** Zapisuje `SetEnv` do `.htaccess`, ale realny stos to nginx + Passenger, a nginx nie czyta `.htaccess` w ogóle - zmienne wyglądają na "dodane" i nawet resolvują się poprawnie przy weryfikacji przez SSH (bo wrapper CloudLinuksa wstrzykuje je do interaktywnej sesji), ale nigdy nie docierają do działającej aplikacji. Zawsze zakładaj `.env` ręcznie zamiast polegać na tym panelu.
- **Duplikacja query stringa przez serwer WWW (rozwiązane, ale mechanizm zostaje na stałe jako zabezpieczenie).** Serwer potrafił duplikować cały query string w requeście (np. `?foo=bar` docierało do WSGI jako `foo=bar?foo=bar`), przez co pierwszy parametr parsował się poprawnie, a każdy kolejny dostawał doklejony ogon i psuł strict parsing (np. `datetime.strptime`), co przy wspólnym `try/except` dla wielu parametrów potrafiło zresetować cały filtr do wartości domyślnych. Naprawione dwuwarstwowo: (1) middleware w `wsgi.py` obcina `QUERY_STRING` na pierwszym `?` (naprawa główna, na poziomie całej aplikacji - zostaw ją na stałe), (2) `admin_stats()` w `app.py` dodatkowo waliduje `inst_date_from`/`inst_date_to` niezależnie od siebie. Jeśli pojawi się zgłoszenie w stylu "filtr/parametr w URL nic nie zmienia, zawsze pokazuje to samo" - to nie cache, tylko ewentualny nawrót tego mechanizmu; sprawdź najpierw czy middleware nadal jest na miejscu.
- **Nowa zależność w `requirements.txt` = wymagany ręczny `pip install` w `~/virtualenv/profit/3.11` przed restartem Passengera.** Inaczej pada cała aplikacja, nie tylko nowa funkcja.

---

## Bezpieczeństwo i pliki niecommitowane

- `.env` (sekrety), `instance/` (baza SQLite), `venv/`, `static/*_uploads/`, `.claude/` - w `.gitignore`, nigdy nie commitować.
- `C:\Users\Marek Palczowski\.profit_bulk_checklist_config.json` (klucz API narzędzia bulk_checklist_entry.py) - lokalny, poza repo.
- Ten plik (`CLAUDE.md`) **jest** commitowany i publikowany na GitHub celowo, żeby umożliwić pracę z innego komputera - nie wklejaj tu nigdy realnych wartości sekretów (kluczy API, tokenów, haseł), tylko nazwy zmiennych i ścieżki do miejsc, gdzie są przechowywane.
