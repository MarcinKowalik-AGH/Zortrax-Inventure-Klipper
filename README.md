# Zortrax Inventure — CONTROL-R7-T1-TIMING

Jeden interfejs MCU do kolejnych prób wybieranych z Klippera: A/B/Z/E,
CoreXY X/Y, oba podajniki kaset, cztery grzałki, sześć wentylatorów, zamki,
RGB, osiem ADC, 16 wejść, tachometry, liczniki filamentu i oba UART-y RFID.
Dostępne jest też natywne STEP/PWM Klippera. Parametry prób ustawia host.
Opcja `TIMER=1` uruchamia sprzętowe impulsy STEP w pojedynczych próbach
serwisowych X/Y/Z/A/B/E. X i Y korzystają z pary A/B w układzie CoreXY.
[Polecenia i limity prób timerowych](#sprzętowe-impulsy-step-dla-xyzabe).

Profil `SPEED_PROFILE=OEM` odtwarza żądane prędkości XY r0323 dla obu
kaset. Przejazdy i czyszczenie używają F8000, oscylacje L1/L4 F3000,
a docisk selektora F250. T1 korzysta z drugiej, prawej kasety
(kanał MCU 1) i ma własną trasę od zwolnionych krańcówek X/Y.
Do próby szybkiego profilu T1 użyj MCU T1-TIMING. W logu dwóch prób
Z-SUPPORT ruch L1 został przerwany przez `timing_fault` przy pierwszym
kroku E, przy odczytach T1 232,41°C i 231,12°C. Limit czasu obsługi kroku
MCU zależy teraz od okresu ruchu, z limitem 5 µs przy 125 kHz. Klipper
pokazuje pierwotny błąd ruchu i dane timera także po odcięciu grzałki.
T0 i T1 używają tej samej fabrycznej skali ADC. Okno temperatur i limity
grzania obowiązują również po aktualizacji.
T0 OEM działa także z MCU FACTORY-SPEED i ALL-AXIS-SPEED, a profil SERVICE
z MCU FACTORY-FEED. Oba tory przyjmują te same materiały modelowe
i podporowe w profilach OEM i SERVICE.
[Instalacja i warunki L1–L4 oraz U0–U3](docs/FACTORY_LOAD_2026-11-10.md).

## Płynność menu i komunikacja — transfer z M300

`RC8-CONTROL-R7-UIFAST` przenosi dobór okien po kompresji, kolejkę
DMA budzoną przez ACK i priorytet tła z aktualnego M300. Najnowsze
zaznaczenie zastępuje oczekujący obraz. Nawigacja w procedurach jest
pilna; ruch i grzałki zachowują pierwszeństwo przed pikselami. Podczas
grzania/ruchu obowiązuje budżet UART, również dla prób SERVICE.
Przycisk OK jest związany z widokiem potwierdzonym przed jego odebraniem.

Transfer OLED działa po aktualizacji hosta, także z poprzednim MCU R7.
Instalator obejmuje transport OLED i menu również przy przywracaniu kopii.
[Źródła M300, opis zmian, wyniki i próba na drukarce](docs/M300_TRANSFER_COMM_OLED_2026-11-10.md).

## Menu Zortrax na OLED i w konsoli

Pięć oryginalnych gałęzi Inventure korzysta teraz ze wspólnej warstwy
procedur: `INVENTURE_ZORTRAX`. OLED i G-code otwierają te same widoki,
potwierdzenia, postęp oraz anulowanie. Czynności serwisowe wykonuje R7;
poziomowanie, alignment, szpule i wydruk korzystają z dostępnych natywnych
właścicieli P09/P10/P11/P13/P15/P17. Nieobecny lub wyłączony wykonawca daje
konkretną informację, bez zmiany flag kwalifikacji.

```gcode
INVENTURE_ZORTRAX OP=STATUS
INVENTURE_ZORTRAX OP=CATALOG
```

Otwarcie czynności nie rozpoczyna ruchu/grzania. Potwierdzenie jest
jednorazowe i związane z bieżącym widokiem/operacją. Procedury z napędem
kasety wymagają `factory_feed_clock: 1`. [Obsługa menu, procedury i rzeczywista dostępność](docs/ZORTRAX_MENU_PROCEDURES_2026-11-10.md).

## Wspólne procedury T0/T1

`INVENTURE_PROCEDURE` udostępnia osobno `HOME`, `PARK`, `SELECT`, `SWEEP`,
`PURGE`, `CLEAN`, `MANUAL`, `UNLOAD` i pełne `AUTO`. Korzystają z tych samych modułów,
geometrii, PID i nadzoru MCU. AUTO bazuje raz na początku, a następnie
składa moduły pod jednym właścicielem. Samodzielna procedura również
zaczyna od bazowania, ponieważ po zakończeniu zwalnia napędy.

```gcode
INVENTURE_PROCEDURE ACTION=LIST
INVENTURE_PROCEDURE ACTION=PARAMS TOOL=T0 PROCEDURE=SELECT
```

W trybie SERVICE `INVENTURE_PROCEDURE ACTION=SELECT TOOL=T0` sprawdza
docisk bez grzania i E. W STATUS można odczytać kroki A/B docisku
w `factory.motion_legs.selector_press`. Osobne
`INVENTURE_PROCEDURE ACTION=SWEEP TOOL=T0` wykonuje ruchy X jednej
porcji L1 bez grzania, E i podajnika.

Nastawy są rozpatrywane w kolejności od wartości wbudowanych przez
opcjonalne `procedure_defaults` i `procedure_t0`/`procedure_t1`
w `[inventure_mcu]` po argumenty polecenia. Procedury kasetowe wymagają
obsługi zegara podajnika `factory_feed_clock: 1`.
[Moduły, osobne polecenia, profile i diagnostyka](docs/MODULAR_PROCEDURES_2026-11-10.md).

## Ładowanie zależne od czujników — T0 i T1

Nowe `INVENTURE_LOAD_TEST ACTION=AUTO TOOL=T0` albo `TOOL=T1` wykonuje
odtworzony automat L1–L4: krótka próba przy działającym transporcie,
odzyskanie podawania, dłuższy transport oraz końcowe wyciskanie/czyszczenie.
Przygotowuje ruch, nagrzewa i mechanicznie przełącza właściwą dyszę.
Progi impulsów, startów kasety, czasy, odcinki, prędkości i geometria są
parametrami polecenia.

```gcode
INVENTURE_LOAD_TEST ACTION=PARAMS TOOL=T0
```

PARAMS odczytuje i waliduje nastawy. Po aktualizacji hosta i MCU użyj
`FIRMWARE_RESTART`. Dla profilu OEM sprawdź w `INVENTURE_VERSION`
`factory_abi: 1`, `factory_feed_clock: 1` i `factory_motion_abi: 1`.
Pole `factory_motion_tools: 3` oznacza obsługę T0 i T1.

MCU T1-TIMING uruchamia się z globalnym `Door lock` wyłączonym.
Przed pierwszą próbą przy otwartych osłonach sprawdź we Fluidd
`INVENTURE_DOOR_POLICY`. Oczekiwane pola to `scope: global`,
`enabled: false` i `lock_command_mask: 0`. Próby na starszym MCU
zaczynają się od `INVENTURE_DOOR_POLICY ENABLE=0` po restarcie.
[Opcja drzwi i diagnostyka](docs/OEM_DOOR_POLICY.md).

Pierwszą próbę prędkości OEM wykonaj na zimno.

```gcode
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_PROCEDURE ACTION=SWEEP TOOL=T0 SPEED_PROFILE=OEM
```

Po zakończeniu SWEEP wybierz jedną operację. Przykład używa profilu
transportu kasety Z-PLA. Dla innego materiału zmień `MATERIAL`.

```gcode
INVENTURE_PROCEDURE ACTION=AUTO TOOL=T0 SPEED_PROFILE=OEM MATERIAL=Z-PLA TARGET=230 MAX_POWER=40
```

```gcode
INVENTURE_PROCEDURE ACTION=UNLOAD TOOL=T0 SPEED_PROFILE=OEM MATERIAL=Z-PLA TARGET=230 MAX_POWER=40
```

### Prawa kaseta i T1 w profilu OEM

Po włączeniu SERVICE uruchom osobno `SELECT` i `SWEEP`. Pierwsze
polecenie sprawdza docisk selektora w lewo, drugie przejazd i oscylacje
nad koszem. Obie próby są bez grzania, podawania kasetą i ruchu E.

```gcode
INVENTURE_PROCEDURE ACTION=SELECT TOOL=T1 SPEED_PROFILE=OEM
```

```gcode
INVENTURE_PROCEDURE ACTION=SWEEP TOOL=T1 SPEED_PROFILE=OEM
```

Po sprawdzeniu mechaniki wybierz jedną operację z materiałem zgodnym
z prawą kasetą. Poniższe przykłady zakładają Z-SUPPORT.

```gcode
INVENTURE_PROCEDURE ACTION=AUTO TOOL=T1 SPEED_PROFILE=OEM MATERIAL=Z-SUPPORT MAX_POWER=40
```

```gcode
INVENTURE_PROCEDURE ACTION=UNLOAD TOOL=T1 SPEED_PROFILE=OEM MATERIAL=Z-SUPPORT MAX_POWER=40
```

`TOOL` wybiera dyszę i kasetę, a `MATERIAL` określa profil filamentu
dla AUTO i UNLOAD. Z-SUPPORT używa 235°C i budżetów 50/50 mm,
Z-SUPPORT-PLUS 240°C i 30/70 mm, a Z-SUPPORT-PREMIUM oraz
BASF-ULTRAFUSE-BVOH 230°C i 30/70 mm. Te same nastawy obowiązują
dla lewej i prawej kasety. `TARGET` jawnie nadpisuje temperaturę.

Przykłady z materiałem podporowym w lewej kasecie i modelowym w prawej.
Odczyty PARAMS pozwalają sprawdzić nastawy przed uruchomieniem AUTO.

| Kaseta i materiał | Podgląd nastaw |
|---|---|
| T0, Z-SUPPORT | `INVENTURE_PROCEDURE ACTION=PARAMS PROCEDURE=AUTO TOOL=T0 SPEED_PROFILE=OEM MATERIAL=Z-SUPPORT` |
| T1, Z-PLA | `INVENTURE_PROCEDURE ACTION=PARAMS PROCEDURE=AUTO TOOL=T1 SPEED_PROFILE=OEM MATERIAL=Z-PLA` |

Fabryczny cel temperatury powyżej 240°C pozostaje widoczny w
`oem_material_target_c`. Grzany cykl wymaga wtedy jawnego `TARGET`
w zakresie serwisowym i odpowiedniego dla rzeczywistego filamentu.
[Zakres temperatur i profile materiałowe](docs/FACTORY_LOAD_2026-11-10.md#profile-materiałowe-obu-kaset)

[Audyt ładowania T0 i T1](docs/FACTORY_PROCEDURE_AUDIT_2026-11-12.md) oraz
[próba PC14 przed pełnym homingiem Z](docs/Z_CONTACT_AND_HOMING_R0323.md).

`INVENTURE_PROCEDURE ACTION=Z_PREPARE` bazuje dolne Z i osie XY, nagrzewa
T0 i ustawia dyszę nad punktem pomiarowym. `ACTION=HOME_Z` dodaje
ograniczony dojazd, pomiar kontaktu i rozwarcia PC14 oraz powrót do
najniższej potwierdzonej pozycji rozwarcia. Krótkie zwarcie i krótkie
rozwarcie PC14 uruchamiają ograniczone ponowienia. Profil `OEM` stosuje odczytane prędkości odcinków
Z i zachowuje serwisową prędkość precyzyjnego pomiaru.
[Parametry i polecenia Z](docs/Z_CONTACT_AND_HOMING_R0323.md#rozgrzana-próba-odniesienia-z-w-service)

Stan sprawdzisz przez `INVENTURE_PROCEDURE ACTION=STATUS`, a próbę
przerwiesz przez `INVENTURE_STOP`. Włóż kasetę przed startem. Procedura
SERVICE pomija RFID. Domyślny profil SERVICE obsługuje T0 i T1 oraz
ogranicza ruchy XY do 20 000 zdarzeń DDA/s.
[Pełna instalacja, parametry, nominalna geometria i warunki L1–L4](docs/FACTORY_LOAD_2026-11-10.md).

Opisane dalej `ACTION=LOAD` i `ACTION=RETRACT` zachowują wcześniejsze próby
stałego odcinka, niezależnie od nowego automatu AUTO.

## Aktualizacja Orange Pi

Wykonaj w terminalu Orange Pi przez SSH.

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
python3 "$HOME/inventure-orange-pi/install-orange-pi.py"
```

Pierwsza instalacja, jeśli katalog jeszcze nie istnieje:

```bash
git clone --depth 1 --single-branch --no-tags --branch orange-pi-all-axis https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper.git "$HOME/inventure-orange-pi" && python3 "$HOME/inventure-orange-pi/install-orange-pi.py"
```

Instalator wykrywa działającą usługę i właściwy `klippy.py`, zachowuje kopie
modułów, instaluje adapter, OLED i zgodny transport UART oraz restartuje usługę.
Przyjmuje także dokładny poprzedni moduł MOTOR-R4. Zachowuje `printer.cfg`,
750000 baud i aktualną geometrię. MCU otrzymujesz osobno w czacie jako
`INVENTURE_MCU_CONTROL_R7_T1_TIMING_BOOT17.zip` z aplikacją
`MCU/INVENTUR.IVB` do bootloadera 1.7. Po wgraniu wykonaj
`FIRMWARE_RESTART` i odczytaj `INVENTURE_VERSION`. Nowe profile materiałowe
są dostępne po aktualizacji hosta również z dotychczasowym MCU
obsługującym daną procedurę. T1-TIMING zachowuje globalną opcję drzwi
wprowadzoną w CASSETTE-POLICY.

## Po aktualizacji hosta

```gcode
INVENTURE_VERSION
INVENTURE_DOOR_POLICY
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_SENSORS
```

Oczekiwane `host_revision` = `RC8-CONTROL-R7-UIFAST-HOME-Z-RETRY`, `mcu_release` = `RC8-CONTROL-R7-T1-TIMING`,
`control_abi: 1`, `motion_abi: 1`, `output_abi: 1`, `baud_match: true`.
`door_policy_global: true` i `door_policy_default_enabled: false` opisują
politykę osłon tego wydania. `INVENTURE_DOOR_POLICY ENABLE=1` włącza
kontrolę osłon w bieżącej sesji, gdy drukarka jest bezczynna.
Nowe `load_stream_abi: 1` potwierdza możliwość ciągłego podawania i PID podczas ruchu.
`factory_abi: 1` potwierdza sesję MCU dla automatu i ruchów XY/E.
`factory_feed_clock: 1` potwierdza obsługę zegara kasety w tej sesji.
`factory_motion_abi: 1` potwierdza obsługę profilu OEM, a
`factory_motion_max_hz: 125000` podaje limit zdarzeń DDA/s.
`factory_motion_tools: 3` potwierdza szybki ruch w obu torach.
`axis_motion_abi: 1` potwierdza dostępność `TIMER=1` na wszystkich napędach.
Czas ruchu i zachowanie mechaniki wymagają sprawdzenia na drukarce.
`procedure_api: 1` potwierdza wspólny planer z osobnymi procedurami serwisowymi.
`zortrax_menu_api: 1` potwierdza wspólny sterownik procedur menu OLED/G-code.

Osobna próba podawania stałego odcinka z prawej kasety do T1.

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
`ENABLE=0` pozwala na obserwację przy otwartych drzwiach i klapie
z wyłączonymi zamkami. Surowe odczyty pozostają widoczne. Po restarcie
MCU T1-TIMING znów stosuje ustawienie wyłączone.

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

## Sprzętowe impulsy STEP dla XYZABE

`INVENTURE_MOVE ... TIMER=1` wybiera sprzętowe impulsy STEP w trybie
`SERVICE`. Do niezależnej próby A/B służą `AXIS=A` i `AXIS=B`.
`INVENTURE_MOTION ... TIMER=1` przyjmuje podpisane liczby kroków A i B.
W tym trybie Z i E wykonuje się w osobnych poleceniach. Bez `TIMER=1`
polecenia używają dotychczasowego sterowania serwisowego.

Po wybraniu `INVENTURE_CONTROL MODE=SERVICE` zapewnij wolny odcinek
dla wybranego ruchu. Wydawaj po jednym poleceniu i odczytuj
`INVENTURE_MOTION ACTION=STATUS` po każdej próbie.

| Ruch na zimno | Krótka próba |
|---|---|
| X | `INVENTURE_MOVE AXIS=X TIMER=1 DISTANCE=1 SPEED=2` |
| Y | `INVENTURE_MOVE AXIS=Y TIMER=1 DISTANCE=1 SPEED=2` |
| A | `INVENTURE_MOVE AXIS=A TIMER=1 STEPS=160 DIR=1 RATE=640` |
| B | `INVENTURE_MOVE AXIS=B TIMER=1 STEPS=160 DIR=1 RATE=640` |
| Z w dół | `INVENTURE_MOVE AXIS=Z TIMER=1 STEPS=80 DIR=0 RATE=400` |

Dla E najpierw ustaw fizycznie właściwą dyszę i przygotuj tor filamentu.
Wybierz ją w `INVENTURE_LOAD_TEST ACTION=HEAT TOOL=T0 TARGET=230 DURATION=900`.
Po `ready_for_jog: true` możesz wykonać krótki ruch E z działającym PID.

```gcode
INVENTURE_LOAD_TEST ACTION=JOG TIMER=1 DIR=0 STEPS=480 RATE=960
```

DIR=0 podaje T0, DIR=1 podaje T1. Bezpośredni `INVENTURE_MOVE AXIS=E
TIMER=1` wymaga `TOOL=T0` albo `TOOL=T1` i świeżego odczytu
180–249°C z wybranej dyszy. W aktywnym teście grzania użyj `ACTION=JOG`.
`INVENTURE_STOP` zatrzymuje napędy i grzanie.

Limity prób wynoszą 125 000 impulsów/s dla A/B, 3200 dla Z i 9600 dla E.
Wersja wykorzystuje TIM14/13/10/11. Natywna kolejka ruchu Klippera działa
w trybie `NATIVE`. Prędkość i powtarzalność prób wymagają pomiaru na drukarce.
[Mapowanie, warunki i diagnostyka ruchu](docs/CONTROL_R5_REFERENCE.md#sprzętowe-impulsy-step-w-service).

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
