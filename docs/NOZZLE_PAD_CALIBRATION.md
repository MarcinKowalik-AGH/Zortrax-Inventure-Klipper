# Zortrax Inventure — kalibracja XYZ dysz na padzie

`ZORTRAX_NOZZLE_PAD_CALIBRATE` wyznacza względne położenie T0 i T1
w osiach XYZ na jednym metalowym polu tacki. Zacznij od środkowego
okrągłego pada spośród pięciu fabrycznych pól poziomowania. Na tacce
testowanej w projekcie średnica zmierzona suwmiarką wynosi 7 mm.
Pomiar T0 → T1 → T0 sprawdza też powtarzalność osadzenia dyszy
po przełączeniu. Wynik opisuje geometrię obu dysz przy 160°C.

## Instalacja

Zaktualizuj [moduł hosta](../README.md#aktualizacja-orange-pi) do wersji
`RC8-CONTROL-R7-UIFAST-PAD-CENTER-R5`. Tryb `CENTER` korzysta z firmware MCU
`RC8-CONTROL-R7-PAD-CENTER`. `INVENTURE_VERSION` po restarcie pokazuje
`nozzle_pad_cold_abi: 1` dla automatycznej próby na zimno.
W istniejącej sekcji `[inventure_mcu]` procedura wymaga
`allow_actuators: True`.

Skopiuj `config/INVENTURE_NOZZLE_PAD.cfg` do katalogu konfiguracji Klippera
i dołącz plik w `printer.cfg`.

```ini
[include INVENTURE_NOZZLE_PAD.cfg]
```

Po przeładowaniu konfiguracji makro jest dostępne w Fluidd. Polecenie
`INVENTURE_NOZZLE_PAD_CALIBRATE` przyjmuje te same argumenty.

## Środek pada na zimnej T0

Przy czystych dyszach poniżej 50°C i osadzonej tacce wywołaj jedno polecenie.
`T0_Z_MAX` to zmierzony maksymalny dojazd stołu od PE12. W próbie na
opisywanym egzemplarzu przyjęto 139,50 mm.

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=CENTER T0_Z_MAX=139.50
```

Tryb `CENTER` ustala referencję, mechanicznie wybiera T0 i mierzy po kolei
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
pozycje. Domyślnie kończy pomiar dotknięciami, gdy przedział krawędzi
ma szerokość do 0,1 mm. Zakres XY i limit Z pochodzą z ustawień tego
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
PE12. W `SCAN=SWEEP` stół osiąga wysokość styku wyłącznie w punktach
startowych wewnątrz pola. Dysza przesuwa się nad polem na wysokości
pierwszego zwarcia. PC14 mierzy ciągłość elektryczną. Nierówność tacki,
zanieczyszczenie albo przerwa w przewodzeniu mogą zmienić odczyt granicy.
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
   T0 obie dysze grzeją się do 160°C, a mierzona dysza przejeżdża po
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

Przy `PREP=RETRACT` dysze nagrzewają się do temperatury cofania filamentu.
Po cofnięciu i pierwszym przetarciu obie dysze schładzają się do 160°C.
Każda dysza ponownie przejeżdża po szczotce przed pomiarem.

Ustaw temperaturę wycofania odpowiednią osobno dla każdego materiału.
Przykładowe 240°C dotyczy filamentu, dla którego taka nastawa jest
właściwa. Drugie przetarcie i skan przy 160°C odbywają się przy wyłączonym
podawaniu filamentu.

Wycofanie do 100 mm odbywa się samym napędem ekstrudera E. Sprawdź, czy
załadowany tor pozwala bezpiecznie cofnąć wybraną długość. W próbie
T0 materiał przeszedł około 90 mm między uchwyceniem przez zębatkę
i pierwszym wypływem. Przy cofnięciu o 100 mm przygotuj się do pełnego
załadowania materiału po pomiarze. [Obserwacja z próby T0](CASSETTE_LOAD_2026-11-10.md).

## Obsługa z konsoli

Przejdź do trybu serwisowego, gdy drukarka jest bezczynna.

```gcode
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
opuszczona nad właściwym padem, a T1 podniesiona. MCU potwierdza kroki
docisku, a położenie mechanizmu potwierdza operator. Jeśli położenie
jest prawidłowe, wznów tę samą sesję. Dla `VERIFY_T0 PREP=RETRACT` ten
postój następuje po przygotowaniu obu dysz i ponownym wybraniu T0.

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

Dla sprawdzonego wyniesionego pola wybierz `SCAN=TAP` i limity Z
odpowiednie dla pionowych prób w całym obszarze skanu.

Poleceniem `STATUS` sprawdzisz etap i wynik. Próbę zatrzymuje
`ACTION=STOP` lub `INVENTURE_STOP`.

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
| `T0_TARGET`, `T1_TARGET` | Jawne cele 190–240°C dla `PREP=RETRACT` | Temperatury właściwe dla wycofania obu materiałów |
| `MAX_POWER` | 40% domyślnie, zakres 1–40% | Limit mocy każdej z dwóch grzałek |
| `T0_RETRACT_MM`, `T1_RETRACT_MM` | Jawne długości 0,1–100 mm dla `PREP=RETRACT` | Nominalny ruch ekstrudera w kierunku cofania |
| `T0_PAD_X/Y`, `T1_PAD_X/Y` | X i Y podawane parami | Przybliżone środki pola w układzie serwisowym. Jawne nazwy to np. `T0_PAD_X` i `T0_PAD_Y` |
| `PAD_DIAMETER_MM` | 7 mm dla fabrycznego pada, 5 mm dla osobnego pola w `TAP`. Zakres 2–10 mm | Zmierzona średnica pola używana do kontroli szerokości styku |
| `SCAN_RADIUS_MM` | 5 mm dla pada Ø7 mm, 3,5 mm dla Ø5 mm. Zakres 3–6 mm | Połowa szerokości obszaru prób w X i w Y od przybliżonego środka. Tryb `CENTER` domyślnie używa 5 mm. Pojedynczy przejazd `SWEEP` ma limit 6 mm |
| `XY_SWEEP_SPEED` | 0,25 mm/s domyślnie, zakres 0,05–0,5 mm/s | Prędkość przejazdu po styku w `SCAN=SWEEP` |
| `EDGE_TOLERANCE_MM` | 0,02 mm domyślnie, zakres 0,005–0,1 mm | Docelowa szerokość przedziału między stykiem a jego brakiem w `SCAN=TAP` |
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
