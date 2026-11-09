# Zortrax Inventure — CONTROL-R5

Jeden interfejs MCU do kolejnych prób wybieranych z Klippera: A/B/Z/E,
CoreXY X/Y, oba podajniki kaset, cztery grzałki, sześć wentylatorów, zamki,
RGB, osiem ADC, 16 wejść, tachometry, liczniki filamentu i oba UART-y RFID.
Dostępne jest też natywne STEP/PWM Klippera. Parametry prób ustawia host.

## Aktualizacja Orange Pi

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only && python3 "$HOME/inventure-orange-pi/install-orange-pi.py"
```

Pierwsza instalacja, jeśli katalog jeszcze nie istnieje:

```bash
git clone --depth 1 --single-branch --no-tags --branch orange-pi https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper.git "$HOME/inventure-orange-pi" && python3 "$HOME/inventure-orange-pi/install-orange-pi.py"
```

Instalator wykrywa działającą usługę i właściwy `klippy.py`, zachowuje kopie
modułów, instaluje adapter, OLED i zgodny transport UART oraz restartuje usługę.
Przyjmuje także dokładny poprzedni moduł MOTOR-R4. Zachowuje `printer.cfg`,
750000 baud i aktualną geometrię. MCU otrzymujesz osobno w czacie jako
`INVENTURE_MCU_CONTROL_R5_BOOT17.zip`: aplikacja `MCU/INVENTUR.IVB` do bootloadera 1.7.

## Po aktualizacji hosta i jednorazowym wgraniu MCU

```gcode
INVENTURE_VERSION
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_DOOR_POLICY ENABLE=0
INVENTURE_SENSORS
```

Oczekiwane: `host_revision` i `mcu_release` = `RC8-CONTROL-R5`,
`control_abi: 1`, `motion_abi: 1`, `output_abi: 1`, `baud_match: true`.
Po zmianie MCU użyj `FIRMWARE_RESTART`, jeśli host utrzymuje poprzedni stan.
`ENABLE=0` zachowuje ustaloną możliwość obserwacji przy otwartych drzwiach
oraz klapie. Ich surowe odczyty nadal są widoczne. Restart MCU przywraca kontrolę osłon.

Wspólny STOP napędów, podajników, grzałek, wyjść serwisowych i zamków:

```gcode
INVENTURE_STOP
```

Chłodzenie wymagane przez temperaturę działa dalej. W SERVICE można od razu
wybrać następną próbę. STOP po NATIVE zatrzaskuje wyjścia; do ponownego
uzbrojenia potrzeba `FIRMWARE_RESTART`, aby stare kolejki STEP/PWM nie wznowiły ruchu.

## Pojedyncze próby

Poniższe wiersze są osobnymi przykładami. Wybieraj jeden element naraz.
X/Y mają potwierdzone kierunki. Skale mm oraz kierunki Z/E wymagają dalszego
pomiaru. Ruchy serwisowe nie ustawiają pozycji ani stanu homingu.

| Element | Przykład |
|---|---|
| X, 1 mm od minimum | `INVENTURE_MOVE AXIS=X DISTANCE=1 SPEED=2` |
| Y, 1 mm od minimum | `INVENTURE_MOVE AXIS=Y DISTANCE=1 SPEED=2` |
| Mała próba Z z obserwacją kontaktu dyszy | `INVENTURE_MOVE AXIS=Z STEPS=40 DIR=0 RATE=160 MAX_MS=2000 STOP_MASK=8 STOP_VALUE=0` |
| E po zwolnieniu toru filamentu | `INVENTURE_MOVE AXIS=E STEPS=48 DIR=0 RATE=160 MAX_MS=2000` |
| Podajnik kasety 0 | `INVENTURE_FEEDER CHANNEL=0 DIRECTION=0 DURATION_MS=100` |
| Podajnik kasety 1 | `INVENTURE_FEEDER CHANNEL=1 DIRECTION=0 DURATION_MS=100` |
| F0, połowa wysterowania | `INVENTURE_OUTPUT CHANNEL=F0 ACTION=SET POWER=50 DURATION=20` |
| PC9, stałe 75%, próg 50°C | `INVENTURE_OUTPUT CHANNEL=PC9 ACTION=SET POWER=75 DURATION=180 LIMIT=50` |
| Wyłączenie PC9 | `INVENTURE_OUTPUT CHANNEL=PC9 ACTION=OFF` |
| Zamek klapy: włącz / zwolnij | `INVENTURE_LOCK CHANNEL=0 VALUE=1` / `INVENTURE_LOCK CHANNEL=0 VALUE=0` |
| Zamek przedni: włącz / zwolnij | `INVENTURE_LOCK CHANNEL=1 VALUE=1` / `INVENTURE_LOCK CHANNEL=1 VALUE=0` |
| Stan sterowania i prób | `INVENTURE_CONTROL_STATUS` |
| Wszystkie odczyty | `INVENTURE_SENSORS` |

`LIMIT` oznacza wyłączenie po przekroczeniu progu, nie zadaną temperaturę.
Grzałka SERVICE utrzymuje `POWER`, bez wymogu szybkiego przyrostu temperatury.
Moc, czas, próg i parametry ruchu można zmieniać bez kolejnego wsadu.
Przykład Z obserwuje PC14. PE12 ma obecnie surowy stan 1 i nie jest jeszcze
zakwalifikowane mechanicznie; nie zmieniono jego polaryzacji na podstawie tego odczytu.

## Natywny Klipper

`INVENTURE_CONTROL MODE=NATIVE` otwiera standardowe STEP/PWM i transport P13.
SERVICE i NATIVE mają osobnych właścicieli wyjść; po restarcie domyślny tryb
jest OFF. Dotychczasowe testy R1–R4 pozostają dostępne.

R5 dodaje chip `inventure_r5` z aktywną niskim stanem polaryzacją wszystkich
czterech grzałek i właściwymi sprzętowymi okresami PWM. `Inventure R5 ADC`
korzysta z dotychczasowego pojedynczego odczytu ośmiu ADC. Host odmawia
uzbrojenia rozpoznanej natywnej grzałki o niezgodnej polaryzacji lub PWM.
Dotychczasowy `printer.cfg` nie jest automatycznie przekształcany w konfigurację wydruku.

Parametry, piny i konfiguracja: [CONTROL_R5_REFERENCE.md](docs/CONTROL_R5_REFERENCE.md).
Historia R4: [HOST_R4_HISTORY.md](docs/HOST_R4_HISTORY.md).

## Sprawdzenie

Użytkownik potwierdził wcześniej X/Y, dojazd do obu krańcówek oraz odjazd
z ponownym wolnym kontaktem R4. Jeden taki test na oś nie wyznacza jeszcze
statystycznej powtarzalności ani pełnych wymiarów pola roboczego.

R5 przeszedł testy hosta, aktualizatora, prawdziwego parsera G-code, kodeka
ze skompilowanego słownika i rzeczywistego kodu MCU z rejestrami zastępczymi.
Nowe MCU R5 nie było jeszcze testowane na drukarce. PC4 jest nadal pomiarem
niezweryfikowanym i domyślnie nie należy do maski zabezpieczeń. Z, podajniki
i procedury wydruku wymagają kolejnych pojedynczych prób sprzętowych.
R5 wymaga domyślnie pierwszych siedmiu czujników temperatury.
