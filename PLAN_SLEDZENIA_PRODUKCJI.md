# Plan: śledzenie produkcji na buforach (następca Kontroli zamówień)

Dokument roboczy, uzupełniany w miarę dodawania zadań. Stan na 2026-10-18 (po pełnej analizie planu): wszystkie decyzje podjęte, nic nie jest jeszcze zakodowane. Punkt wyjścia: wdrożony commit `dd667df`.

## 1. Cel

Śledzić każde zlecenie (ZO + produkt, ilość) od wpisania na bufor pierwszego etapu aż do pakowania. Zlecenie rozkłada się na elementy produktu. Każdy element idzie własną trasą przez etapy i w danej chwili leży w innym miejscu. Do etapów można wystawiać kontrole.

## 2. Podjęte decyzje

| Temat | Decyzja |
|---|---|
| Bufor | Wejściowy, stoi przed etapem. Element czeka tam na etap. |
| Przepływ | Bufor etapu N, potem przejęcie etapu N przez pracownika (w realizacji), potem wpis na bufor etapu N+1. |
| Elementy produktu | Szablon elementów i trasy per produkt (dopasowanie po nazwie z kodu QR) plus korekta przy zleceniu. |
| Ilości | Same ilości, bez numerów sztuk. Wszystko może iść częściami (np. 3 z 6 szt.). |
| Stary moduł | Nowy moduł wchodzi w miejsce Kontroli zamówień. Lista produkcyjna (Excel) i Kontrola Końcowa zostają. |
| Kto wpisuje | Wspólne konto stanowiska (rola `produkcja`) i konta operatorów. Zawsze przy wpisie skanuje się plakietkę QR pracownika działu (`DepartmentEmployee`), także na koncie operatora. Sesja pracownika na urządzeniu jak w Liście produkcyjnej. |
| Kontrola kroku | Administrator lub kontroler wystawia kontrolę w planie kontroli zlecenia (zaznacza etapy). Kontrola odbywa się na buforze danego etapu: kontrola "po etapie N" to kontrola przy wejściu na bufor etapu N+1, ilość czeka tam wstrzymana do wyniku. Operator nie decyduje. |
| Kontrole doraźne | Kontroler może wystawić kontrolę w trakcie produkcji, doraźnie na dowolnym buforze (poza planem kontroli zlecenia). |
| Blokada przez kontrolę | Kontrolowane są ilości. Etap może mieć ustawienie **"kontrola obowiązkowa"**. Na takim etapie do przejęcia przechodzą tylko ilości z wynikiem OK. Na pozostałych etapach ilość jest wolna, chyba że kontroler wystawił dla niej kontrolę (w planie lub doraźnie) i nie ma jeszcze wyniku OK. |
| Wymuszenie przejęcia | Kontroler lub admin może wymusić przejęcie po potwierdzeniu. Operator nie może. Operator, który próbuje przejąć zablokowaną ilość, dostaje alert o braku możliwości przejęcia. Przy zablokowanej ilości operator ma przycisk **"Zleć decyzję"**, który zgłasza prośbę do kontrolera i admina (wniosek nie powstaje automatycznie, tylko po kliknięciu). Kontroler lub admin podejmują decyzję: wymusić przejęcie (po potwierdzeniu) albo odrzucić. Operator dostaje informację o decyzji. |
| Punkty kontrolne | Obecna wspólna lista punktów jest przypisana do etapu Kontrola końcowa. Pozostałe etapy dostają własne listy (do utworzenia). |
| Widoczność stanu | Stan produkcji (zlecenia, bufory, etapy) widzą wszyscy zalogowani, tylko do odczytu. |
| Bufory | Nazwa, numer i kod bufora są edytowalne, ale **tylko w ustawieniach modułu** (Ustawienia produkcji), nigdzie indziej. |
| Kod bufora i etykiety | QR i kod kreskowy na etykiecie zawierają sam kod bufora (np. `0001`, `A001`), bez przedrostka. Zmiana kodu unieważnia wydrukowaną etykietę (system ostrzega). Istnieje już 60 gotowych etykiet 50x100 mm (QR, Code128 i numer): kody `0001` do `0010` oraz `A001` do `E010` (katalog `D:\Dane\Desktop\KODY QR POLA`). System ma je od razu rozpoznawać. |
| Etap a dział | Jeden dział na etap. Wycinanie dostaje nowy dział "Wycinanie" (pracownicy z Cięcia, Lasera i Wykrawarki są przenoszeni ręcznie, do tego potrzebna jest akcja "przenieś do działu"). Przygotowanie, Kompletacja i Pakowanie dostają nowe, puste działy. |
| Ekrany etapów | Jedna strona "Etapy i bufory" w Ustawieniach produkcji: etapy w kolejności, przy każdym dział, maszyny i bufory. |
| Edycja konfiguracji | Szablony produktów, etapy, maszyny, bufory i punkty kontrolne edytują admin i kontroler. Stan produkcji widzą wszyscy, w tym Zamawiający i Konstruktor, tylko do odczytu. |
| Wycofanie wpisu | Admin i kontroler wycofują dowolny wpis. Operator wycofuje **tylko własny** wpis, bez limitu czasu. Przeniesienie między buforami zdejmuje ilość z poprzedniego bufora (nie zostaje w nim). |
| Kontrola doraźna | Kontroler lub admin wybiera element i ilość objętą kontrolą. |
| Tożsamość pracownika | Pracownik z działów (`DepartmentEmployee`, Ustawienia produkcji), a nie `ProdEmployee` z Listy produkcyjnej. Dla stanowiska potrzebna będzie plakietka QR dla pracowników działów (patrz etap 1 i 3). Przejęcie etapu jest ograniczone do pracowników działu przypisanego do etapu. |
| Wynik NG | Raport QAR plus wstrzymanie ocenionej ilości na buforze, reszta zlecenia idzie dalej. Decyzję podejmuje kontroler lub admin (patrz 4f): zwolnić, zwrócić do wskazanego etapu do poprawki z ponowną kontrolą, albo zezłomować (wpis do archiwum, nowy element wpisywany od początku). |
| Maszyna | Każdy etap z maszynami (lasery, wykrawarki, ewentualnie inne) wymaga wpisania, na której maszynie wykonano etap. Zapisywane przy przejęciu etapu i w historii. |
| Zgrzewanie i spawanie | Dwa osobne etapy. Element może przejść jeden z nich albo oba (trasa elementu). |
| Składanie | Zablokowane do uzyskania elementów, ale można składać częściowo: liczba sztuk wynika z elementów na buforze (patrz 4b). |
| Ponowny skan | Skan zlecenia (ZO + produkt), które już istnieje, nie tworzy nowego, tylko pokazuje je wraz z buforem lub buforami, na których leży (patrz 4e). |
| Powstanie zlecenia | Zlecenie powstaje przy pierwszym zeskanowaniu kodu QR zlecenia (ten sam kod `p`, `c`, `o`, `q`, który dziś tworzy listy w Kontroli Końcowej). Przy tym skanie rozkłada się na elementy z szablonu produktu. Bez ręcznego tworzenia. Produkt bez szablonu elementów: skan nie tworzy zlecenia, najpierw trzeba utworzyć szablon dla produktu (patrz 4d). |
| Ilości elementów | Szablon produktu zawiera tylko elementy i trasy. Ilość każdego elementu na sztukę produktu zależy od zlecenia (np. dwie pary drzwi), więc kontroler lub admin podaje ją przy skanie, przed utworzeniem zlecenia (0 oznacza, że element nie dotyczy tego zlecenia, pole nigdy nie ma wartości domyślnej). Wymagana ilość elementu to `q` razy ta liczba. |
| Poziomy etapów | Etapy od wycinania do **kompletacji** dotyczą elementów. Od **składania** zlecenie jest liczone w sztukach gotowego produktu: składanie zużywa elementy i tworzy sztuki produktu, kontrola końcowa i pakowanie dotyczą produktu (patrz 4g). |
| Koniec zlecenia | Po kontroli końcowej ilość trafia na bufor Pakowania. Pracownik ją przejmuje i oznacza jako **"Spakowane"**. Gdy spakowano całe `q`, zlecenie ma status Zakończone. |
| Korekta zlecenia | Tylko admin zmienia ilość całego zlecenia (`q`, z przeliczeniem wymaganych ilości) oraz anuluje i przywraca zlecenie (z zachowaniem historii). Kontroler i admin korygują elementy zlecenia: ich ilości, trasy, dopisanie i usunięcie. |
| Lista produkcyjna (Excel) | Nowy moduł działa osobno, bez zapisu do kolumn etapów w Liście produkcyjnej. |
| Kontrola końcowa | "Ostatni bufor" to bufor etapu Kontrola końcowa. Pierwszy wpis zlecenia na ten bufor tworzy listę kontrolną Kontroli Końcowej (patrz 4c). |

## 2a. Zmiana planu: wózki bez etapu (2026-10-20)

Decyzja z produkcji: bufory nie są ściśle przypisane do etapów. Operator każdego etapu sam pobiera wózek (bufor odstawczy pobranych elementów), a następnie odstawia wykonane elementy na ten sam albo inny wolny wózek (wózków jest mało). Ustalenia:

- Kontrola następuje **po wykonanym etapie** (punkty i flaga obowiązkowa należą do etapu wykonanego).
- Po odstawieniu na wózek ilość czeka na **następny etap trasy**, operator może wskazać dowolny późniejszy etap.
- System nie pokazuje, który wózek jest wolny, wózek dobiera operator.
- Na wózku mogą leżeć różne zlecenia i etapy bez ograniczeń.
- Realizacja: `StockPosition.stage_id` i `Movement.wait_stage_id` niosą etap oczekiwania, bufor traci etap (patrz CLAUDE.md, sekcja „Wózki bez etapu”). Zapisy o buforach przypisanych do etapów w dalszej części planu i w opisach etapów 1 do 4 są nieaktualne.

## 3. Etapy (nazwy i kolejność edytowalne)

1. Wycinanie laserowe (2 lasery) i wykrawarki (2 wykrawarki)
2. Gięcie (nie każdy element)
3. Przygotowanie, pemowanie (nie każdy)
4. Zgrzewanie (nie każdy)
5. Spawanie (nie każdy)
6. Szlifowanie, czyszczenie (nie każdy)
7. Mycie (nie każdy)
8. Malowanie (nie każdy)
9. Kompletacja
10. Składanie
11. Kontrola końcowa
12. Pakowanie

Numeracja jest przykładowa i edytowalna. Zgrzewanie i spawanie są teraz osobnymi etapami.

Etapy 1 do 9 (do kompletacji włącznie) dotyczą elementów, etapy 10 do 12 (składanie, kontrola końcowa, pakowanie) dotyczą gotowego produktu.

Każdy etap ma własny bufor (może być więcej niż jeden, np. po jednym przy każdej maszynie).

## 4. Szkic modelu danych

- `ProductionStage`: nazwa, kolejność, aktywny, poziom (element lub produkt), flaga "kontrola obowiązkowa", powiązanie z jednym działem (`ProductionDepartment`), z którego pochodzą pracownicy etapu.
- `Machine`: etap, nazwa (np. Laser 1, Wykrawarka 2), aktywna. Etap z maszynami wymaga wyboru maszyny przy przejęciu.
- `Buffer`: etap (może być nieprzypisany: etykiety istnieją wcześniej niż przypisanie), nazwa, numer, kod (dokładnie to, co jest w QR i kodzie kreskowym etykiety, unikalny, bez spacji i wielkimi literami), aktywny. Wszystkie pola edytowalne tylko w ustawieniach.
- `ProductTemplate` i `ProductComponent`: produkt (wzorzec nazwy), element i trasa (wybór etapów). Bez ilości, bo ta zależy od zlecenia.
- `WorkOrder`: ZO, produkt, klient, ilość `q`, skąd (skan QR), status. Zastępuje `ControlRecord` jako "zlecenie".
- `WorkOrderComponent`: element zlecenia, ilość na sztukę produktu (podana przy skanie), wymagana ilość (q razy ta liczba), trasa (kopia z szablonu, edytowalna).
- `StockPosition`: element zlecenia (lub gotowy produkt od składania), bufor lub stan "w realizacji", ilość, status (`ok`, `wstrzymane`, `do_poprawki`).
- `Movement`: z, do, element, ilość, maszyna (gdy etap ją ma), pracownik (`DepartmentEmployee`), czas, typ (wpis, przejęcie, przeniesienie, cofnięcie). Pełna historia i podstawa rozliczenia.
- `StageControl`: zlecenie, bufor (kontrola odbywa się na buforze etapu), rodzaj (plan zlecenia lub doraźna), ilość objęta kontrolą, punkty, wynik, powiązanie z QAR. Kontrola bez wyniku OK blokuje przejęcie.
- `ScrapRecord` (archiwum zezłomowanych i powtórnie wykonanych elementów): zlecenie, element, ilość, etap, powód (z kontroli), kto i kiedy zezłomował, powiązanie z ponownym wykonaniem.
- Punkty kontrolne `ControlPoint` przypisane do etapu (dziś jedna wspólna lista, która trafia do etapu Kontrola końcowa).

Niezmiennik rozliczenia: dla elementu zlecenia suma ilości na buforach, w realizacji i na zakończonym etapie końcowym nie przekracza wymaganej ilości. Przeniesienie jest jedną transakcją (zdjęcie z A i dodanie do B).

## 4a. Ustalenia doprecyzowujące

- Kontrola jest wystawiana na zamówienie: admin lub kontroler zaznacza w planie kontroli zlecenia, na których etapach ma być kontrola. Odbywa się ona na buforze etapu, a kontrola "po etapie N" to kontrola na buforze N+1. Etap z ustawieniem "kontrola obowiązkowa" blokuje przejęcie bez wyniku OK niezależnie od planu.
- Wynik NG wstrzymuje tylko ocenioną ilość. Decyzję o niej podejmuje kontroler lub admin (patrz 4f).

## 4b. Kompletacja i składanie

- Składanie nie może rozpocząć się, dopóki na buforze składania nie ma wymaganych elementów.
- Liczba sztuk produktu, które można złożyć, to minimum po elementach z (ilość na buforze podzielona przez ilość elementu na sztukę), z zaokrągleniem w dół. Przykład: szafa wymaga 2 drzwi i 1 korpusu, są 5 drzwi i 3 korpusy, więc można złożyć 2 szafy.
- System blokuje przejęcie składania na ilość większą niż wynika z powyższego wzoru. Składanie częściowe jest dozwolone (np. 2 z 6 szafy), ustalone 2026-10-17. Elementy liczą się tylko z **zeskanowanego bufora** (ustalone 2026-10-18).

## 4c. Kontrola końcowa

- Ostatni bufor to bufor etapu Kontrola końcowa. Lista powstaje przy **pierwszym** wpisie na ten bufor, a kolejne wpisy tego samego zlecenia dopisują się do już utworzonej listy (nie tworzą nowych).
- Mechanizm: istniejące tworzenie list Kontroli Końcowej (szablon dopasowany po nazwie produktu, klucz QR `produkt|klient|ZO`, seria wg ilości `q`).
- Lista dotyczy **ilości wpisanej na bufor**, a nie całego `q`. Pierwszy wpis tworzy serię tylu list, ile sztuk wpisano. Kolejne wpisy dopisują do tej samej serii kolejne listy, **maksymalnie do ilości `q`** ze zlecenia. Numeracja serii to `i/q` (np. po pierwszym wpisie 3 sztuk: 1/6, 2/6, 3/6, po drugim wpisie 2 sztuk: 4/6, 5/6).
- Na buforze Kontroli końcowej jednostką jest **sztuka gotowego produktu** (po składaniu), nie element. Wpis powyżej `q` łącznie jest blokowany.
- Wymaga rozszerzenia istniejącego mechanizmu: dziś `from-qr` zawsze tworzy pełną serię `q` list naraz, więc potrzebna jest funkcja dopisywania do istniejącej serii (wspólny `batch_id`, rosnący `batch_index`, stałe `batch_total = q`).

## 4d. Szablon produktu przy skanie

- Skan kodu QR zlecenia, którego produkt nie ma szablonu elementów, nie tworzy zlecenia. Wyświetla komunikat z wymogiem utworzenia szablonu dla produktu.
- Przycisk "Utwórz szablon" jest dostępny **tylko dla kontrolera i administratora** (od razu z komunikatu o braku szablonu). Stanowisko ze wspólnym kontem dostaje tylko komunikat, że szablon musi założyć kontroler.
- Po utworzeniu szablonu ponowny skan tworzy zlecenie.

## 4e. Ponowny skan zlecenia

- Skan kodu QR zlecenia (para ZO + produkt, jak dziś w Kontroli zamówień, bez względu na wielkość liter i nadmiarowe spacje), które już jest w systemie, otwiera istniejące zlecenie.
- Widok pokazuje, na którym buforze lub buforach leży każdy element i w jakiej ilości, ile jest w realizacji i ile ukończonych etapów.
- Dopiero z tego widoku można wpisać ilość na bufor, przejąć etap lub przenieść ilość.
- Nowe zlecenie powstaje wyłącznie wtedy, gdy pary ZO + produkt nie ma w systemie (i produkt ma szablon).

## 4f. Decyzje o ilości z wynikiem NG

Wstrzymana ilość czeka na decyzję kontrolera lub admina. Do wyboru:
- **Zwolnij:** ilość wraca do obiegu jako OK (decyzja kontrolera, np. uwaga niekrytyczna).
- **Zwróć do poprawki:** kontroler wskazuje etap, na który ilość wraca do wykonania. Po poprawce ilość przechodzi ponownie kontrolę (ponowna kontrola po poprawce).
- **Zezłomuj:** element nie nadaje się do poprawki. Ilość trafia do archiwum elementów zezłomowanych i powtórnie wykonanych (`ScrapRecord`), wymagana ilość elementu wraca do uzupełnienia, a nowy element jest wpisywany od początku na bufor i przechodzi etapy jak każdy inny.

Każda decyzja zostaje w historii (kto, kiedy, powód). Dostęp do archiwum: stan widzą wszyscy (jak reszta stanu produkcji).

## 4g. Elementy i produkt, składanie jako przejście poziomu

- Trasa elementu obejmuje etapy do kompletacji. Ilości elementów liczone są w sztukach elementów.
- Składanie zużywa elementy z bufora składania (według wzoru z 4b) i tworzy sztuki gotowego produktu (`StockPosition` produktu). Od tego miejsca stan zlecenia liczony jest w sztukach produktu do `q`.
- Kontrola końcowa i pakowanie operują na sztukach produktu (kontrola końcowa tworzy listy według 4c).

## 4h. Zakończenie, korekta i anulowanie zlecenia

- Pakowanie: pracownik przejmuje ilość z bufora Pakowania i oznacza ją jako "Spakowane". Spakowane całe `q` kończy zlecenie (status Zakończone).
- Tylko admin zmienia ilość całego zlecenia `q` (nie poniżej już wpisanych) i anuluje całe zlecenie. Kontroler i admin korygują elementy zlecenia (ilości, trasy, dopisanie, usunięcie). Anulowanie zachowuje historię ruchów.
- Skan kodu QR anulowanego zlecenia pokazuje je jako anulowane i nie tworzy nowego.

## 5. Plan działań (kolejność)

1. **Fundament.** Edytowalne etapy i bufory w Ustawieniach produkcji, etykiety QR buforów (PDF), nowe tabele, migracja.
2. **Elementy produktu.** Szablony elementów i tras per produkt, rozbicie zlecenia na elementy ze skanu QR (`p`, `c`, `o`, `q`), korekta elementów przy zleceniu.
3. **Wpis na bufor.** Skan plakietki pracownika, skan bufora i zlecenia, wybór elementu i ilości.
4. **Przejęcie i przeniesienie.** Przejęcie etapu z wyborem maszyny, wpis na bufor następnego etapu z trasy (pomijanie etapów spoza trasy), częściowe ilości, blokada składania do skompletowania elementów, cofanie błędnych wpisów, rozliczenie.
5. **Kontrole etapów.** Punkty per etap, plan kontroli zlecenia, kolejka kontrolera, OK/NG, QAR, wstrzymanie, decyzje o ilościach z NG (zwolnienie, poprawka, złomowanie z archiwum). Automatyczne tworzenie listy Kontroli Końcowej po wpisie na ostatni bufor.
6. **Śledzenie i widoki.** Macierz zlecenie, element i etap z ilościami. Widok bufora ("co leży"), historia ruchów, filtry, widok dla operatora.
7. **Wycofanie Kontroli zamówień.** Migracja lub archiwizacja danych `ControlRecord`, usunięcie starego modułu, aktualizacja menu, ról i dokumentacji.

Każdy krok: rozpoznanie, testy, akceptacja, commit, push, deploy.

## 6. Pytania otwarte

Wszystkie wcześniejsze pytania (do nr 20) są rozstrzygnięte i wpisane w sekcje 2 i 4. Ostatnia tura (2026-10-18) rozstrzygnęła: blokada tylko na etapach z kontrolą obowiązkową, kontrola "po etapie" na buforze następnego etapu, poziomy elementów i produktu, koniec zlecenia przez "Spakowane", trzy decyzje dla NG z archiwum złomu, zawsze plakietka pracownika, korekta i anulowanie tylko przez admina, Lista produkcyjna osobno.

Rozstrzygnięte w ostatniej turze: korekty elementów, tras, ilości i anulowanie zlecenia tylko admin, stare dane Kontroli zamówień usuwamy z kopią bazy.

Brak pytań otwartych. Nowe zadania i zmiany dopisujemy poniżej w miarę zgłaszania.

## 7. Ryzyka

- Największa zmiana struktury od początku projektu: nowy model zleceń zastępuje `ControlRecord`.
- Praca na stanowisku wymaga szybkiego skanowania (kamera telefonu lub skaner ręczny), więc interfejs na telefon jest wymagany od pierwszej wersji.
- Rozliczanie ilości przy częściowych przeniesieniach wymaga transakcji i testów obejmujących przypadki graniczne.

## 8. Rozpis etapów realizacji

Rozmiar: M (około dnia), L (kilka dni). Każdy etap kończy się wdrożeniem na serwer i jest używalny sam w sobie. Nowy moduł rozwijamy pod nowym adresem (np. `/produkcja-sledzenie`), a obecna Kontrola zamówień działa do etapu 8.

### Etap 1. Fundament: etapy, maszyny, bufory (rozmiar M)

Cel: administrator definiuje etapy produkcji, maszyny i bufory, a istniejące etykiety buforów są rozpoznawane.

Zadania:
- Modele `ProductionStage` (nazwa, kolejność, aktywny, poziom, kontrola obowiązkowa, dział), `Machine`, `Buffer`. Tabele przez `db.create_all()`, dodanie kolumny z tokenem do `department_employees` przez `ALTER` w `_migrate_schema()`.
- Dane startowe (jednorazowo, bez nadpisywania): 12 etapów z sekcji 3, maszyny Laser 1, Laser 2, Wykrawarka 1, Wykrawarka 2 przy etapie Wycinanie, nowe działy Wycinanie, Przygotowanie, Kompletacja i Pakowanie, powiązanie etapów z działami po nazwie (Gięcie, Zgrzewanie, Spawanie, Czyszczenie, Mycie, Malarnia, Składanie, Kontrola Jakości).
- Strona "Etapy i bufory" w Ustawieniach produkcji: edycja etapów (nazwa, kolejność, aktywny, dział, poziom, kontrola obowiązkowa), maszyn i buforów per etap.
- Bufory: dodawanie pojedyncze i zbiorcze (zakres kodów, np. `A001` do `A010`, aby od razu wczytać 60 istniejących etykiet), przypisanie do etapu, edycja nazwy, numeru i kodu, unikalność kodu, ostrzeżenie przy zmianie kodu bufora z historią lub etykietą.
- Wyszukiwanie bufora po kodzie (ten sam kod zeskanowany kamerą lub skanerem ręcznym, normalizacja bez spacji i wielkimi literami), przygotowane pod etap 3.
- Akcja "przenieś do działu" dla pracowników działów (Dział i pracownicy), aby przenieść pracowników do działu Wycinanie.
- Plakietki QR dla pracowników działów: token w `DepartmentEmployee` (generowany dla wszystkich istniejących osób przy migracji), wydruk pojedynczy i zbiorczy PDF (także per dział), unieważnienie przez "Nowy kod". Przedrostek kodu plakietki: `PSH-DEP:` (inny niż `PSH-EMP:` z Listy produkcyjnej, żeby kody się nie myliły).
- Opcjonalny generator etykiet buforów PDF w formacie istniejących (50x100 mm: QR, Code128, numer) dla nowych buforów.
- Menu: zakładki w Ustawieniach produkcji (Działy i pracownicy, Ścieżki produkcyjne, Etapy i bufory).

Testy: dane startowe i ich idempotencja, CRUD etapów, maszyn i buforów, zbiorcze dodawanie zakresu kodów, unikalność i normalizacja kodu, wyszukiwanie bufora po kodzie, token plakietki i unieważnienie, przeniesienie pracownika, uprawnienia (admin i kontroler tak, pozostali nie), PDF zwraca plik.

Akceptacja: wczytane 60 kodów z istniejących etykiet, bufor przypisany do etapu, plakietka pracownika działu wydrukowana i rozpoznawana po tokenie.

### Etap 2. Elementy produktu i zlecenie (rozmiar L)

Cel: skan kodu QR zlecenia tworzy zlecenie rozłożone na elementy z szablonu produktu.

Zadania:
- Modele `ProductTemplate`, `ProductComponent` (nazwa elementu i trasa jako wybór etapów, bez ilości), `WorkOrder`, `WorkOrderComponent` (ilość na sztukę podawana przy skanie, wymagana ilość = q razy ta liczba, trasa jako edytowalna kopia).
- Ekrany szablonów produktów w Ustawieniach produkcji: lista, tworzenie, edycja (elementy, ilości, trasy). Dopasowanie szablonu po nazwie produktu tym samym algorytmem tokenów, który już istnieje dla szablonów kontroli i ścieżek.
- Skan QR zlecenia (`p`, `c`, `o`, `q`, parser z naprawą polskich znaków jak dziś): para ZO + produkt, istniejące zlecenie otwiera się (sekcja 4e), brak szablonu daje komunikat z przyciskiem "Utwórz szablon" tylko dla kontrolera i admina (sekcja 4d). Dla nowego zlecenia skan pokazuje elementy szablonu z polami ilości na sztukę produktu (0 = nie dotyczy, bez wartości domyślnej), a zlecenie powstaje po zatwierdzeniu.
- Widok zlecenia: elementy, wymagane ilości, trasy. Zmiana ilości `q` i anulowanie tylko przez admina, korekta elementów, ich ilości i tras przez kontrolera i admina (patrz 4h).
- Obecne ścieżki produkcyjne (`RoutingTemplate`) jako punkt wyjścia do tras elementów (opcjonalny jednorazowy import).

Testy: tworzenie zlecenia ze skanu, powtórny skan, brak szablonu (dwie role), przeliczenie ilości, korekta elementów, dopasowanie po nazwie.

Akceptacja: skan prawdziwego kodu QR zlecenia tworzy zlecenie z elementami i poprawnymi ilościami.

### Etap 3. Wpis na bufor i stan buforów (rozmiar L)

Stan: zrealizowany lokalnie 2026-10-18, ustalenia z realizacji: kody skanowane w dowolnej kolejności z automatycznym rozpoznaniem, wpis na dowolny etap z trasy elementu, wycofanie wpisu od razu w tym etapie (admin i kontroler dowolny, operator i stanowisko własny, bez limitu czasu), rola `produkcja` otwiera nowe stanowisko, kontroler i admin mogą wpisywać bez plakietki.

Cel: pracownik wpisuje ilość elementu zlecenia na bufor, system trzyma stan i historię.

Zadania:
- Modele `StockPosition` i `Movement`. Niezmiennik rozliczenia: suma ilości dla elementu nie przekracza wymaganej.
- Stanowisko: skan plakietki pracownika działu (`DepartmentEmployee`, sesja na urządzeniu jak w `produkcja/scan.py`), skan bufora, skan zlecenia, wybór elementu i ilości. Wspólne konto stanowiska (rola `produkcja`) i konto operatora.
- Walidacje: bufor musi należeć do etapu z trasy elementu (przy pierwszym wpisie do pierwszego etapu trasy), ilość od 1 do pozostałej niewpisanej.
- Widok bufora ("co leży": zlecenie, element, ilość), widok zlecenia z położeniem elementów (sekcja 4e), widok tylko do odczytu dla wszystkich ról (pytanie 16).
- Menu i dashboard dla ról stanowiska i operatora.

Tożsamość pracownika w ruchach: `DepartmentEmployee` (ustalone).

Testy: wpis poprawny, ilość ponad limit, zły etap, rozliczenie sum, odczyt przez role, historia ruchu.

Akceptacja: na stanowisku da się wpisać zlecenie na bufor lasera i zobaczyć je w widoku bufora i zlecenia.

### Etap 4. Przejęcie etapu i przenoszenie (rozmiar L)

Stan: zrealizowany lokalnie 2026-10-18. Ustalenia z realizacji: wpis po etapie na **dowolny późniejszy etap** trasy elementu (po trasie na etap składania), kontroler i admin przejmują i przenoszą bez plakietki i bez ograniczenia działu (maszynę nadal wskazują), składanie tylko z elementów leżących na **zeskanowanym buforze**, element można wpisać z zewnątrz także na bufor składania, pakowanie kończy zlecenie (status Zakończone) po spakowaniu całego `q`, wycofanie obejmuje wszystkie ruchy. **Alerty i "Zleć decyzję" przeniesione do etapu 5**, bo blokady pojawią się dopiero z kontrolami.

Cel: pełny cykl bufor, przejęcie, bufor następnego etapu, z maszyną i wycofaniem.

Zadania:
- Przejęcie etapu: zdjęcie ilości z bufora do stanu "w realizacji" (pracownik, czas), wybór maszyny wymagany dla etapów z maszynami.
- Wpis po etapie na bufor następnego etapu trasy elementu (pomijanie etapów spoza trasy). Przeniesienie jest jedną transakcją.
- Częściowe ilości na każdym kroku.
- Kompletacja i składanie (sekcje 4b i 4g): limit sztuk do złożenia z elementów na buforze, składanie częściowe, blokada ponad limit, zużycie elementów i utworzenie sztuk gotowego produktu na buforze kolejnego etapu.
- Wycofanie wpisu: admin i kontroler dowolny, operator tylko własny, bez limitu czasu. Nie wycofuje się wpisu, od którego odeszły dalsze ruchy.
- Pakowanie: przejęcie ilości z bufora Pakowania i oznaczenie "Spakowane", status Zakończone po spakowaniu całego `q` (4h).
- (Przeniesione do etapu 5: alert dla operatora o braku możliwości przejęcia i przycisk "Zleć decyzję" z wnioskiem `DecisionRequest`.)
- Przejęcie etapu tylko dla pracowników działu przypisanego do etapu.

Testy: pełna ścieżka elementu przez kilka etapów, pominięcie etapów spoza trasy, limit składania (przykład z 4b), wycofanie i jego ograniczenia, transakcyjność przeniesienia.

Akceptacja: jeden element przechodzi od lasera do kompletacji, szafa składa się częściowo, wycofanie działa.

### Etap 5. Kontrole etapów (rozmiar XL, trzy wdrożenia 5a, 5b, 5c)

Stan: opisany 2026-10-19, decyzje poniżej, kod jeszcze nie napisany. Etap jest duży, więc dzielimy go na trzy samodzielnie wdrażane części.

#### Ustalenia (2026-10-19)

| Temat | Decyzja |
|---|---|
| Kiedy powstaje kontrola | Gdy ilość wchodzi na bufor etapu z kontrolą: etap z ustawieniem "kontrola obowiązkowa" albo etap wskazany w planie kontroli zlecenia. Dotyczy wpisu, wpisu po etapie i zwrotu z poprawki. Ilość dostaje status `do_kontroli` i nie można jej przejąć. |
| Plan kontroli | Domyślny plan (etapy) w szablonie produktu, kopiowany do zlecenia przy tworzeniu. Kontroler i admin korygują go na stronie zlecenia. Flaga "kontrola obowiązkowa" na etapie działa niezależnie od planu. |
| Partie | Scalone: jedna oczekująca kontrola na element i bufor. Nowa partia dopisuje się do oczekującej kontroli, dopóki kontroler jej nie otworzy. Otwarta kontrola ("w toku") jest zamknięta na nowe ilości, kolejna partia tworzy nową kontrolę. |
| Ocena | Punkty kontrolne etapu (OK lub NG, uwagi) plus podział ilości: kontroler wpisuje, ile sztuk jest zgodnych, a ile niezgodnych (suma równa kontrolowanej ilości). |
| Wynik | Ilość zgodna dostaje status `ok`. Ilość niezgodna dostaje status `wstrzymane` i **każdy punkt NG tworzy osobny raport QAR** (jak w Kontroli zamówień). |
| Punkty per etap | Osobna lista punktów kontrolnych dla każdego etapu, edytowana w Ustawieniach produkcji (admin i kontroler). Obecna lista z Kontroli zamówień jest jednorazowo kopiowana do etapu Kontrola końcowa. Etap bez punktów: kontrola bez listy (sam podział ilości i uwagi). |
| Decyzje o wstrzymanej ilości | Kontroler lub admin: zwolnij, zwróć do poprawki albo zezłomuj. |
| Zwrot do poprawki | Kontroler wskazuje etap i **bufor** tego etapu. Ilość trafia tam ze statusem `do_poprawki` (można ją przejąć, dział etapu poprawia). |
| Ponowna kontrola | Automatyczna: ilość po poprawce niesie znacznik ponownej kontroli, więc przy następnym wpisie na bufor system wymusza dla niej kontrolę niezależnie od planu. |
| Złomowanie | Wpis do archiwum zezłomowanych i powtórnie wykonanych elementów (`ScrapRecord`), wymagana ilość elementu wraca do uzupełnienia (ilość zezłomowana odlicza się od wpisanej), nowy element wpisuje się od początku. |
| Kontrola doraźna | Kontroler lub admin wybiera bufor, element i ilość leżące na buforze ze statusem `ok`. Ilość przechodzi do `do_kontroli`. |
| Zleć decyzję | Operator lub stanowisko przy zablokowanej ilości dostaje komunikat z powodem i przycisk "Zleć decyzję". Wniosek (`DecisionRequest`) z alertem trafia do kontrolerów i adminów. |
| Wymuszenie | Kontroler lub admin zatwierdza wniosek: ilość zostaje **zwolniona jak po wyniku OK** z adnotacją "zwolnione decyzją". Albo odrzuca wniosek. Operator widzi wynik przy swoich wnioskach. |
| Wycofanie wpisu | Ilość czekająca na kontrolę: wycofanie wpisu anuluje oczekującą kontrolę (lub zmniejsza jej ilość). Po otwarciu lub wykonaniu kontroli wycofanie jest blokowane. |

#### Model danych (szkic)

- `StockPosition.status`: `ok`, `do_kontroli`, `wstrzymane`, `do_poprawki`. `WorkInProgress` dostaje znacznik `recheck` (niesiony z `do_poprawki`).
- `StageControlPoint` (etap, tytuł, opis, kolejność, aktywny): punkty per etap. Migracja kopiuje punkty ze starego modułu do etapu Kontrola końcowa.
- `StageControl` (zlecenie, element, bufor, etap, rodzaj: obowiązkowa, plan, doraźna, ponowna, status: oczekuje, w_toku, zakończona, anulowana, ilość kontrolowana, ilość zgodna, ilość niezgodna, kontroler, czasy) i `StageControlResult` (kopia punktu, OK lub NG, uwagi, numer QAR).
- `ProductTemplateControl` i `WorkOrderControl` (szablon lub zlecenie, etap): plan kontroli.
- `DecisionRequest` (zlecenie, element, bufor, etap, ilość, zgłaszający, status, decydujący, czas, uwaga).
- `ScrapRecord` (zlecenie, element, etap, ilość, powód z kontroli, kontrola, kto zezłomował, czas).
- Nowe rodzaje ruchów: `kontrola`, `zwolnienie`, `zwrot`, `zlomowanie`, `wymuszenie`.

#### 5a. Kontrole planowane i blokada (rozmiar L)

Stan: zrealizowane lokalnie 2026-10-19. Odstępstwo od szkicu: status kontroli `w_toku` nie jest potrzebny, bo ocena sprawdza widzianą ilość (`expected`), a nowsza partia po wydzieleniu ocenionej części czeka dalej w oryginalnej kontroli. Statusy kontroli: `oczekuje`, `zakonczona`, `anulowana`.

- Punkty kontrolne per etap (model, migracja kopii, ekran edycji w Ustawieniach produkcji).
- Plan kontroli w szablonie produktu (zaznaczane etapy) i na zleceniu (kopiowany przy tworzeniu, edytowalny przez kontrolera i admina).
- Wejście na bufor etapu z kontrolą tworzy lub zasila oczekującą kontrolę i nadaje status `do_kontroli` (wpis, wpis po etapie).
- Kolejka kontrolera `/kontrolaprodukcji/kontrole`: oczekujące kontrole z filtrami, otwarcie, ocena punktów, podział ilości zgodnych i niezgodnych, zatwierdzenie.
- Wynik: zgodne na `ok`, niezgodne na `wstrzymane`, raporty QAR za każdy punkt NG.
- Przejęcie etapu tylko dla ilości `ok` (komunikat z powodem dla pozostałych), widoki buforów i zlecenia pokazują statusy ilości.
- Alert dla kontrolerów i adminów o nowej oczekującej kontroli.
- Testy: tworzenie i scalanie kontroli, ocena, QAR, blokada przejęcia, wycofanie wpisu z kontrolą.

#### 5b. Decyzje, kontrole doraźne i archiwum złomu (rozmiar M)

Stan: zrealizowane lokalnie 2026-10-20. Ustalenia przy realizacji: zwrot do poprawki wskazuje etap i wózek (kod), ponowna kontrola dotyczy punktów etapu, który zawinił, kontroler doraźną wybiera etap punktów kontrolnych z listy. Decyzje działają na dowolną część ilości, gotowego produktu nie złomuje się.

- Decyzje o ilości `wstrzymane`: zwolnij, zwróć do poprawki (wybór etapu i bufora, status `do_poprawki`, znacznik ponownej kontroli), zezłomuj (archiwum, odliczenie od wpisanej ilości).
- Automatyczna ponowna kontrola po poprawce.
- Kontrola doraźna na dowolnym buforze (element i ilość).
- Archiwum zezłomowanych i powtórnie wykonanych elementów (lista z filtrami, widoczna dla wszystkich).
- Testy: każda decyzja, ponowna kontrola, złomowanie i rozliczenie, kontrola doraźna.

#### 5c. Wnioski o decyzję i alerty (rozmiar M)

- Przycisk "Zleć decyzję" na stanowisku przy zablokowanej ilości, `DecisionRequest`, lista wniosków dla kontrolera i admina.
- Zatwierdzenie (zwolnienie jak po OK z adnotacją) i odrzucenie, alerty dla kontrolerów i adminów, lista własnych wniosków na stanowisku.
- Testy: zgłoszenie, zatwierdzenie, odrzucenie, uprawnienia, blokady po zmianie stanu.

Akceptacja etapu: ilość wchodząca na obowiązkowy etap czeka na kontrolę i nie da się jej przejąć, kontroler dzieli ją na zgodną i niezgodną, niezgodna tworzy QAR i czeka na decyzję, a decyzje (zwolnij, zwróć, zezłomuj) działują zgodnie z opisem.

#### Pytania otwarte do etapu 5

- Brak (wszystkie ustalone 2026-10-19). Do dopisania przy realizacji: układ ekranu oceny kontroli i widok kolejki na telefonie.

### Etap 6. Kontrola Końcowa automatycznie (rozmiar M)

Cel: wpis na bufor etapu Kontrola końcowa tworzy listę Kontroli Końcowej (sekcja 4c). Jest to dodatek: ręczne tworzenie list skanem QR i raportów w module `/kontrola` zostaje bez zmian (decyzja 2026-10-20). Unikanie dubli (decyzja 2026-10-20): automat nie tworzy ani nie dopisuje list dla zlecenia, które ma już listy utworzone w module `/kontrola` (po kluczu `produkt|klient|ZO`), i odwrotnie, ręczne tworzenie nie jest blokowane.

Zadania:
- Funkcja dopisywania do serii list (wspólny `batch_id`, rosnący `batch_index`, stałe `batch_total` równe `q`), obok obecnego tworzenia pełnej serii.
- Wyzwalacz: pierwszy wpis na bufor Kontroli końcowej tworzy serię na wpisaną ilość, kolejne wpisy dopisują do `q`, wpis ponad `q` blokowany.
- Powiązanie listy ze zleceniem (klucz QR `produkt|klient|ZO`), wynik listy widoczny w widoku zlecenia.

Testy: pierwszy wpis, dopisywanie do serii, limit `q`, spójność numeracji, produkt bez szablonu listy kontrolnej, zlecenie z już istniejącymi listami ręcznymi (automat nic nie tworzy).

Akceptacja: przykład z planu (q równe 6, wpis 3 szt., potem 2 szt.) daje listy 1/6 do 5/6.

### Etap 7. Widoki śledzenia i raporty (rozmiar M)

Cel: czytelny podgląd całej produkcji dla wszystkich ról.

Zadania:
- Macierz zlecenie, element i etap z ilościami (gotowe, w realizacji, na buforze, wstrzymane).
- Filtry: zlecenie, klient, etap, bufor, status. Tabela na komputerze i karty na telefonie.
- Historia ruchów zlecenia (kto, kiedy, maszyna, z którego bufora na który).
- Dashboard: zlecenia w toku, wstrzymane, oczekujące kontrole.

Testy: agregacje ilości, filtry, uprawnienia odczytu.

### Etap 8. Porządki (rozmiar S) - ZMIANA 2026-10-20

Decyzja: moduł Kontroli Końcowej (`/kontrola`, wcześniej Kontrola zamówień) **zostaje i działa równolegle** do nowego modułu, bez zmian: tworzenie listy skanem kodu QR i ręczne tworzenie raportu działają jak dotąd. Nic w nim nie jest usuwane (ani kod, ani dane `ControlRecord`, ani rola Operator, ani menu).

Zadania:
- Aktualizacja CLAUDE.md, dokumentacji i planu.
- Ewentualne drobne porządki w nowym module (np. przestarzałe pole `Buffer.stage_id`).

### Kolejność i zależności

1, potem 2, potem 3, potem 4, potem 5 i 6 (6 zależy od 3 i 4, 5 od 4), potem 7, na końcu 8. Etapy 1 i 2 nie zmieniają dotychczasowej pracy w aplikacji, więc można je wdrażać bez ryzyka.
