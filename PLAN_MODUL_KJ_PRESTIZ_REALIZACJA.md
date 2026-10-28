# Plan realizacji: moduł poglądowy "KJ Prestiż"

**Stan (2026-10-28): zrealizowano etapy 1 do 8.** Opis działającego modułu jest w `CLAUDE.md` (sekcja "KJ Prestiż"). Poniżej plan z chwili zatwierdzenia, z odstępstwami od niego:

- Logika jest w kilku plikach zamiast jednego `service.py`: `orders.py`, `inspections.py`, `repairs.py`, `reports_data.py`, `scenario.py`, `demo_history.py`, `clock.py` (zegar do danych historycznych).
- Zamiast osobnej trasy `qr-lookup` aplikacja KJ używa wspólnego skanera list (`list-search.js`): dokładny numer otwiera zlecenie.
- Dodane ponad plan: przycisk "Pozostałe punkty: OK", filtr rodzaju zlecenia (ZP, ZN), strona "Pytania do Streamsoft", pasek nawigacji modułu, test w przeglądarce, ustawienia kosztów (`repair_op_hours`, `scrap_material_cost`).
- Dane historyczne to 11 zleceń `ZP-DEMO-101` do `111`, a razem ze scenariuszem i trzema bieżącymi 15 zleceń (decyzja 4B).
- Operacje zlecenia naprawczego nie mają własnej kontroli KJ, kontrola sprawdzająca dotyczy oryginalnego materiału.

Stan z chwili planowania: plan do akceptacji, żaden kod nie był jeszcze napisany.
Wejście: `PLAN_MODUL_KJ_PRESTIZ.md` (co pokazać), `CLAUDE.md` (zasady), `STANDARD_LIST.md` (wygląd list).
Ten plik odpowiada na pytanie "jak to zbudować w profit". Sekcja 9 zbiera punkty do Twojej decyzji (z sekcji 14 planu wejściowego).

---

## 1. Rozpoznanie: co w profit już jest i z czego skorzystamy

| Obszar | Stan w kodzie | Wniosek dla modułu |
|---|---|---|
| Rejestracja blueprintów | `app.py` ok. linii 44-63: `from kontrola import kontrola_bp` + `app.register_blueprint(...)`, jeden blok na pakiet | Dodajemy trzy linie w tym samym stylu |
| Dostęp do modułów | `models.MODULES` (klucz: nazwa), `DEFAULT_PERMISSIONS` (rola: zbiór kluczy), `RolePermission` w bazie. `_seed_role_permissions()` przy starcie dopisuje brakujące wiersze macierzy z wartości domyślnych, nie rusza istniejących | Nowy klucz pojawi się sam w macierzy superusera. Superuser ma zawsze wszystko, Administrator dostaje moduł z `set(MODULES)` |
| Mapowanie endpointów | `_module_for_endpoint()` + `_BLUEPRINT_MODULES = {'kontrola': 'kontrola_etapowa', ...}`, `_enforce_module_access` robi `abort(403)` | Jedna linia: `'prestiz_kj': 'kj_prestiz'` |
| Menu | `NAV_DEFAULT`, `MODULE_HOME`, pozycje `{% if can('...') %}` w `templates/base.html` (ok. linii 96-160), przestawianie przez `User.menu_order` | Dopisujemy klucz `kj_prestiz` w trzech miejscach (NAV_DEFAULT, MODULE_HOME, base.html) |
| Wzorzec pakietu | `kontrola/__init__.py` (blueprint + `from . import routes`), jeden duży `routes.py` (1109 linii), dekoratory `view_required`, `controller_required`, `admin_required`, własna paginacja, zapis "Zapisz wszystko" z polami `title_<id>` | Kopiujemy układ, ale `routes.py` dzielimy na kilka plików (patrz sekcja 2), żeby nie powtarzać 1100 linii w jednym pliku |
| Pracownicy i działy | `ProductionDepartment` (Cięcie, Laser, Gięcie, Zgrzewanie, Spawanie, Czyszczenie, Mycie, Malowanie, Składanie, Kontrola Jakości), `DepartmentEmployee` (nazwa, dział, token) | Do odczytu możliwe, ale patrz decyzja 2 (dane osobowe w statystykach) |
| Punkty z kopią treści | `ControlResult.title` jest kopią punktu, `ControlRecordElement.name` kopią elementu | Ten sam wzorzec dla A5 |
| Lista wzorcowa | `kontrola/list.html`, `templates/_list_search.html` (makro `list_search`), `.rs*` w `main.css` | Wszystkie listy modułu według `STANDARD_LIST.md` |
| Skan QR | `templates/kontrola/scan.html` używa `html5-qrcode@2.3.8` z unpkg, odczyt pól `p`,`c`,`o`,`q` | Powtarzamy to samo, aplikacja KJ czyta te same etykiety |
| Zdjęcia | `qar/routes.py::upload_photo`: walidacja obrazu (`_allowed_image`, `_verify_image`), plik `qar_<uuid>.<ext>` w `QAR_UPLOAD_FOLDER` (`static/qar_uploads`), wysyłka przez `fetch` | Ten sam mechanizm, folder `static/pkj_uploads/` (wzorzec `static/*_uploads/` już jest w `.gitignore`), dodajemy `PKJ_UPLOAD_FOLDER` w `config.py` i `os.makedirs` w `init_db` |
| PDF | `qar/pdf_export.py` (ReportLab 4.2.2, DejaVu z `fonts/`, rejestracja `_register_fonts`, `make_response`), `pdf_generator.py` (DVSans) | Nowy `prestiz_kj/pdf.py` z tym samym schematem fontów i kolorów. Brak kopiowania kodu: korzystamy z gotowego `_register_fonts` tylko jeśli da się go zaimportować bez efektów ubocznych, inaczej własna rejestracja pod unikalną nazwą fontu |
| Excel | `kosztorys/excel_export.py` (openpyxl 3.1.5, `BytesIO`), `produkcja/excel_io.py` | Nowy `prestiz_kj/xlsx.py`, jeden helper "tabela do arkusza" używany przez wszystkie raporty |
| Wykresy | `templates/admin/stats.html` używa `chart.js@4.4.4` z jsdelivr | Ten sam pakiet i wersja do FPY i Pareto, bez nowych zależności w `requirements.txt` |
| Migracje | `init_db()`: `_migrate_schema()`, potem `db.create_all()` | Nowe tabele `pkj_*` powstają z `create_all()` bez `ALTER`. `_migrate_schema()` nie jest ruszane |
| Testy | `tests/conftest.py`: sesyjny `app` z SQLite `:memory:`, konta `admin`, `oper` (kontroler), `super`; `login()` i `_csrf()` z `test_app.py`; czyszczenie danych funkcją `_wipe()` w teście | Ten sam wzorzec. Uwaga: `test_roles.py:151` liczy `len(DEFAULT_PERMISSIONS) * len(MODULES)`, więc nowy moduł nie psuje go (liczy dynamicznie). `test_menu.py` korzysta z `NAV_DEFAULT`, trzeba będzie sprawdzić, czy nowy klucz nie wymaga poprawki oczekiwań |
| Nagłówki ramek | Brak `X-Frame-Options` i `frame-ancestors` w `app.py`, `wsgi.py`, `config.py` | Strona w `<iframe>` na tej samej domenie działa bez zmian (ważne dla decyzji 3) |

### Co moduł zmienia poza własnym pakietem (minimum konieczne)

Tylko wpisy rejestrujące, nic z istniejącej logiki:

1. `models.py`: jeden wpis w `MODULES` (`'kj_prestiz': 'KJ Prestiż (pogląd)'`) i jeden w `DEFAULT_PERMISSIONS['kontroler']` (jeśli wybierzesz dostęp kontrolera).
2. `app.py`: import i `register_blueprint`, `NAV_DEFAULT`, `MODULE_HOME`, `_BLUEPRINT_MODULES`, `os.makedirs` folderu zdjęć w `init_db`.
3. `config.py`: `PKJ_UPLOAD_FOLDER`.
4. `templates/base.html`: jedna pozycja menu.
5. `STANDARD_LIST.md` (wpisy w "Historii zmian"), `CLAUDE.md` (sekcja o module), opcjonalnie `DOKUMENTACJA_SYSTEMU.md`.

Żadna istniejąca tabela, kolumna ani trasa nie jest zmieniana. Usunięcie modułu = usunięcie pakietu, szablonów, tych wpisów i `DROP` tabel `pkj_*`.

---

## 2. Struktura pakietu

Nazwa pakietu: `prestiz_kj/` (blueprint `prestiz_kj`, prefiks URL `/kj-prestiz`). Do potwierdzenia w decyzji 1.

```
prestiz_kj/
  __init__.py        blueprint + import podmodułów (jak kontrola/__init__.py)
  models.py          klasy Pkj* (db z models.py), importowane w __init__ przed create_all
  access.py          dekoratory: view_required, controller_required, admin_required
  config_routes.py   konfiguracja: operacje, materiały, grupy, punkty, przyczyny, ustawienia
  order_routes.py    lista zleceń demo, szczegóły z osią czasu, meldunki (ZP i ZN)
  mobile_routes.py   aplikacja KJ: skan, operacja, materiały, punkty, zdjęcia, zakończ/przerwij
  return_routes.py   zawrót, decyzja poprawka/złom, rejestr zawrotów, zatwierdzanie (opcja)
  report_routes.py   raporty, karta PDF, eksporty Excel
  demo_routes.py     wczytaj/resetuj demo, scenariusz, etykieta QR
  service.py         logika domenowa bez Flaska: meldunek, RW, ocena kontroli, tworzenie ZN, statusy
  demo_data.py       generator danych demo (deterministyczny)
  reports_data.py    zapytania agregujące (FPY, Pareto, koszty), wspólne dla ekranu, PDF i Excela
  pdf.py             karta kontroli zlecenia
  xlsx.py            helper eksportu
templates/prestiz_kj/
  base_kj.html (rozszerza base.html), index.html, scenariusz.html, konfiguracja_*.html,
  zlecenia.html, zlecenie.html, zawroty.html, zawrot.html, raporty/*.html,
  m/_frame.html, m/start.html, m/zlecenie.html, m/kontrola.html, m/zawrot.html, m/historia.html
static/js/pkj-mobile.js     zapis oceny punktu, zdjęcia, skan (jedyny nowy plik JS)
static/css/pkj.css          układ mobilny, ramka telefonu, drobne style modułu (bez ruszania main.css)
tests/test_prestiz_kj*.py
```

Zasada: **logika w `service.py`, trasy tylko parsują formularz i wołają serwis**. Dzięki temu scenariusz pokazu da się przetestować bez klienta HTTP, a generator demo korzysta z tych samych funkcji co ekrany (dane demo przechodzą tę samą logikę RW, ZN i statusów).

Style: nowe klasy z prefiksem `.pkj-`, statusy wyłącznie `.rs*` z `main.css`. Nie edytujemy `main.css` ani `erp-design.css`. Jeśli zabraknie klasy `.rs*` (np. dla "wstrzymane RW"), użyjemy istniejącej `rs-warn` z inną ikoną, bez nowego koloru.

---

## 3. Model danych: weryfikacja propozycji z sekcji 11

Ogólnie szkielet z sekcji 11 jest dobry i wystarcza do scenariusza. Po rozpisaniu przepływu znalazłem **poprawki i braki**:

### 3.1 Poprawki do istniejących tabel

| Tabela | Uwaga | Zmiana |
|---|---|---|
| `pkj_check_points` | Połączenie `operation_id` / `material_id` / `group_id` z wartościami null jest niejednoznaczne ("null = wspólny" dla punktu bez operacji, ale też "null = punkt operacji" dla braku materiału). Przy trzech nullowalnych kluczach łatwo o zły zapis i trudne zapytanie | Jawna kolumna `scope`: `all` (wspólny dla wszystkich operacji, A4), `operation` (z `operation_id`), `material` (z `material_id`), `group` (z `group_id`). Zapytanie "punkty dla kontroli" staje się czytelne |
| `pkj_check_points` | Brakuje pola na wymóg zdjęcia przy NOK i na typ pola | Dodać `photo_required_on_nok` (bool), `kind` (`ok_nok` / `value`), `image` (nazwa pliku grafiki), `help_text` |
| `pkj_orders` | `type` ZP/ZN i `parent_id` są poprawne. Brakuje pola, skąd wzięło się ZN | Dodać `source_return_id` (FK do `pkj_returns`, tylko dla ZN) oraz `is_demo_scenario` (bool, żeby przycisk "Przywróć scenariusz pokazu" czyścił tylko ZP-DEMO-001 i jego ZN, a nie dane do raportów) |
| `pkj_order_operations` | `rw_status` na operacji nie rozróżnia materiałów. W scenariuszu Korpus jest OK, a Drzwi NOK, więc RW dla operacji nie może być "wydane" | RW trzymać **na materiale zlecenia**, a status operacji wyliczać (operacja ma RW wydane dopiero, gdy wszystkie materiały KJ są zgodne). Szczegóły niżej |
| `pkj_order_operations` | Brak informacji, czy operacja pochodzi z trasy, czy dodano ją ręcznie przy zawrocie | Dodać `added_manually` (bool), `repair_for_return_id` (FK, dla operacji naprawczych na ZN) |
| `pkj_order_materials` | Powiązany z "operacją zlecenia", ale status elementu (C3) dotyczy materiału na całym zleceniu, a nie jednej operacji | Zostawić powiązanie z operacją (to ona ma flagę KJ), status elementu trzymać w tym samym wierszu, bo jeden materiał występuje na jednej operacji KJ. Dodać `rw_status` (`brak` / `wstrzymane` / `wydane` / `anulowane`) i `element_status` (`oczekuje`, `w_kontroli`, `zawrocony`, `w_naprawie`, `do_ponownej_kontroli`, `zgodny`, `zlom`) |
| `pkj_inspections` | Wynik kontroli to wynik **zbiorczy**, a każdy materiał ma własny werdykt (Korpus OK, Drzwi NOK w jednej kontroli) | `result` kontroli wyliczany z werdyktów materiałów. Dodać `status` (`w_toku` / `zakonczona` / `przerwana`). `previous_id` zostaje, `round` zostaje |
| `pkj_inspection_materials` | Tylko kopia nazwy, brak werdyktu | Dodać `order_material_id`, `verdict` (`OK` / `NOK` / `null`), `interrupted` (bool, B7: błąd wyklucza element) |
| `pkj_results` | Brakuje pełnej kopii punktu (A5 wymaga kopii **treści**, a nie tylko tytułu) | Kopia: `title`, `kind`, `unit`, `min_value`, `max_value`, `section` (nazwa sekcji "Wspólne" / materiał). Dodać `inspection_material_id` (kiedy punkt dotyczy materiału), `out_of_range` (bool wyliczany przy zapisie) |
| `pkj_photos` | `static/pkj_uploads/` poprawne | Dodać `caption`, `created_at`. Wynik: wiele zdjęć na wynik |
| `pkj_returns` | Operacje docelowe jako lista w jednej kolumnie byłyby niewygodne (statystyki, raport kosztów) | Osobna tabela `pkj_return_operations` (patrz niżej) |

### 3.2 Brakujące tabele

| Tabela | Po co |
|---|---|
| `pkj_events` | **Oś czasu zlecenia** (sekcja 9.2 planu): meldunek, wstrzymanie RW, start/koniec kontroli, zawrót, ZN, zwolnienie RW, symulowany dokument RW/ZN. Bez niej oś trzeba by składać z 6 tabel, a symulowane dokumenty (RW, ZN) nie mają gdzie żyć. Pola: `order_id`, `ts`, `type`, `text`, `ref_type`, `ref_id`, `actor` |
| `pkj_return_operations` | Operacje docelowe zawrotu: `return_id`, `operation_id`, `position`, `added_manually`. Z nich powstają wiersze `pkj_order_operations` zlecenia naprawczego |
| `pkj_employees` | Tylko jeśli decyzja 2 = osobna lista demo (patrz niżej) |
| `pkj_settings` | Opcje wariantów z decyzji 5 (klucz/wartość, jak `ProdSetting`) |
| `pkj_users_demo` | **Nie potrzebna**: kontrolera bierzemy z istniejącego `User` (tylko FK i kopia imienia w polu tekstowym) |

### 3.3 Powiązania z istniejącymi tabelami (tylko odczyt)

- `pkj_inspections.inspector_id` → `users.id` (kontroler). FK do cudzej tabeli tylko na odczyt, bez `cascade`. Alternatywa: zapisać samą nazwę, jeśli chcemy, żeby `DROP` modułu nie zależał od `users`. Zalecenie: FK nullable plus kopia nazwy (`inspector_name`), tak jak profit robi kopie w wynikach.
- Pracownicy: patrz decyzja 2. W obu wariantach w wyniku zapisujemy **kopię imienia i działu**.

### 3.4 Reguły stanu (do implementacji w `service.py` i testów)

1. **Meldunek operacji z `kj_required`:** dla każdego materiału z `kj_flag` w tej operacji `rw_status = wstrzymane`, `element_status = oczekuje`. Operacja ma status `do_kontroli`. Zdarzenia: "Meldunek", "RW wstrzymane".
2. **Operacje bez KJ:** meldunek od razu `rw_status = wydane` (symulowany dokument RW).
3. **Kontrola:** start ustawia `element_status = w_kontroli` dla zaznaczonych materiałów. Ocena kompletna, gdy każdy punkt ma wynik (`OK`/`NOK`/`ND`, `ND` liczy się jako ocenione), a punkty z wymaganym zdjęciem przy NOK je mają.
4. **Werdykt materiału:** `NOK` jeśli którykolwiek punkt materiału jest `NOK` (punkty wspólne z `NOK` dotykają wszystkich zaznaczonych materiałów, patrz otwarta kwestia niżej), inaczej `OK`.
5. **OK:** `element_status = zgodny`, `rw_status = wydane`, zdarzenie "RW wydane (symulacja)". Operacja przechodzi dalej, gdy wszystkie jej materiały KJ są zgodne.
6. **NOK:** wymusza ekran zawrotu przed zakończeniem kontroli (materiał dostaje `zawrocony`). Zawrót: operacje docelowe, przyczyna, opis, decyzja.
   - **Poprawka:** powstaje ZN (`ZN-<ZP>/<n>`), jego operacje to wybrane operacje docelowe (`pkj_order_operations` ZN). `element_status = w_naprawie`. RW dalej `wstrzymane`.
   - **Złom:** `element_status = zlom`, RW `anulowane`, powstaje ZN na nowy element od pierwszej operacji, zdarzenie "Rozchód materiału (symulacja)".
7. **Meldunek ostatniej operacji ZN:** `element_status = do_ponownej_kontroli`. Kontrola sprawdzająca (`round + 1`, `previous_id`) pokazuje **tylko** zawrócone materiały i punkty, które były NOK, plus wspólne (do ustalenia, patrz decyzja 5). Wynik OK: `zgodny`, `rw_status = wydane`, ZN status `zamkniete`.
8. **Przerwanie (B7):** `interrupted = true` na materiale, `element_status = zawrocony`, wymusza zawrót lub złom (błąd wyklucza element).
9. **FPY (pierwsze przejście):** materiał jest "zgodny za pierwszym razem", gdy jego kontrola `round = 1` ma werdykt `OK`. To jest jedyne źródło wskaźnika, więc zawrót nie może go zmienić po fakcie.

**Otwarta kwestia projektowa (do potwierdzenia przy etapie 3):** czy `NOK` w punkcie wspólnym (sekcja Wspólne) zawraca wszystkie zaznaczone materiały, czy kontroler wskazuje materiał ręcznie. Zalecenie: przy `NOK` punktu wspólnego kontroler wybiera, których zaznaczonych materiałów dotyczy (domyślnie wszystkie), co jest jednym dodatkowym ekranem tylko w tym przypadku.

### 3.5 Kolejność usuwania przy "Resetuj demo"

`pkj_photos` → `pkj_results` → `pkj_inspection_materials` → `pkj_inspections` → `pkj_return_operations` → `pkj_returns` → `pkj_events` → `pkj_order_materials` → `pkj_order_operations` → `pkj_orders`. **Konfiguracja** (operacje, materiały, punkty, przyczyny) zostaje, chyba że użyto "Resetuj wszystko" (z potwierdzeniem). Pliki zdjęć z `static/pkj_uploads/` kasowane razem z wierszami. Reset nie dotyka żadnej tabeli poza `pkj_`.

### 3.6 Gdzie umieścić klasy modeli

Zalecenie: **`prestiz_kj/models.py`** (a nie `models.py` projektu), z `from models import db`. Powód: moduł ma być łatwy do usunięcia, a `models.py` ma już 1349 linii. Warunek: pakiet musi być zaimportowany w `app.py` przed `init_db()` (jest, bo blueprint importujemy na górze), wtedy `create_all()` zobaczy tabele. Test sprawdzi, że `pkj_*` istnieją po `create_all`.

---

## 4. Ekrany i trasy

Wszystkie pod `/kj-prestiz`. Uprawnienie modułu `kj_prestiz` egzekwuje się automatycznie przez `_module_for_endpoint`. Akcje wewnątrz (konfiguracja, reset) dodatkowo `admin_required`, jak w `kontrola`.

### 4.1 Desktop (Zarząd, Streamsoft)

| Ekran | Trasa | Opis | Rola |
|---|---|---|---|
| Przegląd | `GET /` | Kafle: liczba zleceń demo, kontrole dziś, otwarte zawroty, FPY. Przyciski: Otwórz aplikację KJ, Scenariusz, Wczytaj demo. Stopka "Dane demonstracyjne, bez połączenia z Prestiżem" | moduł |
| Scenariusz | `GET /scenariusz` | 7 kroków z sekcji 10 planu wejściowego, każdy z linkiem do właściwego ekranu i znacznikiem "gotowe" po wykonaniu (stan liczony z danych ZP-DEMO-001). Przycisk "Przywróć scenariusz pokazu" | moduł |
| Konfiguracja: operacje | `GET /konfiguracja/operacje`, `POST .../zapisz-wszystko` | Nazwa, kolejność, flaga KJ, aktywna. Jeden formularz i "Zapisz wszystko" jak w `kontrola/points` | admin |
| Konfiguracja: materiały i grupy | `GET /konfiguracja/materialy`, `POST .../zapisz-wszystko` | Materiały (flaga KJ, grupa), grupy materiałów | admin |
| Konfiguracja: punkty | `GET /konfiguracja/punkty`, `POST .../zapisz-wszystko`, `POST .../dodaj`, `POST .../<id>/usun` | Lista punktów z filtrem zakresu (wspólne / operacja / materiał / grupa), typ, min/max/jednostka, grafika (upload), wymóg zdjęcia | admin |
| Konfiguracja: przyczyny, ustawienia | `GET /konfiguracja/przyczyny`, `GET /konfiguracja/ustawienia` | Słownik przyczyn niezgodności, przełączniki wariantów (decyzja 5) | admin |
| Zlecenia demo | `GET /zlecenia` | Lista według `STANDARD_LIST.md`: `list_search` (szukanie po numerze, produkcie, kliencie, skan QR), filtry status/klient/dział/okres, status jako `.rs*`, na telefonie karty `tbl-cards`, paginacja. Tworzenie i usuwanie ręczne (pojedyncze i zbiorcze, wymóg z pamięci projektu) | moduł, akcje admin |
| Zlecenie | `GET /zlecenia/<id>` | Trasa operacji ze statusami, materiały KJ z RW i statusem elementu, oś czasu (`pkj_events`), kontrole i zawroty, przycisk **Melduj operację** z wyborem pracownika, link do karty PDF | moduł |
| Nowe zlecenie demo | `POST /zlecenia/nowe`, `POST /zlecenia/<id>/usun`, `POST /zlecenia/usun-zaznaczone` | Ręczne dodanie ZP (produkt, klient, ilość, trasa z operacji) i usuwanie | admin |
| Meldunek | `POST /zlecenia/<id>/melduj` | Operacja i pracownik, wywołuje `service.report_operation` | kontroler/admin |
| Rejestr zawrotów | `GET /zawroty`, `GET /zawroty/<id>` | Lista zawrotów z przyczyną, decyzją i statusem ZN, szczegóły ze zdjęciami | moduł |
| Zatwierdzenie zawrotu | `POST /zawroty/<id>/zatwierdz` | Tylko gdy włączony wariant "zatwierdza kierownik" (decyzja 5) | admin |
| Raporty | `GET /raporty` + 7 podstron | Patrz 4.3 | moduł |
| Demo | `POST /demo/wczytaj`, `POST /demo/reset`, `POST /demo/scenariusz-reset` | Tylko admin, z potwierdzeniem, tylko tabele `pkj_*` | admin |
| Etykieta QR | `GET /zlecenia/<id>/etykieta.pdf` | Etykieta ze skanowalnym QR w formacie `p`,`c`,`o`,`q`, żeby pokazać skan z telefonu na żywo | moduł |
| Zdjęcia | `GET /zdjecie/<filename>`, `GET /grafika/<filename>` | Serwowanie z `PKJ_UPLOAD_FOLDER` (jak `qar.serve_photo`) | moduł |

### 4.2 Aplikacja KJ (mobilna) pod `/kj-prestiz/m`

Zasada z `CLAUDE.md`: prosto na telefonie. **Skan, operacja, materiały, ocena, zakończ.** Pięć ekranów w przepływie podstawowym, dodatkowe tylko przy NOK.

| Krok | Trasa | Treść |
|---|---|---|
| 1. Start | `GET /m/` | Duży przycisk "Skanuj QR zlecenia", pole numeru ZP, lista ostatnich zleceń do kontroli (z RW wstrzymanym) |
| (lookup) | `POST /m/qr-lookup` | JSON, jak `kontrola.qr_lookup`: dopasowanie po `o` (numer), zwraca zlecenie |
| 2. Operacja i materiały | `GET /m/zlecenie/<id>`, `POST /m/kontrola/start` | Tylko zameldowane operacje z KJ, pod nimi materiały z flagą KJ jako duże checkboxy. "Zaznacz wszystkie", "Rozpocznij" |
| 3. Punkty | `GET /m/kontrola/<id>` | Sekcje: Wspólne, potem każdy materiał. Przyciski **OK / NOK / ND** na pełną szerokość (min. 56 px wysokości, B8), pole wartości z min/max i czerwonym oznaczeniem, uwagi, zdjęcie (`<input type=file accept=image/* capture=environment>`), wykonawca podpowiedziany z meldunku (zmiana jednym tapnięciem, B4) |
| (zapis) | `POST /m/kontrola/<id>/punkt/<result_id>` | JSON, autozapis oceny przy każdym tapnięciu (jak oceny w Kontroli etapowej, żeby nie tracić danych) |
| (zdjęcie) | `POST /m/kontrola/<id>/zdjecie` | `fetch` + walidacja obrazu, jak `qar.upload_photo` |
| 4a. Zakończ OK | `POST /m/kontrola/<id>/zakoncz` | Jeśli wszystko OK: RW wydane (symulacja), ekran potwierdzenia z podsumowaniem |
| 4b. Zawrót (NOK) | `GET/POST /m/zawrot/<return_id>` | Jeden ekran: operacja docelowa (lista trasy), "Dodaj operację" (ręcznie), przyczyna (lista), opis, decyzja **Poprawka / Złom**, "Utwórz zlecenie naprawcze" |
| 4c. Przerwij | `POST /m/kontrola/<id>/przerwij` | "Błąd wyklucza element" (B7), prowadzi do zawrotu |
| 5. Historia | `GET /m/historia/<order_id>` | Wszystkie kontrole zlecenia i elementu (B6), z wynikami |
| Kontrola sprawdzająca | to samo `GET /m/kontrola/<id>` | Rozpoznaje `round > 1` i pokazuje tylko punkty do ponownej oceny plus oznaczenie "Po naprawie" |

**Ramka telefonu:** wariant w decyzji 3. Strony `/m/*` renderują się w układzie `m/_frame.html` (tylko `<main>` bez sidebaru profit), z parametrem `?embed=1` dla wersji w ramce.

### 4.3 Raporty (`/raporty/...`)

Jeden zestaw zapytań w `reports_data.py` służy ekranowi, PDF i Excelowi. Filtry wspólne: okres, klient, produkt, dział (`list_search` w trybie filtrów).

| Raport | Trasa | Wyjście |
|---|---|---|
| Karta kontroli zlecenia | `GET /raporty/karta/<order_id>`, `.pdf` | Ekran + PDF (operacje, elementy, punkty, wyniki, wartości, zdjęcia, kontroler, wykonawca, daty), logo `static/k_logo.jpg`, DejaVu |
| Rejestr niezgodności | `GET /raporty/niezgodnosci`, `.xlsx` | Tabela NOK |
| FPY | `GET /raporty/fpy`, `.xlsx` | Tabela + wykres słupkowy (Chart.js) wg operacji, działu, tygodnia |
| Pareto | `GET /raporty/pareto`, `.xlsx` | Wykres słupkowy z linią skumulowaną, wybór osi: punkt / przyczyna / materiał / operacja |
| Koszt zawrotów | `GET /raporty/koszt-zawrotow`, `.xlsx` | Liczba ZN, czas napraw, materiał na złom (z danych demo: czasy i koszty stałe w konfiguracji) |
| Wyniki wg pracownika | `GET /raporty/pracownicy`, `.xlsx` | NOK wg wykonawcy i działu, z dopiskiem "do szkoleń, nie do kar" |
| Czas kontroli | `GET /raporty/czas-kontroli`, `.xlsx` | Średni i maksymalny czas wg operacji i kontrolera |

Raporty liczone zapytaniami SQL z `GROUP BY` na tabelach `pkj_*`, bez wczytywania całości do pamięci (dane demo są małe, ale wzorzec ma być prawidłowy).

---

## 5. Uprawnienia

| Element | Ustalenie |
|---|---|
| Klucz modułu | `kj_prestiz`, nazwa w macierzy "KJ Prestiż (pogląd)" |
| Domyślnie | Administrator (z `set(MODULES)`) i Superuser. Opcjonalnie Kontroler (do ustalenia w decyzji 1, bo zmienia `DEFAULT_PERMISSIONS['kontroler']`) |
| Istniejąca baza | `_seed_role_permissions()` dopisze wiersze dla nowego modułu przy starcie wg wartości domyślnych, nie nadpisze innych. Superuser zmienia je w `/admin/users/uprawnienia` |
| Akcje w module | `view_required` (moduł), `controller_required` (meldunek, kontrola, zawrót), `admin_required` (konfiguracja, tworzenie/usuwanie zleceń, demo/reset, zatwierdzanie zawrotu). Wzorzec dekoratorów jak w `kontrola/routes.py:20-60` |
| Ukrycie po prezentacji | Odebranie modułu rolom w macierzy (menu znika, adres daje 403). Nie wymaga zmian w kodzie |
| API | Brak. Moduł nie dodaje nic do `/api/v1` |

---

## 6. Dane demo

Źródło: `demo_data.py`, wołane przez `POST /demo/wczytaj` (admin). **Deterministyczne** (`random.Random(2026)`), daty względem dnia uruchomienia (4 tygodnie wstecz), więc raporty wyglądają świeżo i testy są powtarzalne.

### 6.1 Konfiguracja startowa

- **Operacje (trasa):** Wycinanie, Gięcie, Spawanie, Szlifowanie, Malowanie, Składanie. KJ na: Gięcie, Spawanie, Malowanie, Składanie (cztery operacje z K1, bo raporty wg operacji mają wtedy sens). Szlifowanie bez KJ, żeby dało się je dodać ręcznie jako operację naprawczą (scenariusz pkt 5).
- **Materiały:** Korpus, Drzwi, Płyta montażowa (flaga KJ) oraz Aparatura (bez flagi, żeby pokazać K2: nie pojawia się w aplikacji). Grupa "Obudowa" = Korpus + Drzwi (K4), Płyta montażowa osobno.
- **Punkty:** ok. 4 wspólne, po 2-3 na operację, po 2-3 na materiał, 2 na grupę, w tym 3 punkty z wartością (np. wymiar 600 ±2 mm, grubość powłoki 60-120 µm, moment dokręcenia).
- **Przyczyny:** spoina niepełna, rysa na powłoce, wymiar poza tolerancją, brak otworu, odkształcenie, zanieczyszczenie przed malowaniem.

### 6.2 Zlecenia

| Zestaw | Zawartość |
|---|---|
| **ZP-DEMO-001** (scenariusz na żywo) | Szafa PSH 600x800x300, klient demo, ilość 1, trasa pełna. Zameldowane Wycinanie i Gięcie (kontrola Gięcia OK z historii). **Spawanie jeszcze niezameldowane**, żeby zameldować je na żywo. `is_demo_scenario = true`, można przywrócić jednym przyciskiem |
| 14 zleceń historycznych | Zakończone i w toku, rozłożone na 4 tygodnie. Rozkład: ok. 70% materiałów zgodnych za pierwszym razem, 25% jeden zawrót z poprawką, 5% złom. Zawroty z przyczynami o rozkładzie nierównym (spoina niepełna ok. 40%), żeby Pareto miało czytelny kształt. 2-3 zlecenia z kontrolą przerwaną. 1-2 zlecenia "w naprawie" i "do ponownej kontroli" |
| Zdjęcia | 3-4 małe zdjęcia przykładowe (wygenerowane Pillow, prosta grafika z opisem "Przykład wady", bez prawdziwych zdjęć z produkcji) przypisane do wybranych NOK |

### 6.3 Osoby

- Kontrolerzy: istniejące konto zalogowane lub dwa nazwiska demo zapisane tekstem (bez zakładania kont).
- Wykonawcy: zależnie od decyzji 2.

### 6.4 Zasady bezpieczeństwa danych demo

- Wczytanie demo **nie nadpisuje** istniejących danych modułu bez potwierdzenia (komunikat: "Dane demo już istnieją, użyj Resetuj").
- Dane demo oznaczone `is_demo` w zleceniach, a na każdej stronie modułu stały pasek "Dane demonstracyjne".
- Zdjęcia wygenerowane przy wczytaniu i kasowane przy resecie.

---

## 7. Testy

Plik(i): `tests/test_prestiz_kj.py` (logika i trasy), `tests/test_prestiz_kj_reports.py` (raporty), wzorzec fixture `app`/`client`, `login()` i `_csrf()` z `test_app.py`, `_wipe()` czyszczące tabele `pkj_*`. Cel: ok. 45-55 testów, całość `pytest tests/ -v` zielona (stan obecny: 303 testy).

| Grupa | Co sprawdzamy |
|---|---|
| Rejestracja i dostęp | Brak logowania: przekierowanie. Kontroler bez uprawnienia: 403. Admin i superuser: 200. Macierz uprawnień dostaje nowy moduł z wartościami domyślnymi. Pozycja menu znika bez uprawnienia. Moduł nie zmienia istniejących tras (smoke test kluczowych adresów) |
| Izolacja | Po `create_all` istnieją tabele `pkj_*`. `demo/reset` nie ruszy `control_records`, `qar_reports`, `department_employees` (liczniki przed i po). Moduł nie zapisuje do `production_departments` ani `department_employees` |
| Konfiguracja | CRUD operacji, materiałów, grup, punktów, przyczyn. Punkt `scope` waliduje wymagane klucze. Usuwanie punktu używanego w wynikach zachowuje historię (kopia treści). Zapis zbiorczy jak w `test_kontrola_zapis.py` |
| Meldunek i RW | Meldunek operacji KJ: RW `wstrzymane`, status `do_kontroli`. Operacja bez KJ: RW od razu `wydane`. Materiał bez flagi nie trafia do kontroli |
| Kontrola | Start tworzy wyniki w sekcjach Wspólne + materiał, ze skopiowaną treścią punktu. Edycja punktu po starcie nie zmienia wyniku (A5). Zakończenie bez kompletnej oceny odrzucone. Wartość poza min/max oznaczona `out_of_range`. Wymóg zdjęcia przy NOK egzekwowany. Autozapis oceny |
| Werdykty | Korpus OK + Drzwi NOK w jednej kontroli: Korpus `zgodny`, RW Korpusu wydane, Drzwi `zawrocony`, RW Drzwi wstrzymane |
| Zawrót i ZN | Poprawka: powstaje ZN o numerze `ZN-<ZP>/<n>` z wybranymi i ręcznymi operacjami, powiązane z ZP, materiałem i wynikiem. Złom: ZN na nowy element, anulowane RW, zdarzenie rozchodu. Nie da się zakończyć kontroli z NOK bez zawrotu |
| Kontrola sprawdzająca | Po zameldowaniu ostatniej operacji ZN status `do_ponownej_kontroli`. Kontrola `round=2` z `previous_id`, tylko zawrócone materiały. OK zamyka ZN i zwalnia RW. Ponowne NOK tworzy kolejny zawrót |
| Przerwanie | `przerwij` ustawia `interrupted`, wymusza zawrót lub złom |
| Statusy elementu | Pełna ścieżka C3 w teście scenariusza (jak w sekcji 10 planu wejściowego) |
| Oś czasu | Zdarzenia w prawidłowej kolejności dla pełnego scenariusza |
| Demo | `wczytaj` jest deterministyczne (dwa uruchomienia = te same liczby). Nie dubluje danych. `scenariusz-reset` czyści tylko ZP-DEMO-001 i jego ZN. Reset kasuje zdjęcia z dysku |
| Raporty | FPY na zbiorze o znanym wyniku (np. 7 z 10 = 70%), Pareto: kolejność i suma skumulowana, filtry okresu, klienta, działu. Koszt zawrotów liczy ZN i złom. PDF karty zaczyna się od `%PDF` i zawiera polskie znaki. Excel otwiera się w `openpyxl`, nagłówki zgodne |
| Lista wg standardu | Test w stylu `test_list_standard.py`: lista zleceń używa `.rs*`, bez `.badge` dla statusu |
| Mobile | Trasy `/m/*` zwracają 200 dla kontrolera. `qr-lookup` dopasowuje po numerze (także z polami `p`,`c`,`o`,`q`). Upload odrzuca nie-obraz |
| Regresja | Pełny `pytest tests/ -v`, w tym `test_menu.py` i `test_roles.py` |
| Przeglądarka (opcjonalnie, etap 8) | Test Playwright jak `test_produkcja_browser.py` (pomijany bez Chromium): przejście skan, operacja, materiały, NOK, zawrót na widoku 390 px |

Zgodnie z workflow: po każdym etapie uruchamiam pełny zestaw, pokazuję wynik i czekam na akceptację przed commitem.

---

## 8. Etapy realizacji

Skala: **S** = do pół dnia, **M** = ok. 1 dzień, **L** = 1,5-2 dni (praca ciągła z testami). Każdy etap kończy się testami, listą zmian i listą plików oraz **akceptacją przed commitem** (zgodnie z `CLAUDE.md`).

| Etap | Zakres | Wielkość | Co widać po etapie |
|---|---|---|---|
| 0 | Rozpoznanie zakończone. Ten plan i decyzje | S | Plan |
| 1 | **Szkielet i konfiguracja.** Pakiet, blueprint, modele `pkj_*`, rejestracja w `app.py`/`models.py`/`config.py`/`base.html`, uprawnienia, menu, ekran Przegląd, ekrany konfiguracji (operacje, materiały, grupy, punkty, przyczyny, ustawienia), `service.py` zalążek, `demo_data.py` z samą konfiguracją startową, `POST /demo/wczytaj` i `reset`. Testy: dostęp, izolacja, CRUD | **L** | Można pokazać część "konfiguracja K1-K4" z danymi |
| 2 | **Zlecenia i meldunki.** Lista zleceń (standard list), strona zlecenia, trasa, melduj operację, RW wstrzymane, oś czasu `pkj_events`, zlecenia demo (ZP-DEMO-001 i historyczne bez kontroli), ręczne tworzenie/usuwanie. Testy: meldunek i RW | **M** | Część "meldunek ze wstrzymanym RW" |
| 3 | **Aplikacja KJ.** Układ mobilny, skan QR i etykieta QR, wybór operacji i materiałów, punkty w sekcjach, autozapis, wartości z zakresem, zdjęcia, zakończ OK (RW wydane), przerwij, historia, wybrany wariant ramki telefonu. Testy: kontrola i werdykty | **L** | Główna część pokazu na telefonie |
| 4 | **Zawroty i ZN.** Ekran zawrotu, operacje docelowe i ręczne, przyczyna, poprawka/złom, tworzenie ZN i symulowanych RW, meldunek ZN, kontrola sprawdzająca, statusy elementu, rejestr zawrotów, zatwierdzanie (jeśli wariant włączony). Testy: pełny scenariusz | **L** | Cały proces end-to-end z sekcji 10 |
| 5 | **Dane demo do raportów.** Pełny generator (14 zleceń, zdjęcia, rozkłady), reset scenariusza | **M** | Dane do raportów |
| 6 | **Raporty.** `reports_data.py`, 7 ekranów, wykresy FPY i Pareto, karta PDF, eksporty Excel. Testy: liczby i formaty | **L** | Zarząd widzi liczby |
| 7 | **Tryb prezentacji.** Strona Scenariusz z krokami i stanem, "Przywróć scenariusz pokazu", pasek "dane demo", szlify mobilne, wersja w ramce, test w przeglądarce | **M** | Gotowe do pokazu |
| 8 | **Dokumentacja i wdrożenie.** Sekcja w `CLAUDE.md`, wpisy w `STANDARD_LIST.md`, `DOKUMENTACJA_SYSTEMU.md`, pełne testy, commit, push, deploy (tabele powstają przy restarcie, bez `pip install`: nowych zależności brak), kontrola na produkcji | **S** | Wdrożone |

Szacunek łączny: ok. 7-9 dni pracy ciągłej, rozłożonych na 8 akceptowanych porcji. Możliwy wcześniejszy pokaz po etapie 4 (cały proces bez raportów).

### Ryzyka

| Ryzyko | Mitygacja |
|---|---|
| Zbyt rozbudowany ekran telefonu (powtórka z wycofanego śledzenia produkcji) | Pięć ekranów w ścieżce głównej, duże przyciski, autozapis, test przejścia na 390 px, Twoja akceptacja po etapie 3 przed kolejnymi |
| Dane osobowe w statystykach pracowników przed Zarządem | Decyzja 2: fikcyjne osoby |
| Moduł "wycieka" do istniejących danych | Testy izolacji (liczniki tabel), brak zapisów do tabel spoza `pkj_` |
| `test_menu.py` zakłada określoną listę `NAV_DEFAULT` | Sprawdzić i dostosować oczekiwanie w etapie 1 (tylko dopisanie klucza) |
| Kamera i `html5-qrcode` z unpkg wymagają internetu | Jak w obecnej Kontroli etapowej. Zapasowo ręczne pole numeru ZP i przycisk wyboru z listy |
| Duży `routes.py` jak w `kontrola` | Podział na pliki i logika w `service.py` |

---

## 9. Do decyzji (punkty z sekcji 14 planu wejściowego)

Po każdym punkcie podaję rekomendację. **Nie zaczynam implementacji, dopóki nie wybierzesz.**

### Decyzja 1: nazwa modułu, adres, pakiet i dostęp

- **A. "KJ Prestiż (pogląd)", `/kj-prestiz`, pakiet `prestiz_kj`, tylko Administrator i Superuser.** Nazwa mówi, że to pokaz. Adres krótki i czytelny. Najmniej ryzyka, bo nikt poza Tobą nie zobaczy modułu bez zmiany macierzy.
- **B. "KJ Prestiż (pogląd)", jak wyżej, ale dodatkowo domyślnie dla Kontrolera.** Wygodne, jeśli Zarząd lub kontrolerzy mają sami kliknąć w aplikację. Wymaga edycji `DEFAULT_PERMISSIONS['kontroler']`.
- **C. Krótka nazwa "Jakość (demo)", adres `/jakosc-demo`.** Mniej techniczna dla Zarządu, ale zaciera związek z Prestiżem, a on jest sednem pokazu.

**Rekomendacja: A.** Superuser może w razie potrzeby odblokować Kontrolera w macierzy bez zmian w kodzie.

### Decyzja 2: pracownicy (wykonawcy w meldunkach i statystykach)

- **A. Istniejące działy i pracownicy (`ProductionDepartment`, `DepartmentEmployee`), tylko odczyt.** Realistyczne przy pokazie. Minus: dane demo (w tym wymyślone NOK) przypisałyby błędy prawdziwym osobom w raportach "Wyniki wg pracownika" pokazywanych Zarządowi i obcej firmie. Dodatkowo zależność od danych produkcyjnych, które się zmieniają.
- **B. Osobna lista demo (`pkj_employees`, ok. 8 fikcyjnych osób przypisanych do nazw działów demo).** Brak ryzyka dla prawdziwych osób, pełna kontrola nad rozkładem NOK, łatwe usunięcie. Minus: nie widać prawdziwych nazwisk (ale to przy pokazie raczej zaleta).
- **C. Hybryda: osoby demo, ale nazwy działów z istniejących `ProductionDepartment`.** Realistyczna struktura, fikcyjne osoby. Minus: zależność od zmian w słowniku działów (np. zmiana nazwy "Spawanie" zmienia raport).

**Rekomendacja: B.** Statystyka "NOK wg pracownika" na danych wymyślonych nie powinna nosić prawdziwych nazwisk. Odtwarzamy w niej strukturę działów KUBIAK nazwami własnymi demo.

### Decyzja 3: ramka telefonu na komputerze

- **A. Tylko responsywny widok.** Najprostsze (S). Przy pokazie na ekranie 27" strona mobilna rozciąga się na całą szerokość i nie wygląda jak aplikacja.
- **B. Ramka CSS (div w kształcie telefonu, ta sama strona w środku).** Tanie (S). Minus: zapytania `@media` reagują na szerokość okna, nie ramki, więc na dużym monitorze wewnętrzny układ nadal byłby "desktopowy". Pokaz wyglądałby inaczej niż na telefonie.
- **C. Ramka z `<iframe>` 390x844 ładującym `/m/...?embed=1`.** Wewnątrz iframe `@media` widzi szerokość telefonu, więc to dokładnie ten sam kod co na prawdziwym urządzeniu. Na telefonie przekierowanie do pełnego ekranu. Koszt: lekkie (M w etapie 7), brak nagłówków blokujących ramki w aplikacji (sprawdzone). Minus: iframe i skan kamery: kamera w iframe wymaga atrybutu `allow="camera"`, a na komputerze skan i tak zwykle zastępuje się wyborem z listy lub numerem.

**Rekomendacja: C**, z przyciskiem "Pełny ekran" i ręcznym polem numeru ZP jako zastępstwem skanu na pokazie na komputerze. Realistyczny wygląd przy niewielkim koszcie.

### Decyzja 4: ile danych demo do raportów

- **A. 8 zleceń z ostatnich 2 tygodni.** Szybkie ładowanie, ale FPY i Pareto na tak małej próbie wyglądają losowo (kilka słupków po 1).
- **B. 15 zleceń z 4 tygodni (propozycja z planu), ok. 40-50 kontroli materiałów, kilkanaście zawrotów.** Czytelne wykresy, trendy tygodniowe, wciąż mała baza i szybkie wczytanie.
- **C. 40 zleceń z 8 tygodni.** Najbardziej wiarygodne statystyki. Minus: dłuższe generowanie, więcej zdjęć, trudniej tłumaczyć konkretne zlecenie na pokazie.

**Rekomendacja: B**, z 14 zleceniami historycznymi plus osobnym ZP-DEMO-001 do pokazu na żywo (razem 15). Rozkład wad ustawiony tak, żeby Pareto miało wyraźnego lidera (spoina niepełna).

### Decyzja 5: które warianty pytań otwartych pokazać

Pytania z sekcji 7 planu wejściowego: praca offline, kto zatwierdza zawrót, rozliczenie materiału przy złomie, kontrola końcowa w nowej aplikacji, historia, etykiety QR, forma dostępu do danych, wycena.

- **A. Nic w ustawieniach, tylko strona statyczna "Pytania do Streamsoft" z opisem zamierzenia.** Najtańsze (S). Pokaz mniej interaktywny: Streamsoft zobaczy listę, ale nie poczuje różnicy.
- **B. Dwa przełączniki w "Ustawieniach" i reszta w opisie:**
  1. *Zatwierdzanie zawrotu*: "kontroler tworzy ZN od razu" (domyślnie) albo "ZN czeka na zatwierdzenie kierownika" (dodatkowy status zawrotu `do_zatwierdzenia`, akcja `Zatwierdź` w rejestrze).
  2. *Rozliczenie złomu*: "tylko ZN na nowy element" albo "ZN + symulowany dokument rozchodu materiału z kosztem" (wpływa na raport kosztu zawrotów).
  Pozostałe pytania (offline, KK, historia, etykiety, API, wycena) jako opisane punkty na stronie "Pytania do Streamsoft" z odnośnikami do miejsc w aplikacji, gdzie pytanie wyskakuje.
- **C. B plus symulacja pracy offline** (kolejka zapisów w przeglądarce i przycisk "Synchronizuj" z odznaką "Brak sieci"). Efektowne, ale duże (L), wprowadza Service Worker/IndexedDB do modułu, który ma być prosty, i łatwo o niedomówienia typu "to działa tak w Prestiżu".

**Rekomendacja: B.** Dwa przełączniki pokrywają najważniejsze rozbieżności procesowe (kto zatwierdza, jak rozliczyć złom) kosztem jednego dodatkowego statusu, bez nowej technologii. Offline zostaje w opisie jako pytanie do Streamsoft.

### Decyzja 6: kolejność etapów

- **A. Proponowana w planie: konfiguracja i dane demo, zlecenia i meldunki, aplikacja KJ, zawroty i ZN, raporty, tryb prezentacji** (czyli etapy 1-8 z sekcji 8). Naturalne zależności, dane demo od początku. Minus: raporty dopiero na końcu.
- **B. Pionowy wycinek najpierw:** najpierw cienki przepływ ZP-DEMO-001 przez wszystkie ekrany (bez konfiguracji w UI, na danych wpisanych w seedzie), potem pogłębianie. Szybciej widać proces, ale konfigurację K1-K4 pokazujemy później, a część ekranów trzeba by przerabiać.
- **C. Aplikacja KJ najpierw** (na twardo zakodowanych danych), reszta potem. Najwcześniej widać telefon, który jest sednem pokazu, ale to ryzyko przeróbek i niezgodności z modelem danych.

**Rekomendacja: A**, z dwiema korektami względem planu wejściowego: (1) generator demo powstaje od etapu 1 (dane startowe do dalszych etapów), a dane do raportów dopiero w etapie 5; (2) strona Scenariusz i ramka telefonu są w etapie 7, ale po etapie 4 możliwy jest wczesny pokaz bez raportów. Po etapie 3 proszę o ocenę prostoty ekranu telefonu, zanim zbudujemy zawroty.

---

### Wybór użytkownika (2026-10-28): wszystko wg rekomendacji

Zatwierdzone: 1A, 2B, 3C, 4B, 5B, 6A (tabela poniżej). Następny krok: etap 1.

### Podsumowanie rekomendacji

| # | Decyzja | Rekomendacja |
|---|---|---|
| 1 | Nazwa, adres, dostęp | A: "KJ Prestiż (pogląd)", `/kj-prestiz`, `prestiz_kj`, tylko Administrator i Superuser |
| 2 | Pracownicy | B: osobna lista demo (fikcyjne osoby) |
| 3 | Ramka telefonu | C: iframe 390x844 z `?embed=1`, pełny ekran na telefonie |
| 4 | Ilość danych | B: 14 zleceń historycznych z 4 tygodni + ZP-DEMO-001 |
| 5 | Warianty pytań otwartych | B: przełączniki "zatwierdzanie zawrotu" i "rozliczenie złomu", reszta jako opis |
| 6 | Kolejność etapów | A: proponowana, z korektami opisanymi wyżej |
