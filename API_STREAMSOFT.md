# API PSH QC — integracja ze Streamsoft ERP

Dokument techniczny dla integracji zewnętrznej (Streamsoft PCBiznes ↔ PSH QC). Opisuje: jak uwierzytelnić się, co API dziś obsługuje z przykładami żądań/odpowiedzi, oraz dalsze możliwości rozwoju integracji.

Kontekst ogólny systemu: [`DOKUMENTACJA_SYSTEMU.md`](DOKUMENTACJA_SYSTEMU.md). Ten dokument skupia się wyłącznie na API i integracji.

**Stan wdrożenia (2026-07):** `/api/v1/*` jest zabezpieczone kluczem API i gotowe do integracji maszyna-maszyna. Import zamówień przez CSV (`/orders/import-csv`) nadal działa równolegle jako kanał ręczny.

---

## 1. Uwierzytelnianie

Każde żądanie do `/api/v1/*` musi zawierać nagłówek:

```
X-API-Key: <wartość zmiennej środowiskowej API_KEY>
```

Brak nagłówka, zły klucz lub pusty klucz → `401 Unauthorized`:
```json
{ "error": "unauthorized" }
```

Klucz konfiguruje się przez zmienną środowiskową `API_KEY` w pliku `.env` (patrz `CLAUDE.md`, sekcja „Zmienne środowiskowe”). **Nie używać wartości domyślnej `change-this-api-key` na produkcji** — musi być ustawiony realny, wygenerowany sekret zarówno lokalnie, jak i na serwerze produkcyjnym.

`/api/v1/*` jest zwolnione z sesyjnej ochrony CSRF (autoryzacja kluczem API zastępuje ją dla tej ścieżki) — dzięki temu wywołania `POST`/`PUT`/`DELETE` działają z zewnętrznego skryptu bez logowania przez przeglądarkę.

Pozostałe dwie warstwy API (`/api/item/*` itp. — wewnętrzne AJAX frontendu, oraz `/api/desktop/*` — aplikacja desktopowa) **nie są przeznaczone do integracji ze Streamsoft** i nie są opisane w tym dokumencie.

---

## 2. Szablony kontrolne

### `GET /api/v1/templates`

```bash
curl -s https://TWOJ-SERWER/api/v1/templates -H "X-API-Key: $API_KEY"
```

```json
[
  { "id": 3, "name": "PSH-200 – kontrola QA", "type": "kontroler", "task_count": 24 },
  { "id": 7, "name": "PSH-200 – montaż",       "type": "monter",    "task_count": 12 }
]
```

`type` to `kontroler` (lista kontrolna QA) lub `monter` (lista montażowa). Używane m.in. do dobrania `template_id` przy ręcznym tworzeniu checklisty — w normalnym przepływie szablon dobiera się **automatycznie** po nazwie produktu przy tworzeniu zamówienia (sekcja 3).

---

## 3. Zamówienia

### `GET /api/v1/orders`

Lista zamówień. Bez parametrów zwraca wszystkie poza statusem `shipped`. Parametr `external_number` filtruje po numerze ERP i wtedy zwraca zamówienia niezależnie od statusu.

```bash
curl -s "https://TWOJ-SERWER/api/v1/orders?external_number=STR-99011" -H "X-API-Key: $API_KEY"
```

```json
[
  {
    "id": 118,
    "number": "ZAM-2026-0341",
    "external_number": "STR-99011",
    "product_name": "RU-200 PRAWA",
    "client": "ABC Sp. z o.o.",
    "quantity": 10,
    "due_date": "2026-07-30",
    "status": "in_control",
    "template": "PSH-200 – kontrola QA",
    "monter_template": null,
    "reports_total": 10,
    "reports_done": 4,
    "has_ng": false,
    "created_at": "2026-07-01T09:12:44.120000"
  }
]
```

`status` ∈ `active | in_control | ready_to_ship | shipped`. `status` zmienia się **automatycznie** w PSH QC, gdy wszystkie powiązane raporty QC zostaną zamknięte bez NG — nie ma dziś endpointu do ręcznego ustawienia statusu z zewnątrz (patrz sekcja 7, możliwości rozwoju).

### `GET /api/v1/orders/<id>`

Szczegóły jednego zamówienia (ten sam kształt co element listy powyżej).

```bash
curl -s https://TWOJ-SERWER/api/v1/orders/118 -H "X-API-Key: $API_KEY"
```

### `POST /api/v1/orders` — utworzenie zamówienia z ERP

```bash
curl -X POST https://TWOJ-SERWER/api/v1/orders \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{
    "number": "ZAM-2026-0341",
    "external_number": "STR-99011",
    "product_name": "RU-200 PRAWA",
    "client": "ABC Sp. z o.o.",
    "quantity": 10,
    "due_date": "2026-07-30"
  }'
```

Pola: `number`, `product_name`, `client` wymagane; `quantity` (domyślnie 1), `due_date` (`RRRR-MM-DD`), `external_number` opcjonalne.

**Odpowiedź `201 Created`:**
```json
{
  "ok": true, "id": 118, "number": "ZAM-2026-0341",
  "template_matched": "PSH-200 – kontrola QA",
  "monter_template_matched": null
}
```

Tak jak przy zamówieniach zakładanych ręcznie w aplikacji, system automatycznie dobiera szablon QA (i montażu, jeśli pasuje) po dopasowaniu tokenów nazwy produktu do nazw szablonów, i generuje `quantity` raportów kontrolnych. `template_matched: null` oznacza brak dopasowania — zamówienie i tak zostaje utworzone, ale bez wygenerowanej listy kontrolnej (można przypisać ręcznie w aplikacji).

**Błędy:** `400` brak wymaganego pola; `409` numer zamówienia już istnieje; `500` brak konta administratora w systemie (przypadek konfiguracyjny, nie powinien wystąpić na działającym wdrożeniu).

---

## 4. Checklisty (raporty QC)

### `GET /api/v1/checklists`

Filtry: `status` (`in_progress`/`completed`), `template_id`, `order_number`, `date_from`, `date_to` (`RRRR-MM-DD`), `page` (50/stronę).

```bash
curl -s "https://TWOJ-SERWER/api/v1/checklists?order_number=ZAM-2026-0341&status=completed" -H "X-API-Key: $API_KEY"
```

```json
{
  "items": [
    {
      "id": 5502,
      "title": "RU-200 PRAWA – ABC Sp. z o.o. – kontrola QA szt. 3/10",
      "status": "completed",
      "template": "PSH-200 – kontrola QA",
      "order_number": "ZAM-2026-0341",
      "author": "jkowalski",
      "created_at": "2026-07-02T07:40:11.000000",
      "completed_at": "2026-07-02T08:15:03.000000",
      "completion_percent": 100,
      "stats": { "total": 24, "ok": 23, "ng": 1, "na": 0, "dw": 0, "done": 24 }
    }
  ],
  "page": 1, "pages": 1, "total": 1
}
```

### `GET /api/v1/checklists/<id>` (alias: `GET /api/v1/report/<id>`)

Pełne dane raportu wraz z czasem trwania, oceną i zgodnością:

```bash
curl -s https://TWOJ-SERWER/api/v1/checklists/5502 -H "X-API-Key: $API_KEY"
```

```json
{
  "id": 5502,
  "title": "RU-200 PRAWA – ABC Sp. z o.o. – kontrola QA szt. 3/10",
  "status": "completed",
  "author": "jkowalski",
  "created_at": "2026-07-02T07:40:11.000000",
  "started_at": "2026-07-02T07:41:00.000000",
  "completed_at": "2026-07-02T08:15:03.000000",
  "duration_seconds": 2043,
  "duration_str": "34 min 03 sek",
  "order": { "id": 118, "number": "ZAM-2026-0341", "product_name": "RU-200 PRAWA", "client": "ABC Sp. z o.o." },
  "template": "PSH-200 – kontrola QA",
  "stats": { "total": 24, "ok": 23, "ng": 1, "na": 0, "dw": 0, "done": 24 },
  "score": { "pct": 96, "grade": 5, "ok": 23, "scored": 24, "na": 0 },
  "compliant": false,
  "items": [
    {
      "id": 88231, "category": "Malowanie", "task": "Sprawdź malowanie obudowy",
      "task_type": "ok_ng", "unit": null, "value_min": null, "value_max": null,
      "value": null, "result": "ok", "notes": null,
      "checked_at": "2026-07-02T07:52:30.000000"
    }
  ]
}
```

`score` to ocena 1–6 wyliczona z % pozycji OK wśród ocenionych (`null`, jeśli żadna pozycja nie ma jeszcze wyniku ok/ng). `compliant` = `true`, gdy raport nie ma żadnej pozycji NG — to najprostszy sygnał „zgodności” do wykorzystania po stronie ERP bez konieczności analizowania pełnej listy `items`.

### `POST /api/v1/checklists` — utworzenie ID listy kontrolnej

```bash
curl -X POST https://TWOJ-SERWER/api/v1/checklists \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"template_id": 3, "order_number": "ZAM-2026-0341"}'
```

`template_id` wymagane; `title`, `order_number` opcjonalne. Zwraca `201` z `id` nowego raportu. W normalnym przepływie z ERP raporty powstają **automatycznie** przy `POST /api/v1/orders` — ten endpoint służy do przypadków, gdy trzeba dodać pojedynczą listę kontrolną poza cyklem zamówienia.

### `POST /api/v1/checklists/<id>/start` — rozpoczęcie pracy

```bash
curl -X POST https://TWOJ-SERWER/api/v1/checklists/5502/start -H "X-API-Key: $API_KEY"
```

Ustawia `started_at` (jeśli jeszcze nie ustawione) i otwiera nową sesję pracy (`ChecklistSession`), tak jak pierwsze otwarcie listy w aplikacji webowej. Zwraca pełny obiekt raportu (jak w `GET /api/v1/checklists/<id>`).

### `POST /api/v1/checklists/<id>/complete` — zakończenie i podsumowanie

```bash
curl -X POST https://TWOJ-SERWER/api/v1/checklists/5502/complete -H "X-API-Key: $API_KEY"
```

Zamyka wszystkie otwarte sesje pracy, liczy `duration_seconds`, ustawia status `completed`, i **jeśli raport jest powiązany z zamówieniem** — sprawdza, czy to była ostatnia oczekująca lista kontrolna zamówienia (jeśli tak, zamówienie automatycznie przechodzi w status `ready_to_ship`, tak samo jak przy zamykaniu z poziomu aplikacji). Zwraca pełne podsumowanie: `stats`, `score`, `compliant`, `duration_str`. Wywołanie na już zamkniętym raporcie zwraca `400`.

To jest właściwy endpoint do „wysłania danych z listy kontrolnej jako podsumowania, oceny i zgodności” do systemu zewnętrznego — odpowiedź zawiera komplet danych potrzebnych do zasilenia ERP bez dodatkowego zapytania.

---

## 5. Kosztorysy

### `GET /api/v1/prices`

```bash
curl -s https://TWOJ-SERWER/api/v1/prices -H "X-API-Key: $API_KEY"
```
```json
[{ "code": "dc01", "name": "Blacha DC01", "price": 4.0, "unit": "PLN/kg", "updated_at": "2026-06-01T10:00:00" }]
```

### `GET /api/v1/labor-rates`

```json
[{ "volume_range": "do400", "label": "do 400 l", "laser": 12.0, "bending": 8.0, "welding": 20.0, "grinding": 10.0, "assembly": 15.0, "packaging": 5.0, "total": 70.0 }]
```

### `GET /api/v1/quotes` i `GET /api/v1/quotes/<id>`

Lista/szczegóły wycen (opcjonalny filtr `?status=draft|sent|accepted|closed` na liście). Szczegóły zawierają wymiary i snapshot pełnej kalkulacji (`calculation`, ten sam JSON co w module kosztorysów — pełny opis pól w [`KOSZTORYSY_DOKUMENTACJA.md`](KOSZTORYSY_DOKUMENTACJA.md)).

Te trzy endpointy są **tylko do odczytu** — pozwalają Streamsoft synchronizować aktualny cennik/wyceny, ale nie tworzą nowych wycen (tworzenie oferty pozostaje procesem w aplikacji, z uwagi na złożoność konfiguracji szafy).

---

## 6. Spawalnia (produkcja wg numeru ZO)

### `GET /api/v1/spawalnia/<zo_number>`

```bash
curl -s https://TWOJ-SERWER/api/v1/spawalnia/ZO-2026-441 -H "X-API-Key: $API_KEY"
```
```json
{
  "zo_number": "ZO-2026-441",
  "records": [
    {
      "id": 901, "batch_index": 1, "batch_total": 5,
      "is_empty": false, "has_ng": false,
      "otworowanie": "OK", "przekatna": "OK", "przekatna_odchylka": 0.4,
      "pomiar1": 501.2, "pomiar2": 300.1, "pomiar3": null,
      "jakosc_wyciecia": "OK",
      "operator": "JK", "giecie_operator": "AN", "ciecie_operator": null,
      "created_at": "2026-07-01T08:00:00", "updated_at": "2026-07-01T08:20:00"
    }
  ]
}
```

Numer ZO nieistniejący w systemie zwraca `200` z pustą listą `records` (nie `404`) — to celowe, bo ZO może jeszcze nie mieć założonych list kontrolnych po stronie hali produkcyjnej.

### `POST /api/v1/spawalnia/<zo_number>`

Zakłada `quantity` (domyślnie 1, maks. 99) pustych list kontrolnych dla numeru ZO — odpowiednik przycisku „Nowy wpis” w module spawalni, wywoływany automatycznie np. gdy Streamsoft zwalnia zlecenie produkcyjne.

```bash
curl -X POST https://TWOJ-SERWER/api/v1/spawalnia/ZO-2026-442 \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" -d '{"quantity": 5}'
```
```json
{ "ok": true, "zo_number": "ZO-2026-442", "created_ids": [910, 911, 912, 913, 914] }
```

Wypełnianie pomiarów/operatorów pozostaje zadaniem obsługi hali w aplikacji webowej — API tylko zakłada wpisy i pozwala odczytać ich stan.

---

## 7. QAR (niezgodności jakościowe)

### `GET /api/v1/qar` i `GET /api/v1/qar/<id>`

Lista (filtr `?status=open|in_progress|closed`) i szczegóły zgłoszenia NCR (opis, ustalenia, sposób rozwiązania, data weryfikacji).

### `POST /api/v1/qar` — zgłoszenie niezgodności z ERP

```bash
curl -X POST https://TWOJ-SERWER/api/v1/qar \
  -H "X-API-Key: $API_KEY" -H "Content-Type: application/json" \
  -d '{"title": "Wgniecenie na obudowie", "description": "Opis problemu", "category": "Malowanie"}'
```
```json
{ "ok": true, "id": 44, "number": "QAR-2026-0044" }
```

Numer NCR generowany jest tą samą logiką co w aplikacji (`QAR-RRRR-NNNN`, licznik roczny).

---

## 8. Import CSV — kanał równoległy dla zamówień masowych

`POST /api/v1/orders` obsługuje jedno zamówienie na żądanie. Do masowego, jednorazowego importu wielu zamówień (np. eksport wsadowy ze Streamsoft na koniec dnia) nadal wygodniejszy jest `/orders/import-csv` opisany w `DOKUMENTACJA_SYSTEMU.md`, sekcja 5.1 — format CSV z kolumną `Numer zew.` również trafia teraz do dedykowanego pola `Order.external_number`. Oba kanały mogą współistnieć: REST do zdarzeń pojedynczych/czasu rzeczywistego, CSV do wsadów.

---

## 9. Możliwości dalszego rozwoju

1. **Webhook zwrotny do Streamsoft** — dziś ERP musi odpytywać `GET /api/v1/orders` / `GET /api/v1/checklists`, żeby dowiedzieć się o zmianie statusu. Alternatywa: PSH QC wywołuje HTTP POST na adres skonfigurowany w Streamsoft po zdarzeniach takich jak zamknięcie ostatniej listy kontrolnej zamówienia czy wykrycie NG.
2. **Klucze per-integracja** — obecnie jeden wspólny `API_KEY` dla wszystkich wywołań (świadomy wybór na start — jedna integracja, jeden klucz). Przy kolejnych systemach zewnętrznych warto przejść na tabelę kluczy z możliwością odwołania dostępu pojedynczej integracji.
3. **`PATCH /api/v1/orders/<id>`** — aktualizacja ilości/terminu istniejącego zamówienia z poziomu ERP (dziś `POST` tylko tworzy nowe).
4. **Rate limiting** — `/api/v1/*` nie ma dziś ograniczenia liczby żądań (w przeciwieństwie do `/login`); przy błędnie skonfigurowanej integracji (pętla) może to obciążyć serwer.
5. **Kontrakt/wersjonowanie API** — spisanie schematu JSON (OpenAPI) dla `/api/v1/*` i wprowadzanie zmian niekompatybilnych wstecz jako `/api/v2`, żeby zmiany pól nie zrywały integracji ze Streamsoft bez ostrzeżenia.

---

## 10. Skrócona checklista wdrożeniowa integracji ze Streamsoft

- [x] Dekorator `api_key_required` na wszystkich `/api/v1/*`
- [x] Wyjątek CSRF dla `/api/v1/*`
- [x] Kolumna `Order.external_number` + migracja
- [x] `POST /api/v1/orders` z dopasowaniem szablonu
- [x] Rozszerzenie checklisty o `start`/`complete`, `score`, `compliant`, czas trwania
- [x] Endpointy odczytu dla kosztorysów, spawalni (ZO), QAR
- [ ] Ustawić realny `API_KEY` w `.env` na serwerze produkcyjnym (`host82388.iqhs.pl`) — zmienna nie jest w repo, trzeba założyć ręcznie przy pierwszym wdrożeniu
- [ ] Przekazać zespołowi Streamsoft: adres serwera, wartość `API_KEY`, ten dokument
- [ ] Zdecydować: webhook zwrotny czy polling po stronie Streamsoft (sekcja 9, pkt 1)
