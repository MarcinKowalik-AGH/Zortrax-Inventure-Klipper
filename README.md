# Zortrax Inventure — host CONTROL-R7-MODULAR / MCU CONTROL-R7-FACTORY

Jeden interfejs MCU do kolejnych prób wybieranych z Klippera: A/B/Z/E,
CoreXY X/Y, oba podajniki kaset, cztery grzałki, sześć wentylatorów, zamki,
RGB, osiem ADC, 16 wejść, tachometry, liczniki filamentu i oba UART-y RFID.
Dostępne jest też natywne STEP/PWM Klippera. Parametry prób ustawia host.

## Wspólne procedury T0/T1

`INVENTURE_PROCEDURE` udostępnia osobno `HOME`, `SELECT`, `PURGE`, `CLEAN`,
`MANUAL`, `UNLOAD` i pełne `AUTO`. Korzystają z tych samych modułów,
geometrii, PID i nadzoru MCU. AUTO bazuje raz na początku, a następnie
składa moduły pod jednym właścicielem. Samodzielna procedura również
zaczyna od bazowania, ponieważ po zakończeniu zwalnia napędy.

```gcode
INVENTURE_PROCEDURE ACTION=LIST
INVENTURE_PROCEDURE ACTION=PARAMS TOOL=T1 PROCEDURE=SELECT
```

Nastawy: wartości wbudowane → opcjonalne `procedure_defaults` →
`procedure_t0`/`procedure_t1` w `[inventure_mcu]` → argumenty polecenia.
Zmiana hosta wystarcza, jeśli MCU ma już `RC8-CONTROL-R7-FACTORY`.
[Moduły, osobne polecenia, profile i diagnostyka](docs/MODULAR_PROCEDURES_2026-11-10.md).

## Ładowanie zależne od czujników — T0 i T1

Nowe `INVENTURE_LOAD_TEST ACTION=AUTO TOOL=T0` albo `TOOL=T1` wykonuje
odtworzony automat L1–L4: krótka próba przy działającym transporcie,
odzyskanie podawania, dłuższy transport oraz końcowe wyciskanie/czyszczenie.
Przygotowuje ruch, nagrzewa i mechanicznie przełącza właściwą dyszę.
Progi impulsów, prawdziwych startów kasety, czasy, odcinki, prędkości oraz
geometria są parametrami polecenia. Pierwsza fizyczna próba pełnego R7
pozostaje do wykonania.

```gcode
INVENTURE_LOAD_TEST ACTION=PARAMS TOOL=T0
```

PARAMS jest tylko odczytem/walidacją. Właściwa próba wymaga nowej aplikacji
MCU `RC8-CONTROL-R7-FACTORY`, z paczki `INVENTURE_MCU_CONTROL_R7_FACTORY_BOOT17.zip`,
oraz aktualizacji hosta opisanej poniżej. Po instalacji IVB i hosta użyj
`FIRMWARE_RESTART`; `INVENTURE_VERSION` ma pokazać `factory_abi: 1`.

```gcode
INVENTURE_STOP
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_DOOR_POLICY ENABLE=0
INVENTURE_LOAD_TEST ACTION=AUTO TOOL=T0 TARGET=230 MAX_POWER=40 DURATION=900 PROCEDURE_TIMEOUT=1800
```

Dla prawej kasety zamień `TOOL=T0` na `TOOL=T1`. Wykonuj jedną próbę naraz.
Normalne zakończenie AUTO i błędy wyłączają grzanie oraz ruch. Ręczne
przerwanie: `INVENTURE_STOP`. Prędkości XY domyślnie 20 mm/s mieszczą się
w obecnym limicie DDA; nie są deklaracją odtworzenia szybszych przejazdów
fabrycznych. Kaseta musi być już włożona; SERVICE nie wykonuje RFID.
[Pełna instalacja, parametry, nominalna geometria i warunki L1–L4](docs/FACTORY_LOAD_2026-11-10.md).

Opisane dalej `ACTION=LOAD` i `ACTION=RETRACT` zachowują wcześniejsze próby
stałego odcinka, niezależnie od nowego automatu AUTO.

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
`INVENTURE_MCU_CONTROL_R7_FACTORY_BOOT17.zip`: aplikacja `MCU/INVENTUR.IVB` do bootloadera 1.7.
AUTO wymaga tej aplikacji R7 oraz aktualnego hosta; wcześniejsze próby
stałego odcinka pozostają dostępne.

## Po aktualizacji hosta

```gcode
INVENTURE_VERSION
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_DOOR_POLICY ENABLE=0
INVENTURE_SENSORS
```

Oczekiwane: `host_revision` = `RC8-CONTROL-R7-MODULAR`, `mcu_release` = `RC8-CONTROL-R7-FACTORY`,
`control_abi: 1`, `motion_abi: 1`, `output_abi: 1`, `baud_match: true`.
Nowe `load_stream_abi: 1` potwierdza możliwość ciągłego podawania i PID podczas ruchu.
`factory_abi: 1` potwierdza nową sesję MCU dla automatu i ruchów XY/E.
`procedure_api: 1` potwierdza wspólny planer z osobnymi procedurami serwisowymi.

Ładowanie z prawej kasety do T1, na początek na tym samym odcinku co T0:

```gcode
INVENTURE_LOAD_TEST ACTION=LOAD TOOL=T1 TARGET=230 DURATION=900 TUBE_SECONDS=50 HEAD_STEPS=95000 SMOOTH=1
```

Wymaga mechanicznego ustawienia głowicy na T1 przed próbą; polecenie nie
wykonuje przejazdu przełączającego dyszę. Host nagrzewa T1 i wybiera prawy
podajnik 1. Podawanie E jest przeciwne niż dla T0: DIR1 dla T1, DIR0 dla T0,
zgodnie z oryginalnym r0323. Kierunek kasety to DIR1 w obu torach.
[Kierunki, polecenia T1, cofanie i zakres pierwszej próby](docs/T1_LOAD_2026-11-10.md).

Nagrzanie i cofnięcie około 75% poprzedniego pełnego ładowania:

```gcode
INVENTURE_LOAD_TEST ACTION=RETRACT TOOL=T0 TARGET=230 STEPS=128850 SMOOTH=1
```

Oba napędy cofają ciągle, po czym grzanie automatycznie się wyłącza.
[Podstawa długości, polecenia, wynik i STOP](docs/RETRACT_2026-11-10.md).
Po zmianie MCU użyj `FIRMWARE_RESTART`, jeśli host utrzymuje poprzedni stan.
`ENABLE=0` zachowuje ustaloną możliwość obserwacji przy otwartych drzwiach
oraz klapie. Ich surowe odczyty nadal są widoczne. Restart MCU przywraca kontrolę osłon.

Pierwsza wspólna próba ładowania z lewej kasety, z podłączoną rurką i wybranym T0:

```gcode
INVENTURE_LOAD_TEST ACTION=LOAD TOOL=T0 TARGET=230 DURATION=900 TUBE_SECONDS=50 HEAD_STEPS=95000 SMOOTH=1
```

Host sam nagrzewa T0, czeka na gotowość i podaje oboma napędami w kierunku
ładowania: kaseta DIR1, E DIR0. Pierwszy budżet odtwarza poprzednie 50 s
podawania, drugi — pięć sprawdzonych odcinków E po 19000 kroków, około 90 mm
według pomiaru użytkownika. Są to parametry robocze do korekty; wcześniejsze
80 mm nie oznacza pomiaru pełnej długości rurki. Po podawaniu napędy stają,
a temperatura jest podtrzymywana. R6 wykonuje cały odcinek w jednym ruchu,
z ciągłą pracą podajnika oraz regulacją mocy w trakcie ruchu E.
[Instalacja, krótka próba z już załadowanym filamentem i STOP](docs/SMOOTH_LOAD_2026-11-10.md).

Osobne grzanie i ręczny etap przejęcia filamentu przez ekstruder przy regulowanych 230°C:

```gcode
INVENTURE_LOAD_TEST ACTION=HEAT TOOL=T0 TARGET=230 DURATION=900
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
czujników, tachometru i STOP pozostają aktywne.

LOAD15 podtrzymuje temperaturę przez 15 minut od pierwszej gotowości,
a grzanie działa również w krótkich oknach E. PID jest aktualizowany między
oknami, z zachowaniem dzierżawy mocy 1500 ms. `ACTION=ZERO` zaczyna pomiar
od chwili chwycenia filamentu; STATUS pokazuje sumę potwierdzonych kroków
i nominalną różnicę długości. [Polecenia pomiaru i zasady podtrzymania](docs/LOAD15_MEASUREMENT_2026-11-10.md).

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
