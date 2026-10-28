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
- **Testy:** `.\venv\Scripts\python.exe -m pytest tests/ -v` - 511 testów w `tests/` (m.in. `test_app.py`, `test_kontrola.py`, `test_api_v1.py`, `test_briefing.py`, `test_dokumentacja.py`, `test_installer_stats.py`, `test_produkcja.py`, `test_produkcja_stanowisko.py`, `test_qar_employee.py`, `test_qar_from_checklist.py`, `test_prestiz_kj*.py` dla modułu KJ Prestiż), wszystkie muszą przejść (pełny zestaw trwa ok. 4 minuty). Testy w przeglądarce (Playwright) są pomijane bez Chromium. Współdzielone fixture'y są w `tests/conftest.py`.
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

- **Menu:** nazwa "Kontrola zamówień" zmieniona na **Kontrola etapowa** (adres `/kontrola` bez zmian). Kolejność domyślna panelu admina i nawigacji kontrolera: Dashboard, Kontrola etapowa, Kontrola Końcowa, (Nowa kontrola, Zlecenia QA kontrolera), Raporty QAR, Lista produkcyjna, Stanowisko, Ustawienia produkcji, Szablony, Dokumentacja, KJ Prestiż (pogląd, od 2026-10-28, tylko Administrator i Superuser). Sekcja Zarządzanie (Użytkownicy, Statystyki, Dziennik) bez zmian.
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

## Moduł "KJ Prestiż (pogląd)" (`/kj-prestiz`)

Dodany 2026-10-28 (plan: `PLAN_MODUL_KJ_PRESTIZ.md` i `PLAN_MODUL_KJ_PRESTIZ_REALIZACJA.md`, realizacja w 8 etapach). **Moduł poglądowy** do prezentacji Zarządowi i firmie Streamsoft docelowego procesu kontroli jakości (Prestiż plus aplikacja Android). Działa na danych demo, **nie łączy się z Prestiżem** i **nie zmienia istniejących modułów ani ich tabel**. Po prezentacji wystarczy odebrać moduł rolom w macierzy (menu znika, adres daje 403) albo usunąć pakiet i tabele `pkj_*`.

- **Kod:** pakiet `prestiz_kj/` (blueprint `prestiz_kj_bp`, prefiks `/kj-prestiz`), szablony `templates/prestiz_kj/` (aplikacja KJ w `m/`), `static/css/pkj.css`, `static/js/pkj-mobile.js` i `pkj-reports.js`. Modele w `prestiz_kj/models.py` (tabele `pkj_*`, tworzy je `db.create_all()`, bez `ALTER`). Poza pakietem zmienione tylko wpisy rejestrujące: `models.MODULES` (`kj_prestiz`), `app.py` (blueprint, `NAV_DEFAULT`, `MODULE_HOME`, `_BLUEPRINT_MODULES`, `makedirs` folderu zdjęć), `config.py` (`PKJ_UPLOAD_FOLDER`), `templates/base.html`, `.gitignore` (`static/pkj_uploads/`).
- **Dostęp:** moduł `kj_prestiz` domyślnie tylko Administrator i Superuser (Kontroler nie ma). Dekoratory w `prestiz_kj/access.py`: `view_required`, `controller_required` (aplikacja KJ, meldunki), `admin_required` (konfiguracja, tworzenie i usuwanie zleceń, resety, zatwierdzanie zawrotów).
- **Moduły kodu:** `orders.py` (trasa zlecenia, meldunek, statusy, usuwanie), `inspections.py` (start kontroli z kopią punktów, ocena, zakończenie, przerwanie, kontrola sprawdzająca, "Pozostałe punkty: OK"), `repairs.py` (zawrót, ZN, zatwierdzanie, zamknięcie), `reports_data.py` (jedno źródło danych ekranu, Excela i karty), `xlsx.py`, `card_pdf.py`, `pdf.py` (etykieta QR), `scenario.py`, `demo_data.py` i `demo_history.py` (dane demo), `clock.py` (zegar do generowania danych wstecz), `service.py` (ustawienia, resety).
- **Konfiguracja (admin, `/kj-prestiz/konfiguracja/...`):** operacje z flagą KJ (K1), materiały z flagą KJ i grupą (K2, K4), punkty o zakresie `all`, `operation`, `material`, `group` (K3, A4) z typem OK/NOK albo wartość (min, max, jednostka), grafiką i wymogiem zdjęcia przy NOK, przyczyny, pracownicy demo (fikcyjni, bez odczytu `DepartmentEmployee`), ustawienia. Każda strona ma jeden przycisk "Zapisz wszystko" (jak w Kontroli etapowej).
- **Reguły procesu:** każda operacja KJ dostaje wszystkie aktywne materiały z flagą KJ. Meldunek operacji KJ ustawia RW materiałów na `wstrzymane` (operacja bez KJ wydaje RW od razu). Następną operację można zameldować dopiero po zakończeniu poprzedniej. Wynik kontroli jest **per materiał**: OK wydaje RW, NOK zawraca materiał (`zawrocony`, RW wstrzymane), NOK w punkcie wspólnym zawraca wszystkie kontrolowane materiały, przerwanie kontroli (błąd wyklucza element) zawraca je wszystkie. Wynik punktu ma kopię treści z chwili kontroli (A5). Wartość poza zakresem nie może być OK.
- **Zawroty i ZN:** NOK wymaga zgłoszenia zawrotu (operacja docelowa z trasy, dodatkowe operacje ręcznie, przyczyna, opis, decyzja poprawka albo złom). Poprawka tworzy `ZN-<ZP>/<n>` z wybranych operacji (bez własnego KJ, bez RW), złom tworzy ZN na nowy element od pierwszej operacji do kontrolowanej włącznie i symuluje rozchód materiału. Po zameldowaniu ostatniej operacji ZN materiał dostaje `do_ponownej_kontroli`, a kontrola sprawdzająca (`round` 2 i więcej, `previous_id`) pokazuje tylko punkty, które były NOK. OK zamyka zawrót, zamyka ZN (`zamkniete`) i wydaje RW, NOK zamyka ten zawrót i wymaga następnego. Ustawienie `approval_mode=manager` wstrzymuje ZN do zatwierdzenia w rejestrze zawrotów.
- **Aplikacja KJ (`/kj-prestiz/m/`):** samodzielne strony mobilne (bez menu profit), skan QR przez wspólny skaner list (`list-search.js`), autozapis każdej oceny przez JSON, zdjęcia z aparatu, duże przyciski. Na komputerze w ramce telefonu (`/kj-prestiz/aplikacja`, iframe 390 px, żeby zapytania `@media` widziały szerokość telefonu). Etykieta QR zlecenia (`.../zlecenia/<id>/etykieta.pdf`) ma te same pola `p`,`c`,`o`,`q` co obecne etykiety.
- **Raporty (`/kj-prestiz/raporty`):** rejestr niezgodności, FPY (wg operacji, działu, tygodnia, materiału), Pareto (punkt, przyczyna, materiał, operacja, jedna oś 0 do 100%), koszt zawrotów, wyniki wg pracownika, czas kontroli. Filtry: okres (wg startu kontroli), klient, produkt, dział wykonawcy. Excel (`.../xlsx`) i karta kontroli zlecenia w PDF. Założenia kosztów w ustawieniach: `repair_hour_cost`, `repair_op_hours`, `scrap_material_cost`. Wykresy Chart.js 4.4.4 (jak statystyki), pod każdym tabela z danymi.
- **Dane demo:** "Wczytaj dane demo" zakłada konfigurację, zlecenia `ZP-DEMO-001` do `004` i 11 zleceń historycznych `ZP-DEMO-101` do `111` z 4 tygodni (`demo_history.py`: deterministyczny generator, `random.Random(2026)`, te same funkcje co ekrany przy czasie ustawionym wstecz przez `clock`, godziny pracy w dni robocze, zdjęcia przykładowe jako osobne pliki w `static/pkj_uploads/`). `ZP-DEMO-001` służy do pokazu na żywo, "Przywróć scenariusz pokazu" odtwarza tylko je. "Resetuj zlecenia" i "Resetuj wszystko" kasują wyłącznie tabele `pkj_*` i pliki zdjęć.
- **Tryb prezentacji:** strona Scenariusz (7 kroków z sekcji 10 planu, stan liczony z danych), strona Pytania do Streamsoft, pasek nawigacji modułu w `_demo_bar.html`. Lista zleceń domyślnie pokazuje tylko ZP (filtr rodzaju: produkcyjne, naprawcze, wszystkie).
- **Wdrożenie:** nowych zależności brak (Pillow, ReportLab, openpyxl już są), tabele powstają przy restarcie (`init_db`). Po wdrożeniu superuser nadaje moduł rolom w `/admin/users/uprawnienia` (domyślnie tylko Administrator i Superuser), a admin klika "Wczytaj dane demo".
- **Poza zakresem:** prawdziwa integracja z Prestiżem, natywna aplikacja Android, automatyczny dobór punktów z plików konstrukcyjnych (D2), praca offline.
- Testy: `tests/test_prestiz_kj.py` (rejestracja, uprawnienia, izolacja, konfiguracja), `test_prestiz_kj_orders.py`, `test_prestiz_kj_mobile.py`, `test_prestiz_kj_returns.py`, `test_prestiz_kj_history.py`, `test_prestiz_kj_reports.py`, `test_prestiz_kj_scenario.py` oraz test w przeglądarce `test_prestiz_kj_browser.py` (`_browser_kj.py`, pełny scenariusz pokazu na telefonie, pomijany bez Chromium).

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
- **Zakres dat i widok (2026-10-26):** import wczytuje cały plik, a widok arkusza pokazuje wiersze z terminem realizacji (a bez niego datą zamówienia, `ProdRow.row_date`, kolumna dodana w `_migrate_schema` z jednorazowym uzupełnieniem znacznikiem `row_date_backfilled`) od 14 dni wstecz do 14 dni naprzód. Zakres zmienia się w widoku (pola Termin od/do, parametry `od` i `do`, puste = bez granicy, "Wszystkie daty"), wiersze bez daty widać tylko bez zakresu, arkusze bez kolumn dat (REKLAMACJE, BRAKI) nie są filtrowane. Nowe wiersze w eksporcie trafiają za koniec arkusza w pliku. Każdy użytkownik ma własny widok kolumn (`ProdUserView`, ukrywanie kliknięciem w nagłówek, także Wiersz i Listy QA), puste kolumny są ukrywane automatycznie (poza etapami, polami zlecenia i kolumnami powiązanymi z kontrolami, przełącznik `?puste=1`).
- **Powiązanie z kontrolami (`produkcja/controllink.py`):** kolumny etapów mają powiązanie z działami i elementami kontroli (strona "Kolumny i etapy", zapis w `sheetcfg` pod `controls`, bez zapisu działają podpowiedzi z nazw kolumn: GIĘCIE OB. = Gięcie + Korpus, G/D, Boki; DRZWI i SPAW DRZW. = Spawanie + Zgrzewanie + Drzwi itd.). W komórce etapu kwadrat pokazuje stan kontroli etapowej (żółty utworzona, zielony zakończona, czerwony NG, licznik zakończone/wszystkie), w kolumnie KJ stan list Kontroli Końcowej. Zlecenie łączone po ZO, produkt porównywany po wymiarach (`same_product`, bo ERP zapisuje `PSH-600-800-300 IP65`, a lista `PSH 600X800X300 (P)`). Filtry listy: `typ` (etapowa/koncowa) i `kontrola` (brak/otwarta/zakonczona/ng).
- **Układ wzorcowy (`produkcja/layout.py`, 2026-10-27):** widok każdego arkusza pokazuje domyślnie tylko kolumny z `CANON` oznaczone jako widoczne: DATA, NAZWA TOW., NAZWA ODBIORCY, NUMER ZO, GIĘCIE OB., GIĘCIE DR, SPAW. OB., SPAW DRZW., MASKI, ELEMENTY, CZYSZ. OB., CZYSZ. DRZW., MYJNIA, MAL, Drzwi.ZAW., Drzwi. PRZYW., SKŁADANIE, KONTROLA KOŃCOWA, PAKOWANIE (w tej kolejności i z tymi nazwami). Kolumny arkusza dopasowują się po nazwie (np. SPAW./GRZ/SP = SPAW. OB., DRZWI/DRZW. = SPAW DRZW., ZŁOŻONE/SKŁAD. = SKŁADANIE, KJ = KONTROLA KOŃCOWA, ROZP. = DATA), pozostałe (ZAM, CZAS, TERMIN, ILOŚĆ, Z, KOLOR, StreamSoft, WYPALANIE, LASER, SZYKOWANIE i kolumny bez wzorca) są domyślnie ukryte, a użytkownik włącza je w menu "Kolumny". ELEMENTY to kolumna wirtualna (klucz `el`, tylko do odczytu) z elementami kontroli etapowej zlecenia (`control_info()['elements']`). Nazwy wzorca nadaje import i `layout.sync_names()` przy otwarciu arkusza (nie nadpisuje nazw wpisanych przez użytkownika, `LEGACY_NAMES` to nazwy dawnych wersji układu). PROFIT nie ma nagłówków, więc jego etapy wynikają z pozycji (`HEADERLESS_STAGES`, jak w RZ RW IF). Układ można wyłączyć dla arkusza ("Wyłącz układ", `/produkcja/sheet/<id>/uklad`).
- **Własny widok użytkownika (`ProdUserView`, `sheetcfg.get_prefs/save_prefs`):** ukryte kolumny, kolumny pokazane mimo domyślnego ukrycia, kolejność (klucze = oryginalne nagłówki, `@el` dla ELEMENTY), szerokości (`widths_json`). Kolejność zmienia się przeciągnięciem nagłówka albo przyciskami w okienku nagłówka (`POST /produkcja/sheet/<id>/kolumna/kolejnosc`), szerokość przeciągnięciem krawędzi (dwuklik: automatyczna), nazwę (wspólną) i ukrycie kliknięciem w nagłówek. Wiersz i Listy QA są na stałe z lewej. Lista wyboru arkusza z liczbą zleceń i strzałkami jest w nagłówku strony arkusza (zachowuje zakres dat), ostatnio otwarty arkusz zapamiętuje `ProdSetting` `last_sheet:<id użytkownika>`, a wejście z menu otwiera go od razu (lista arkuszy: `/produkcja/?lista=1`). Test w prawdziwej przeglądarce: `tests/test_produkcja_browser.py` (Playwright, pomijany bez Chromium).
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
