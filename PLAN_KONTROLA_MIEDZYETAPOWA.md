# Plan: Kontrola międzyetapowa (zgłoszenia, bufory, transport)

Stan na 2026-11-05. Ustalenia z użytkownikiem zapisane, kod jeszcze nie ruszony. Pozycje oznaczone **DO ZATWIERDZENIA** czekają na decyzję.

## 1. Ustalone

| Temat | Decyzja |
|---|---|
| Ekran pracownika | Rozbudowa Stanowiska `/produkcja/skan` (`produkcja/scan.py`), konto wspólne + plakietka. Konta pracowników i logowanie plakietką to plan na przyszłość. |
| Zgłoszenie wykonania | Zlecenie (ZO + produkt), etap, wykonawca, ilość zgodna, ilość niezgodna. |
| Niezgodność (NG) | Nie tworzy raportu QAR. Tworzy alert (patrz 3). |
| Etapy zlecenia | Z `ProductionStage` (z możliwością pominięcia etapów). |
| Lista produkcyjna | Zgłoszenie dopisuje też inicjały w kolumnie etapu (jeśli kolumna jest powiązana). Lista może w przyszłości zniknąć, więc nowy moduł ma własny rejestr jako źródło prawdy. Nowa kolumna "W systemie" (znacznik pierwszego skanu zlecenia). |
| Bufor | Skan QR albo ręczne wpisanie kodu (`A001`, `find_buffer`). |
| Kontrola buforu | Kontroler skanuje bufor, widzi listę zleceń, ocenia osobno każde zlecenie (punkty Kontroli etapowej). |
| Zwolnienie | Bufor ma status "Do transportu", gdy wszystkie zlecenia na nim mają wynik pozytywny. |
| Transport | Pracownik transportu skanuje bufor i widzi: może wywieźć albo nie (z powodem). Tylko odczyt plus przycisk "Wywieziono". |
| Uprawnienia | Nowe akcje w macierzy ról (zgłaszanie, kontrola buforu, transport). |

## 2. Model danych (nowe tabele przez `create_all`, bez ALTER poza jedną kolumną)

- `StageReport`: `control_order_id`, `stage_id`, `employee_id` (DepartmentEmployee), `user_id`, `qty_ok`, `qty_ng`, `machine_id` (opcjonalnie), `created_at`.
- `BufferEntry`: `buffer_id`, `control_order_id`, `qty`, `stage_id` (po którym etapie), `employee_id`, `placed_at`, `check_status` (`czeka`, `pozytywna`, `negatywna`), `checked_by_id`, `checked_at`, `removed_at`, `removed_reason`.
- `BufferTransport`: `buffer_id`, `user_id`, `at` (kto i kiedy wywiózł).
- `ControlOrder.system_at` (nowa kolumna, data pierwszego skanu) oraz `ProdRow` łączone po ZO + produkt do kolumny "W systemie".
- `Alert` istniejący mechanizm dla alertów (nowy typ).

## 3. Nazwa alertu NG (DO ZATWIERDZENIA)

Propozycja: **"Niezgodność do weryfikacji"**. Treść: "ZO 1234 PSH 600x800: zgłoszono 2 szt. niezgodne na etapie Gięcie (J. Kowalski). Wymagana weryfikacja kontrolera." Odbiorcy: kontroler i administrator. Znika po wpisie kontrolera "Zweryfikowano" (z decyzją: do poprawy, złom, uznane za zgodne).
Inne możliwości: "Wymagana kontrola niezgodności", "Zgłoszone NG".

## 4. Kiedy wpis znika z bufora (DO ZATWIERDZENIA)

Propozycja, wpis dostaje `removed_at` i powód (nic nie jest kasowane, historia zostaje):

1. **Przeniesienie:** to samo zlecenie odstawione skanem na inny bufor zdejmuje ilość z poprzedniego (jedna ilość leży w jednym miejscu).
2. **Pobranie przez następny etap:** zgłoszenie wykonania następnego etapu zdejmuje z bufora ilość równą zgłoszonej (od najstarszego wpisu), bo to znaczy, że elementy zostały pobrane z wózka.
3. **Ręcznie:** przycisk "Zdejmij z bufora" dla kontrolera i administratora (np. pomyłka skanu).
4. **Koniec zlecenia:** zlecenie spakowane albo usunięte zdejmuje wszystkie swoje wpisy.
5. **Wywiezienie przez transport NIE zdejmuje wpisu.** Zapisuje tylko, że wózek został wywieziony (wózek jedzie do kolejnego etapu, zlecenie dalej na nim leży do pobrania).
6. Ilości częściowe: wpis ma własną ilość, zdejmowanie pomniejsza ją (0 oznacza zdjęty).
7. Wynik negatywny kontroli: wpis zostaje na buforze ze statusem `negatywna` (wstrzymany), reszta bufora może być zwolniona.

## 5. Etapy realizacji (każdy kończy się testami i akceptacją przed commitem)

1. Modele, migracje, kolumna "W systemie", testy.
2. Stanowisko: zgłoszenie wykonania (zgodne/niezgodne, etap, wykonawca), alert NG, wpis w Liście produkcyjnej.
3. Odstawienie na bufor (skan lub ręcznie) i zasady zdejmowania z sekcji 4.
4. Kontrola buforu (skan, lista zleceń, ocena każdego zlecenia, status "Do transportu").
5. Ekran transportu (skan bufora, może/nie może wywieźć, "Wywieziono").
6. Listy: etap, bufor, zgodne/niezgodne w Kontroli etapowej i Liście produkcyjnej (zgodnie z `STANDARD_LIST.md`).
7. Uprawnienia, menu, dokumentacja w `CLAUDE.md`.

## 6. Decyzje o niezgodnościach i poprawkach (2026-11-05)

- Na bufor trafia tylko ilość zgodna. Sztuki niezgodne zostają przy pracowniku, system tworzy alert "Niezgodność do weryfikacji" (**DO ZATWIERDZENIA** nazwa).
- Kontroler musi podjąć decyzję: **do poprawy** (wskazuje etap, tabela `ReworkItem`), **złom**, **uznane za zgodne**, albo **odłóż do decyzji** (bufor wstrzymań `NG01`, przypomnienie po 24 h).
- Poprawka: przycisk "Poprawka wykonana" na Stanowisku (`StageReport.kind = poprawka`), potem odstawienie na bufor ze znacznikiem "Ponowna kontrola".
- **Nieudana poprawka albo złom** tworzy pozycję na liście **"Do wykonania od nowa"** (`RemakeItem`: zlecenie, ilość, element/opis opcjonalny, etap startowy wskazany przez kontrolera, powód, status `otwarte`, `w_toku`, `wykonane`). Lista widoczna na Stanowisku po skanie zlecenia i w Kontroli etapowej. Zgłoszenie wykonania od wskazanego etapu pomniejsza pozycję.

## 7. Statystyki (**DO ZATWIERDZENIA**)

Zasada: nie ma osobnych liczników. Źródłem są zdarzenia z sekcji 2 oraz `ReworkItem`, `RemakeItem` i czasy kontroli. Nazwy pracownika, działu i etapu kopiowane przy zapisie (historia nie zmienia się po edycji ustawień).

Dodatkowe pola: `BufferEntry.check_started_at` i `check_completed_at` (czas kontroli zlecenia), `Nonconformity` (zgłoszenie NG: ilość, etap powstania wady, wykonawca odpowiedzialny, przyczyna z edytowalnej listy, decyzja, czas decyzji).

Odpowiedzialny: wykonawca z meldunku etapu, w którym powstała wada (kontroler może wskazać inny etap, domyślnie ostatni meldunek tego zlecenia).

Raporty w `/admin/stats` (sekcja "Produkcja i kontrole", filtry: okres, dział, etap, pracownik, klient, produkt, Excel):
1. Pracownik: ile zleceń, ile sztuk zgodnych i niezgodnych, jakie produkty, wskaźnik niezgodności.
2. Wady: ile, co (przyczyna), na jakim etapie powstały, kto odpowiedzialny, decyzja (poprawa, złom, uznane).
3. Etapy i działy: wydajność, FPY (zgodne za pierwszym razem), złom.
4. Kontrole: liczba, wynik, czas od rozpoczęcia do zakończenia (średni, mediana, maksimum, wg kontrolera), czas oczekiwania od odstawienia na bufor do kontroli.
5. Bufory: czas leżenia, zlecenia wstrzymane, czas do decyzji.
6. Poprawki i powtórki: skuteczność poprawek, lista "Do wykonania od nowa" i jej koszt w sztukach.
