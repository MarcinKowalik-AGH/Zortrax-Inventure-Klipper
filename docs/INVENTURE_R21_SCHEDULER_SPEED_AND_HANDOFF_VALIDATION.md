# Inventure R21 — odciążenie harmonogramu STM32 + walidacja R19/R20

Status: **kandydat do prób sprzętowych**, niezweryfikowany na fizycznej Inventure.

## Punkt odniesienia: działająca M300

Kod `Zortrax-m300-Klipper/M300_HOST/payload/m300_native_motion.py`
zachowuje natywną kolejkę, `G28`, `HomingMove` i sterowanie osiami
w Klipperze. Nie uruchamia równolegle własnego programu normalnego Home.
Inventure R19/R20 odtwarza ten podział i przekazuje sterowanie STM32
jedynie na czas wyboru T0 i czyszczenia.

Bieżące profile bazowe (mogą różnić się od ustawień na drukarkach):
- M300 Cartesian: 128 microsteps, 200 steps/obrót, rotation_distance=40 mm
  => 640 step/mm. Bazowe max_velocity=300 mm/s.
- Inventure CoreXY: 16 microsteps, 200 steps/obrót, rotation_distance=5 mm
  => również 640 step/mm. Bazowe max_velocity=30 mm/s.
- Polecenie 800 mm/s wymaga do około 512 000 impulsów/s na silnik w
  kartezjańskim ruchu osiowym. W CoreXY przy ruchu 45 stopni jeden z
  napędów potrzebuje do około 724 077 impulsów/s. Realna prędkość
  zależy ponadto od przyspieszenia, długości drogi i sterowników silników.

**To są wymagane częstości impulsów, a nie potwierdzenie uzyskania
800 mm/s.** Test jedynie ustawionej prędkości nie potwierdza chwilowej
prędkości fizycznej. R21 nie podnosi limitów osi ani nie deklaruje
wydajności 800 mm/s na Inventure.

## R18 i przyczyna obserwowana

W logu MCU R18 występuje `Timer too close` podczas natywnego
`INVENTURE_HOME_XY` po wcześniejszym ukończonym `Home XYZ`, przy
niskim `sysload`. Nie jest to błąd progów SERVICE ani dowód na winę UART.
R18 posiada własny `sample_event` co 50 mikrosekund przez całą sesję
NATIVE i SERVICE. Kolejne wywołanie było planowane na podstawie
**starego** `waketime`; opóźnienia mogły tworzyć kaskadę zaległych
wywołań. Jest to konkretna hipoteza wydajnościowa, nie dowód wyłącznej
przyczyny `Timer too close`.

## Punkt 3 — zmiany MCU R21

- 20 kHz próbkowania pozostaje w SERVICE oraz podczas aktywnego
  transportu P13 / sesji factory / autonomicznego DDA / testu grzałki.
- NATIVE bez takich sesji używa 10 kHz próbkowania czujników. Jest to
  **częstotliwość przerwania próbnika**, nie limit prędkości STEP.
- Przy spóźnionym wywołaniu nie odtwarza zaległych 50-us cykli i nie
  zajmuje na nie wyłącznego czasu rdzenia. Termin jest ustawiany
  względem realnego bieżącego zegara.
- Kontrola PC13, PD11, temperatur, heartbeat i innych osłon nadal
  otrzymuje termin co 10 ms czasu fizycznego. Przy opóźnieniu
  próbkowania nadzorca uruchamia się z prawdziwym `now` — nie dopiero
  po odliczeniu 200 nowych wywołań próbnika.
- Okna wentylatorów i impulsów P13 uwzględniają rzeczywisty upływ
  czasu. Pominięte zbocza nie są syntetycznie dopisywane.
- `inventure_scheduler_query` w MCU i read-only
  `INVENTURE_TIMING_STATUS` w Fluidd raportują czas pracy i spóźnienia
  próbnika: `resync`, `skipped`, `peak_late`, `peak_work`.
- Żaden wskaźnik nie wyłącza mechanicznego interlocku albo monitoringu
  grzania, a pętla NATIVE nie zmienia sposobu generowania STEP w Klipperze.

## Punkty 1 i 2 — diagnostyka przejęcia

Host zapisuje zdarzenia `INVENTURE_OWNER_AUDIT` w `klippy.log`:
1. `klipper_xy_certified` (pojedynczy natywny home/XY frame)
2. `service_handoff_requested` (MCU owner/run/token)
3. `service_handoff_verified` (osobny odczyt MCU mode=NATIVE)
4. `klipper_xy_imported` (dopiero po poprawnym receipt)

Błędny właściciel, brak zwrotu, powtórne przejęcie i import pozycji
bez potwierdzenia są blokowane przez R20, a logi można dodatkowo
zbadać za pomocą **wyłącznie odczytowego** analizatora:

```bash
python3 tools/inventure_fault_analyzer.py ~/printer_data/logs/klippy.log
python3 tools/inventure_fault_analyzer.py klippy-log.zip --output raport.json
```

Analizator odróżnia `Timer too close` od fizycznego `local safety
fault`, ostrzega o retransmisjach UART bez fałszywego przypisywania
ich za przyczynę oraz wykrywa brak / konflikt tokena zwrotnego.
Stary log R18 nie ma zdarzeń R21; ich brak jest raportowany jako
ograniczenie danych, a nie jako potwierdzenie awarii sterowania.

## Walidacja i kryterium stabilności

CI:
- rzeczywiste źródła C MCU w testach rejestrowych: NATIVE/SERVICE,
  sztuczny backlog 50 us, przepełnienie `uint32`, opóźnienie 13 ms,
  okno tacho/encodera, real-time guard i nieprzywracalne STOP;
- kompilacja `INVENTUR.IVB` przypiętym GNU ARM 14.2.1 z kontrolą
  firmware/dictionary/CRC/ABI i sumy SHA256;
- host: natywne HOME X/Y/XY/Z/All, oddzielna selekcja T0 SERVICE,
  brak drugiego Home XY, exact owner/run/token + readback, odmowa przy
  braku receipt, testy konfiguracji, OLED, instalacji i rollback;
- log analyzer: wzorce wypadków MCU R18, powtórny token, niezgodny
  etap, błędne i zgodne liczniki planera.

**Próby fizyczne, jeszcze niewykonane**:
- kilka cykli Home X/Y, Home Z, Home All bez ruchu sprzecznego z PE12/PC13;
- szybkie pojedyncze przejazdy X, Y i ukośne CoreXY z porównaniem
  *rzeczywistej* prędkości, błędów timera i UART; zwiększać prędkość
  dopiero po potwierdzeniu stabilności niższej;
- obserwować `INVENTURE_TIMING_STATUS` przed i po obciążeniu,
  `klippy.log` i komunikaty fizycznego OLED;
- wymagana zgodność T0/T1, weryfikacja rzeczywistego wyboru dyszy
  oraz poprawny powrót bez drugiego bazowania;
- druk i kompensacja Bed Mesh nie mogą generować konkurencyjnych
  kroków. Natomiast OEM pięciopunktowe poziomowanie nadal jest
  odrębną procedurą SERVICE; jego działanie nie jest dowodem, że
  skonfigurowano niezależny probe dla BED_MESH_CALIBRATE.

R21 nie jest gotowym produkcyjnym firmware tylko dlatego, że CI
zbudowało obraz. Issue #3 pozostaje otwarte do fizycznego wyeliminowania
`Timer too close`.
