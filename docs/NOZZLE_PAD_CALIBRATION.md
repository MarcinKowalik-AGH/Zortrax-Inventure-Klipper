# Zortrax Inventure — kalibracja XYZ dysz na padzie

`ZORTRAX_NOZZLE_PAD_CALIBRATE` wyznacza względne położenie T0 i T1
w osiach XYZ na jednym metalowym polu tacki. Zacznij od środkowego
okrągłego pada spośród pięciu fabrycznych pól poziomowania. Na tacce
testowanej w projekcie średnica zmierzona suwmiarką wynosi 7 mm.
Pomiar T0 → T1 → T0 sprawdza też powtarzalność osadzenia dyszy
po przełączeniu. Wynik opisuje geometrię obu dysz przy 100°C.

## Instalacja

Pełny pomiar przy 100°C wymaga hosta `RC8-CONTROL-R7-UIFAST-PAD-XYZ-R13`
i aplikacji MCU `RC8-CONTROL-R7-PAD-THERMAL-R9`. Najpierw zaktualizuj
[moduł hosta](../README.md#aktualizacja-orange-pi). Host R12 odrzuca
nowy limit serwisowy 310°C podczas łączenia z MCU R9. Pobierz artefakt
`INVENTURE_MCU_PAD_100C_BOOT17` z
[GitHub Actions](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/actions/workflows/orange-pi-mcu.yml).
Sprawdź `SHA256SUMS`, skopiuj `INVENTUR.IVB` do głównego katalogu karty SD
FAT32 i wgraj aplikację przez InventureBoot 1.7. MCU pobiera prędkość P4
z ustawień bootloadera.
Po `FIRMWARE_RESTART` odczytaj `INVENTURE_VERSION`. Sprawdź obie wersje,
`baud_match: true` i `nozzle_pad_measurement_band_c: [95, 105]`.

Zimny tryb `CENTER` dla obu dysz obsługuje też wcześniejsze wersje MCU
z `nozzle_pad_cold_abi: 2`. Zimny pomiar T0 działa również z
`RC8-CONTROL-R7-PAD-CENTER`.
W istniejącej sekcji `[inventure_mcu]` procedura wymaga
`allow_actuators: True`.

Skopiuj `config/INVENTURE_NOZZLE_PAD.cfg` do katalogu konfiguracji Klippera
i dołącz plik w `printer.cfg`.

```ini
[include INVENTURE_NOZZLE_PAD.cfg]
```

Po przeładowaniu konfiguracji makro jest dostępne w Fluidd. Polecenie
`INVENTURE_NOZZLE_PAD_CALIBRATE` przyjmuje te same argumenty.

## Limity grzania

W konfiguracji do prób homingu Z starszy limit `[extruder] max_temp: 220`
obejmował także odczyt T0 podczas procedur serwisowych. Dla oryginalnych
grzałek i czujników Inventure R5 zaktualizuj ten limit do 310°C.
Instalator znajduje aktywny plik konfiguracji wraz z dołączanymi plikami
i robi kopię przed zmianą starej nastawy.

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
python3 "$HOME/inventure-orange-pi/install-orange-pi.py" --upgrade-nozzle-limits
```

Aplikacja MCU R9 przyjmuje cele serwisowe do 310°C dla T0 i T1.
`INVENTURE_VERSION` podaje `nozzle_service_target_max_c: 310`. Odczyt
310°C uruchamia odcięcie MCU. Nastawa przy samej granicy może skończyć
się odcięciem podczas dojścia do celu. Przy niższych celach odcięcie
sesji jest najwyżej 20°C ponad celem i uwzględnia niższy limit aktywnej
konfiguracji. Po sekundzie bez heartbeat MCU odcina grzałki i prowadzi
chłodzenie lokalnie.

Jeśli `native_nozzle_max_temp_c.T1` ma wartość `null`, skopiuj
[INVENTURE_NATIVE_T1_HEATER.cfg](../config/INVENTURE_NATIVE_T1_HEATER.cfg)
do katalogu konfiguracji i dodaj `[include INVENTURE_NATIVE_T1_HEATER.cfg]`
w `printer.cfg`. W istniejących sekcjach `[heater_fan ...]` dla wyjść
`inventure_r5:F0`, `inventure_r5:F2` i `inventure_r5:F3` ustaw
`heater: extruder, inventure_t1`. Klipper uruchomi wtedy chłodzenie
także przy grzaniu samej T1. W trybie `INVENTURE_CONTROL MODE=NATIVE` ustawiasz
temperatury z Fluidd albo poleceniami `SET_HEATER_TEMPERATURE HEATER=extruder`
dla T0 i `SET_HEATER_TEMPERATURE HEATER=inventure_t1` dla T1 z parametrem
`TARGET` do 310°C. `TARGET=0` wyłącza grzanie. Po dodaniu grzałki
`INVENTURE_VERSION` pokazuje natywny limit 310°C dla obu dysz.

W `PREP=RETRACT` podaj indywidualne cele dla obu materiałów, np. 260°C
dla materiału wymagającego takiej temperatury do wycofania. Pomiar pada
odbywa się po schłodzeniu do 100°C, w paśmie 95–105°C dla obu dysz.

## Środek pada na zimnej dyszy

Przy czystych dyszach poniżej 50°C i osadzonej tacce wywołaj jedno polecenie.
`T0_Z_MAX` i `T1_Z_MAX` określają maksymalny dojazd stołu od PE12,
zweryfikowany osobno dla wybranej dyszy. Próba T0 na opisywanym
egzemplarzu użyła 139,50 mm i wyznaczyła środek X81,7 / Y68,2 mm.

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=CENTER T0_Z_MAX=139.50
```

Do skanu T1 użyj tego samego pola. Poniższe współrzędne T0
wyznaczają nominalny start skanu T1 przy X70,9 / Y68,2 mm. Wpisz
zweryfikowaną wartość `T1_Z_MAX` w milimetrach z kropką dziesiętną.

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=CENTER TOOL=T1 T0_PAD_X=81.70 T0_PAD_Y=68.20 T1_Z_MAX=<limit_T1_mm>
```

Do sprawdzenia ustawienia T1 z pozycją stołu na PE12 służy
`ZORTRAX_NOZZLE_Z_TEST ACTION=PREPARE TOOL=T1 T0_PAD_X=81.70 T0_PAD_Y=68.20`.
Oba pomiary środka podają położenie karetki nad tym samym fizycznym
padem. Przesunięcie karetki po zmianie dyszy z T0 na T1 jest różnicą
`środek_T1 − środek_T0`. Względem nominalnego przesunięcia X−10,8 mm
korekta ruchu wynosi `X_T1 − 70,9` i `Y_T1 − 68,2` dla tego pomiaru
T0. `CENTER` kończy się odczytem diagnostycznym.

W próbie z 17 listopada 2026 T1 zmierzyła środek X70,8 / Y68,7 mm
z limitem `T1_Z_MAX=137.95`. Dla tej pary zimnych odczytów ruch karetki
po przełączeniu T0 → T1 wynosi X−10,9 / Y+0,5 mm. Te środki służą
jako punkty startowe pełnego pomiaru przy 100°C.

Tryb `CENTER` ustala referencję, mechanicznie wybiera wskazaną dyszę i mierzy po kolei
X+, X−, Y+ i Y−. Domyślnie szuka do 5 mm w każdym kierunku. Dla pada
Ø7 mm akceptuje zmierzoną szerokość styku do 8,5 mm. Czoło dyszy może
poszerzyć obrys elektryczny. W próbie X zmierzono 7,54 mm. Głowica wraca
po obniżeniu stołu. Jeśli zwarcie PC14 zaniknie przed rozpoczęciem
przejazdu, stół cofa się i powtarza próbę z tym samym limitem od PE12.
Jeśli styk rozewrze się w pierwszych 0,25 mm przejazdu albo nie da się
potwierdzić krawędzi, procedura mierzy obie strony tej osi pionowymi
dotknięciami. Przy niestabilnym styku w punkcie startu szuka zwarcia do
2 mm w obie strony wzdłuż tej osi. Potwierdzenie punktu poza polem wymaga
dwóch dojazdów do limitu próby bez zwarcia. Przy niestabilnym odczycie
procedura zachowuje oba potwierdzone końce przedziału i sprawdza inne
pozycje. Po zawężeniu przedziału procedura liczy tylko niejednoznaczne
próby wewnątrz nowych granic. Domyślnie kończy pomiar dotknięciami, gdy
przedział krawędzi ma szerokość do 0,05 mm. Zakres XY i limit Z pochodzą z ustawień tego
samego przebiegu. Stół opada przed przejazdem między punktami dotknięcia.
Pomiar Y zaczyna się przy wyznaczonym X. Po wyznaczeniu czterech krawędzi
stół opuszcza się, a głowica wraca nad środek.

Na czas próby host wstrzymuje odświeżanie OLED. Fluidd wypisuje jeden
wiersz z pozycją środka i czterema krawędziami w serwisowym układzie XY
z rozdzielczością 0,1 mm albo z etapem i przyczyną przerwania. Pozycje
krawędzi i przedziały z prób pionowych trafiają do `klippy.log`.
`ACTION=STATUS` odczytuje bieżący etap albo końcowy wynik.
`INVENTURE_STOP` przerywa próbę.

## Przygotowanie fabrycznej tacki

Osadź tackę, opróżnij kosz, sprawdź obie szczotki i oczyść pad wraz z jego
otoczeniem na trasie dyszy. Domyślny `SCAN=SWEEP` obsługuje fabryczne pole
zlicowane z tacką. W `INVENTURE_INPUTS` zwarcie PC14 odpowiada
`nozzle_contact=0`, a rozwarcie `nozzle_contact=1`.

Potwierdź, że dysza trafia we wnętrze pada i PC14 powtarzalnie sygnalizuje
tam styk. Przybliżone współrzędne w układzie serwisowym to X82,8 / Y68,0
dla T0 i X72,0 / Y68,0 dla T1. Dostępny jest zimny podgląd położenia
nad polem. [Podgląd i współrzędne](NOZZLE_Z_TEST_R0323.md#pozycje-nad-padem).

Ustal dla każdej mierzonej dyszy górny limit dojazdu w Z od dolnej krańcówki
PE12. W `SCAN=SWEEP` dysza przesuwa się nad polem na wysokości pierwszego
zwarcia PC14. Przy niepewnej krawędzi pełny pomiar potwierdza ją pionowymi
dotknięciami tak jak `CENTER`. Przy 100°C dojazdy do krawędzi są ograniczone
do potwierdzonej wysokości styku plus 0,05 mm i do limitu danej dyszy.
Stół opada przed każdym przejazdem między punktami.
`ACK=PAD_GEOMETRY_MEASURED` potwierdza sprawdzenie punktów startowych,
limitów Z i powierzchni w obszarze skanu. Pierwsza próba na danej geometrii
powinna objąć tylko T0.

`SCAN=TAP` jest przeznaczony do osobnego, wyniesionego pola o płaskim
przewodzącym wierzchu. W tym trybie limit podniesienia stołu musi zapewniać
prześwit w całym obszarze prób poza polem, bo STM32 dochodzi tam do limitu
Z bez zwarcia PC14. Obie dysze korzystają z tego samego nieruchomego pola.

## Przebieg

1. Klipper ustala referencję XY i PE12 na początku sesji, po czym wykonuje
   mechaniczny docisk selektora T0. Przy `VERIFY_T0 PREP=EMPTY` głowica
   zatrzymuje się na zimno nad padem ze stołem na PE12. Po potwierdzeniu
   T0 obie dysze grzeją się do 100°C, a mierzona dysza przejeżdża po
   szczotce. Budżet kroków E wynosi zero.
2. Przed każdym przejazdem pomiarowym stół podnosi się przy ustalonym XY
   wewnątrz pada. STM32 zatrzymuje Z przy pierwszym zwarciu PC14.
3. Przy tym samym Z głowica wykonuje powolny przejazd wzdłuż jednej osi.
   STM32 wstrzymuje przejazd przy pierwszym rozwarciu PC14. Jeśli styk wróci
   przed upływem 150 ms, STM32 wznawia ten sam przejazd. Po 150 ms rozwarcia
   w spoczynku Klipper przyjmuje krawędź w połowie między ostatnim krokiem
   ze stykiem a pierwszym krokiem bez styku. Stół obniża się przed powrotem
   nad pole i kolejnym dojazdem Z.
4. Para przejazdów X− i X+ wyznacza środek X jako średnią obu krawędzi.
   Para Y− i Y+ odbywa się przy wyznaczonym X. Środek Y jest średnią
   obu krawędzi Y. Drugi przebieg powtarza pomiar od wyznaczonego środka.
   W środku wykonywane są dwie próby rozwarcia styku na osi Z.
5. Między skanami stół najpierw obniża się do 20 mm od PE12, a potem
   o kolejne 10 mm przed przełączeniem dyszy. Pełna sesja porównuje
   krawędzie i zmierzone poziomy Z T0 i T1 oraz powtarzalność ponownego
   osadzenia T0. Wyznaczony punkt jest środkiem zmierzonego obrysu
   elektrycznego pola dla danej końcówki dyszy.

W `SCAN=TAP` granice są wyznaczane przez pionowe próby zwarcia w szeregu
pozycji XY. Przed każdym przejazdem XY stół jest obniżany do poziomu
prześwitu. Bisekcja zawęża przedział między stykiem a jego brakiem.

Przy `MODE=FULL PREP=RETRACT` obie dysze nagrzewają się do indywidualnych
temperatur cofania filamentu. T0 cofa filament nad koszem i przejeżdża po
szczotce. Po wyborze T1 te same czynności wykonuje T1. Po każdym
przetarciu karetka zjeżdża z linii szczotki w osi Y i wraca na środek
kosza dla wybranej dyszy. Przy fabrycznych nastawach serwisowych jest to
X82,8 Y152,5 dla T0 i X72,0 Y152,5 dla T1. Nad koszem dysze czekają
na nagrzanie i schłodzenie do 100°C. Po stabilizacji następują drugie
przetarcie i skan T0, drugie przetarcie i skan T1, a potem kontrolny
pomiar ponownie osadzonej T0.

Gdy MCU odmawia startu przejazdu `SWEEP`, host sprawdza aktualne
zabezpieczenia przed powtórnym dojazdem Z. Styk PC14 może ponownie się
zamknąć po chwilowym rozwarciu, które skasowało potwierdzenie styku w MCU.
Procedura wycofuje Z i powtarza dojazd. W razie dalszej odmowy przechodzi
na pionowe próby na tej osi. Domyślnie potwierdzony styk i rozwarcie
dzieli wtedy najwyżej 0,05 mm. Krawędź przyjmuje się w połowie tego
przedziału. Para rozwarć z obrysem węższym niż średnica pola minus
1,5 mm wymaga potwierdzenia obu krawędzi pionowymi próbami.

Ustaw temperaturę wycofania odpowiednią osobno dla każdego materiału.
Przykładowe 240°C dotyczy filamentu, dla którego taka nastawa jest
właściwa. Drugie przetarcie i skan przy 100°C odbywają się przy wyłączonym
podawaniu filamentu.

Wycofanie do 100 mm odbywa się samym napędem ekstrudera E. Sprawdź, czy
załadowany tor pozwala bezpiecznie cofnąć wybraną długość. W próbie
T0 materiał przeszedł około 90 mm między uchwyceniem przez zębatkę
i pierwszym wypływem. Przy cofnięciu o 100 mm przygotuj się do pełnego
załadowania materiału po pomiarze. [Obserwacja z próby T0](CASSETTE_LOAD_2026-11-10.md).

## Obsługa z konsoli

Przejdź do trybu serwisowego, gdy drukarka jest bezczynna.
Kalibracja odczytuje wspólną opcję `Door lock`, sterowaną z OLED lub
poleceniem `INVENTURE_DOOR_POLICY`. Gdy opcja jest włączona, obie
osłony muszą być zamknięte. Przy wyłączonej blokadzie kalibracja może
pracować z otwartymi osłonami. [Globalna polityka drzwi](OEM_DOOR_POLICY.md).

```gcode
INVENTURE_DOOR_POLICY
INVENTURE_CONTROL MODE=SERVICE
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=STATUS
```

Pierwszą próbę na fabrycznym padzie z pustymi dyszami wykonaj z
`MODE=VERIFY_T0 PREP=EMPTY`. Zastąp `<Z_mm_T0>` ustalonym limitem dojazdu
T0 od PE12 w milimetrach.

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=VERIFY_T0 SCAN=SWEEP PREP=EMPTY ACK=PAD_GEOMETRY_MEASURED T0_Z_MAX=<Z_mm_T0>
```

Po `phase: awaiting_t0_confirmation` sprawdź wzrokowo, czy T0 jest
opuszczona, a T1 podniesiona. MCU potwierdza kroki docisku, a położenie
mechanizmu potwierdza operator. Przy `PREP=EMPTY` sprawdzenie odbywa się
nad padem przed grzaniem. Przy `PREP=RETRACT` potwierdzenie odbywa się
nad koszem, po przygotowaniu obu dysz i ponownym wybraniu T0.

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=CONFIRM_T0
```

Jeśli T1 pozostaje opuszczona, zakończ próbę poleceniem `ACTION=STOP`.

Po sprawdzeniu krawędzi T0 i położenia T1 nad tym samym polem przejdź do
pełnej kalibracji. Podaj sprawdzone limity obu dysz.

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=FULL SCAN=SWEEP PREP=EMPTY ACK=PAD_GEOMETRY_MEASURED T0_Z_MAX=<Z_mm_T0> T1_Z_MAX=<Z_mm_T1>
```

Przy załadowanych dyszach wybierz `PREP=RETRACT` i wpisz temperatury oraz
długości cofania obu materiałów. Po pierwszej próbie przywróć stan
załadowania zgodny z zaplanowanym cofnięciem w nowej sesji.

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=FULL SCAN=SWEEP PREP=RETRACT ACK=PAD_GEOMETRY_MEASURED T0_Z_MAX=<Z_mm_T0> T1_Z_MAX=<Z_mm_T1> T0_TARGET=<degC_T0> T1_TARGET=<degC_T1> T0_RETRACT_MM=<mm_T0> T1_RETRACT_MM=<mm_T1>
```

Dla zmierzonej tacki i konfiguracji Z-PLA w T0 oraz Z-SUPPORT w T1 przykład
z cofnięciem obu filamentów o 100 mm wygląda następująco. Limity Z pochodzą
z prób zimnych obu dysz. Pełny pomiar w tej konfiguracji wymaga odbioru na drukarce.

```gcode
INVENTURE_CONTROL MODE=SERVICE
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=FULL SCAN=SWEEP PREP=RETRACT ACK=PAD_GEOMETRY_MEASURED T0_PAD_X=81.70 T0_PAD_Y=68.20 T1_PAD_X=70.80 T1_PAD_Y=68.70 T0_Z_MAX=139.50 T1_Z_MAX=137.95 T0_TARGET=230 T1_TARGET=235 T0_RETRACT_MM=100 T1_RETRACT_MM=100 PROCEDURE_TIMEOUT=3600
```

Dla sprawdzonego wyniesionego pola wybierz `SCAN=TAP` i limity Z
odpowiednie dla pionowych prób w całym obszarze skanu.

Poleceniem `STATUS` sprawdzisz etap i wynik. Po błędzie odczytasz również
ostatnią dyszę, etap, odczyty temperatur i dostępny powód zatrzymania
sesji na MCU. Zakończone pomiary dysz pozostają w `measurements`.
Zapis offsetu wymaga pełnego wyniku w stanie `candidate`.
Próbę zatrzymuje `ACTION=STOP` lub `INVENTURE_STOP`.

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=STOP
```

Zatrzymanie aktywnej próby albo jej zakończenie przełącza MCU w tryb
`OFF`. Przed kolejną próbą lub załadowaniem filamentu ponownie wybierz
`INVENTURE_CONTROL MODE=SERVICE`.

## Parametry START

| Parametr | Wartość lub zakres | Znaczenie |
|---|---|---|
| `MODE` | `FULL` domyślnie, `VERIFY_T0` do pierwszej próby | Zakres pomiaru |
| `SCAN` | `SWEEP` domyślnie, `TAP` dla wyniesionego pola | Przejazdy na wysokości styku albo pionowe próby |
| `PREP` | `RETRACT` domyślnie, `EMPTY` dla dwóch pustych dysz | Przygotowanie dysz |
| `T0_Z_MAX`, `T1_Z_MAX` | Wartości zweryfikowane na drukarce | Najwyższa dozwolona pozycja stołu od PE12. T1 jest wymagane w trybie `FULL` |
| `T0_TARGET`, `T1_TARGET` | Jawne cele od 190°C do granicy MCU i konfiguracji | Temperatury właściwe dla wycofania obu materiałów. MCU R9 obsługuje cele do 310°C |
| `MAX_POWER` | 40% domyślnie, zakres 1–40% | Limit mocy każdej z dwóch grzałek |
| `T0_RETRACT_MM`, `T1_RETRACT_MM` | Jawne długości 0,1–100 mm dla `PREP=RETRACT` | Nominalny ruch ekstrudera w kierunku cofania |
| `T0_PAD_X/Y`, `T1_PAD_X/Y` | X i Y podawane parami | Przybliżone środki pola w układzie serwisowym. Jawne nazwy to np. `T0_PAD_X` i `T0_PAD_Y` |
| `PAD_DIAMETER_MM` | 7 mm dla fabrycznego pada, 5 mm dla osobnego pola w `TAP`. Zakres 2–10 mm | Zmierzona średnica pola używana do kontroli szerokości styku |
| `SCAN_RADIUS_MM` | 5 mm dla pada Ø7 mm, 3,5 mm dla Ø5 mm. Zakres 3–6 mm | Połowa szerokości obszaru prób w X i w Y od przybliżonego środka. Tryb `CENTER` domyślnie używa 5 mm. Pojedynczy przejazd `SWEEP` ma limit 6 mm |
| `XY_SWEEP_SPEED` | 0,25 mm/s domyślnie, zakres 0,05–0,5 mm/s | Prędkość przejazdu po styku w `SCAN=SWEEP` |
| `EDGE_TOLERANCE_MM` | 0,05 mm w `SWEEP`, 0,02 mm w `TAP`. Zakres 0,005–0,1 mm | Maksymalna szerokość przedziału krawędzi przy pionowych dotknięciach |
| `Z_CLEARANCE_MM` | 1,5 mm domyślnie, zakres 0,5–4 mm | O ile poziom przejazdu pomiędzy próbami leży poniżej podanego limitu |
| `Z_TAP_SPEED` | Domyślnie 0,25 mm/s dla `SWEEP` i 0,8 mm/s dla `TAP`. Zakres 0,2–1 mm/s | Prędkość pionowego podejścia do styku |
| `RETRACT_SPEED` | 1,6 mm/s domyślnie, zakres 0,5–6 mm/s | Prędkość cofania filamentu podczas przygotowania |
| `XY_REPEAT_MM`, `Z_REPEAT_MM` | 0,08 i 0,05 mm domyślnie | Tolerancje powtórnego T0. Limit XY służy też porównaniu środków dwóch przebiegów `SWEEP`, a limit Z porównaniu prób rozwarcia w środku pola |
| `PROCEDURE_TIMEOUT` | 1800 s domyślnie, zakres 60–3600 s | Całkowity limit czasu liczony od startu sesji |

Gdy brak jawnego środka T1, Klipper korzysta z nominalnego odstępu
dysz i uwzględnia zapisany wcześniej offset XY z kalibracji wydrukiem.
Po przeliczeniu na kroki Z poziom przejazdu między próbami,
czyli limit `T0_Z_MAX` lub `T1_Z_MAX` pomniejszony o
`Z_CLEARANCE_MM`, musi wynosić co najmniej 20 mm od PE12.

## Odczyt i zapis

Po pełnym, powtarzalnym pomiarze odczytaj `run_id` z odpowiedzi STATUS.
Sprawdź kierunek i wartość korekty oraz rozrzut próbek. Wariant
`VERIFY_T0` raportuje pomiar pola. Do zapisu korekty podaj identyfikator
sprawdzonego przebiegu `FULL` w stanie `candidate`.

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=SAVE ACK=PAD_RESULT_REVIEWED RUN_ID=<run_id>
```

Polecenie zapisuje dane w `inventure_nozzle_pad.json` obok `printer.cfg`.
Plik zawiera docelową temperaturę pomiaru, odczyty temperatur na
początku każdego skanu oraz dane do weryfikacji i ewentualnego
przeniesienia korekt do konfiguracji druku.
Względny ruch karetki od T0 do T1 to środek dla T1 minus średni środek
z obu pomiarów T0. Fizyczne przesunięcie końcówek dysz ma przeciwny
znak XY. Jeżeli PC14 rozwiera się dla T1 przy niższej pozycji stołu
liczonej od PE12, T1 wystaje niżej.

| Pole wyniku | Znaczenie |
|---|---|
| `measurements` | Środki, krawędzie elektryczne i pomiary Z dla T0, T1 i powtórnego T0 |
| `scan_method` | Metoda użyta dla danej dyszy |
| `xy_center_repeat_mm` | Największa różnica współrzędnej środka między przebiegami `SWEEP` |
| `sample_temperatures_c` | Zmierzona temperatura obu dysz na początku każdego skanu |
| `t1_offset_mm` | Względne przesunięcie końcówki T1 w XY wobec T0 |
| `t1_carriage_shift_mm` | Zmiana pozycji karetki w XY, aby T1 trafiła w punkt T0 |
| `t1_lower_by_mm` | Różnica wysokości dysz. Wartość dodatnia oznacza niżej wystającą T1 |
| `t0_reseat_mm` | Położenie T0 po powrocie pomniejszone o położenie w pierwszym pomiarze |

Po `PREP=RETRACT` filament pozostaje cofnięty. Załaduj materiał nad koszem
zgodnie z [procedurą dla właściwego toru](FACTORY_LOAD_2026-11-10.md)
i potwierdź wypływ przed następnym drukiem.

Wynik `VERIFY_T0` służy do sprawdzenia zgodności zmierzonych krawędzi
z geometrią tacki na drukarce. Raport podaje elektryczną szerokość pola
w obu osiach. Rozmiar i stan końcówki dyszy wpływają na tę szerokość.
