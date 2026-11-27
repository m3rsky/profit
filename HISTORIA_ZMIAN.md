# Historia zmian profit

Opisy usuniętych i wyłączonych funkcji, przeniesione z CLAUDE.md w oryginalnym brzmieniu (2026-11-27).
To zapis historyczny: część stwierdzeń nie jest już aktualna. Stan obecny opisuje CLAUDE.md.

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

