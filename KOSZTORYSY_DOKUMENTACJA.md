# Dokumentacja modułu KOSZTORYSY
## System PS — Kalkulacja kosztów szaf elektrycznych

---

## Spis treści

1. [Ogólny opis modułu](#1-ogólny-opis-modułu)
2. [Rodziny szaf](#2-rodziny-szaf)
3. [Dane wejściowe — co użytkownik podaje](#3-dane-wejściowe--co-użytkownik-podaje)
4. [Skąd pochodzą ceny — tabele konfiguracyjne](#4-skąd-pochodzą-ceny--tabele-konfiguracyjne)
5. [Obliczanie kosztów — krok po kroku](#5-obliczanie-kosztów--krok-po-kroku)
6. [Od kosztu własnego do ceny sprzedaży](#6-od-kosztu-własnego-do-ceny-sprzedaży)
7. [Różnice między rodzinami szaf](#7-różnice-między-rodzinami-szaf)
8. [Statusy wyceny i przepływ pracy](#8-statusy-wyceny-i-przepływ-pracy)
9. [Panel administratora](#9-panel-administratora)
10. [Eksport danych](#10-eksport-danych)
11. [Słownik pojęć](#11-słownik-pojęć)

---

## 1. Ogólny opis modułu

Moduł KOSZTORYSY służy do wyceny szaf elektrycznych produkowanych przez firmę.
Na podstawie wymiarów szafy i wybranych opcji system automatycznie oblicza:

- koszt każdego elementu blaszanego (blacha + malowanie)
- koszt odpadu materiałowego
- koszt robocizny (laser, gięcie, spawanie, szlifowanie, montaż, pakowanie)
- koszt osprzętu (uszczelki, zawiasy, zamki, śruby)
- koszty dodatkowe (transport, naklejki, opakowanie, koszty stałe)
- cenę katalogową po nałożeniu marży
- cenę końcową po rabacie i bonusie
- zyskowność danej wyceny

Każda wycena jest zapisywana w bazie danych z pełnym zrzutem kalkulacji (tzw. snapshot),
dzięki czemu zmiana cen materiałów w przyszłości nie zmienia już zapisanych wycen.

---

## 2. Rodziny szaf

System obsługuje cztery rodziny (typy) szaf. Każda ma nieco inną logikę obliczania:

### PSH IP65 — Szafa stojąca stalowa
Standardowa wolnostojąca szafa elektryczna ze stali DC01.
Może mieć monoblok (zamiast osobnych boków i góry/dołu), cokół, daszek,
drzwi pojedyncze lub podwójne, tył spawany lub przykręcany.

### PSH COMPACT — Szafa kompaktowa ścienna
Mała szafa montowana na ścianie. Zawsze liczona w kategorii objętości `do400`.
Ma uproszczoną listę elementów — brak daszku, brak opcji monobloku.

### PSH INOX — Szafa ze stali nierdzewnej
Wykonana z blachy INOX 304. Nie wymaga malowania (brak kosztu farby).
Robocizna przy spawaniu i szlifowaniu jest droższa — mnożona przez współczynnik
`inox_labor_factor` (domyślnie ×1,4), bo spawanie stali nierdzewnej jest
trudniejsze i bardziej czasochłonne niż DC01.
Odpad materiałowy liczony jest od kosztów INOX, nie DC01.

### PSH MODULAR — Szafa z szynami DIN
Szafa wyposażona w szyny DIN 35mm do montażu aparatury elektrycznej.
Parametr `vertical_beam` oznacza liczbę rzędów szyn DIN.
Koszt szyn to: `liczba_rzędów × (szerokość_szafy / 1000) × cena_za_metr_szyny`.

---

## 3. Dane wejściowe — co użytkownik podaje

### 3.1 Informacje ogólne o wycenie

| Pole | Opis |
|------|------|
| Klient | Nazwa klienta (informacyjnie, nie wpływa na obliczenia) |
| Typ szafy | Wybór rodziny: IP65 / Compact / INOX / Modular |
| Uwagi | Dowolny tekst (informacyjnie) |

### 3.2 Wymiary szafy (obowiązkowe, w milimetrach)

| Pole | Opis | Uwagi |
|------|------|-------|
| Szerokość (W) | Szerokość szafy | np. 600 mm |
| Wysokość (H) | Wysokość szafy | np. 1000 mm |
| Głębokość (D) | Głębokość szafy | np. 400 mm |
| Grubość blachy korpusu | Grubość blachy DC01/INOX | domyślnie 1,2 mm |
| Grubość płyty montażowej | Grubość płyty DX51 | domyślnie 3,0 mm |

> **Ważne:** Wymiary są podawane w milimetrach. Wszystkie obliczenia powierzchni
> i wagi używają przeliczenia: mm² ÷ 1 000 000 = m².

### 3.3 Opcje konstrukcyjne (tak/nie)

| Opcja | Co dodaje do kalkulacji |
|-------|------------------------|
| Płyta montażowa | Płyta DX51 (H-50 × W-50 mm) + 6 prowadnic + trzpienie M6 + nakrętki M8 |
| Tył spawany | Element blachy tylnej + wzmocnienie tyłu (profil 60 mm) |
| Tył przykręcany | Element blachy tylnej (bez wzmocnienia) |
| Uszczelka tyłu | Uszczelka na obwodzie tyłu szafy |
| Drzwi pojedyncze | Element drzwi + 3 zawiasy + uszczelka drzwi |
| Drzwi podwójne | Drzwi lewe + drzwi prawe + 4 zawiasy + uszczelka |
| Zamek trzypunktowy | Zamek 3-punktowy (bezpieczny) |
| Zamek krzywkowy | Zamek krzywkowy (prosty) |
| Zamek zwykły | Zamek standardowy |
| Cokół | Ramka cokołu (obwód szafy × 150 mm, grubość 2,0 mm) |
| Daszek | Blacha daszku (W+200 × W+50 mm) — tylko IP65 |
| Kolor niestandardowy | Dopłata za lakier inny niż RAL 7035 |

### 3.4 Opcje ilościowe (liczba)

| Opcja | Co dodaje |
|-------|-----------|
| Ilość monobloków | Zamiast osobnych Boków i Góry/dołu (tylko IP65) |
| Wzmocnienia drzwi | Dodatkowe profile wzmacniające drzwi (1,5 mm) |
| Belki pionowe / Rzędy szyn DIN | Pionowe belki IP65 lub szyny DIN w Modular |
| Kapy na przewody | Każda kapa: blacha 300×150 mm + śruby + zaślepki + uszczelka |
| Godziny projektowania | Płatna robocizna projektanta (cena/h × ilość godzin) |

### 3.5 Parametry cenowe (per wycena)

| Parametr | Opis | Domyślna wartość |
|----------|------|-----------------|
| Marża | Mnożnik nakładany na koszt własny | ×2,15 |
| Rabat | Procent odejmowany od ceny katalogowej | 0% |
| Bonus | Dodatkowy procent odejmowany od ceny po rabacie | 0% |

---

## 4. Skąd pochodzą ceny — tabele konfiguracyjne

Administrator systemu może zmieniać ceny materiałów i stawki robocizny
bez modyfikacji kodu. Zmiany te wpływają na wszystkie **nowe** wyceny.
Już zapisane wyceny mają swój snapshot i pozostają niezmienione.

### 4.1 Ceny materiałów i usług (`/kosztorys/admin/prices`)

| Kod | Domyślna cena | Jednostka | Co oznacza |
|-----|--------------|----------|-----------|
| `dc01` | 4,00 PLN | za kg | Blacha stalowa DC01 (standard) |
| `dx51` | 4,60 PLN | za kg | Blacha cynkowana DX51 (płyta montażowa, prowadnice) |
| `inox304` | 18,00 PLN | za kg | Blacha nierdzewna INOX 304 |
| `paint` | 18,00 PLN | za m² | Malowanie proszkowe |
| `seal` | 11,00 PLN | za metr | Uszczelka (gumowa lub piankowa) |
| `stud_m6` | 0,20 PLN | za szt. | Trzpień wstrzeliwany M6 (do płyty) |
| `nut_m8` | 0,05 PLN | za szt. | Nakrętka z podkładką M8 |
| `screw_cap` | 0,05 PLN | za szt. | Śruba do kapy na przewody |
| `plug` | 0,16 PLN | za szt. | Zaślepka otworów |
| `hinge` | 6,00 PLN | za szt. | Zawias (stalowy lub INOX) |
| `lock_3pt` | 50,00 PLN | za szt. | Zamek trzypunktowy |
| `lock_cam` | 5,00 PLN | za szt. | Zamek krzywkowy |
| `lock_standard` | 5,00 PLN | za szt. | Zamek zwykły |
| `custom_color` | 100,00 PLN | za szt. | Dopłata za kolor niestandardowy |
| `design_hour` | 200,00 PLN | za godz. | Stawka projektanta |
| `packaging` | 2,00 PLN | za szt. | Opakowanie kartonowe/folia |
| `labels` | 2,00 PLN | za kpl. | Komplet naklejek (tabliczki, ostrzeżenia) |
| `transport` | 100,00 PLN | za szt. | Koszt dostawy do klienta |
| `fixed_costs` | 50,00 PLN | za szt. | Koszty stałe (energia, amortyzacja, itp.) |
| `din_rail` | 5,00 PLN | za metr | Szyna DIN 35mm (tylko Modular) |
| `inox_labor_factor` | 1,40 | — | Mnożnik robocizny dla INOX (spawanie, szlifowanie) |

### 4.2 Stawki robocizny (`/kosztorys/admin/rates`)

Robocizna jest podzielona na 6 operacji i 3 zakresy objętości szafy.
Administrator podaje kwotę w PLN dla każdej kombinacji.

| Zakres objętości | Opis |
|-----------------|------|
| `do400` | Szafy do 400 dm³ (małe) |
| `do900` | Szafy od 401 do 900 dm³ (średnie) |
| `pow900` | Szafy powyżej 900 dm³ (duże) |

| Operacja | Opis |
|----------|------|
| `laser` | Cięcie laserowe blach |
| `bending` | Gięcie blach (prasa krawędziowa) |
| `welding` | Spawanie elementów |
| `grinding` | Szlifowanie spoin |
| `assembly` | Montaż końcowy (zawiasy, zamki, płyta) |
| `packaging` | Pakowanie do wysyłki |

---

## 5. Obliczanie kosztów — krok po kroku

### KROK 1: Wyznaczenie kategorii objętości

Pierwszym krokiem jest obliczenie objętości szafy, żeby wybrać odpowiednie stawki robocizny:

```
Objętość = Szerokość × Wysokość × Głębokość ÷ 1 000 000   [dm³]

Przykład: 600 × 1000 × 400 ÷ 1 000 000 = 240 dm³  →  kategoria "do400"
```

---

### KROK 2: Obliczenie każdego elementu blaszanego

Każdy element (boki, góra/dół, drzwi, tył, płyta, itp.) obliczany jest tym samym wzorem:

#### 2a. Rozmiary elementu

Rozmiary elementów są obliczane automatycznie z wymiarów szafy:

| Element (IP65) | Długość L | Szerokość Sz | Grubość | Materiał | Malowanie |
|---------------|-----------|-------------|---------|---------|---------|
| Monoblok | H + 2×(D+25) | W + 2×(D+25) | tb | DC01 | TAK |
| Boki (×2) | H | D + 50 | tb | DC01 | TAK |
| Góra/dół (×2) | W | D + 50 | tb | DC01 | TAK |
| Tył spawany | H | W | tb | DC01 | TAK |
| Tył przykręcany | H | W | tb | DC01 | TAK |
| Wzmocnienie tyłu | H - 50 | 60 | 1,5 | DC01 | TAK |
| Drzwi pojedyncze | H + 50 | W + 50 | tb | DC01 | TAK |
| Drzwi prawe | H + 50 | W/2 + 50 | tb | DC01 | TAK |
| Drzwi lewe | H + 50 | W/2 + 50 | tb | DC01 | TAK |
| Wzmocnienie drzwi | H - 50 | 60 | 1,5 | DC01 | TAK |
| Belka pionowa | H + 50 | 120 | tb | DC01 | TAK |
| Kapa na przewody | 300 | 150 | 1,5 | DC01 | TAK |
| Cokół (kpl) | 2×(W+D) | 150 | 2,0 | DC01 | TAK |
| Płyta montażowa | H - 50 | W - 50 | tp | **DX51** | **NIE** |
| Prowadnice (×6) | 550 | 120 | 2,0 | **DX51** | **NIE** |
| Daszek | W + 200 | W + 50 | tb | DC01 | TAK |

> `tb` = grubość blachy korpusu (domyślnie 1,2 mm)
> `tp` = grubość płyty montażowej (domyślnie 3,0 mm)
> Naddatki (+50 mm) to zakładki na zagięcia i spawy.

#### 2b. Wzory obliczeniowe

```
Powierzchnia [m²] = L [mm] × Sz [mm] ÷ 1 000 000

Waga sztuki [kg]  = Powierzchnia × Grubość [mm] × 8,0
                              (gęstość stali z 2% naddatkiem vs. 7,85)

Koszt blachy [PLN] = Waga × cena_materiału
                     DC01:    waga × cena_dc01
                     DX51:    waga × cena_dx51
                     INOX304: waga × cena_inox304

Koszt malowania [PLN] = Powierzchnia × cena_malowania
                        (tylko DC01 i gdy malowanie=TAK)
                        DX51 i INOX — malowania BRAK

Koszt sztuki = Koszt blachy + Koszt malowania
Koszt łączny = Koszt sztuki × Ilość sztuk
```

#### 2c. Przykład obliczenia — Boki szafy 600×1000×400

```
Bok: L=1000 mm, Sz=450 mm (D+50), grubość=1,2 mm, materiał=DC01, ilość=2

Powierzchnia = 1000 × 450 ÷ 1 000 000 = 0,45 m²
Waga         = 0,45 × 1,2 × 8,0       = 4,32 kg

Koszt blachy  = 4,32 × 4,00 PLN/kg   = 17,28 PLN
Koszt malowania = 0,45 × 18,00 PLN/m² = 8,10 PLN

Koszt 1 boku  = 17,28 + 8,10          = 25,38 PLN
Koszt 2 boków = 25,38 × 2             = 50,76 PLN
```

---

### KROK 3: Odpad materiałowy (15%)

Po obliczeniu wszystkich elementów blaszanych naliczany jest odpad:

```
Odpad = suma kosztów blachy DC01 × 15%
       (dla INOX: suma kosztów blachy INOX304 × 15%)
```

**Dlaczego 15%?**
Przy cięciu blach na wymiar zawsze pozostają skrawki, których nie można w pełni
wykorzystać. 15% to branżowy standard dla szaf elektrycznych uwzględniający
straty na wycinanie otworów, krawędzie, błędy cięcia.

> Odpad wagi = całkowita waga elementów × 15% (informacyjnie)
> Odpad kosztowy = koszt blachy × 15% (wchodzi do kosztu własnego)

---

### KROK 4: Robocizna

Robocizna jest pobierana z tabeli stawek dla danej kategorii objętości.
Administrator wpisuje kwoty dla każdej operacji osobno.

```
Robocizna = laser + gięcie + spawanie + szlifowanie + montaż + pakowanie
```

**Dla szafy INOX** spawanie i szlifowanie są droższe (trudniejsze prowadzenie
elektrody, konieczność ochrony powierzchni przed zarysowaniem):

```
Robocizna INOX = laser + gięcie
               + spawanie × inox_labor_factor
               + szlifowanie × inox_labor_factor
               + montaż + pakowanie
```

Domyślnie `inox_labor_factor = 1,40` (robocizna spawania i szlifowania ×1,4).

---

### KROK 5: Osprzęt i uszczelki

Osprzęt jest naliczany automatycznie na podstawie wybranych opcji:

#### Elementy stałe (gdy wybrana opcja):

| Pozycja | Ilość | Wzór |
|---------|-------|------|
| Trzpień M6 | 20 szt. | gdy płyta montażowa |
| Nakrętka M8 | 20 szt. | gdy płyta montażowa |
| Śruby do kap | N × 10 szt. | gdy N kap |
| Zaślepki otworów | N × 2 szt. | gdy N kap |
| Zawias | 3 szt. | drzwi pojedyncze |
| Zawias | 4 szt. | drzwi podwójne |
| Zamek (wybrany typ) | 1 szt. | gdy zamek zaznaczony |

#### Uszczelki (obliczane z wymiarów):

Długość uszczelki = obwód miejsca uszczelnienia w metrach × cena za metr.

```
Uszczelka drzwi pojedynczych:
  długość = 2 × (H + W) ÷ 1000   [metry]
  koszt   = długość × cena_seal

Uszczelka drzwi podwójnych:
  długość = 2 × (2H + W) ÷ 1000  [metry]
  koszt   = długość × cena_seal

Uszczelka tyłu:
  długość = 2 × (H + W) ÷ 1000   [metry]
  koszt   = długość × cena_seal

Uszczelka kapy (każda):
  długość = 2 × (300 + 150) ÷ 1000 = 0,90 m
  koszt łączny = N_kap × 0,90 × cena_seal
```

#### Szyny DIN (tylko Modular):

```
Metrów szyny = liczba_rzędów × (Szerokość ÷ 1000)
Koszt szyn   = metrów_szyny × cena_din_rail
```

---

### KROK 6: Usługi dodatkowe

Te pozycje są doliczane do każdej wyceny (chyba że wynoszą 0):

| Pozycja | Kiedy | Obliczenie |
|---------|-------|-----------|
| Robocizna | zawsze | suma operacji wg stawek |
| Kolor niestandardowy | gdy zaznaczono | stała kwota `custom_color` |
| Koszt projektowania | gdy godziny > 0 | godziny × `design_hour` |
| Opakowanie | zawsze | stała kwota `packaging` |
| Naklejki komplet | zawsze | stała kwota `labels` |
| Transport | zawsze | stała kwota `transport` |
| Koszty stałe | zawsze | stała kwota `fixed_costs` |

---

## 6. Od kosztu własnego do ceny sprzedaży

### 6.1 Koszt własny

```
KOSZT WŁASNY = elementy blaszane
             + odpad materiałowy (15%)
             + osprzęt i uszczelki
             + robocizna i usługi
```

To jest rzeczywisty koszt wyprodukowania szafy przez firmę.

### 6.2 Cena katalogowa (po marży)

```
CENA KATALOGOWA = KOSZT WŁASNY × marża

Przykład: 1 200,00 PLN × 2,15 = 2 580,00 PLN
```

Mnożnik marży 2,15 oznacza, że firma dokłada 115% do kosztu własnego
(koszt własny to ok. 46,5% ceny katalogowej).

### 6.3 Cena po rabacie

```
CENA PO RABACIE = CENA KATALOGOWA × (1 - rabat/100)

Przykład: 2 580,00 × (1 - 10/100) = 2 580,00 × 0,90 = 2 322,00 PLN
```

### 6.4 Cena po bonusie (cena finalna)

```
CENA FINALNA = CENA PO RABACIE × (1 - bonus/100)

Przykład: 2 322,00 × (1 - 5/100) = 2 322,00 × 0,95 = 2 205,90 PLN
```

> Bonus jest naliczany po rabacie — to dodatkowe obniżenie ceny, np. dla
> stałych klientów lub przy zamówieniu większych ilości.

### 6.5 Zyskowność

```
ZYSKOWNOŚĆ [%] = (CENA FINALNA - KOSZT WŁASNY) ÷ CENA FINALNA × 100

Przykład: (2 205,90 - 1 200,00) ÷ 2 205,90 × 100 = 45,6%
```

Zyskowność pokazuje jaki procent ceny finalnej stanowi zysk brutto.
Ujemna zyskowność oznacza sprzedaż poniżej kosztów.

### 6.6 Schemat przepływu ceny

```
┌─────────────────────────────────────┐
│         KOSZT WŁASNY                │  ← elementy + odpad + osprzęt + usługi
└──────────────────┬──────────────────┘
                   │ × marża (np. ×2,15)
                   ▼
┌─────────────────────────────────────┐
│         CENA KATALOGOWA             │
└──────────────────┬──────────────────┘
                   │ - rabat% (np. -10%)
                   ▼
┌─────────────────────────────────────┐
│         CENA PO RABACIE             │
└──────────────────┬──────────────────┘
                   │ - bonus% (np. -5%)
                   ▼
┌─────────────────────────────────────┐
│         CENA FINALNA                │  ← to klient płaci
└─────────────────────────────────────┘
```

---

## 7. Różnice między rodzinami szaf

| Cecha | IP65 | Compact | INOX | Modular |
|-------|------|---------|------|---------|
| Materiał korpusu | DC01 | DC01 | INOX304 | DC01 |
| Malowanie | TAK | TAK | NIE | TAK |
| Kategorie objętości | do400/do900/pow900 | zawsze do400 | do400/do900/pow900 | do400/do900/pow900 |
| Monoblok | TAK | NIE | NIE | NIE |
| Daszek | TAK | NIE | NIE | NIE |
| Szyny DIN | NIE | NIE | NIE | TAK |
| Belki pionowe | TAK | NIE | NIE | NIE |
| Prowadnice płyty | TAK (×6) | NIE | NIE | NIE |
| Mnożnik INOX | NIE | NIE | ×1,4 spawanie/szlif | NIE |
| Odpad od | DC01 | DC01 | INOX304 | DC01 |
| Cokół | TAK | NIE | TAK | TAK |

---

## 8. Statusy wyceny i przepływ pracy

Każda wycena przechodzi przez stany:

```
SZKIC  →  WYSŁANY  →  ZAAKCEPTOWANY  →  ZAMKNIĘTY
(draft)   (sent)      (accepted)         (closed)
```

| Status | Opis |
|--------|------|
| **Szkic** | Wycena w trakcie przygotowania, niezatwierdzona |
| **Wysłany** | Wycena wysłana do klienta |
| **Zaakceptowany** | Klient zaakceptował ofertę |
| **Zamknięty** | Sprawa zakończona (realizacja lub rezygnacja) |

Numer wyceny jest generowany automatycznie:
```
Format: KSZ-RRRR-NNNN
Przykład: KSZ-2026-0042
```

---

## 9. Panel administratora

Administrator ma dostęp do czterech sekcji zarządzania:

### `/kosztorys/admin/prices` — Ceny materiałów i usług
Edycja wszystkich cen używanych w kalkulatorze.
Zmiana ceny wpływa na wszystkie nowo tworzone wyceny.
Istniejące wyceny nie są przeliczane.

### `/kosztorys/admin/rates` — Stawki robocizny
Edycja stawek dla 6 operacji × 3 zakresy objętości.
To jest największy składnik kosztu własnego przy dużych szafach.

### `/kosztorys/admin/cabinets` — Typy szaf
Zarządzanie listą dostępnych rodzin szaf (aktywacja/dezaktywacja).
Każdy typ jest powiązany z kodem kalkulatora (`PSH_IP65`, `PSH_COMPACT`, itp.).

### `/kosztorys/admin/catalog` — Katalog produktów
Baza standardowych produktów z cenami katalogowymi.
Produkty pogrupowane są według rodziny.
Kreator wyceny (`/kosztorys/kreator`) może używać tych produktów jako punktu startowego.

---

## 10. Eksport danych

Każda wycena może być wyeksportowana do dwóch formatów:

### Excel (.xlsx)
Plik zawiera wszystkie sekcje kalkulacji w arkuszu kalkulacyjnym:
- dane nagłówkowe (klient, typ szafy, wymiary)
- tabela elementów blaszanych
- tabela osprzętu
- tabela robocizny i usług
- podsumowanie kosztów i ceny

### PDF
Sformatowany dokument do wysłania klientowi:
- logo firmy
- pełne dane wyceny
- podsumowanie cenowe

---

## 11. Słownik pojęć

| Pojęcie | Wyjaśnienie |
|---------|-------------|
| **DC01** | Stalowa blacha walcowana na zimno — podstawowy materiał korpusów szaf |
| **DX51** | Blacha stalowa cynkowana — używana na płyty montażowe i prowadnice (nie malowana) |
| **INOX304** | Stal nierdzewna chromowo-niklowa (18/8) — droższa, nie wymaga malowania |
| **Monoblok** | Jeden arkusz blachy wygięty w kształt litery U (zastępuje boki + górę/dół) |
| **Kapa na przewody** | Skrzynka odprowadzająca przewody z szafy |
| **Cokół** | Podstawa szafy, profil obwodowy, podnosi szafę nad podłogę |
| **Daszek** | Zadaszenie szafy chroniące przed deszczem |
| **Płyta montażowa** | Perforowana blacha DX51 wewnątrz szafy do montażu aparatury |
| **Prowadnice** | Profile prowadzące płytę montażową (6 szt.) |
| **Uszczelka** | Profil gumowy/piankowy zapewniający IP65 (woda i pył) |
| **Trzpień M6** | Element wstrzeliwany w płytę montażową do jej mocowania |
| **Koszt własny** | Rzeczywisty koszt produkcji szafy (bez zysku) |
| **Marża** | Mnożnik na koszt własny; ×2,15 = 115% narzutu |
| **Zyskowność** | Procent ceny finalnej stanowiący zysk (po wszystkich rabatach) |
| **Snapshot kalkulacji** | Zapis wyniku obliczeń w momencie zapisu wyceny — niezmieniany przy aktualizacji cen |
| **WASTE_PCT** | Stały współczynnik odpadu materiałowego = 15% |
| **STEEL_DENSITY** | Przyjęta gęstość stali = 8,0 kg/m²/mm (z ~2% naddatkiem ponad 7,85) |

---

*Dokument wygenerowany: 2026-06-29 | System PS — moduł KOSZTORYSY*
