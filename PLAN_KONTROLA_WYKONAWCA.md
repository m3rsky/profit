# Plan: wykonawca i dział w Kontroli zamówień (`/kontrola`)

Stan: plan zaakceptowany 2026-10-21. Zaimplementowane lokalnie (kroki 1 do 4), testy przechodzą, czeka na akceptację zmian przed commitem i wdrożeniem.

## 1. Cel

Podczas kontroli w module Kontrola zamówień (`https://host82388.iqhs.pl/kontrola/`), po ocenie punktów kontrolnych, kontroler oznacza **pracownika, który wykonał kontrolowaną pracę**, oraz **dział** tego pracownika. Informacja ma być widoczna na liście kontroli i trafiać do raportu QAR tworzonego przy NG.

## 2. Ustalenia (odpowiedzi użytkownika)

| Temat | Decyzja |
|---|---|
| Kogo dotyczy | Wykonawca kontrolowanej pracy (nie osoba kontrolująca). |
| Wybór | Jedna lista rozwijana pogrupowana po działach (dział jako nagłówek grupy, pracownik jako pozycja). Źródło danych: Ustawienia produkcji, `/ustawienia-produkcji/admin/departments` (działy i pracownicy). |
| Wymóg | Opcjonalne, w obu rundach. Kontrolę można zakończyć bez oznaczenia. |
| Widoczność | Lista kontroli i raport QAR tworzony przy NG. |
| Filtr | Na liście kontroli filtr działu i wyszukiwanie po pracowniku. |
| Moduł | Rozwijamy obecny `/kontrola`. Nie budujemy teraz osobnego modułu "Kontrola zleceń", a plan jego powstania i zastąpienia Kontroli zamówień jest wstrzymany. |

## 3. Stan obecny (rozpoznanie)

- Moduł: `kontrola/routes.py`, szablony `templates/kontrola/detail.html`, `list.html`. Modele: `ControlRecord` (runda 1 wstępna i runda 2 sprawdzająca), `ControlResult` (punkty OK/NG).
- Raport QAR przy NG powstaje w `_create_qar_for_result` (`kontrola/routes.py`). `QARReport` ma już `employee_id` (pracownik z działu), więc dział wynika z pracownika (`employee.department`).
- Wybór pracownika grupowany po działach istnieje w module QAR: `templates/qar/_employee_select.html` i helpery `_employee_departments`, `_parse_employee_id` w `qar/routes.py`. Można go użyć ponownie.
- Pracownicy mają aktywność i dział (`DepartmentEmployee`, `ProductionDepartment`), a ustawienia produkcji pozwalają je edytować.

## 4. Propozycja rozwiązania

### 4.1 Dane
- Nowa kolumna `ControlRecord.employee_id` (FK do `department_employees`, nullable). Dział nie jest osobną kolumną, bierze się z `employee.department`.
- Migracja: `ALTER TABLE control_records ADD COLUMN employee_id INTEGER REFERENCES department_employees(id)` w `_migrate_schema`, bez usuwania danych. Istniejące kontrole mają pustą wartość.
- Kontrola sprawdzająca (runda 2) kopiuje wykonawcę z kontroli wstępnej jako domyślną wartość, kontroler może ją zmienić.

### 4.2 Ekran kontroli (telefon)
- Pod punktami kontrolnymi, nad przyciskami „Zapisz” i „Zakończ kontrolę”, sekcja **„Kto wykonał pracę”**.
- Jedna lista rozwijana pogrupowana po działach (dział jako nagłówek grupy, pracownik jako pozycja), jak w QAR (`templates/qar/_employee_select.html`), z opcją „nie wskazano”. Wybór pracownika ustawia dział. Listę zasilają działy i pracownicy z Ustawień produkcji.
- Zapisuje się razem z ocenami (przycisk „Zapisz” i „Zakończ kontrolę”), pole zablokowane po zakończeniu kontroli (admin może wznowić kontrolę i zmienić).
- Nieaktywny pracownik już zapisany pozostaje widoczny w liście (jak w QAR), nowych nieaktywnych nie można wybrać.

### 4.3 Lista kontroli
- Nowa kolumna „Wykonawca” (imię i dział pod spodem), na telefonie w karcie zamówienia.
- Filtr działu (lista rozwijana wszystkich aktywnych działów, parametr `dzial`) i wyszukiwanie po imieniu pracownika (rozszerzenie istniejącego pola szukania `zo`, które dziś szuka po ZO, produkcie i kliencie). Filtr łączy się z istniejącymi filtrami stanu.

### 4.4 Raport QAR przy NG
- `_create_qar_for_result` ustawia `employee_id` raportu na wykonawcę z kontroli, jeśli jest wskazany. Opis QAR zawiera dopisek „Wykonawca: imię (dział)”.
- Gdy wykonawcę oznaczono dopiero po utworzeniu raportu (np. po ocenie NG i późniejszym wskazaniu), zapis ze strony kontroli uzupełnia `employee_id` w raportach QAR tej kontroli, które go jeszcze nie mają. Raportów z już ustawionym pracownikiem nie nadpisuje.

### 4.5 Uprawnienia
- Wskazuje kontroler i admin (jak ocena punktów). Operator i pozostałe role widzą wykonawcę tylko do odczytu.

## 5. Zakres testów (`tests/test_kontrola.py`)
- Zapis wykonawcy przy „Zapisz” i „Zakończ”, pusty wykonawca dozwolony.
- Kopiowanie wykonawcy do kontroli sprawdzającej.
- Wykonawca w QAR przy NG (od razu i uzupełnienie po zapisie), brak nadpisania istniejącego.
- Lista pokazuje wykonawcę i dział, filtr działu, wyszukiwanie po pracowniku, odczyt dla operatora, brak edycji dla operatora.
- Nieaktywny pracownik: niewybieralny nowy, zapisany pozostaje.
- Migracja kolumny na bazie bez niej.

## 6. Kroki realizacji (po akceptacji planu)
1. Model i migracja `employee_id`, formularz na ekranie kontroli, zapis (S).
2. Kopiowanie do rundy 2, kolumna na liście, QAR (S).
3. Filtr działu i wyszukiwanie pracownika na liście (S).
4. Testy, dokumentacja w `CLAUDE.md`, lista zmian i plików, akceptacja, backup bazy, commit, push, wdrożenie (jak zawsze po osobnej akceptacji).

## 7. Pytania otwarte
- Brak. Rozstrzygnięte 2026-10-21: jedna lista pogrupowana po działach, filtr działu i wyszukiwanie pracownika na liście, rozwijamy obecny moduł.

## 8. Wpływ na inne plany
- Dawny plan nowego modułu "Kontrola zleceń" (w `PLAN_SLEDZENIA_PRODUKCJI.md`, sekcja "Nowy moduł") jest wstrzymany: zamiast zastępować Kontrolę zamówień rozwijamy ją. Kontrola Końcowa (listy, szablony, zamówienia) nadal bez żadnych zmian.
