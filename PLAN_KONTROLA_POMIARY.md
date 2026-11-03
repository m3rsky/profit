# Plan: pomiar z miernika w punktach Kontroli etapowej (`/kontrola`)

Stan: plan zaakceptowany 2026-11-03. Zaimplementowane lokalnie, testy przechodzą, czeka na akceptację zmian przed commitem i wdrożeniem. Kontrola Końcowa nie jest zmieniana.

## 1. Cel

Punkt kontrolny w Kontroli etapowej może być **pomiarem**: kontroler wpisuje wartość odczytaną z miernika, a system porównuje ją z ustalonym minimum i maksimum. Tak jak w Kontroli Końcowej dla zadań liczbowych (`Task.task_type = numeric`, `value_min`, `value_max`, `unit`).

## 2. Ustalenia (odpowiedzi użytkownika, 2026-11-03)

| Temat | Decyzja |
|---|---|
| Postać | Jedna wartość liczbowa na punkt. |
| Wynik | Automatycznie z zakresu: w zakresie min..max OK, poza zakresem NG. Bez ręcznej zmiany wyniku. |
| Typ punktu | OK/NG (jak dotąd) albo pomiar, wybierany przy punkcie w ustawieniach. |

## 3. Stan obecny (rozpoznanie)

- Kontrola Końcowa: `POST /api/item/<id>/value` zapisuje wartość (przecinek lub kropka), liczy wynik z `value_min` i `value_max` (każde opcjonalne), zapisuje `ReportItem.value_text`.
- Kontrola etapowa: `ControlPoint` ma tytuł, opis, dział, element, aktywność. `ControlResult` ma wynik OK/NG, uwagi, raport QAR. Wynik ustawia kontroler przyciskami OK/NG.

## 4. Propozycja

### 4.1 Ustawienia punktu (`/kontrola/points`)
- Przy każdym punkcie nowa lista **Typ** (OK/NG albo Pomiar). Dla pomiaru pola **Jednostka** (np. mm, V, Ω), **Minimum** i **Maksimum** (oba opcjonalne, wystarczy jedno). Pola mieszczą się w jednym formularzu z jednym przyciskiem „Zapisz wszystko”, także dla nowego punktu.
- Walidacja: minimum nie większe niż maksimum, liczby z przecinkiem lub kropką, zmiana typu na OK/NG czyści limity.
- Punkty pomiarowe działają tak samo jak inne w zasięgach (dział, element, ogólne), więc pomiar można ustawić na jednym etapie (dziale) lub dla wybranego elementu.

### 4.2 Dane
- `ControlPoint.point_type` (`ok_ng` domyślnie, `pomiar`), `unit`, `value_min`, `value_max`.
- `ControlResult` dostaje **kopię** typu, jednostki i limitów z chwili utworzenia kontroli (zmiana punktu nie zmienia historii) oraz pole `value_text` na wpisaną wartość.
- Migracja: `ALTER` w `_migrate_schema`, istniejące punkty zostają OK/NG.

### 4.3 Ekran kontroli
- Punkt pomiarowy pokazuje pole liczby (klawiatura numeryczna na telefonie, przecinek lub kropka), jednostkę i wskazówkę „zakres: min – max”. Zamiast przycisków OK/NG pokazuje się **wynik wyliczony** (zielone OK albo czerwone NG) po wpisaniu wartości.
- Wartość zapisuje się razem z resztą kontroli przyciskami „Zapisz” i „Zakończ kontrolę”. Wynik liczony po stronie serwera, więc nie da się go podstawić z przeglądarki.
- „Zakończ kontrolę” wymaga wpisania wszystkich wartości pomiarowych (jak oceny OK/NG).
- Wartość nieliczbowa jest odrzucana z komunikatem i nic nie zapisuje się dla tego punktu.

### 4.4 Historia, lista i QAR
- Widok kontroli (też zakończonej i dla operatora) pokazuje wartość, jednostkę i zakres.
- NG tworzy raport QAR z opisem „Wartość: 12,3 mm (zakres 10–12)” oraz dotychczasowymi danymi (dział, element, wykonawca).
- Zmiana wartości w kontroli w toku, która wraca do zakresu, nie usuwa utworzonego już raportu QAR (jak przy OK/NG).

### 4.5 Uprawnienia
- Definiowanie pomiarów: admin (jak punkty). Wpisywanie wartości: kontroler i admin. Operator tylko odczyt.

## 5. Testy
- Typ punktu, walidacja limitów i jednostki, zapis w jednym formularzu, kopia limitów do wyniku.
- Wpis w zakresie, poza zakresem (min, max, brzegi), tylko minimum, tylko maksimum, przecinek i kropka, wartość nieliczbowa.
- Wymóg wpisania wszystkich pomiarów do zakończenia, wynik nie do podstawienia z formularza.
- QAR z wartością i limitami, historia po zmianie limitów punktu, odczyt operatora.

## 6. Kroki (po akceptacji)
1. Model, migracja, ustawienia punktu (typ, jednostka, min, max) w jednym zapisie (S).
2. Ekran kontroli: pole pomiaru, wyliczenie wyniku, zakończenie, historia (M).
3. QAR z wartością, testy, dokumentacja, lista zmian i plików, akceptacja, backup, commit, push, wdrożenie (po osobnej akceptacji).
