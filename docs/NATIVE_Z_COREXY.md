# Inventure — kontaktowe bazowanie w natywnym CoreXY

`INVENTURE_NATIVE_HOME_Z` wyznacza robocze Z=0 bieżącej sesji Klippera.
Stół zatrzymuje się na zweryfikowanym rozwarciu PC14, a po pomiarze
zostaje nad tym samym polem. Współrzędna nie przetrwa restartu ani
wyłączenia napędu. `G28` zachowuje znaczenie nadane w konfiguracji.

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
repeat_count: 101
```

## Profil próbny na Orange Pi

Plik [`INVENTURE_NATIVE_Z_TEST.cfg`](../config/INVENTURE_NATIVE_Z_TEST.cfg)
zawiera osobny profil do sprawdzenia bazowania. Adres `/dev/ttyS1` i 750000
baud odpowiadają konfiguracji z `klippy(11).log`. Firmware `Z-HOST` już
zgłosił `native_z_guard_abi: 1`. W przebiegu serwisowym uzyskano 147 cykli,
101 bez poprawy i rozwarcie na wysokości 139,34414 mm od PE12.

Ostatnia udana próba `HOME_Z TOOL=T0` fizycznie wybrała T0. Jeśli dysza
została przełączona, wykonaj `INVENTURE_PROCEDURE ACTION=Z_PREPARE TOOL=T0
TARGET=200 SPEED_PROFILE=OEM` w SERVICE, sprawdź zakończenie i ostudź dyszę
przed zmianą konfiguracji. Zainstaluj profil w terminalu SSH przy bezczynnej
drukarce. Polecenie tworzy kopię serwisowego `printer.cfg` z datą w nazwie
i wypisuje jej ścieżkę.

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
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
INVENTURE_NATIVE_HOME_Z Z_APPROACH_MM=137 Z_PROBE_MM=3 ACK=PAD_AND_CLEARANCE_CHECKED
GET_POSITION
M104 S0
```

Po pomyślnym wyniku `GET_POSITION` powinno pokazać robocze `Z=0`.
Krańcówki X/Y są szukane przy 5 mm/s, a przejazd do pola ma zadaną
prędkość 80 mm/s. Zgrubny najazd Z odbywa się jednym ruchem przy 8 mm/s
po zwolnieniu PE12. PC14 może go zatrzymać w dowolnym punkcie, a MCU
nadzoruje osadzenie tacki. Ruchy pomiarowe mają własną niższą prędkość.
Jeżeli próba zgłosi błąd, prześlij pełny `klippy.log` z tej sesji.
Po błędzie w rozpoczętej procedurze host zeruje cel T0 i zatrzymuje MCU,
jeśli rozpoczął uzbrajanie ochrony ruchu. Aby wrócić do SERVICE,
po ostygnięciu przywróć wypisaną wcześniej kopię `printer.cfg` i wykonaj
`FIRMWARE_RESTART`. Profil przeszedł uruchomienie w trybie symulacji
Klippera z rzeczywistym słownikiem MCU. Fizyczna próba tej ścieżki
pozostaje do wykonania.

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
INVENTURE_NATIVE_HOME_Z Z_APPROACH_MM=137 Z_PROBE_MM=3 ACK=PAD_AND_CLEARANCE_CHECKED
GET_POSITION
```

Przykładowa para 137/3 mm obejmuje zmierzony wcześniej kontakt około
139,35 mm od PE12. Dobierz początek pomiaru z potwierdzonej geometrii
i zmierz pozostałą szczelinę do metalu. Musi ona zmieścić się w
`Z_PROBE_MM`. Całe okno, wraz z oboma krańcami, musi mieścić się w
skonfigurowanym zakresie osi także względem pobliskich elementów
głowicy. Przy podanym przykładzie dolne ograniczenie natywnego Z
musiałoby dopuszczać co najmniej -0,65 mm.

MCU zatrzymuje poszczególne ruchy na PC14 i blokuje kroki po utracie
tacki. Host wybiera minimalne rozwarcie w krokach native Z oraz
sprawdza pozycję zatrzymania przed przypisaniem zera. Przy braku styku,
braku rozwarcia lub utracie zabezpieczenia czynność zwraca błąd.
Po prawidłowym wyniku `V9_NATIVE_Z` podaje `coordinate_saved: true`,
`persistence: current_klipper_session` oraz `completion_basis`.
Ten moduł nie generuje siatki kompensacji. Zmiany grzania i wybór
pozycji po bazowaniu należą do używanej procedury użytkownika.

Model sprawdza własność ochrony, pozycje natywne oraz unieważnienie
referencji przy wyłączeniu napędu i błędzie. Aktualna konfiguracja
serwisowa nie uruchamia tego modułu i nie ma jeszcze odbioru
ruchowego nowej natywnej ścieżki.
