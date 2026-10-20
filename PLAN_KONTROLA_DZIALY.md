# Plan: kontrole po działach w Kontroli zamówień (`/kontrola`), wersja 2

Stan: wersja 2 planu zaakceptowana 2026-10-21 i zaimplementowana lokalnie (kroki 1 do 3), testy przechodzą, czeka na akceptację zmian przed commitem i wdrożeniem. Kontrola Końcowa (listy, szablony, zamówienia) nie jest zmieniana.

## 1. Cel

Każde zlecenie można skontrolować po każdym dziale. Zlecenie trafia na listę zleceń po skanie QR, z listy otwiera się kontrole, a przy każdym zleceniu widać, w jakim dziale kontrola została wykonana i przez kogo.

## 2. Przepływ (opis użytkownika)

1. **Skan QR zlecenia** tworzy zlecenie na liście zleceń (albo otwiera istniejące, gdy zlecenie już jest: ponowny skan).
2. **Lista zleceń**: wiersz zlecenia z podlistą wykonanych kontroli (dział, osoba odpowiedzialna, wynik, data, stan) i przyciskami „Nowa kontrola” oraz „Otwórz”.
3. **Nowa kontrola** z listy:
   1. wybór **działu kontroli**,
   2. rozwija się lista kontrolna z punktami przypisanymi do tego działu (oraz punktami ogólnymi),
   3. ocena punktów OK/NG (NG tworzy raport QAR),
   4. na końcu **oznaczenie osoby odpowiedzialnej** za wykonanie (lista pracowników wybranego działu, opcjonalne),
   5. „Zakończ kontrolę” dopisuje wpis przy zleceniu: dział, osoba, wynik.
4. **Kolejna kontrola** (inny dział): przycisk „Nowa kontrola” na liście albo ponowny skan QR tego samego zlecenia.
5. Bez „Wykonane” operatora i bez kontroli sprawdzającej (tylko kontrola z działem).

## 3. Decyzje

| Temat | Decyzja |
|---|---|
| Punkty | Przypisane do działów w `/kontrola/points` (jak w wersji 1), punkty bez działu są ogólne i trafiają do każdej kontroli. |
| Dział kontroli | Wybierany na początku kontroli, od niego zależy zestaw punktów i lista pracowników. |
| Osoba | Wybierana na końcu, z pracowników wybranego działu, opcjonalna. |
| Cykl | Brak. Kontrola kończy się wpisem działu i osoby. |
| Start | Skan QR dodaje zlecenie do listy, kontrolę zaczyna się z listy („Nowa kontrola”) albo kolejnym skanem. |
| Stare dane | Dotychczasowe kontrole ogólne (z cyklem wstępna, Wykonane, sprawdzająca) zostają jako wiersze „ogólna” i działają jak dotąd. |

## 4. Stan lokalnego kodu (wersja 1, niewdrożony)

Do zachowania: `ControlPoint.department_id`, `ControlRecord.department_id`, przypisywanie punktów do działów i filtr w `/kontrola/points`, walidacja pracownika po dziale, kontrola działu bez cyklu, QAR z działem i osobą, kolumna i filtr działu na liście, migracja dwóch kolumn, testy punktów i kontroli działu.

Do przebudowy: karta zlecenia z listą wszystkich działów (`/kontrola/zlecenie`) zastąpiona listą zleceń z podlistą kontroli oraz wyborem działu na początku kontroli.

## 5. Propozycja zmian

### 5.1 Dane
- Nowa tabela `control_orders`: ZO, produkt, klient, sztuki, kto dodał i kiedy. Zlecenie jest jednoznaczne po parze ZO + produkt (bez wielkości liter i nadmiarowych spacji, jak dziś).
- `ControlRecord.order_id` (FK do `control_orders`, nullable): kontrole działu należą do zlecenia, stare kontrole ogólne zostają bez zlecenia.
- Kontrola rozpoczęta (stan „w trakcie”) ma już wybrany dział i punkty, zakończona jest zamknięta.
- Migracja: tabela przez `create_all`, jedna kolumna `ALTER`.

### 5.2 Skan QR
- Skan tworzy wpis zlecenia (jeśli go nie ma) i otwiera stronę zlecenia z listą kontroli i przyciskiem „Nowa kontrola”.
- Dopóki żaden dział nie ma punktów (stan produkcji dziś), skan działa jak dotąd (kontrola ogólna z cyklem), więc wdrożenie nie zmienia obecnej pracy.

### 5.3 Lista zleceń (`/kontrola/`)
- Wiersz zlecenia (karta na telefonie): ZO, produkt, klient, sztuki, podlista kontroli (dział, osoba, wynik, data, stan), przyciski „Nowa kontrola” i „Otwórz”.
- Filtr działu (dział kontroli albo osoby), szukanie po ZO, produkcie, kliencie i pracowniku. Wiersze „ogólna” dla starych kontroli.

### 5.4 Nowa kontrola
- Krok 1: wybór działu (dział z aktywnymi punktami), „Rozpocznij”.
- Krok 2: ekran kontroli jak dziś (punkty OK/NG, uwagi), na końcu „Osoba odpowiedzialna” (pracownicy działu) i „Zakończ kontrolę”.
- Po zakończeniu powrót na stronę zlecenia z dopisaną kontrolą.

### 5.5 Uprawnienia
- Tworzenie zleceń, kontroli i ocena: kontroler i admin. Punkty i przypisanie do działów: admin. Operator tylko odczyt zakończonych kontroli.

## 6. Zakres testów
- Skan tworzy zlecenie bez duplikatów (ponowny skan otwiera istniejące, inny produkt z tym samym ZO osobno).
- Lista zleceń z podlistą kontroli, filtry, odczyt operatora.
- Nowa kontrola: wybór działu, punkty działu i ogólne, osoba tylko z działu, zakończenie bez cyklu, wpis na liście.
- Kilka kontroli jednego zlecenia w różnych działach, ponowna kontrola tego samego działu zapisana w historii (poprzednia zostaje).
- Stare kontrole ogólne z cyklem bez zmian, skan bez działów z punktami działa jak dotąd.
- QAR z działem i osobą, migracja.

## 7. Kroki realizacji (po akceptacji)
1. Model `control_orders`, migracja, skan tworzy zlecenie (S).
2. Lista zleceń z podlistą kontroli i filtrami (M).
3. Nowa kontrola: wybór działu, punkty, osoba na końcu, powrót na stronę zlecenia (M).
4. Testy, dokumentacja, lista zmian, akceptacja, backup, commit, push, wdrożenie (po osobnej akceptacji).

## 8. Rozstrzygnięte
- **Ponowna kontrola tego samego działu:** dozwolona. Przy zleceniu, które ma zakończoną kontrolę danego działu, przycisk „Ponowna kontrola” tworzy nową kontrolę tego działu, a po zakończeniu trafia do **historii kontroli** zlecenia (poprzednie zostają, nic się nie nadpisuje). Podlista przy zleceniu pokazuje wszystkie kontrole od najnowszej, najnowsza oznaczona jako aktualna.
- **Stare kontrole ogólne:** na produkcji są tylko 2 rekordy (jeden cykl: wstępna zakończona i sprawdzająca w trakcie, utworzone 2026-10-20). Zostają bez zmian jako wiersze „ogólna” z dotychczasowym cyklem. Nie przenosimy ich pod zlecenia.
