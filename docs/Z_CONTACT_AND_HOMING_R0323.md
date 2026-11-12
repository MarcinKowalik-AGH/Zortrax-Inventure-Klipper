# Inventure V9 — kontakt dyszy i odniesienie Z

## Próba styku na postoju

Drukarka ma być w bezruchu, a dysza i tacka ostudzone. Użyj czystego metalowego
pola kalibracyjnego tacki i czystej końcówki wysuniętej dyszy. Bez poruszania
stołem połącz te dwie powierzchnie czystym przewodzącym łącznikiem. Łącznik
dotyka wyłącznie końcówki dyszy i pola na tacce.

Wykonaj `INVENTURE_INPUTS` osobno przed połączeniem, przy utrzymanym
połączeniu i po zabraniu łącznika. Polecenie pobiera świeży odczyt z MCU.

| Warunek | `raw.nozzle_contact` | Bit 3 w `raw_mask` |
|---|---:|---:|
| Rozwarte | 1 | 1 |
| Połączone | 0 | 0 |
| Ponownie rozwarte | 1 | 1 |

PC14 to kontakt dyszy aktywny w stanie niskim. PE12 to dolna krańcówka Z,
aktywna w stanie wysokim. PC13 to detektor osadzenia tacki, aktywny w stanie
niskim. Zmiana PC14 przy nieruchomym PE12 potwierdza niezależność torów.

## Ograniczona próba zatrzymania

Po uzyskaniu `1 → 0 → 1` można sprawdzić zatrzymanie w MCU. Początek próby
wymaga ustawienia wysuniętej dyszy nad metalowym polem z widoczną szczeliną
mieszczącą się w zadanym zakresie. Łącznik użyty podczas próby na postoju
ma być już usunięty. Przykład ogranicza ruch do 80 kroków, nominalnie 0,10 mm.

```gcode
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_MOVE AXIS=Z STEPS=80 DIR=1 RATE=160 MAX_MS=2000 TIMER=1
```

`DIR=1` podnosi stół. Host i MCU dodają maskę `8` i poziom `0` dla PC14.
Po zatrzymaniu odczytaj `INVENTURE_MOTION ACTION=STATUS`. Dotknięcie pola
powinno dać `reason: stop_input`, `hit: 8` i `hit_inputs: [nozzle_contact]`.
Aktywny styk przy starcie daje zero kroków. Wykonanie wszystkich kroków
z `hit: 0` oznacza koniec zadanego zakresu. Odczyt bez kontaktu oraz kontakt
przy starcie pozostawiają próbę zatrzymania w ruchu do sprawdzenia.
`INVENTURE_STOP` przerywa ruch. Ruch serwisowy nie ustanawia współrzędnej Z.

## Sekwencja firmware 1.6.1 r0323

| Etap | Zachowanie r0323 | Ślad kodu |
|---|---|---|
| Początek | Opróżnienie kolejki, zapewnienie bazowania Y, potem X. Stan bez referencji uruchamia pomocnika dolnego Z na PE12. | `0x0801DD34`, `0x0801D5A2`, `0x0801D59C`, `0x0801D6C0` |
| Podejście | Ruch XY z zadanym F8000 do punktu 14, X67,5 / Y65,5 mm w fabrycznym układzie. Następnie zadane Z = 1604 kroki, czyli 2 mm w tym układzie. | `0x0801DD62–0x0801DD92` |
| Pomiar | Szukanie kontaktu na PC14, odjazd do rozwarcia i ponowienia. Zakres wynosi 8020 kroków względem pozycji początkowej. Kod zachowuje najlepsze rozwarcie i kończy normalny cykl po 101 kolejnych cyklach bez poprawy. | `0x0801DD9E–0x0801DDB0`, `0x0801DC70`, `0x0801D368` |
| Wynik | Ustanowienie Z w pozycji rozwarcia i zapis głębokości w rekordzie 52. Silnik rozróżnia brak pierwszego kontaktu (`INT_MAX`) i niezwalniający kontakt (`INT_MIN`). | `0x0801DDB4–0x0801DDEA`, `0x0801DE0C`, `0x0801DE3E` |

W wywołaniu silnika pomiarowego `100` steruje licznikiem powtórzeń, a trzeci
argument `800` pozostaje nieodczytany. Kroki pomiarowe są rozdzielane pętlą
opóźnienia i startem timera. F8000 dotyczy dojazdu XY. Dokładna prędkość
kontaktowa na sprzęcie wymaga pomiaru. Handler homingu Z nie zadaje wprost
temperatury dyszy. Interaktywny kreator poziomowania i kalibracja wysokości
dysz mają własne przygotowanie termiczne.

## Implementacje Klippera

R7 SERVICE używa PE12 dla dolnej referencji oraz PC14 jako lokalnego
warunku zatrzymania przy podnoszeniu stołu. Wynik fizycznej próby dolnej
krańcówki jest zapisany osobno. `INVENTURE_PROCEDURE ACTION=HOME` w R7
wyznacza referencję dolną i XY do ruchów serwisowych. [Wynik próby PE12](Z_LOWER_LIMIT_TEST_2026-11-09.md)

Repozytorium zawiera wcześniejszą implementację P03 w archiwum
`INVENTURE_RC7_2_PUBLIC_GNU_ARM_SOURCE.zip` z części `source/parts/part*.bin`.
W `regression_tests/overlay/klippy/extras/` pliki `inventure_motion.py`,
`inventure_motion_core.py` i `inventure_contact.py` mają lokalne wyzwalanie
`HomingMove` na PC14, ograniczone podejście i rozwarcie, `ReleaseTracker`
oraz `INVENTURE_REFERENCE_Z`. Konfiguracja `regression_tests/config/printer.cfg`
ma P03 domyślnie wyłączone. Natywne P09 korzysta ze wspólnego pomiaru PC14
do regulacji pięciu pól. Bieżąca aktualizacja R7 nie instaluje tych modułów.
Pełne odniesienie Z wymaga potwierdzenia styku, położenia pola w bieżącym
układzie XY, drogi kontaktu i odjazdu, powtarzalności oraz warunków cieplnych.

Źródła projektu obejmują pełny listing Thumb i aplikację r0323 z
`ZORTRAX_INVENTURE_KOMPLET_2026-10-02_CZESC_1_z_2.zip`, sekcję 12 raportu
o ruchu z `Audyt firmware.zip`, audyt wysokości dysz z 23.10.2026 oraz
kontrakt P09 z 25.10.2026.
