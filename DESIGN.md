# Zasady wyglądu aplikacji profit (RP-Sys)

Zaakceptowany kierunek z 2026-11-27. Nowe ekrany rób od razu według tego dokumentu, a istniejące zmieniaj etapami. Etap czytelności (rozmiary od 12 px, brak wersalików, kolory jasnego motywu) jest wprowadzony w CSS. Pozostałe zasady, w tym wymiary na telefonie i porządek w stylach, to nadal stan docelowy.

Listy opisuje osobno `STANDARD_LIST.md` (znaczniki statusu, postęp, serie, kolumny, wyszukiwanie). Ten dokument go nie zastępuje. Tam, gdzie dotyczy rozmiarów i kolorów, obowiązują wartości stąd.

## 1. Dla kogo i po co

Narzędzie do codziennej pracy na produkcji i w biurze: kontrola jakości, lista produkcyjna, raporty. Używane na komputerze i na telefonie, także na hali. Priorytety w tej kolejności:

1. Czytelność.
2. Szybkie wykonanie czynności.
3. Spójność między modułami.

Wygląd ma być prosty i stonowany. Wyraźnie wyróżnia się tylko to, co użytkownik ma teraz zrobić albo sprawdzić.

## 2. Kolory

| Rola | Kolor | Użycie |
|---|---|---|
| Tekst | `#16202C` | treść, tytuły |
| Tekst pomocniczy | `#4A586A` | nagłówki tabel, podpowiedzi, opisy |
| Tło strony | `#EEF1F4` | tło pod panelami |
| Panel | `#FFFFFF` | panele, tabele, pola |
| Akcja i linki | `#1257A8` | główny przycisk, linki, fokus, status "do skontrolowania" |
| Zgodne | `#17623A` | ukończone, zgodne, OK |
| W trakcie | `#8A4B00` | w trakcie, do wykonania |
| Niezgodne | `#B3261E` | NG, błędy, usuwanie |
| Linia | `#CBD3DC` | ramki paneli, podziały |
| Ramka pola | `#8593A3` | pola formularzy i przyciski drugorzędne |

- Kolory tekstu mają na bieli, na tle strony i na jasnym tle nagłówka tabeli (`#F7F9FC`) kontrast co najmniej 5,7:1 (wymagane minimum to 4,5:1).
- Ramka pola ma 3,1:1 na bieli, ale 2,8:1 na tle strony. Pola umieszczaj na białym panelu.
- Kolor nigdy nie jest jedyną informacją: status ma też ikonę i słowo.
- Nie wpisuj kolorów na sztywno w szablonach. Używaj zmiennych CSS.
- Tryb ciemny zachował dotychczasowe kolory. Sprawdzone kolory tekstu i statusów mają tam kontrast co najmniej 5,2:1.

## 3. Pismo

Czcionka systemowa: `'Segoe UI', system-ui, -apple-system, sans-serif`. Bez czcionek z zewnętrznych serwerów.

| Zastosowanie | Rozmiar | Grubość |
|---|---|---|
| Tytuł strony | 22 px | 700 |
| Tytuł sekcji | 18 px | 700 |
| Pola i przyciski na telefonie | 16 px | 400 lub 600 |
| Treść, komórki tabel, statusy | 14 px | 400, statusy 600 |
| Nagłówek tabeli | 13 px | 600 |
| Podpowiedź pod polem, teksty pomocnicze | 12 px | 400 |

- **12 px to minimum.** Mniejszych rozmiarów nie używamy.
- Rozmiary z tabeli to wartości docelowe w pikselach. Bazowy rozmiar w `main.css` to 14 px, więc przeliczając na `rem`, dziel przez 14.
- Nagłówki, etykiety i przyciski zwykłą wielkością liter, bez wersalików i rozstrzelenia.
- Linie tekstu ciągłego do około 80 znaków.

## 4. Przyciski

- Na ekranie jest **jedna** pełna niebieska akcja główna.
- Akcje drugorzędne: białe tło i ramka.
- Usuwanie: czerwony obrys i tekst, pełne tło dopiero po najechaniu. Wymaga potwierdzenia, które mówi, co zniknie.
- Napis nazywa czynność ("Zakończ kontrolę", "Zatwierdź kod"). Ta sama czynność ma w całej aplikacji tę samą nazwę.
- Wyjątek: przy ocenie punktu napisy **OK** i **NG** to wynik kontroli i zostają.

## 5. Formularze

- Każde pole ma widoczną etykietę. Tekst w pustym polu jej nie zastępuje.
- Fokus klawiatury jest widoczny na polach, przyciskach i linkach.
- Dla liczb ustawiaj `inputmode="decimal"`, a zakres i jednostkę pokazuj przy polu.
- Błąd pokazuj przy polu albo nad przyciskiem, który go wywołał.

## 6. Telefon (ekrany pracownika)

- Przyciski i pola co najmniej 48 px wysokości.
- Wybór OK i NG: dwa duże przyciski obok siebie, co najmniej 56 px. Wybrany ma pełne tło.
- Jedna kolumna, najważniejsza czynność bez szukania.
- Widać postęp, na przykład "Ocenione 1 z 3".
- Gdy czegoś brakuje, komunikat mówi czego i pozwala przejść do tego miejsca.

## 7. Teksty

- Po polsku, prostym językiem, bez nazw technicznych.
- **Bez długiego myślnika** (znak U+2014). Zamiast niego przecinek, nawias albo osobne zdanie. W pustej komórce pisz "Brak".
- Listy wyboru: "Wybierz montera".
- Błąd mówi, co się stało i co zrobić: "Nie zapisano, brak połączenia. Spróbuj ponownie." Samo "Błąd" nie wystarcza.
- Pusty stan mówi, dlaczego jest pusto i co można zrobić.
- Nazwy w menu i motywach po polsku ("Pulpit", "Jasny motyw", "Ciemny motyw").

## 8. Ruch i ozdobniki

- Ruch tylko jako odpowiedź na działanie użytkownika (rozwinięcie, potwierdzenie, ładowanie).
- Bez cyklicznych animacji ozdobnych.
- Szanuj ustawienie ograniczonego ruchu (`prefers-reduced-motion`).
- Ramki, numery i etykiety tylko wtedy, gdy coś informują.

## 9. Zasady dla kodu

- Najpierw użyj istniejącej klasy, dopiero potem twórz nową.
- Bez nowych atrybutów `style="..."` i bloków `<style>` w szablonach. Wyjątek: wartości liczone z danych, na przykład szerokość paska.
- Nie dodawaj nowych `!important`.

## 10. Kontrola przed oddaniem ekranu

- [ ] Sprawdzony na komputerze i przy szerokości telefonu.
- [ ] Sprawdzony w jasnym i ciemnym motywie.
- [ ] Żaden tekst nie jest mniejszy niż 12 px.
- [ ] Obsługa klawiaturą pokazuje fokus.
- [ ] Błędy i puste stany mają opis i dalszy krok.
- [ ] Zmiana listy wpisana do historii w `STANDARD_LIST.md`.
