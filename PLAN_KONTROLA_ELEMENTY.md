# Plan: elementy kontroli w Kontroli etapowej (`/kontrola`)

Stan: plan zaakceptowany 2026-10-21 (wybór elementu opcjonalny). Zaimplementowane lokalnie, testy przechodzą, czeka na akceptację zmian przed commitem i wdrożeniem. Kontrola Końcowa nie jest zmieniana.

## 1. Cel

Po zeskanowaniu zlecenia, przy wyborze działu, kontroler wybiera także **elementy kontroli** (np. Drzwi, Korpus, Płyta montażowa), dopiero potem rozpoczyna kontrolę. Zestaw punktów zależy od działu i wybranych elementów. Elementy ustawia się razem z punktami kontrolnymi w `/kontrola/points`.

## 2. Ustalenia (odpowiedzi użytkownika, 2026-10-21)

| Temat | Decyzja |
|---|---|
| Elementy | **Wspólna lista** dla wszystkich działów (Drzwi, Korpus, ...), każdy element można kontrolować w każdym dziale. |
| Punkty | Po wyborze działu i elementów: punkty elementu, punkty działu i punkty ogólne. |
| Wybór | **Można zaznaczyć kilka elementów naraz** w jednej kontroli. |
| Widoczność | Historia kontroli zlecenia i lista, raport QAR przy NG, filtr elementu na liście. |

## 3. Przepływ (strona zlecenia)

1. Skan QR dodaje zlecenie do listy (jak dziś), kontrola zaczyna się na stronie zlecenia.
2. Wybierasz **dział kontroli**.
3. Zaznaczasz **elementy** (pola wyboru ze wspólnej listy aktywnych elementów).
4. „Rozpocznij kontrolę” tworzy kontrolę dla działu i zaznaczonych elementów.
5. Ekran kontroli: sekcja **Wspólne** (punkty ogólne i punkty działu bez elementu), a pod nią sekcja każdego wybranego elementu z jego punktami.
6. Na końcu osoba odpowiedzialna (z działu) i „Zakończ kontrolę”.
7. „Ponowna kontrola” przy zakończonej kontroli startuje z tym samym działem i tymi samymi elementami (można zmienić przed startem).

## 4. Które punkty trafiają do kontroli

Punkt ma dwa opcjonalne przypisania: dział i element.

| Dział punktu | Element punktu | Gdzie się pojawia |
|---|---|---|
| brak | brak | w każdej kontroli (sekcja Wspólne) |
| jest | brak | w kontroli tego działu (sekcja Wspólne) |
| brak | jest | w sekcji tego elementu w każdym dziale |
| jest | jest | w sekcji tego elementu tylko w tym dziale |

Kontrola bez zaznaczonych elementów ma tylko sekcję Wspólne (jak dziś). Punkt elementu pojawia się osobno dla każdego wybranego elementu, więc ocena i uwagi są osobne.

## 5. Dane

- Nowa tabela `control_elements`: nazwa, kolejność, aktywny.
- `ControlPoint.element_id` (FK, nullable).
- Nowa tabela łącząca `control_record_elements` (kontrola, element, kolejność) oraz kopia nazwy elementu, żeby historia przetrwała zmianę nazwy lub usunięcie.
- `ControlResult.element_id` i `ControlResult.element_name` (kopia): wynik punktu elementu należy do konkretnego elementu kontroli.
- Migracja: `ALTER` dwóch kolumn i dwie nowe tabele przez `create_all`, stare kontrole bez zmian.

## 6. Ekrany i funkcje

- **Strona zlecenia:** lista działu, pola wyboru elementów, przycisk „Rozpocznij kontrolę”. Rozpoczęta, niezakończona kontrola tego działu z **tym samym zestawem elementów** jest otwierana zamiast tworzenia drugiej, inny zestaw to osobna kontrola.
- **Historia zlecenia i lista:** badge „Dział: Element 1, Element 2”.
- **Filtr listy:** lista elementów obok filtra działu, szukanie obejmuje nazwy elementów.
- **Raport QAR przy NG:** w opisie „Element: nazwa” (dla punktu elementu), zgodnie z działem i wykonawcą.
- **Ustawienia (`/kontrola/points`):** nowa sekcja **Elementy** (dodaj, zmień nazwę, kolejność, aktywny, usuń gdy nieużywany, w przeciwnym razie dezaktywuj). Przy punkcie lista „Element” obok listy „Dział”, filtr punktów po elemencie.
- **Tworzenie i usuwanie:** elementy dodaje i usuwa admin (pojedynczo), kontrole i zlecenia zostają usuwalne jak dotąd (zbiorczo i pojedynczo).
- **Uprawnienia:** wybór elementów i start kontroli: kontroler i admin; elementy i punkty: admin; operator tylko odczyt.

## 7. Testy

- Elementy: dodawanie, zmiana, kolejność, dezaktywacja, usuwanie (blokada przy użyciu).
- Reguły punktów z tabeli w sekcji 4 (cztery kombinacje, kilka elementów, brak elementów).
- Start kontroli: wiele elementów, osobne sekcje i wyniki, ten sam zestaw otwiera istniejącą, inny tworzy nową, nieznany lub nieaktywny element odrzucony.
- Ponowna kontrola z tym samym zestawem, historia zachowuje starsze kontrole.
- Lista: badge, filtr elementu, szukanie po nazwie elementu, odczyt operatora.
- QAR z elementem, zakończenie kontroli, migracja kolumn i tabel.

## 8. Kroki realizacji (po akceptacji)

1. Model, migracja, strona Elementy i przypisanie punktów do elementów (M).
2. Wybór elementów na stronie zlecenia, tworzenie kontroli z sekcjami, ekran kontroli i zakończenie (M).
3. Historia, lista, filtr, QAR (S).
4. Testy, dokumentacja, lista zmian i plików, akceptacja, backup bazy, commit, push, wdrożenie (po osobnej akceptacji).

## 9. Do potwierdzenia

- **Wybór elementu: opcjonalny** (decyzja użytkownika). Bez zaznaczenia powstaje kontrola samego działu (punkty wspólne), jak dotychczas. Wymóg wyboru co najmniej jednego elementu, gdy istnieją aktywne elementy, wystarczy włączyć jednym warunkiem.
