# Inventure — kontaktowe bazowanie w natywnym CoreXY

Makra `INVENTURE_HOME_*` składają bazowanie z natywnych ruchów krańcowych,
przygotowania T0, pomiaru PC14 i końcowego prześwitu. Przy pomiarze Z
Klipper przypisuje Z=0 zweryfikowanemu rozwarciu PC14, po czym stół
opuszcza się do zadanego Z. Referencja obowiązuje w bieżącej sesji przy
włączonym napędzie. Polecenie `G28` zachowuje znaczenie z konfiguracji,
w tym `G28 Z` dla dolnej krańcówki PE12.

## Profil i wejścia

Konfiguracja z logu `klippy(10)` pracuje z `kinematics: none`. W tym
profilu używaj serwisowego `INVENTURE_PROCEDURE ACTION=HOME_Z` i odczytaj
wynik względem PE12. Komenda natywna wymaga oddzielnie przygotowanego
profilu CoreXY z bazowanymi osiami X/Y, napędem Z i aktywnym
`allow_actuators: True` w istniejącej sekcji `[inventure_mcu]`.
Poniższe pozycje dotyczą konfiguracji napędu Z. Wartości położeń i
prędkości przyjmij z pomiarów swojej maszyny.

| Element | Wymaganie |
|---|---|
| STEP Z | PB8, aktywne zbocze narastające, bez trybu obu zboczy |
| DIR Z | `!PE7`, dodatnie Z opuszcza stół |
| Wspólne włączenie napędów | Aktywne wysokim PE10 |
| Dolna krańcówka | Aktywne wysokim `^PE12`, na tej samej płycie co Z |
| Skala Z | 802 kroki/mm, np. 200 × 16 / 3,99002493766 |
| Kontakt | `^!PC14`, rezerwowany przez ten moduł |
| Tacka | PC13 aktywne niskim i lokalna ochrona `INVENTURE_NATIVE_Z_GUARD_ABI=1` |
| Pomiar kontaktu | Aplikacja `RC8-CONTROL-R7-Z-OEM-NATIVE`, `INVENTURE_NATIVE_Z_REFINE_ABI=1` |

Jeżeli sekcja `[inventure_contact]` istnieje, pozostaw w niej
`enabled: False`, aby PC14 miał jednego właściciela. Ustaw `pad_x`,
`pad_y` w **natywnym** układzie danego profilu. Przykładowy układ r0323
ma pole 14 w X67,5/Y65,5. Układ R7 liczony od zwolnionych krańcówek
podaje inne liczby. Podaj niżej zamiast `bottom_position` zmierzoną
wysokość od roboczego zera do zadziałania PE12. Musi się mieścić w
`position_max` natywnego Z.

```ini
[inventure_native_z]
pad_x: 67.5
pad_y: 65.5
# Przykład formatu. Wpisz wynik pomiaru PE12 z własnej drukarki.
bottom_position: 139.35
nozzle_temperature: 200
coarse_speed: 20
```

## Makra Inventure

Zaktualizuj hosta na Orange Pi przez SSH. Następnie skopiuj plik makr do
katalogu aktywnego `printer.cfg`.

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
python3 "$HOME/inventure-orange-pi/install-orange-pi.py" &&
install -m 0644 "$HOME/inventure-orange-pi/config/INVENTURE_HOME.cfg" "$HOME/printer_data/config/INVENTURE_HOME.cfg"
```

Dodaj raz tę linię w używanym profilu CoreXY, nad blokiem `SAVE_CONFIG`,
jeśli ten blok istnieje.

```ini
[include INVENTURE_HOME.cfg]
```

W Fluidd wykonaj `RESTART`, odczytaj `INVENTURE_VERSION` i sprawdź
`native_z_refine_abi: 1`. Przed uruchomieniem makr wykonaj
`INVENTURE_CONTROL MODE=NATIVE`. Do bazowania kontaktowego T0 musi być fizycznie wysunięta,
tacka osadzona, a skonfigurowane `pad_x` i `pad_y` sprawdzone na zimno.
`ACK=PAD_AND_CLEARANCE_CHECKED` potwierdza te warunki oraz wolną drogę stołu.
Makra korzystają z już wgranego firmware `RC8-CONTROL-R7-Z-OEM-NATIVE`.

| Zadanie | Polecenie we Fluidd | Kolejność |
|---|---|---|
| X | `INVENTURE_HOME_X` | Prześwit Z, krańcówka X |
| Y | `INVENTURE_HOME_Y` | Prześwit Z, krańcówka Y |
| XY | `INVENTURE_HOME_XY` | Prześwit Z, krańcówka Y, krańcówka X |
| Z | `INVENTURE_HOME_Z ACK=PAD_AND_CLEARANCE_CHECKED` | Wymaga bazowanych XY. PE12, dojazd T0 nad pole, grzanie, PC14, końcowy prześwit |
| XYZ | `INVENTURE_HOME_XYZ ACK=PAD_AND_CLEARANCE_CHECKED` | PE12, Y, X, dojazd T0 nad pole, grzanie, PC14, końcowy prześwit |

X, Y i XY działają bez grzania. Gdy robocze Z jest potwierdzone, stół
pozostaje na pozycji dającej wymagany prześwit lub opuszcza się do niej
przed ruchem bocznym. Gdy robocze Z jest nieznane, makro używa dolnej
krańcówki PE12 do zapewnienia prześwitu. Takie przygotowanie nie ustala
zera kontaktowego.

| Parametr | Domyślnie | Makra i działanie |
|---|---:|---|
| `XY_SPEED_FACTOR` | 1,5 | X, Y, XY, XYZ. Mnożnik prędkości obu najazdów oraz odjazdu od krańcówki XY. W profilu próbnym najazdy 5 i 2 mm/s dają 7,5 i 3 mm/s. |
| `XY_CLEARANCE` | 10 mm | X, Y, XY. Minimalna wartość roboczego Z przed ruchem bocznym, jeśli Z jest znane. |
| `TARGET` | 200°C w profilu próbnym | Z, XYZ. Cel grzania T0 podczas pomiaru. |
| `PAD_SPEED` | 80 mm/s | Z, XYZ. Prędkość ruchu XY do pola kontaktowego. |
| `PARK_Z` | 10 mm | Z, XYZ. Końcowa pozycja stołu względem zmierzonego Z=0. |
| `PARK_SPEED` | 5 mm/s | Z, XYZ. Prędkość opuszczania stołu po pomiarze. |
| `Z_APPROACH_MM` | `bottom_position` − 2 mm | Z, XYZ. Droga od PE12 do początku dokładnego pomiaru. |
| `Z_PROBE_MM` | 10 mm | Z, XYZ. Maksymalny zakres pomiaru STM32 po każdej stronie pozycji początkowej. |

Przykładowa próba z indywidualnymi nastawami, po fizycznym potwierdzeniu
T0 i pola kontaktowego.

```gcode
INVENTURE_CONTROL MODE=NATIVE
INVENTURE_HOME_XYZ ACK=PAD_AND_CLEARANCE_CHECKED TARGET=200 XY_SPEED_FACTOR=1.5 PARK_Z=10
GET_POSITION
```

Po sukcesie oczekuj `Z=10.000` w odczycie pozycji, z zerem na punkcie
rozwarcia. Końcowe 10 mm jest nastawą makra. Odczytana opcjonalna
sekwencja r0323 przechodzi przez Z=1 mm i XY=0,0, następnie wraca do Z=0.
[Sekwencja fabryczna](Z_CONTACT_AND_HOMING_R0323.md#sekwencja-firmware-161-r0323).

## Profil próbny i ręczna diagnostyka

Plik [`INVENTURE_NATIVE_Z_TEST.cfg`](../config/INVENTURE_NATIVE_Z_TEST.cfg)
zawiera osobny profil do sprawdzenia bazowania. Adres `/dev/ttyS1` i 750000
baud odpowiadają konfiguracji z `klippy(11).log`. W poprzednim przebiegu
serwisowym uzyskano 147 cykli,
101 bez poprawy i rozwarcie na wysokości 139,34414 mm od PE12.

Ostatnia udana próba `HOME_Z TOOL=T0` fizycznie wybrała T0. Jeśli dysza
została przełączona, wykonaj `INVENTURE_PROCEDURE ACTION=Z_PREPARE TOOL=T0
TARGET=200 SPEED_PROFILE=OEM` w SERVICE, sprawdź zakończenie i ostudź dyszę
przed zmianą konfiguracji. Zainstaluj profil w terminalu SSH przy bezczynnej
drukarce. Polecenie tworzy kopię serwisowego `printer.cfg` z datą w nazwie
i wypisuje jej ścieżkę.

```bash
cfg="$HOME/printer_data/config/printer.cfg" &&
backup="$cfg.service-$(date +%Y%m%d-%H%M%S)" &&
cp -p "$cfg" "$backup" &&
cp "$HOME/inventure-orange-pi/config/INVENTURE_NATIVE_Z_TEST.cfg" "$cfg" &&
printf '%s\n' "$backup"
```

W Fluidd wykonaj `FIRMWARE_RESTART`. Gdy Klipper będzie gotowy, opuść stół na
PE12 i zabazuj XY na zimno. W tym profilu krańcówki XY mają współrzędną
0. Pierwszy przejazd sprawdź wzrokowo. W razie odchylenia skoryguj `pad_x`
i `pad_y` po zmierzeniu położenia pola, po czym powtórz zimną część próby.

```gcode
INVENTURE_CONTROL MODE=NATIVE
G28 Z
G28 Y
G28 X
G90
G0 X82.8 Y68 F4800
SET_STEPPER_ENABLE STEPPER=stepper_z ENABLE=1
M400
INVENTURE_INPUTS
```

Sprawdź, czy wysunięta T0 jest nad metalowym polem, a odczyt pokazuje
`z_bottom=1`, `nozzle_contact=1` i `tray=0`. Podczas grzania profil uruchamia
F0, F2 i F3. Zacznij od `M104 S50` i sprawdź po kilku sekundach dodatnie
liczniki tachometru tych wentylatorów w `INVENTURE_SENSORS`.

```gcode
M109 S200
INVENTURE_NATIVE_HOME_Z ACK=PAD_AND_CLEARANCE_CHECKED
```

Po zakończeniu wyłącz grzanie przez `M104 S0` i sprawdź `GET_POSITION`.
STM32 wykonuje mikroruchy i liczy cykle lokalnie. `V9_NATIVE_Z_PROGRESS`
podaje ich liczbę podczas pomiaru i przy zakończeniu. Parametry
`Z_PROBE_CYCLES` i `Z_STABLE_CYCLES` wybierają starszą diagnostykę, w której
Klipper osobno synchronizuje każdy ruch.

W r0323 bazowanie jednego pola wywołuje procedurę mikroruchów z progiem
100 (`0x0801DDAE–0x0801DDB0`). Oddzielna kalibracja może zlecić trzy pełne
przejścia (`0x0801888C–0x08018890`). Każde z nich ma własną wewnętrzną
pętlę pomiarową. Polski opis pomiaru jednego pola jest w [audycie r0323](Z_CONTACT_AND_HOMING_R0323.md#sekwencja-firmware-161-r0323).
Profil próbny szuka krańcówek X/Y przy 5 mm/s i jedzie do pola przy 80 mm/s.
Z zjeżdża do PE12 przy 8,285 mm/s i odjeżdża do rozwarcia przy 1,246 mm/s.
Po odsunięciu się od PE12 o dodatkowy 1 mm stół jedzie do pozycji
początkowej pomiaru z limitem 20 mm/s. PC14 może zatrzymać ten ruch, a MCU
nadzoruje osadzenie tacki. W pobliżu styku licznik kończy pomiar po 101
kolejnych parach bez poprawy. Domyślny odstęp 4 ms między krokami daje
ok. 0,312 mm/s. To odpowiednik programowego opóźnienia r0323. Dokładny
czas między impulsami w oryginalnej aplikacji wymaga pomiaru na płycie.
Każdy etap ma limit obejmujący przewidywany czas ruchu i potwierdzenia.
Gdy limit upłynie, Klipper wykonuje awaryjne zatrzymanie i w logu podaje nazwę etapu.
Przy błędzie lub braku reakcji użyj przycisku awaryjnego STOP w Fluidd
i zachowaj pełny `klippy.log` z tej sesji.
Po błędzie w rozpoczętej procedurze host zeruje cel T0 i zatrzymuje MCU,
jeśli rozpoczął uzbrajanie ochrony ruchu. Aby wrócić do SERVICE,
po ostygnięciu przywróć wypisaną wcześniej kopię `printer.cfg` i wykonaj
`FIRMWARE_RESTART`. Próba ręczna z 14 listopada 2026 r. zakończyła się
po 102 cyklach, w tym 101 bez poprawy. Rozwarcie wypadło 139,369077 mm
od PE12, a `GET_POSITION` potwierdził Z=0. Złożone makra i zwiększona
prędkość bazowania XY czekają na próbę na drukarce.

## Kontrolowana próba na własnym profilu CoreXY

Na przygotowanym profilu uzyskaj prawidłowe natywne X/Y i doprowadź
platformę do sprawdzonej dolnej krańcówki PE12. Utrzymaj włączony napęd Z.
Przy dole ustaw **fizycznie wysuniętą** dyszę T0 nad skonfigurowanym
polem bez przeszkód. Aktywny logiczny ekstruder ma być `extruder`.
Ustabilizuj temperaturę T0 za pomocą natywnego grzania. Sprawdź
`INVENTURE_INPUTS` w Fluidd. Oczekiwane są `z_bottom=1`,
`nozzle_contact=1` i `tray=0`. Wyłącz aktywną transformację
współrzędnych przed próbą.

```gcode
INVENTURE_NATIVE_HOME_Z ACK=PAD_AND_CLEARANCE_CHECKED
GET_POSITION
```

Domyślny początek dokładnego pomiaru odpowiada Z=2 mm względem
`bottom_position`. `Z_APPROACH_MM` zmienia drogę od zadziałania PE12,
a `Z_PROBE_MM` zmienia zakres do 10 mm w każdą stronę. Zakres pomiaru
lokalnego STM32 jest niezależny od `position_min` ruchów G-code. Dlatego
położenie dyszy nad polem i prześwit należy zweryfikować przed próbą.

STM32 wybiera najlepsze rozwarcie, wraca do niego i utrzymuje ochronę
tacki, temperatury oraz napędu do chwili przekazania sterowania. Klipper
sprawdza otwarte PC14 i zgodność liczników natywnego planera, po czym
ustanawia Z=0 na zmierzonym rozwarciu. Przy braku styku,
braku rozwarcia lub utracie zabezpieczenia czynność zwraca błąd.
Po prawidłowym wyniku `V9_NATIVE_Z` podaje `coordinate_saved: true`,
`persistence: current_klipper_session` oraz `completion_basis`.
Pomiar siatki kompensacji jest osobną procedurą. Przy ręcznym wywołaniu
`INVENTURE_NATIVE_HOME_Z` operator steruje grzaniem i końcową pozycją.

Model sprawdza własność ochrony, pozycje natywne oraz unieważnienie
referencji przy wyłączeniu napędu i błędzie. Konfiguracja serwisowa
korzysta z odrębnego `INVENTURE_PROCEDURE ACTION=HOME_Z`.
