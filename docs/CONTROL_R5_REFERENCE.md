# CONTROL-R5 — interfejs i mapa sprzętu

## Ruch i STOP

`INVENTURE_CONTROL MODE=OFF/SERVICE/NATIVE` wybiera właściciela wyjść.
Bez MODE odczytuje stan. `INVENTURE_STOP` zatrzymuje wszystkie wykonawcze
wyjścia, pozostawiając wymagane chłodzenie. SERVICE umożliwia kolejną próbę;
STOP po NATIVE wymaga `FIRMWARE_RESTART`, aby usunąć poprzednie kolejki.

Heartbeat działa co 250 ms; MCU wyłącza wyjścia po 1 s bez heartbeat albo
utraty zdrowego stanu przewodu PD11. NATIVE wymaga świeżych i poprawnych ADC
oraz zgodności z wybraną polityką osłon.

`INVENTURE_MOVE` przyjmuje `AXIS=X/Y/Z/E/A/B`, podpisane `DISTANCE` w mm
albo `STEPS`, `SPEED` w mm/s albo `RATE` w impulsach/s. `DIR=0/1` bezpośrednio
wybiera kierunek A/B/Z/E. X/Y używają podpisanej odległości i CoreXY.

```gcode
INVENTURE_MOTION A=640 B=-320 Z=0 E=0 RATE=640 PULSE_US=10 MAX_MS=5000 HOLD_MS=0
INVENTURE_MOTION ACTION=STATUS
INVENTURE_MOTION ACTION=STOP
```

DDA zachowuje stosunek niezależnych podpisanych impulsów A/B/Z/E.
Zakres: do 2 000 000 impulsów na napęd; `RATE=1..20000`; `PULSE_US=2..100`,
z co najmniej 2 µs LOW; `MAX_MS=1..600000`; `HOLD_MS=0..60000`.
RATE jest maksimum dla najliczniejszego napędu; działa rozpędzanie/hamowanie.
Zakończenie, STOP, limit czasu lub wejście zwalnia wspólne ENABLE.

| Napęd | STEP | DIR | Nominalna skala do pomiaru |
|---|---|---|---|
| A | PA7 | PB2 | 640 kroków/mm X/Y |
| B | PA6 | PB1 | 640 kroków/mm X/Y |
| Z | PB8 | PE7 | 802 kroków/mm |
| E | PB9 | PE8 | 960 kroków/mm |

Wspólne ENABLE PE10 jest aktywne HIGH: natywny `enable_pin` bez `!`.
OEM PG5=1, PG6=0, PG7=0 zachowano. X+ daje DIR A/B=1/1, X−=0/0;
Y+=1/0, Y−=0/1. Ruch ujemny X/Y domyślnie obserwuje własne minimum aktywne 1.
Dodatni pozwala odjechać z aktywnej krańcówki. Ruch nie ustala pozycji/homingu.

## Dowolne wejścia zatrzymujące

`STOP_MASK` wybiera bity; `STOP_VALUE` podaje ich aktywne poziomy.
Zatrzymuje **dowolny** wybrany bit zgodny z wartością. Aktywny już przed START
kończy próbę bez impulsów. Jawna maska zastępuje maskę domyślną danego ruchu.
Dla Z/E nie przyjęto automatycznie polaryzacji niezakwalifikowanych wejść.

| Bit | Nazwa | Pin | Wynik fizyczny |
|---|---|---|---|
| 0 | x_endstop | PB11 | potwierdzony, aktywny 1 |
| 1 | y_endstop | PC15 | potwierdzony, aktywny 1 |
| 2 | z_bottom | PE12 | surowo 1, próba mechaniczna oczekuje |
| 3 | nozzle_contact | PC14 | surowo 1 otwarty, zwarcie dyszy oczekuje |
| 4 | tray | PC13 | przełączanie potwierdzone |
| 5 | bin | PG2 | przełączanie potwierdzone |
| 6 | front_door | PB10 | potwierdzony, 1 zamknięte |
| 7 | lid | PE15 | potwierdzony, 1 zamknięte |
| 8 | t0_a | PD10 | odczyt dostępny, przełączanie oczekuje |
| 9 | t0_b | PF1 | odczyt dostępny, przełączanie oczekuje |
| 10 | t1_a | PD15 | odczyt dostępny, przełączanie oczekuje |
| 11 | t1_b | PG10 | odczyt dostępny, przełączanie oczekuje |
| 12 | up | PD7 | potwierdzony |
| 13 | down | PD6 | potwierdzony |
| 14 | ok | PD5 | potwierdzony |
| 15 | power | PA15 | odczyt dostępny, test fizyczny oczekuje |

`STOP_MASK=8 STOP_VALUE=0`: PC14 aktywny LOW.
`STOP_MASK=12 STOP_VALUE=4`: PE12=1 lub PC14=0. Przy obecnym PE12=1 drugi
przykład skończy próbę od razu; nie odwracamy wejścia na podstawie samego spoczynku.

## Wyjścia

```gcode
INVENTURE_OUTPUT CHANNEL=F2 ACTION=SET POWER=50 DURATION=20
INVENTURE_OUTPUT CHANNEL=PC8 ACTION=SET POWER=75 DURATION=180 LIMIT=50
INVENTURE_OUTPUT CHANNEL=PC8 ACTION=STATUS
INVENTURE_OUTPUT CHANNEL=PC8 ACTION=OFF
```

Kanały: F0..F5, T0, T1, PC9, PC8. `POWER=0..100`, `DURATION=0.001..600` s;
zero wyłącza. POWER oznacza stałe wysterowanie, nie regulację temperatury.
LIMIT wyłącznie odcina: 40..310°C dla dysz, 40..150°C dla komory, z uwzględnieniem
niższej ustawionej granicy GUARD. Nie ma wymogu szybkiego przyrostu jak hotend.
MCU odcina po czasie, progu, błędnym/starym ADC, utracie heartbeat/cable lub STOP.
Wynik podaje powód wyłączenia. Chłodzenie działa przed grzaniem i co najmniej
5 s po odcięciu, a gorące czujniki utrzymują je do schłodzenia.

| Kanał | PWM / tach | Ustalona funkcja |
|---|---|---|
| F0 | PB15 / PE14 | para chłodzenia głowicy |
| F1 | PB14 / PE13 | dolna dmuchawa modelu |
| F2 | PC7 / PE9 | fizycznie prawa strona komory |
| F3 | PC6 / PE11 | fizycznie lewa strona komory |
| F4 | PA1 / brak | dolny tył HEPA |
| F5 | PD13 / brak | górna tylna para |
| T0 | PA0 / czujnik PC0 | grzałka T0 |
| T1 | PE5 / czujnik PF8 | grzałka T1 |
| PC9 | PC9 / czujnik PC1 | grzałka komory |
| PC8 | PC8 / czujnik PC2 | grzałka komory |

Wszystkie grzałki aktywne LOW. SERVICE F0..F3 używa 400 Hz;
pozostałe sprzętowych stałych podstaw czasu. POWER to wysterowanie;
kalibracja obrotów/przepływu jest osobna. Historyczne nazwy telemetrii
`chamber_left/right` zachowano; fizyczną stronę F2/F3 podano w tabeli.

`INVENTURE_FEEDER CHANNEL=0/1 DIRECTION=0/1 DURATION_MS=0..1500`:
PC5/PG11 RUN, PA8/PB7 DIR, PE6 wspólny zegar. Zero wyłącza.
Natywny P13 zachowuje profile, AUTO, asystę, selekcję E i kontrolę budżetu filamentu.
`INVENTURE_LOCK CHANNEL=0/1 VALUE=0/1`: 0 klapa PA3, 1 drzwi PA2;
przyciąganie 100 ms, podtrzymanie 60/256.
`INVENTURE_RGB MASK=0..7` steruje sygnalizacją.
`INVENTURE_RFID_STATUS` i `INVENTURE_RFID_SEND CHANNEL=0/1 HEX=...`
udostępniają oba UART-y. Przy aktywnym P12/P13 zachowano własność procedur.

## Temperatura i telemetria

| ADC | Pin | Nazwa | Domyślny próg MCU |
|---|---|---|---|
| 0 | PC0 | t0 | 310°C |
| 1 | PF8 | t1 | 310°C |
| 2 | PC1 | chamber_left | 150°C |
| 3 | PC2 | chamber_right | 150°C |
| 4 | PA4 | chamber_center; docelowy monitor przegrzania | 150°C |
| 5 | PF3 | psu | 100°C |
| 6 | PF4 | mainboard | 100°C |
| 7 | PC4 | tool_pcb; niezweryfikowany | wyłączony z maski |

`INVENTURE_SENSORS` pokazuje surowe ADC, temperaturę, stan i wiek próbek,
wejścia, sześć liczników tach/filamentu, PD11 i UART-y RFID. Korzysta z
cyklicznego odświeżania; przeterminowane odczyty nie są pokazywane jako aktualne.
`INVENTURE_SENSOR_TEST SENSOR=PF3 ACTION=START` zachowuje okno obserwacji 20 s.

```gcode
INVENTURE_GUARD SENSOR=PA4 ENABLE=1 MIN=-20 MAX=80
```

Przykład ustawia próg PA4 do danej próby. Nie przypisuje mu wentylatora ani
nie potwierdza położenia fizycznego. Progi zmienia się przy zatrzymanych
wyjściach; restart przywraca wartości domyślne. Pierwsze pięć czujników
pozostaje obowiązkowych przy grzaniu/NATIVE. PF3/PF4 można jawnie wyłączyć,
PC4 dodać po wyjaśnieniu pomiaru. Nastawy MCU nie zastępują konfiguracji Klippera.

## Natywny backend Klippera

Sterowniki silników używają zwykłych pinów i `MODE=NATIVE`. `printer.cfg`
nie jest automatycznie zmieniany. Do PWM R5 przeznaczony jest chip `inventure_r5`.

| Pin wirtualny | Fizyczny | Okres [s] | Sprzętowy |
|---|---|---|---|
| HEATER_T0 | !PA0 | 0.0000469333333333 | tak |
| HEATER_T1 | !PE5 | 0.000106666666667 | tak |
| HEATER_PC9 | !PC9 | 0.139808 | tak |
| HEATER_PC8 | !PC8 | 0.139808 | tak |
| F0 / F1 / F2 / F3 | PB15 / PB14 / PC7 / PC6 | 0.1 | nie |
| F4 | PA1 | 0.0000469333333333 | tak |
| F5 | PD13 | 0.017472 | tak |

Fragment właściwej standardowej sekcji heatera T0:

```ini
heater_pin: inventure_r5:HEATER_T0
sensor_type: Inventure R5 ADC
sensor_channel: 0
pwm_cycle_time: 0.0000469333333333
```

T1: kanał 1 i okres z tabeli; PC9: kanał 2; PC8: 3. Wirtualnych nazw nie odwraca
się `!`; robi to adapter. `diagnostic_adc: True` utrzymuje jeden odczyt wszystkich
ADC. Typ `Inventure R5 ADC` nie tworzy dodatkowego `sensor_pin`.
PID, verify_heater, zakresy temperatur, geometria, homing Z i procedury filamentu
wymagają właściwej konfiguracji oraz prób. Backend jest dostępny, kwalifikacja
wydruku pozostaje kolejnym etapem.

## Weryfikacja

Paczka MCU zawiera nowy overlay, słownik z kompilacji, manifest źródeł, testy
i powrót do R4. `TESTS/run_all.py`: 120 testów hosta/instalacji/bootloadera/protokołu.
Osobne programy C wykonują rzeczywisty kod MCU z rejestrami zastępczymi.
Publiczne CI orange-pi obejmuje 44 testy adaptera/aktualizatora z przejściem R4→R5.
Symulacja nie jest fizycznym testem nowego wsadu.
