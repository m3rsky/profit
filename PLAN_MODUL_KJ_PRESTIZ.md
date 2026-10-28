# Plan: moduł poglądowy "KJ Prestiż" (prezentacja docelowego procesu kontroli jakości)

Stan: materiał wejściowy do zaplanowania modułu, 2026-10-28. Nic nie jest jeszcze zaimplementowane.
Źródła: podsumowanie spotkań ze Streamsoft (Artur Drela), dokument "System kontroli jakości KUBIAK SYSTEM, stan obecny i wymagania dla Streamsoft", dokumentacja Streamsoft Prestiż, obecny stan profit (`CLAUDE.md`).

---

## 1. Cel modułu

Osobny, **poglądowy** moduł w profit, który pokazuje na żywo, jak ma działać docelowy proces kontroli jakości w Prestiżu i aplikacji Android budowanej przez Streamsoft.

- Służy do **prezentacji** dla Zarządu i dla Streamsoft (pokaz procesu, ekranów i raportów zanim Streamsoft zacznie programować).
- Pracuje na **danych demonstracyjnych**, nie łączy się z Prestiżem.
- **Nie zmienia** istniejących modułów (Kontrola etapowa, Kontrola Końcowa, QAR, Lista produkcyjna). Może z nich czytać (np. pracownicy działów), ale nic w nich nie zapisuje.
- Po prezentacji ma się dać łatwo ukryć (uprawnienia modułu) albo usunąć (osobny pakiet i osobne tabele).

Moduł ma być **prosty w obsłudze na telefonie**. Wniosek z października 2026: rozbudowany moduł śledzenia produkcji został wycofany po 3 dniach, bo był zbyt skomplikowany na telefonie. Docelowy przepływ kontrolera: skan, operacja, materiały, ocena, zakończ.

---

## 2. Kontekst: dlaczego standard Prestiża nie wystarcza

- Standardowa kontrola jakości w Prestiżu ([definicja kontroli](https://dokumentacjaprestiz.streamsoft.pl/pages/viewpage.action?pageId=222168420), [procedura i wyniki](https://dokumentacjaprestiz.streamsoft.pl/pages/viewpage.action?pageId=222168427)) wiąże kontrolę z **jednym obiektem**: dostawą, kartoteką, zleceniem produkcyjnym albo zasobem.
- Standard ma:
  - punkty kontroli z parametrami (typ wartości, min/max, tolerancja procentowa, oznaczenie przekroczenia na czerwono);
  - kontrolę tabelową albo graficzną (grafika z naniesionymi punktami);
  - akcje "poprawna" i "niepoprawna", które generują dokument (np. MM na magazyn), procedurę albo technologię naprawczą;
  - możliwość zakończenia kontroli, gdy błąd wyklucza obiekt.
- **KUBIAK** kontroluje **pojedyncze składowe** wyrobu (obudowa, drzwi, płyta montażowa, aparatura) osobno. Wadliwy element **wraca na wybraną operację** zleceniem naprawczym, a nie do magazynu. Dlatego Streamsoft buduje moduł dedykowany.

---

## 3. Ustalenia ze Streamsoft (spotkania, podsumowanie A. Dreli)

### Parametry konfiguracyjne w Prestiżu

| Nr | Ustalenie |
|---|---|
| K1 | Oznaczenie, która **operacja** podlega KJ. Po meldunku takiej operacji **nie powstaje od razu RW** na materiały detalu, żeby można było wygenerować zlecenie naprawcze. |
| K2 | Flaga na **materiałach** operacji, które podlegają kontroli. Materiały bez flagi nie pokazują się w aplikacji KJ. |
| K3 | Konfigurowalne **listy punktów kontrolnych** przypisywane do operacji. |
| K4 | **Grupowanie materiałów** do wspólnej kontroli albo przypisanie materiałów do konkretnych punktów. |

### Aplikacja

- Rozważane warianty:
  - panel meldunkowy na tablecie z Windows: tańszy, szybszy we wdrożeniu, większy ekran, ale słabsze zdjęcia i nieporęczny tablet;
  - aplikacja na Androida: mobilna i z dobrymi zdjęciami, ale sztywne schematy ekranów, dłuższe projektowanie i wyższa cena.
- **Wybrany kierunek: Android.**
- Aplikacja ma odwzorować obecną aplikację profit, w miarę możliwości technicznych.

### Zawroty procesowe

- Kontroler zgłasza zawrót dla wadliwego elementu i wskazuje **operację, na którą element ma wrócić**. Może też dodać operację ręcznie.
- Zgłoszenie generuje **zlecenie naprawcze** z wybranych operacji, które przechodzi przez produkcję jak każde inne zlecenie.

---

## 4. Wymagania wobec Streamsoft (z dokumentu dla Streamsoft)

Wymagania oznaczone "ponad ustalenia" nie padły w podsumowaniu spotkań i są do potwierdzenia.

### A. Moduł w Prestiżu
| Nr | Wymaganie |
|---|---|
| A1 | Parametry K1-K4. |
| A2 | Standardowe parametry punktu: typ wartości, min-max lub tolerancja, wyświetlanie zakresu, oznaczenie przekroczenia, grafika punktu. |
| A3 | Wstrzymanie RW do zakończenia kontroli, po OK rozchód materiałów jak dziś. |
| A4 | Punkty wspólne dla wszystkich operacji, punkty operacji i punkty materiału (ponad ustalenia). |
| A5 | Wynik przechowuje kopię treści punktu z chwili kontroli, edycja punktów nie zmienia historii (ponad ustalenia). |

### B. Aplikacja Android
| Nr | Wymaganie |
|---|---|
| B1 | Logowanie kontrolera, skan QR zlecenia (obecne etykiety: produkt, klient, ZO, ilość) albo wyszukanie zlecenia. |
| B2 | Wybór operacji, zaznaczenie jednego lub kilku materiałów z flagą KJ, lista punktów w sekcjach: wspólne i każdy materiał osobno. |
| B3 | Wynik punktu: OK / NOK / nie dotyczy, wartość zmierzona, uwagi, kilka zdjęć z aparatu (kluczowe przy NOK). |
| B4 | Osoba odpowiedzialna podpowiadana z meldunku operacji, z możliwością zmiany (ponad ustalenia). |
| B5 | Przy NOK: zgłoszenie zawrotu, wybór operacji docelowej, ręczne dodanie operacji, opis co poprawić. |
| B6 | Kontrola sprawdzająca po zleceniu naprawczym, historia wszystkich kontroli zlecenia i elementu. |
| B7 | Przerwanie kontroli, gdy błąd wyklucza element (jak w standardzie Prestiża). |
| B8 | Minimum kroków, obsługa jedną ręką w rękawicach, duże przyciski. |

### C. Zawroty i zlecenia naprawcze
| Nr | Wymaganie |
|---|---|
| C1 | Zawrót tworzy zlecenie naprawcze z wybranych operacji, powiązane z pierwotnym ZO, elementem i wynikiem kontroli. |
| C2 | Decyzja poprawka albo złom. Złom generuje zlecenie na nowy element i rozchód materiału (ponad ustalenia, tak działa procedura NG KUBIAK). |
| C3 | Status elementu widoczny w produkcji: w kontroli, zawrócony, w naprawie, do ponownej kontroli, zgodny. |
| C4 | Rejestracja przyczyny niezgodności i wykonawcy (statystyki NOK wg operacji, materiału, pracownika, przyczyny). |

### D. Dane i integracja
| Nr | Wymaganie |
|---|---|
| D1 | Dostęp do wyników kontroli dla narzędzi KUBIAK (API REST albo widok w bazie): kontrole, punkty, wyniki, zdjęcia, zawroty. |
| D2 | Zasilanie list punktów z zewnątrz (import lub API), np. punkty dobrane automatycznie z plików konstrukcyjnych (ponad ustalenia). |
| D3 | Eksport raportów do PDF i Excel. |

---

## 5. Proces docelowy

1. **Meldunek operacji** z flagą KJ. RW na materiały jest **wstrzymane**.
2. Kontroler w aplikacji: **skan QR zlecenia**.
3. **Wybór operacji** i zaznaczenie **materiałów z flagą KJ**.
4. **Ocena punktów** (sekcja Wspólne + sekcja każdego materiału): OK / NOK / ND, wartość, zdjęcia, uwagi. Wykonawca podpowiedziany z meldunku.
5. Decyzja:
   - **OK**: element zgodny, **RW materiałów** powstaje, element idzie dalej w procesie.
   - **NOK**: **zgłoszenie zawrotu** (operacja docelowa, dodatkowe operacje ręcznie, opis, przyczyna, decyzja poprawka/złom), a potem **zlecenie naprawcze**. Produkcja wykonuje operacje naprawcze, następuje **ponowny meldunek** i **kontrola sprawdzająca** (wraca do kroku 1).
6. Historia wszystkich kontroli zostaje przy zleceniu i elemencie.

---

## 6. Raporty do pokazania

| Raport | Zawartość | Odbiorca |
|---|---|---|
| Karta kontroli zlecenia | Wszystkie kontrole ZO: operacje, elementy, punkty, wyniki, wartości, zdjęcia, kontroler, wykonawca, daty. PDF. | Klient, archiwum |
| Rejestr niezgodności | NOK z przyczyną, elementem, operacją, wykonawcą, decyzją i statusem zlecenia naprawczego. | Dział jakości |
| "Dobrze za pierwszym razem" (FPY) | Odsetek elementów zgodnych w pierwszej kontroli wg operacji, działu, okresu. | Zarząd, kierownicy |
| Pareto niezgodności | Najczęstsze NOK wg punktu, przyczyny, elementu, operacji. | Jakość, technologia |
| Koszt zawrotów | Liczba zleceń naprawczych, czas i materiał na poprawki i złom. | Zarząd |
| Wyniki wg pracownika | NOK wg wykonawcy i działu (do szkoleń, nie do kar). | Kierownicy działów |
| Czas kontroli | Czas kontroli wg operacji i kontrolera. | Dział jakości |

Filtry: okres, klient, produkt, dział. Eksport do Excela.

---

## 7. Pytania otwarte (do Streamsoft, moduł poglądowy może pokazać warianty)

- Praca bez Wi-Fi na hali i późniejsza synchronizacja?
- Kto zatwierdza zawrót i zlecenie naprawcze: kontroler czy kierownik produkcji?
- Rozliczenie materiału przy złomie i nowym elemencie.
- Czy kontrola końcowa całej szafy też przechodzi do nowej aplikacji?
- Historia z profit: migracja do Prestiża czy archiwum w profit?
- Obecne etykiety QR zostają czy Prestiż drukuje własne?
- Forma dostępu do danych (D1) i czy jest w cenie.
- Wycena, harmonogram, pilotaż na jednym dziale.

---

## 8. Mapowanie pojęć: obecny profit a docelowy Prestiż

| Obecny profit (Kontrola etapowa) | Docelowy Prestiż / moduł poglądowy |
|---|---|
| Dział kontroli (Gięcie, Spawanie, Malowanie...) | Operacja z flagą KJ (K1) |
| Element kontroli (Drzwi, Korpus, Płyta...) | Materiał z flagą KJ, grupa materiałów (K2, K4) |
| Punkty ogólne / działu / elementu | Lista punktów: wspólne / operacji / materiału (K3, A4) |
| Para ZO + produkt ze skanu QR | Zlecenie produkcyjne (ZP) w Prestiżu, ten sam kod QR (B1) |
| Wykonawca (pracownik działu) | Pracownik z meldunku operacji (B4) |
| NG tworzy QAR | NOK tworzy zawrót i zlecenie naprawcze (C1) |
| Ponowna kontrola / kontrola sprawdzająca | Kontrola po meldunku zlecenia naprawczego (B6) |
| Brak | Wstrzymane RW, rozchód po OK (K1, A3) |

---

## 9. Zakres modułu poglądowego (propozycja do planu)

### 9.1 Konfiguracja (widok desktop, admin)
- **Operacje**: nazwa, kolejność, flaga KJ, przypisana lista punktów.
- **Materiały**: nazwa, flaga KJ, grupa (do wspólnej kontroli).
- **Punkty kontrolne**: treść, typ (OK/NOK albo wartość z min/max i jednostką), opcjonalna grafika, przypisanie: wspólny, do operacji, do materiału (lub do grupy).

### 9.2 Zlecenia produkcyjne demo
- ZP: numer, produkt, klient, ilość, trasa operacji w kolejności, materiały na każdej operacji.
- Przycisk **"Melduj operację"** (z wyborem pracownika). Po meldunku operacji z KJ: status "do kontroli" i **RW: wstrzymane**.
- Widok osi czasu zlecenia: meldunki, kontrole, zawroty, zlecenia naprawcze, RW.

### 9.3 Aplikacja KJ (widok mobilny)
- Układ telefonu (na komputerze podczas prezentacji wyświetlany w ramce telefonu, na telefonie pełny ekran).
- Kroki: skan QR lub wybór ZP, operacja (tylko zameldowane z KJ), materiały (checkboxy), punkty w sekcjach, OK / NOK / ND, wartość z oznaczeniem przekroczenia, zdjęcie, uwagi, wykonawca z meldunku, **Zakończ** albo **Przerwij (błąd wyklucza element)**.
- Duże przyciski, minimum ekranów (B8).

### 9.4 Zawrót i zlecenie naprawcze
- Przy NOK: ekran zawrotu, czyli operacja docelowa (z trasy ZP), dodanie operacji ręcznie, przyczyna (lista), opis, decyzja **poprawka / złom**.
- Powstaje **ZN** (np. `ZN-ZP-DEMO-001/1`) powiązane z ZP, materiałem i wynikiem. Złom: ZN na nowy element i symulowany rozchód materiału.
- Meldunek operacji ZN, potem kontrola sprawdzająca. Wynik OK zamyka ZN i zwalnia RW.
- Statusy elementu (C3): w kontroli, zawrócony, w naprawie, do ponownej kontroli, zgodny.

### 9.5 Raporty
Wszystkie z sekcji 6 na danych demo. Karta kontroli w PDF (ReportLab, DejaVu Sans, jak inne PDF w profit). Eksport Excel (openpyxl). Wykresy proste (FPY, Pareto).

### 9.6 Tryb prezentacji
- **"Wczytaj dane demo"** i **"Resetuj demo"** (tylko tabele modułu).
- Strona "Scenariusz" z krokami pokazu i linkami do odpowiednich ekranów.

---

## 10. Scenariusz pokazu (5-7 minut)

1. ZP-DEMO-001, szafa PSH 600x800x300, klient demo. Trasa: Wycinanie, Gięcie, Spawanie, Malowanie, Składanie. Materiały KJ: Korpus, Drzwi, Płyta montażowa.
2. Konfiguracja: pokazujemy operacje z flagą KJ, materiały z flagą i grupą, punkty wspólne / operacji / materiału.
3. Meldunek operacji Spawanie (pracownik ze spawalni). RW: wstrzymane.
4. Telefon: skan QR, operacja Spawanie, materiały Korpus + Drzwi. Korpus OK. Drzwi: spoina NOK, zdjęcie, uwaga.
5. Zawrót: operacja docelowa Spawanie, ręcznie dodane Szlifowanie, przyczyna "spoina niepełna", decyzja poprawka. Powstaje ZN.
6. Meldunek ZN, kontrola sprawdzająca Drzwi OK. RW zwolnione, element zgodny.
7. Raporty: karta kontroli PDF, rejestr niezgodności, FPY, Pareto, koszt zawrotów (dane demo z kilkunastu zleceń).

---

## 11. Model danych (propozycja, do weryfikacji w planie)

Osobne tabele z prefiksem `pkj_` we wspólnej bazie (zasada profit: jedna baza). Tworzone przez `db.create_all()`.

- `pkj_operations`: nazwa, kolejność, `kj_required`, aktywna
- `pkj_material_groups`: nazwa
- `pkj_materials`: nazwa, `kj_flag`, `group_id`
- `pkj_check_points`: treść, typ (`ok_nok` / `value`), jednostka, min, max, grafika, `operation_id` (null = wspólny), `material_id` / `group_id` (null = punkt operacji), kolejność, aktywny
- `pkj_orders`: numer, typ (`ZP` / `ZN`), `parent_id` (ZN do ZP), produkt, klient, ilość, status, `qr_key`
- `pkj_order_operations`: zlecenie, operacja, kolejność, status, `reported_at`, `employee_id`, `rw_status` (`brak` / `wstrzymane` / `wydane`)
- `pkj_order_materials`: zlecenie, operacja zlecenia, materiał, ilość, status elementu (C3)
- `pkj_inspections`: zlecenie, operacja zlecenia, kontroler, wykonawca, start, koniec, wynik, `interrupted`, `round` (1 = pierwsza, 2+ = sprawdzająca), `previous_id`
- `pkj_inspection_materials`: kontrola, materiał (kopia nazwy)
- `pkj_results`: kontrola, punkt, materiał, **kopia treści punktu**, wynik (`OK` / `NOK` / `ND`), wartość, uwagi
- `pkj_photos`: wynik, plik (`static/pkj_uploads/`)
- `pkj_returns`: wynik/kontrola, materiał, przyczyna, opis, decyzja (`poprawka` / `zlom`), `repair_order_id`, operacje docelowe
- `pkj_reasons`: słownik przyczyn niezgodności

Pracownicy: do decyzji, czy czytać istniejące `DepartmentEmployee` (działy i plakietki QR już są), czy mieć osobną listę demo.

---

## 12. Zasady z profit, których moduł musi przestrzegać

- Pakiet `prestiz_kj/` (nazwa do potwierdzenia) z blueprintem, szablony w `templates/prestiz_kj/`, rejestracja w `app.py`.
- Nowy moduł dopisać do `models.MODULES`, `DEFAULT_PERMISSIONS`, mapowania endpointów (`_module_for_endpoint`) i menu w `base.html`. Domyślnie dostęp: Superuser i Administrator, opcjonalnie Kontroler.
- Listy według `STANDARD_LIST.md` (znaczniki `.rs*`), zmiany dopisać do "Historii zmian".
- Migracje tylko przez `_migrate_schema()` / `create_all`, bez Alembic.
- Testy w `tests/test_prestiz_kj.py`, cały zestaw `pytest tests/ -v` musi przejść.
- Workflow: rozpoznanie, propozycje, wybór użytkownika, zmiany, testy, akceptacja, commit, push, potwierdzenie.
- Bez myślnika "—" w tekstach dla użytkownika.
- Nie zmieniać istniejących modułów ani ich tabel.

---

## 13. Poza zakresem

- Prawdziwa integracja z Prestiżem (tylko symulacja dokumentów RW i ZN).
- Natywna aplikacja Android (widok mobilny w przeglądarce / PWA pokazuje zamysł).
- Automatyczny dobór punktów z plików konstrukcyjnych (moduł `profit_kj`, osobny, rozwijany poza GitHubem). W pokazie można najwyżej wspomnieć o nim jako o kierunku D2.

---

## 14. Do decyzji na etapie planu

1. Nazwa modułu w menu i adres (np. "KJ Prestiż (pogląd)", `/kj-prestiz`).
2. Pracownicy: istniejące działy i pracownicy czy osobne dane demo.
3. Ramka telefonu na komputerze czy tylko responsywny widok.
4. Ile danych demo do raportów (np. 15 zleceń z historią z 4 tygodni).
5. Które warianty pytań otwartych pokazać (np. zatwierdzanie zawrotu przez kierownika jako opcja w ustawieniach).
6. Kolejność etapów realizacji (proponowana: konfiguracja i dane demo, zlecenia i meldunki, aplikacja KJ, zawroty i ZN, raporty, tryb prezentacji).
