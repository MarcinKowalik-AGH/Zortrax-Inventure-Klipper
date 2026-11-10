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

## Po aktualizacji hosta

```gcode
INVENTURE_VERSION
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_DOOR_POLICY ENABLE=0
INVENTURE_SENSORS
```

Oczekiwane: `host_revision` = `RC8-CONTROL-R5-LOAD230-R3`, `mcu_release` = `RC8-CONTROL-R5`,
`control_abi: 1`, `motion_abi: 1`, `output_abi: 1`, `baud_match: true`.
Po zmianie MCU użyj `FIRMWARE_RESTART`, jeśli host utrzymuje poprzedni stan.
`ENABLE=0` zachowuje ustaloną możliwość obserwacji przy otwartych drzwiach
oraz klapie. Ich surowe odczyty nadal są widoczne. Restart MCU przywraca kontrolę osłon.

Etap przejęcia filamentu przez ekstruder przy regulowanych 230°C:

```gcode
INVENTURE_LOAD_TEST ACTION=HEAT TOOL=T0 TARGET=230
```

Po `ready_for_jog: true`, jeden krótki ruch głównego E:

```gcode
INVENTURE_LOAD_TEST ACTION=JOG DIR=0 STEPS=960 RATE=960
```

Program porównuje wejścia przed i po zakończeniu ruchu. Przeciwny DIR=1
można wydać jako osobne polecenie po `jog_done`. Wspólny `INVENTURE_STOP`
kończy też regulator temperatury. [Miejsce etapu w pełnym load, potwierdzenia
fabrycznego firmware, parametry i procedura](docs/LOAD_STAGE_230_2026-11-10.md).

R3 reguluje moc płynnie przez PID dysz odtworzony w audycie r0323.
Po pierwszym osiągnięciu obszaru celu przez 2 s gotowość E pozostaje aktywna
w osobnym zakresie: dla celu 230°C jest to 215..238°C. Wahanie do 224°C
nie zeruje ponownie gotowości. Przekroczenie zakresu odbiera pozwolenie,
a do jego odzyskania trzeba ponownie osiągnąć obszar celu. Kontrole przegrzania,
czujników, tachometru i STOP pozostają aktywne. PID wstrzymuje całkowanie
podczas krótkiego JOG, gdy grzałka jest wyłączona przez zasady SERVICE.

Aktualizacja SENSORS wykorzystuje już wgrane CONTROL-R5. `INVENTURE_SENSORS`
i `INVENTURE_INPUTS` czekają na odpowiedź MCU. Nowe
`INVENTURE_CASSETTE_DIAG ACTION=BASELINE/COMPARE` porównuje rzeczywiste wejścia
GPIOA–GPIOG, także spoza dotychczasowej listy. Odczytuje MODER/IDR bez zmiany
konfiguracji pinów. [Warunki startu kasety, dowody z binarki i próba przycisku](docs/CASSETTE_START_AND_INPUTS_2026-11-09.md).

Dłuższy suchy transport, z zamontowaną kasetą i odłączonym wylotem rurki:

```gcode
INVENTURE_FEEDER_RUN CHANNEL=0 DIRECTION=1 DURATION=50
```

`DURATION` jest w sekundach, maksymalnie 50 s na jedną próbę. Użytkownik
zaobserwował około 8 cm wysunięcia w 50 s; to pomiar roboczy do ustalenia
ostatniego odcinka wejścia do ekstrudera, bez przelicznika mm w sterowaniu.
Host sam czeka na zakończenie kolejnych okien
MCU i na końcu porównuje wejścia oraz liczniki. `INVENTURE_STOP` anuluje
również dalsze okna zadania. [Zasady próby, postęp, STOP i poprawka G4](docs/FEEDER_RUN_2026-11-09.md).

Wspólny STOP napędów, podajników, grzałek, wyjść serwisowych i zamków:

```gcode
INVENTURE_STOP
```

Chłodzenie wymagane przez temperaturę działa dalej. W SERVICE można od razu
wybrać następną próbę. STOP po NATIVE zatrzaskuje wyjścia; do ponownego
uzbrojenia potrzeba `FIRMWARE_RESTART`, aby stare kolejki STEP/PWM nie wznowiły ruchu.

## Pojedyncze próby

Poniższe wiersze są osobnymi przykładami. Wybieraj jeden element naraz.
X/Y mają potwierdzone kierunki. Dla Z użytkownik potwierdził DIR=1 w górę;
DIR=0 jest kierunkiem w dół. Skale mm i tor E wymagają dalszego pomiaru. Ruchy serwisowe nie ustawiają pozycji ani stanu homingu.

| Element | Przykład |
|---|---|
| X, 1 mm od minimum | `INVENTURE_MOVE AXIS=X DISTANCE=1 SPEED=2` |
| Y, 1 mm od minimum | `INVENTURE_MOVE AXIS=Y DISTANCE=1 SPEED=2` |
| Mała próba Z z obserwacją kontaktu dyszy | `INVENTURE_MOVE AXIS=Z STEPS=1604 DIR=1 RATE=802 MAX_MS=5000` |
| E po zwolnieniu toru filamentu | `INVENTURE_MOVE AXIS=E STEPS=48 DIR=0 RATE=160 MAX_MS=2000` |
| Podajnik kasety 0 | `INVENTURE_FEEDER_TEST CHANNEL=0 DIRECTION=0 DURATION_MS=100` |
| Podajnik kasety 1 | `INVENTURE_FEEDER_TEST CHANNEL=1 DIRECTION=0 DURATION_MS=100` |
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
ZLIMIT automatycznie obserwuje PC14 przy podnoszeniu stołu i PE12 przy opuszczaniu.
PE12 ma 0 po odejściu od dołu i 1 przy dolnym położeniu. Powrót zatrzymał
się na PE12 po 1457 impulsach; dwie kolejne próby opuszczania nie wysłały
żadnego impulsu. Aktywna dolna krańcówka pozwala odjechać w górę.

## Aktualizacja ZLIMIT — wystarczy host, bez wgrywania MCU

R5 wykonało na drukarce 40 impulsów Z w DIR=1; użytkownik potwierdził ruch
stołu w górę. Jednocześnie wcześniejszy odczyt `z_bottom: 1` odpowiadał
stołowi na samym dole. Dolny PE12 to Z_MAX w audycie oryginalnego firmware;
PC14 jest osobnym kontaktem dyszy, aktywnym LOW.

W dotychczasowym raporcie `hit` oznaczało tylko wejście, które zatrzymało
ruch w wybranej masce. `hit: 0` przy podnoszeniu i obserwacji PC14 nie znaczyło,
że dolna krańcówka była nieaktywna. Raport pokazuje teraz także `input_raw`,
`endstop_active`, `inputs_age_s`, `stop_mask`, `stop_value` i `hit_inputs`.
Odczyty okresowe są odróżnione od zatrzaśniętego powodu STOP z MCU.

Przy Z w dół host zawsze dołącza PE12 aktywne 1; w górę PC14 aktywne 0.
Dotyczy to `INVENTURE_MOVE` oraz wektorowego `INVENTURE_MOTION`, także po
podaniu dodatkowej maski. Sprzeczna polaryzacja jest odrzucana przed START.
Sterowanie NATIVE korzysta z krańcówek skonfigurowanych w Klipperze.

Po aktualizacji hosta wybierz SERVICE i aktualną politykę drzwi. Aby sprawdzić
zwolnienie dolnego czujnika, wykonaj osobno ruch Z w górę z tabeli i po wyniku
`phase: done` wywołaj `INVENTURE_INPUTS`. Oczekiwane po zwolnieniu: `z_bottom: 0`.
Ta próba została potwierdzona wraz z powrotem i blokadą dalszego opuszczania.
Wynik: [próba dolnej krańcówki Z](docs/Z_LOWER_LIMIT_TEST_2026-11-09.md).
Nie wykonuj jeszcze pełnego bazowania Z. MCU pozostaje R5 z tej samej paczki.

## Ręczne próby podajników w SERVICE

Wersja hosta `RC8-CONTROL-R5-SERVICE` dodaje `INVENTURE_FEEDER_TEST`.
Komendę `INVENTURE_FEEDER` przechwytuje wcześniejszy globalny właściciel
operacji, wymagający load/unload/calibration. Komunikat
`Inventure feeder requires owned load/unload/calibration` jest odmową przed
wykonaniem próby. Nie potwierdza reakcji silnika.

Do krótkich prób z obecnym MCU R5 użyj osobnego polecenia:

```gcode
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_FEEDER_TEST CHANNEL=0 DIRECTION=0 DURATION_MS=100
```

Testuj jeden kanał naraz; kanał 1 wybierz po potwierdzeniu kanału 0.
`DURATION_MS=0..1500`, `DIRECTION=0/1`; domyślnie 100 ms.
`INVENTURE_FEEDER_TEST CHANNEL=0 ACTION=STOP` wysyła zero czasu pracy.
`INVENTURE_STOP` nadal zatrzymuje wszystkie wyjścia.

START wymaga SERVICE, połączenia, braku błędu MCU, zatrzaśnięcia i innych
aktywnych wyjść serwisowych oraz braku właściciela/błędu operacji i aktywnej
procedury P13. Samo włączenie modułu P13 przy jego bezczynności nie blokuje
tej krótkiej próby. W NATIVE stosuje się standardowe procedury filamentu.
Polecenie nie uruchamia grzania, ruchu XYZ/E ani procedury load/unload.
MCU utrzymuje sprzętowe wyłączenie czasowe i kontrolę heartbeat.

Raport `V9_FEEDER_TEST` z `phase: requested` oznacza wysłanie żądania.
Fizyczną reakcję potwierdza użytkownik; raport nie deklaruje jej samodzielnie.
Zmiana wymaga tylko aktualizacji hosta; plik MCU R5 pozostaje ten sam.

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
R5 zostało połączone z drukarką i potwierdzono ruch Z w obie strony,
zwolnienie PE12, powrót do PE12 oraz blokadę dalszego opuszczania. PC4 pozostaje
niezweryfikowanym pomiarem poza maską zabezpieczeń. Użytkownik potwierdził też reakcję E po 96 impulsach w 0,613 s;
kierunek podawania/cofania pozostaje do ustalenia. Kontakt dyszy PC14,
podajniki i procedury wydruku wymagają kolejnych pojedynczych prób sprzętowych.
R5 wymaga domyślnie pierwszych siedmiu czujników temperatury.
