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
- **Standard wyglądu list:** `STANDARD_LIST.md`. Każda nowa lista i każda zmiana wyglądu listy (pulpit, Kontrola Końcowa, Kontrola etapowa i kolejne) ma trzymać się tego standardu (znaczniki `.rs*`, bez powielania statusu), a zmianę trzeba dopisać do sekcji "Historia zmian" w tym pliku.
- **Testy:** `.\venv\Scripts\python.exe -m pytest tests/ -v` - 303 testy w `tests/` (m.in. `test_app.py`, `test_kontrola.py`, `test_api_v1.py`, `test_briefing.py`, `test_dokumentacja.py`, `test_installer_stats.py`, `test_marszruta.py`, `test_produkcja.py`, `test_produkcja_stanowisko.py`, `test_qar_employee.py`, `test_qar_from_checklist.py`, `test_zadania_qa.py`), wszystkie muszą przejść. Współdzielone fixture'y są w `tests/conftest.py`.
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

## Usunięte moduły i "Ustawienia produkcji" (`/ustawienia-produkcji`)

Zmiana 2026-10-16. Moduły "Kontrola międzyetapowa" (Kontrola etapów, `/spawalnia`) i karty "Marszruty produkcji" (`/marszruta`) zostały usunięte, zastąpiła je Kontrola zamówień. Ich tabele (`spawalnia_records`, `interstage_checks`, `spawalnia_operators`, `giecie_operators`, `ciecie_operators`, `routing_cards`, `routing_card_stages`, `routing_card_photos`) kasuje `_migrate_schema()` przy starcie (nieodwracalnie), konta z rolą `spawacz` przechodzą na `operator`, API `/api/v1/spawalnia` nie istnieje. Zdjęcia kart zostały w `static/marszruta_uploads/` na serwerze (do ręcznego usunięcia).

Z Marszruty zostały dwie części, przeniesione do pakietu `ustawienia/` (od 2026-10-23 zostały tylko Działy i pracownicy: zakładki Ścieżki produkcyjne, Szablony produktów oraz Etapy i bufory wraz z punktami kontrolnymi etapów i etykietami buforów zostały wyłączone jako nieużywane, kod do odzyskania z historii gita, a tabele `production_stages`, `machines`, `buffers`, `routing_templates`, `product_templates` i pokrewne zostają w bazie nietknięte) (blueprint `ustawienia_bp`, szablony `templates/ustawienia/`, dostęp admin + kontroler):
- **Działy i pracownicy** (`ProductionDepartment`, `DepartmentEmployee`): używane też w QAR (osoba odpowiedzialna), Liście produkcyjnej (działy pracowników) i Porannym Briefingu. Pracownika z raportami QAR nie da się usunąć, tylko dezaktywować.
- **Ścieżki produkcyjne** (`RoutingTemplate`, `RoutingTemplateStage`): edytowalne, ale na razie nigdzie nieużywane po usunięciu kart.
- Testy: `tests/test_ustawienia.py` (w tym test migracji usuwającej stare tabele).

### Etapy i bufory (etap 1 planu śledzenia produkcji, `PLAN_SLEDZENIA_PRODUKCJI.md`)

Dodane 2026-10-18 w `ustawienia/` (strona "Etapy i bufory", zakładki Ustawień produkcji). Plan całego modułu (zlecenia, elementy, wpisy na bufory, kontrole etapów) jest w `PLAN_SLEDZENIA_PRODUKCJI.md`, sprawdź go przed kolejnymi etapami.

- **Modele:** `ProductionStage` (nazwa, kolejność, poziom `element`/`produkt`, `control_required`, jeden dział), `Machine` (maszyny etapu), `Buffer` (kod, nazwa, numer, etap opcjonalny). `DepartmentEmployee.token` (plakietka QR pracownika działu).
- **Dane startowe:** `_seed_stages()` w `app.py` zakłada jednorazowo 12 etapów, 4 maszyny wycinania i brakujące działy (Wycinanie, Przygotowanie, Kompletacja, Pakowanie); gdy etapy istnieją, nic nie robi. `_backfill_department_tokens()` nadaje tokeny istniejącym pracownikom. Kolumna `token` dodawana w `_migrate_schema()`.
- **Kod bufora:** QR i Code128 na etykiecie zawierają sam kod (np. `0001`, `A001`, bez przedrostka), normalizacja bez spacji i wielkimi literami (`ustawienia/lookup.py`, `find_buffer`). Istnieje 60 gotowych etykiet 50x100 mm (`0001`-`0010`, `A001`-`E010`) w `D:\Dane\Desktop\KODY QR POLA`, wczytuje się je zakresem kodów. Generator nowych etykiet (`/bufory/etykiety.pdf`) robi ten sam układ 50x100 mm.
- **Plakietki pracowników działów:** QR `PSH-DEP:<token>` (inny przedrostek niż `PSH-EMP:` z Listy produkcyjnej), PDF pojedynczy, per dział i zbiorczy, "Nowy kod" unieważnia plakietkę. `ustawienia/people.py` zawiera też przenoszenie pracownika do innego działu.
- Testy: `tests/test_ustawienia_etapy.py`.

### Usunięty moduł śledzenia produkcji (2026-10-21)

Moduł "Kontrola produkcji" (`/kontrolaprodukcji`, skrót `/kp`: zlecenia ze skanu QR, wózki z ilościami, przejęcia etapów, składanie, pakowanie, kontrole etapów, decyzje NG, złom, kontrola doraźna, stanowisko) został w całości usunięty jako zbyt skomplikowany do użycia na telefonie (etapy 2 do 5b, wdrożone 2026-10-18 do 2026-10-20, ostatni commit z modułem `caafc08`, kod do odzyskania z historii gita). Tabele `work_orders`, `work_order_components`, `work_order_component_stages`, `work_order_controls`, `stock_positions`, `work_in_progress`, `movements`, `stage_controls`, `stage_control_results`, `scrap_records` są kasowane przy starcie (`init_db`, `_migrate_schema`), a alerty "Do kontroli: ZO..." usuwane.

Zostają: etapy, maszyny, działy, pracownicy z plakietkami, bufory (kody i etykiety), punkty kontrolne etapów (`StageControlPoint`, strony `ustawienia/control_points.py`), szablony produktów (`ProductTemplate` z elementami i trasą, `ProductTemplateControl`), Kontrola Końcowa (listy kontrolne, szablony, zamówienia), Kontrola zamówień `/kontrola` i Lista produkcyjna `/produkcja`. Rola `produkcja` po logowaniu trafia na skanowanie QR Listy produkcyjnej.

Nowy, prostszy moduł (szkic): skan QR zlecenia albo ręczne utworzenie jak w Kontroli Końcowej, wybór szablonu kontrolnego, opcjonalny skan bufora i opcjonalne oznaczenie etapu zlecenia, kontrole etapów z punktami OK/NG i QAR, sztuki zgodne i niezgodne bez wózków. Szczegóły do ustalenia w `PLAN_SLEDZENIA_PRODUKCJI.md`.

## Menu i usunięte moduły (2026-10-21)

- **Menu:** nazwa "Kontrola zamówień" zmieniona na **Kontrola etapowa** (adres `/kontrola` bez zmian). Kolejność domyślna panelu admina i nawigacji kontrolera: Dashboard, Kontrola etapowa, Kontrola Końcowa, (Nowa kontrola, Zlecenia QA kontrolera), Raporty QAR, Lista produkcyjna, Stanowisko, Ustawienia produkcji, Szablony, Dokumentacja. Sekcja Zarządzanie (Użytkownicy, Statystyki, Dziennik) bez zmian.
- **Przestawianie przeciąganiem** (admin i kontroler): przycisk "Zmień kolejność menu", przeciąganie myszą i dotykiem (pointer events, `static/js/nav-sort.js`), "Zapisz", "Domyślne", "Anuluj". Kolejność zapisuje się na koncie (`User.menu_order`, JSON z kluczami `NAV_DEFAULT` w `app.py`, `POST /menu/kolejnosc`), wizualnie działa przez CSS `order` (`menu_pos(klucz)` w szablonie). Inne role nie mają przestawiania.
- **Usunięte:** moduł **Zadania QA** (pakiet `zadania_qa`, szablony, `QATask`, tabela `qa_tasks` kasowana przy starcie, testy). **Kosztorysy** i sekcja "Kosztorysy - config" (Ceny materiałów, Stawki robocizny, Typy szaf, Katalog produktów) są ukryte w menu wszystkich ról, strony i dane zostają dostępne po adresie. Moduł **Zamówienia** (strony `/orders`, Import CSV, model `Order`, tabela `orders`, `Report.order_id`, `Alert.order_id`, `/api/v1/orders`, alerty o postępie zamówienia, kafle zamówień na Dashboardzie) oraz role **Monter** (pula i listy montażowe, szablony typu monter) i **Zamawiający** (`order`) zostały usunięte w całości. Przy starcie (`_drop_orders_and_monter` w `init_db`) konta o tych rolach dostają rolę `operator` (odczyt), kasowane są szablony i listy montażowe, kolumny `order_id` (tam gdzie SQLite pozwala) i tabela `orders`. `prod_rows.order_id` zastąpiło `ProdRow.checklists_at` (znacznik utworzenia list z Listy produkcyjnej). Model `Installer` (montażysta wybierany w punktach typu `installer`) i statystyka "Błędy NG wg montera" zostają. `tools/bulk_checklist_entry.py` korzysta z usuniętego `/api/v1/orders` i wymaga przeróbki (odłożone). Testy: `tests/test_removed_modules.py`.
- Testy: `tests/test_menu.py`.

---

## Role i uprawnienia (od 2026-10-23)

- **Role:** Superuser (flaga `User.is_superadmin`, jedno konto Marek, rola w bazie `administrator`), Administrator, Operator, Konstruktor, Kontroler, Technolog (`models.ROLES`). Stare nazwy (`admin`, `planowanie`, `produkcja`, `order`) migrują przy starcie (`_migrate_roles`) i są rozpoznawane przez `LEGACY_ROLES`.
- **Macierz rola x moduł:** tabela `role_permissions`, edytuje ją tylko superuser w `/admin/users/uprawnienia`. Moduły w `models.MODULES`, domyślne uprawnienia w `DEFAULT_PERMISSIONS` (odpowiadają dawnemu dostępowi, Technolog ma tylko Dashboard, Operator Kontrolę etapową i Stanowisko). Superuser ma zawsze wszystko.
- **Egzekwowanie:** `before_request` `_enforce_module_access` mapuje endpointy na moduły (`_module_for_endpoint`), brak dostępu to 403. Menu (jeden wspólny sidebar dla wszystkich ról) pokazuje pozycje wg `can('moduł')`. Dashboard bez dostępu przekierowuje na pierwszy dozwolony moduł (`_first_allowed_url`).
- **Akcje wewnątrz modułów** (usuwanie, edycja cudzych rekordów) nadal wynikają z `is_admin`, `is_kontroler`, `is_konstruktor` (modele `User`). `is_admin` to Administrator i Superuser.
- Nowy moduł trzeba dopisać do `MODULES`, `DEFAULT_PERMISSIONS`, mapowania endpointów i menu w `base.html`.
- Testy: `tests/test_roles.py`.

---

## Moduł "Kontrola zamówień" (`/kontrola`)

Dodany 2026-10-14. Kontrola zamówienia po stałych, edytowalnych punktach (zamiast szablonów jak w "Raportach QA"). Kontroler skanuje ten sam kod QR co kontrola końcowa (pola `p`,`c`,`o`,`q`, wymagane `o`), ocenia punkty OK/NG i kończy kontrolę. Zakończone zamówienia widzi rola **Operator** (linia produkcyjna).

- **Kod:** pakiet `kontrola/` (blueprint `kontrola_bp`), szablony w `templates/kontrola/`. Modele (models.py): `ControlPoint`, `ControlRecord`, `ControlResult`. Tabele tworzy `db.create_all()`, bez `ALTER`.
- **Punkty:** jedna wspólna lista, edytuje ją tylko admin w `/kontrola/points` (dodaj, edytuj, kolejność, aktywny, usuń). Wynik ma kopię tytułu punktu, więc edycja listy nie zmienia historii. Bez punktów nie da się rozpocząć kontroli (lista startuje pusta, admin musi ją uzupełnić).
- **Cykl:** kontrola wstępna (`round=1`) -> koniec kontroli (wymaga oceny wszystkich punktów) -> operator klika "Wykonane" -> powstaje kontrola sprawdzająca (`round=2`, `previous_id`) przypisana do tego samego kontrolera. Stany (`ControlRecord.state`): `in_control`, `to_do`, `recheck`, `closed`. Kontrola jest identyfikowana parą ZO + produkt (`p`, porównanie bez wielkości liter i nadmiarowych spacji), bo pod jednym ZO bywa kilka produktów. Powtórny skan tej samej pary otwiera najnowszy etap zamiast tworzyć nowy, a inny produkt z tym samym ZO tworzy osobną kontrolę. Lista: tabela na komputerze (od 900 px), karty na telefonie.
- **NG:** każdy punkt NG tworzy raport QAR (raz, `qar_report_id`), bez blokowania zamówienia. Zakończenie kontroli wstępnej wysyła alert do operatorów, "Wykonane" wysyła alert do adminów i kontrolerów.
- **Role:** nowa rola `operator` (widok listy i "Wykonane", po zalogowaniu przekierowanie do `/kontrola/`). Skan i wypełnianie: admin + kontroler. Admin może wznowić zakończoną kontrolę (`/kontrola/<id>/reopen`), o ile operator nie oznaczył jeszcze wykonania.
- **Wykonawca pracy** (dodane 2026-10-21, plan w `PLAN_KONTROLA_WYKONAWCA.md`): `ControlRecord.employee_id` (opcjonalny pracownik z działów, dział wynika z pracownika). Kontroler i admin wskazują go na ekranie kontroli jedną listą pogrupowaną po działach (wspólny szablon `templates/qar/_employee_select.html`), zapis razem z ocenami. Nowo można wybrać tylko aktywnego pracownika aktywnego działu, zapisany wcześniej zostaje dozwolony. Kontrola sprawdzająca dziedziczy wykonawcę (`ControlRecord.performer` bierze najnowszego z cyklu). Raport QAR z NG dostaje `employee_id` i dopisek "Wykonawca: imię (dział)", a późniejsze wskazanie uzupełnia tylko puste pola w raportach tej kontroli. Lista kontroli ma kolumnę Wykonawca, filtr działu (`?dzial=`) i szukanie także po imieniu pracownika. Migracja: `ALTER TABLE control_records ADD COLUMN employee_id` w `_migrate_schema`.
- **Kontrole po działach** (dodane 2026-10-21, plan w `PLAN_KONTROLA_DZIALY.md`, wersja 2): `ControlPoint.department_id` (punkt działu albo ogólny, przypisuje admin w `/kontrola/points`, filtr `?dzial=`, `-1` to tylko ogólne), `ControlRecord.department_id` (kontrola działu) oraz tabela `control_orders` (`ControlOrder`: zlecenie z listy, para ZO + produkt) z `ControlRecord.control_order_id`. Dopóki żaden aktywny dział nie ma punktów (`_control_departments`), skan QR działa jak wcześniej (kontrola ogólna z cyklem). Gdy są działy z punktami, pierwszy skan dodaje zlecenie do listy ze statusem "Do skontrolowania" i wraca na listę (komunikat), a ponowny skan otwiera stronę zlecenia `/kontrola/zlecenie/<id>`. Napis przycisku na ekranie skanu ustala `POST /kontrola/qr-lookup`: "Wpis zlecenia na listę" przy pierwszym skanie, "Rozpocznij kontrolę" przy ponownym. Stamtąd "Nowa kontrola": wybór działu, potem punkty działu i ogólne (`_active_points`), ocena OK/NG, na końcu osoba odpowiedzialna (pracownicy tego działu, opcjonalnie, walidacja po stronie serwera) i "Zakończ kontrolę", która dopisuje wpis do historii zlecenia i wraca na jego stronę. "Ponowna kontrola" tego samego działu tworzy nową kontrolę, poprzednie zostają w historii (aktualna to najnowsza zakończona). Rozpoczęta, niezakończona kontrola działu jest otwierana zamiast tworzenia drugiej. Kontrola działu nie ma cyklu: po zakończeniu stan `closed`, brak "Wykonane" i sprawdzającej, brak alertu do operatora. Raport QAR z NG dostaje dopisek "Dział kontroli" i pracownika. Lista `/kontrola/` ma układ jak lista Kontroli Końcowej (`reports_list.html`): nagłówek, `filter-bar--rows` (szukanie `zo` po ZO, produkcie, kliencie i pracowniku, status `s`: w trakcie, zakończone, bez kontroli, do wykonania, do kontroli sprawdzającej, dział `dzial`, daty `date_from` i `date_to`, `per_page`), pasek zaznaczania admina (`bulk-bar`), tabela `data-table tbl-cards` (na telefonie karty) i paginacja. Zlecenia z listy i stare kontrole ogólne (bez zlecenia) są w jednej tabeli, kolumna Kontrole pokazuje badge działów z osobą i wynikiem. Akcje wiersza (Usuń, Wykonane) idą przez `data-post-url` poza formularzem zbiorczym. Operator widzi tylko zakończone kontrole (odczyt). Tworzenie: skan QR albo ręcznie (`/kontrola/zlecenie/nowe`, formularz na liście, kontroler i admin). Usuwanie (admin): zlecenie z kontrolami (`/kontrola/zlecenie/<id>/usun`), zbiorczo zaznaczone zlecenia (`/kontrola/zlecenie/usun-zaznaczone`) i pojedyncza kontrola z historii zlecenia (`/kontrola/<id>/delete`), raporty QAR zostają. Migracja: `ALTER` dwóch kolumn `department_id` i `control_order_id`, tabela `control_orders` przez `create_all`.
- **Elementy kontroli** (dodane 2026-10-21, plan w `PLAN_KONTROLA_ELEMENTY.md`): wspólna lista elementów (`ControlElement`, np. Drzwi, Korpus) zarządzana przez admina w `/kontrola/elementy` (dodaj, zmień nazwę, kolejność, aktywny, usuń; element z przypisanymi punktami nie da się usunąć). Punkt (`ControlPoint`) ma opcjonalny dział i opcjonalny element: bez działu i elementu trafia do każdej kontroli, z działem bez elementu do sekcji **Wspólne** tego działu, z elementem do sekcji tego elementu (w każdym dziale albo tylko we wskazanym). Na stronie zlecenia kontroler wybiera dział, zaznacza **opcjonalnie kilka elementów** i rozpoczyna kontrolę (`_add_department_results`): sekcja Wspólne, potem sekcja każdego elementu z osobno ocenianymi punktami (`ControlResult.element_id` i kopia `element_name`, `ControlRecordElement` z kopią nazwy). Ta sama kontrola w toku (dział i ten sam zestaw elementów) jest otwierana zamiast tworzenia drugiej, inny zestaw to nowa kontrola, "Ponowna kontrola" kopiuje zestaw. Elementy widać w historii zlecenia, na liście (badge "Dział (Elementy)", filtr `?element=`, szukanie po nazwie elementu) i w raporcie QAR przy NG ("Element: …"). Kontrola ogólna (bez działu) ignoruje punkty elementów. **Jeden zapis:** strony `/kontrola/points` i `/kontrola/elementy` mają jeden formularz i jeden przycisk "Zapisz wszystko" (`POST /kontrola/points/zapisz-wszystko`, `/kontrola/elementy/zapisz-wszystko`): pola `title_<id>`, `description_<id>`, `department_<id>`, `element_<id>`, `active_<id>` (punkty) i `name_<id>`, `active_<id>` (elementy) plus nowy wpis z pól `new_*` zapisują się razem. Przesuwanie i usuwanie to przyciski z `formaction` w tym samym formularzu, które najpierw zapisują pozostałe zmiany (`save_first`). Pusta nazwa pomija tylko ten wiersz, duplikat nazwy elementu jest odrzucany. Skrypt ostrzega o niezapisanych zmianach przy wyjściu ze strony. Stare trasy zapisu pojedynczego wiersza zostają. Testy: `tests/test_kontrola_zapis.py`. Migracja: `ALTER` trzech kolumn i tabele `control_elements`, `control_record_elements`.
- Testy: `tests/test_kontrola.py`, `tests/test_kontrola_dzialy.py`, `tests/test_kontrola_elementy.py`.

---

## Moduł "Lista produkcyjna" (`/produkcja`)

Wdrożony 2026-10-13. Wczytuje ręcznie plik Excel z listą produkcyjną (wszystkie niepuste arkusze, także ukryte), pozwala go przeglądać i edytować w aplikacji, dodawać i usuwać zlecenia, tworzyć z nich listy kontrolne i wpisywać pracowników w kolumny etapów skanem QR.

- **Kod:** pakiet `produkcja/` (blueprint `produkcja_bp`): `routes.py` (arkusze, edycja, import/eksport, listy kontrolne, dostęp), `excel_io.py` (odczyt/zapis xlsx), `entries.py` (wpis inicjałów), `staff.py` (pracownicy, plakietki PDF, kolumny etapów, historia), `scan.py` (stanowisko). Szablony w `templates/produkcja/`.
- **Modele (models.py):** `ProdFile`, `ProdSheet`, `ProdRow`, `ProdSheetAccess`, `ProdEmployee`, `ProdEmployeeDept`, `ProdStageColumn`, `ProdEntry`, `ProdSetting`. Tabele tworzy `db.create_all()`, bez `ALTER`.
- **Dane w bazie, nie w pliku:** wiersz to JSON z komórkami (`ProdRow.data`), formuły oryginału osobno (`formulas`), zmiany z aplikacji w `changed`. Pliki (import, kopie, backupy) w `instance/produkcja/` (poza `static`).
- **Eksport:** ładuje wczytany oryginał i nadpisuje tylko zmienione komórki, więc formuły (np. `WORKDAY` w kolumnie terminu), formatowanie i ukryte arkusze zostają. Usunięte zlecenia są czyszczone w tym samym wierszu (przesunięcie wierszy zepsułoby formuły), nowe dopisywane na końcu z przesuniętą formułą. Kolumny liczone formułą są w aplikacji tylko do odczytu. Ponowne wczytanie pliku robi automatyczny backup i zachowuje dostęp do arkuszy, powiązania z listami kontrolnymi i konfigurację etapów (po nazwie kolumny).
- **Role:** `planowanie` (planista: edycja planu, pracownicy, etapy, listy kontrolne, tylko przydzielone arkusze, przydział w `/produkcja/access` robi admin), `produkcja` (wspólne konto stanowiska, tylko `/produkcja/skan`), `admin` (wszystko, import pliku). Dekoratory `planning_required`, `admin_only`, `station_required`.
- **Listy kontrolne:** przycisk tworzy serię list przez `_create_checklist_series` (ilość z kolumny `qty`), ustawia `Report.qr_key` (produkt|klient|ZO), więc skan QR kontroli jakości znajduje te listy, a wiersz dostaje `checklists_at`. Bez pasującego szablonu QA listy nie powstają i nie powstaje żadne zamówienie (modułu zamówień już nie ma). Funkcje z `app.py` trafiają do modułu przez `register_helpers()` (nie `from app import`, bo przy `python app.py` załadowałoby to moduł drugi raz).
- **Pracownicy i skan:** pracownik nie ma loginu, tylko plakietkę QR (`PSH-EMP:<token>`, token losowy, "Nowy kod" unieważnia). Skan plakietki zapisuje pracownika w sesji urządzenia na 12 h, potem skan kodu QR zlecenia (te same pola `p`,`c`,`o`) i wybór etapu wpisuje inicjały. W komórce do 2 osób (`JK/AN`), trzecia i nietypowe wartości (`-`, data, liczba) wymagają potwierdzenia. Historia w `ProdEntry`. Przełącznik `enforce_assignments` (domyślnie wyłączony) ogranicza wpisy do działów pracownika.
- **Kolumny etapów:** wykrywane po nagłówkach (`STAGE_KEYWORDS` w `excel_io.py`), edytowalne w `/produkcja/sheet/<id>/etapy`. Arkusz `PROFIT` nie ma nagłówków, więc etapy trzeba tam zaznaczyć ręcznie.
- **Znane ograniczenia:** termin realizacji przeliczany w aplikacji bez świąt (święta liczy Excel po otwarciu kopii), import ok. 15 s i eksport ok. 25 s dla pliku 4 MB (ok. 40 tys. wierszy).
- Testy: `tests/test_produkcja.py`, `tests/test_produkcja_stanowisko.py`.

---

## Integracja ERP (Streamsoft) - `/api/v1`

Stan na 2026-07-01: `/api/v1/*` jest zabezpieczone i rozszerzone.

- Każdy request do `/api/v1/*` wymaga nagłówka `X-API-Key` zgodnego z `Config.API_KEY` (dekorator `api_key_required` w `app.py`). `/api/v1/*` jest wyłączone z sesyjnej ochrony CSRF (`csrf_protect()`), bo klucz API ją zastępuje.
- Endpointy: szablony (GET), listy kontrolne (GET lista/szczegóły, POST tworzenie, POST `/start`, POST `/complete` - odpowiedź zawiera `score`/`compliant`/`duration_seconds`), kosztorysy - ceny/stawki/wyceny (GET, tylko odczyt), QAR - raporty niezgodności (GET lista/szczegóły, POST tworzenie).
- Endpointy `/api/v1/orders` zostały usunięte razem z modułem Zamówienia (2026-10-21), integracja Streamsoft nie jest obecnie używana. Pola `order_number`/`order` w listach kontrolnych API zniknęły (`order_number` w POST jest ignorowane).
- Rozszerzenie o zbiorczy wpis list kontrolnych (patrz sekcja "Narzędzie: Zbiorczy wpis list kontrolnych"): `POST /api/v1/checklists` przyjmuje dodatkowo `quantity` (seria, `batch_id`/`batch_index`/`batch_total`), `operator`/`operator_id`, `completed`, `performed_at`, `template_name`, `installers` (przypisanie monterów do punktów typu `installer`). Nowe endpointy pomocnicze: `GET /api/v1/users`, `GET /api/v1/installers`.
- Testy: `tests/test_api_v1.py`.
- Znane, celowo odłożone braki: brak outbound webhooka do Streamsoft przy zmianie statusu, jeden wspólny klucz API (nie per-klient), brak rate limitingu na `/api/v1/*`.
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
