# Standard wyglądu list

Obowiązuje dla wszystkich list w aplikacji (pulpit, Kontrola Końcowa, Kontrola etapowa, kolejne).
Każda zmiana wyglądu listy musi być dopisana tutaj (sekcja "Historia zmian"), żeby nowe listy
powstawały w tym samym stylu. Styl ma być prosty i stonowany, bez efektu "kreskówki".

## 1. Status wiersza

Status kontroli pokazuje **jeden** znacznik, bez powielania tej samej informacji.
Nie używamy już: ikony w kółku przy tytule, kolorowej plakietki `.badge` ze statusem
ani osobnej kolumny z paskiem postępu i procentem dla gotowych pozycji.

Znacznik to ikona i słowo, bez ramki, tła i wersalików (klasy `.rs` w `static/css/main.css`):

| Klasa | Kolor | Ikona (Bootstrap Icons) | Użycie |
|---|---|---|---|
| `rs rs-ok` | zielony | `bi-check2-circle` | ukończone, zakończone, zgodne |
| `rs rs-warn` | bursztynowy | `bi-hourglass-split` (do wykonania: `bi-tools`) | w trakcie, do wykonania |
| `rs rs-info` | niebieski | `bi-circle`, `bi-arrow-repeat` | do skontrolowania, do kontroli sprawdzającej |
| `rs rs-ng` | czerwony | `bi-x-circle` | niezgodne, NG |
| `rs rs-muted` | szary | `bi-hourglass-split` | stan neutralny |

Wzór:

```html
<span class="rs rs-ok"><i class="bi bi-check2-circle"></i> Ukończony</span>
```

## 2. Postęp

- Gotowa pozycja (100%) nie pokazuje procentu ani paska.
- Gotowa pozycja poniżej 100%: mały szary procent po słowie (`<span class="rs-pct">93%</span>`).
- Pozycja w toku: słowo, procent i cienki pasek 3 px pod spodem (`<span class="rs-bar"><span style="width: N%"></span></span>`). Pasek tylko dla pozycji w toku.

## 3. Serie

Jeden znacznik na wiersz serii: `Ukończona` (zielony) albo `N/M ukończone` (bursztynowy).
Sztuki liczy się po `.rs-ok` w kolumnie Status (nie po plakietkach ocen).
Sztuki serii chowają się pod strzałką rozwijania (36 px).

## 4. Układ kolumn

Od 769 px: kolumna statusu 150 px, przycisk akcji 112 px, strzałka serii 36 px.
Wiersze bez strzałki rezerwują to samo miejsce (`::after`), żeby przyciski stały równo.
W tabelach długi status może się zawijać (`td .rs`).

## 5. Ocena i wartości liczbowe

Ocena (np. `5/6`) to ten sam znacznik `.rs` w kolorze zależnym od wyniku
(5 i więcej: ok, 3 i więcej: warn, poniżej: ng), w osobnej kolumnie "Ocena".

## 6. Przyciski na listach

- Zwykła wielkość liter, bez wersalików i rozstrzelenia liter (nagłówek strony, pasek zaznaczania, akcje w wierszach, filtry).
- Główna akcja strony (np. "Nowa kontrola") zostaje niebieska i pełna.
- Usuwanie (`btn-danger`) na listach to obrys i czerwony tekst, a pełne tło dopiero po najechaniu. Nie używamy pełnej czerwieni w wierszach i na pasku zaznaczania.

## 7. Chipy z wynikiem (kolumna Kontrole)

Chip to neutralny kapsułkowy znacznik z kolorową kropką wyniku zamiast pełnego zielonego tła:
zielona kropka (zgodny), bursztynowa (w toku), czerwona (niezgodny), szara (brak).
Dotyczy `.kz-ctl-badges .badge`, bez zmian w szablonach.

## 8. Wyszukiwanie list

Każda lista używa tego samego wzoru, makra `list_search` z `templates/_list_search.html` (skrypt `static/js/list-search.js`, style `.ls-*` w `erp-design.css`):

- **Komputer:** pole szukania, obok przyciski "Skanuj QR" (tam, gdzie lista ma numery ZO) i "Szukaj". Dodatkowe filtry są widoczne od razu pod polem.
- **Telefon:** pole na całą szerokość, pod nim w jednym rzędzie **Filtry**, **Skanuj QR**, **Szukaj**. Filtry wysuwają się po kliknięciu "Filtry" (z licznikiem aktywnych). Lista bez dodatkowych filtrów ma tylko Szukaj (i Skanuj QR).
- **Skanuj QR:** kod zlecenia (pole `o`, a gdy go brak `p`) trafia do pola szukania i lista się filtruje.
- **Wyczyść:** link w panelu filtrów albo przy polu (`clear_url`), pokazuje się tylko przy aktywnym szukaniu.
- Przycisk "Filtruj" w panelu nie jest potrzebny, bo "Szukaj" wysyła cały formularz.

Użycie: `{% from "_list_search.html" import list_search %}`, a w formularzu `{% call list_search('q', q, 'Szukaj…', qr=True, active=N, uid='moja-lista') %}...pola filtrów...{% endcall %}` (bez `call`, gdy nie ma filtrów).
Listy ze wzorem: Kontrola etapowa, Kontrola Końcowa, Raporty QAR, Dokumentacja, Lista produkcyjna (arkusz i historia).

## 9. Tryb ciemny

Warianty kolorów są w `main.css` (`body.dark .rs-*`). Nie dodajemy osobnych stylów w szablonach.

## 10. Zasady przy nowej liście

1. Użyj klas `.rs*` zamiast `.badge` do statusu.
2. Nie dubluj statusu (ikona + plakietka + pasek).
3. Przyciski bez wersalików, usuwanie jako obrys.
4. Dopisz zmianę do "Historii zmian" poniżej, razem ze zmienionymi plikami.

## Historia zmian

| Data | Lista | Zmiana | Pliki |
|---|---|---|---|
| 23.10.2026 | Pulpit (Ostatnie raporty) | Jeden znacznik statusu zamiast kółka, plakietki i paska. Pasek tylko w toku. | `templates/dashboard.html`, `static/css/main.css` |
| 23.10.2026 | Kontrola etapowa (`/kontrola`) | Kolumna Status na znacznikach `.rs*` (zakończone, w trakcie, do skontrolowania, do wykonania, do kontroli sprawdzającej, zamknięte zgodny lub niezgodny). Plakietki działów w kolumnie Kontrole bez zmian. | `templates/kontrola/list.html`, `static/css/main.css` |
| 23.10.2026 | Kontrola Końcowa (`/reports`) | Status na `.rs*`, kolumna Postęp zastąpiona kolumną Ocena (`.rs` z wynikiem x/6), licznik sztuk serii po `.rs-ok`. | `templates/reports_list.html`, `static/css/main.css` |
| 23.10.2026 | Wszystkie listy | Przyciski bez wersalików (nagłówek, pasek zaznaczania, akcje w wierszach), usuwanie jako obrys zamiast pełnej czerwieni, chipy w kolumnie Kontrole z kropką wyniku. | `static/css/erp-design.css` |
| 23.10.2026 | Raporty QAR (`/qar`) | Status (Otwarty, W toku, Zamknięty) na znacznikach `.rs*` w tabeli i na kartach mobilnych, usuwanie jako obrys (`btn-danger`) zamiast czerwonych stylów w wierszu, przyciski bez wersalików. | `templates/qar/list.html`, `static/css/erp-design.css` |
| 23.10.2026 | Kontrola etapowa (`/kontrola`) | Filtry na telefonie: pole szukania, przycisk Szukaj i przycisk Filtry, reszta opcji wysuwana. | `templates/kontrola/list.html` |
| 23.10.2026 | Kontrola etapowa (`/kontrola`) | Szukanie skanem QR: przycisk przy polu szukania otwiera kamerę, odczytany numer ZO filtruje listę. | `templates/kontrola/list.html` |
| 23.10.2026 | Kontrola etapowa (`/kontrola`) | Telefon: pole szukania na całą szerokość, przyciski Szukaj, Skanuj QR i Filtry w rzędzie pod polem. | `templates/kontrola/list.html` |
| 23.10.2026 | Wszystkie listy z wyszukiwaniem | Wspólny wzór wyszukiwania (makro `list_search`): pole, Skanuj QR, Szukaj; na telefonie pole na całą szerokość, pod nim Filtry, Skanuj QR, Szukaj, filtry wysuwane. Usunięty przycisk Filtruj. | `templates/_list_search.html`, `static/js/list-search.js`, `static/css/erp-design.css`, szablony list |
