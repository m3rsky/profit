# Zbiorczy wpis list kontrolnych (bulk_checklist_entry.py)

Zewnętrzny program (poza aplikacją PROFIT) do szybkiego wpisywania do bazy
serii list kontrolnych wypełnionych ręcznie poza systemem — np. 30 identycznych
list kontrolnych tego samego produktu dla jednego klienta.

Każdy wpis trafia do bazy jako **zakończona kontrola** (status `completed`,
wszystkie punkty checklisty odhaczone z wynikiem OK, z podaną datą/godziną
kontroli) — tak, jakby kontrola faktycznie się odbyła.

## Jak to działa

Program **nie** łączy się bezpośrednio z bazą danych. Rozmawia z serwerem
PROFIT przez istniejące REST API (`/api/v1/checklists`), zabezpieczone
kluczem API (`X-API-Key`). Dzięki temu:

- każdy wpis przechodzi tę samą walidację co reszta systemu,
- trafia do dziennika audytu (`AuditLog`, akcja `api_create_checklist`),
- program może działać z dowolnego komputera, który ma dostęp do adresu
  serwera (nie musi stać na tej samej maszynie).

## Wymagania

Tylko Python 3 ze standardową biblioteką (`tkinter`) — nic nie trzeba
instalować przez pip.

## Uruchomienie

```
python tools/bulk_checklist_entry.py
```

Przy pierwszym uruchomieniu podaj:

1. **Adres serwera** — np. `https://profit.twojadomena.pl` (bez końcowego `/`).
2. **Klucz API** — wartość `API_KEY` skonfigurowana na serwerze
   (patrz `.env` / `config.py` na serwerze).

Zaznacz „Zapamiętaj na tym komputerze”, aby nie wpisywać tego ponownie —
zapisuje się lokalnie w `~/.profit_bulk_checklist_config.json`. **Ten plik
zawiera klucz API w czystym tekście** — nie kopiuj go między komputerami bez
potrzeby i nie wrzucaj do repozytorium (nie jest w nim śledzony, patrz
`.gitignore`).

Po połączeniu program wczytuje listę szablonów, operatorów (użytkowników
z rolą kontroler/admin) i aktywnych zamówień z serwera.

## Wypełnianie formularza

| Pole | Znaczenie |
|---|---|
| Szablon | Szablon checklisty, na podstawie którego powstaną punkty kontrolne |
| Tytuł raportu | Bazowy tytuł; przy serii program dopisze „– 1/30”, „– 2/30” itd. |
| Ilość w serii | Liczba list kontrolnych do utworzenia (1–99) |
| Operator | Kontroler/admin zapisywany jako autor kontroli |
| Zamówienie | Opcjonalne powiązanie z istniejącym zamówieniem (numer ZO) |
| Data / godzina kontroli | Kiedy kontrola "się odbyła" — trafia do `created_at`/`completed_at` wpisów |

Po zatwierdzeniu program prosi o potwierdzenie i wysyła jedno zapytanie
`POST /api/v1/checklists`, które tworzy całą serię naraz (ten sam mechanizm
`batch_id`/`batch_index`/`batch_total`, co przy seryjnym tworzeniu kontroli
w samej aplikacji).

## Rozszerzone API (dla integratorów)

`POST /api/v1/checklists` przyjmuje teraz dodatkowo:

```json
{
  "template_name": "Kontrola ZO API",
  "title": "Seria dla klienta X",
  "quantity": 30,
  "operator": "jkowalski",
  "order_number": "ZAM-2026-001",
  "completed": true,
  "performed_at": "2026-09-01T08:00:00"
}
```

- `template_id` lub `template_name` — jedno z nich wymagane.
- `operator` (nazwa użytkownika) lub `operator_id` — opcjonalne; bez nich
  autorem zostaje pierwszy użytkownik z rolą `admin` (zachowanie wsteczne
  dotychczasowej integracji Streamsoft).
- `quantity` — 1–99, domyślnie 1; > 1 tworzy serię (`batch_id` wspólny).
- `completed` — domyślnie `false`; `true` odhacza wszystkie punkty jako OK
  i ustawia status `completed`.
- `performed_at` — ISO 8601, domyślnie teraz; ustawia `created_at`/
  `started_at`/`completed_at` utworzonych raportów.

Odpowiedź dla `quantity == 1` jest jak dotychczas; dla serii zwraca
`batch_id` i listę utworzonych `items`.

Zobacz też: `GET /api/v1/users` (lista kontrolerów/adminów) i
`GET /api/v1/templates` (lista aktywnych szablonów).
