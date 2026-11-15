# Zortrax Inventure — kalibracja XYZ dysz na padzie

`ZORTRAX_NOZZLE_PAD_CALIBRATE` wyznacza względne położenie T0 i T1
w osiach XYZ na jednym metalowym polu tacki. Pomiar T0 → T1 → T0
sprawdza też powtarzalność osadzenia dyszy po przełączeniu. Wynik opisuje
geometrię obu dysz przy 160°C i nadaje się do porównania z nakładaniem
ścieżek w [kalibracji wydrukiem](NOZZLE_XY_ALIGNMENT_R0323.md).

## Instalacja

Zaktualizuj [moduł hosta](../README.md#aktualizacja-orange-pi) i wgraj
firmware MCU `RC8-CONTROL-R7-NOZZLE-PAD`. Po `FIRMWARE_RESTART` sprawdź pole
`nozzle_pad_abi: 1` w odpowiedzi `INVENTURE_VERSION`.
W istniejącej sekcji `[inventure_mcu]` procedura wymaga
`allow_actuators: True`.

Skopiuj `config/INVENTURE_NOZZLE_PAD.cfg` do katalogu konfiguracji Klippera
i dołącz plik w `printer.cfg`.

```ini
[include INVENTURE_NOZZLE_PAD.cfg]
```

Po przeładowaniu konfiguracji makro jest dostępne w Fluidd. Polecenie
`INVENTURE_NOZZLE_PAD_CALIBRATE` przyjmuje te same argumenty.

## Ustal zakres pomiaru przed uruchomieniem

Osadź tackę, opróżnij kosz, sprawdź obie szczotki i oczyść okrągły pad
o średnicy około 5 mm.
Sprawdź, czy PC14 powtarzalnie sygnalizuje styk na padzie, a jego
otoczenie pozwala wykonać pionowe próby z prześwitem. Rozwarty PC14 poza
padem może wystąpić także przy mechanicznym kontakcie dyszy z tacką.
W `INVENTURE_INPUTS` zwarcie PC14 odpowiada `nozzle_contact=0`, a
rozwarcie `nozzle_contact=1`.

Dla każdej dyszy ustal niezależnie najwyższą pozycję stołu, do której
bezpiecznie można go podnieść w **całym obszarze szukania krawędzi**.
Odniesieniem jest dolna krańcówka PE12, od której stół porusza się w górę.
Limit ma umożliwić dotknięcie pola i zachować prześwit nad jego otoczeniem.
Gdy geometria tacki nie zapewnia obu warunków jednocześnie, potrzebne jest
osobne pole pomiarowe z odpowiednim prześwitem dookoła.
Potwierdzenie `ACK=PAD_GEOMETRY_MEASURED` oznacza, że te granice
sprawdzono na danym egzemplarzu dla każdej dyszy objętej pomiarem.
Po zmianie tacki ustal bezpieczny zakres dla nowej geometrii.

Fabryczna geometria serwisowa w pobliżu środka pola to X82,8 / Y68,0
dla T0 i X72,0 / Y68,0 dla T1. Dokładne środki zostaną wyznaczone
z przeciwległych krawędzi. [Odniesienie współrzędnych serwisowych](NOZZLE_Z_TEST_R0323.md#pozycje-nad-padem).

## Przebieg

1. Klipper bazuje osie XY i PE12 raz na początku sesji.
2. Nad koszem serwisowym rozgrzewa dysze, kolejno wybiera T0 i T1,
   cofa wskazaną długość filamentu i przeciera każdą dyszę.
3. Obie dysze schładzają się do 160°C. Po ustabilizowaniu odczytów
   każda dysza ponownie przejeżdża po szczotce przed pomiarem.
4. Przed ruchem XY stół odsuwa się od dyszy i PC14 musi być rozwarte.
   STM32 zatrzymuje pionowy ruch w chwili zwarcia PC14 albo po
   dojściu do podanego limitu. Ostatnie punkty ze stykiem i pierwsze
   punkty bez styku wyznaczają krawędzie pola.
5. Klipper oblicza środki z przeciwległych krawędzi przy stałym Y dla
   pary X i stałym X dla pary Y. Między skanami stół najpierw obniża
   się do 20 mm od PE12, a potem o kolejne 10 mm przed przełączeniem
   dyszy. Klipper porównuje pozycje i zmierzone poziomy Z T0 i T1
   oraz różnicę między pierwszym i powtórnym pomiarem T0.

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

Pierwszą próbę wykonaj z `MODE=VERIFY_T0`. Makro przygotuje obie
dysze, a granice i położenie pada zmierzy tylko dla T0. Poniższe
komendy są szablonami. Zastąp wszystkie fragmenty w nawiasach
ostrokątnych zweryfikowanymi wartościami przed uruchomieniem.

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=VERIFY_T0 ACK=PAD_GEOMETRY_MEASURED T0_Z_MAX=<Z_mm_T0> T0_TARGET=<degC_T0> T1_TARGET=<degC_T1> T0_RETRACT_MM=<mm_T0> T1_RETRACT_MM=<mm_T1>
```

Po sprawdzeniu mapy styku T0 przejdź do pełnej kalibracji. Przywróć stan
załadowania materiału zgodny z zaplanowanym cofnięciem w nowej sesji.

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=FULL ACK=PAD_GEOMETRY_MEASURED T0_Z_MAX=<Z_mm_T0> T1_Z_MAX=<Z_mm_T1> T0_TARGET=<degC_T0> T1_TARGET=<degC_T1> T0_RETRACT_MM=<mm_T0> T1_RETRACT_MM=<mm_T1>
```

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
| `T0_Z_MAX`, `T1_Z_MAX` | Wartości zweryfikowane na drukarce, powyżej 20 mm o co najmniej jeden krok Z | Najwyższa dozwolona pozycja stołu od dolnej PE12. T1 jest wymagane w trybie `FULL` |
| `T0_TARGET`, `T1_TARGET` | Jawne cele 190–240°C | Temperatury właściwe dla wycofania obu materiałów |
| `MAX_POWER` | 40% domyślnie, zakres 1–40% | Limit mocy każdej z dwóch grzałek |
| `T0_RETRACT_MM`, `T1_RETRACT_MM` | Jawne długości 0,1–100 mm | Nominalny ruch ekstrudera w kierunku cofania |
| `T0_PAD_X/Y`, `T1_PAD_X/Y` | X i Y podawane parami | Przybliżone środki pola w układzie serwisowym. Jawne nazwy to np. `T0_PAD_X` i `T0_PAD_Y` |
| `SCAN_RADIUS_MM` | 3,5 mm domyślnie, zakres 3–6 mm | Połowa szerokości obszaru prób w X i w Y od przybliżonego środka |
| `EDGE_TOLERANCE_MM` | 0,02 mm domyślnie, zakres 0,005–0,1 mm | Docelowa szerokość przedziału pomiędzy stykiem i jego brakiem |
| `Z_CLEARANCE_MM` | 1,5 mm domyślnie, zakres 0,5–4 mm | O ile poziom przejazdu pomiędzy próbami leży poniżej podanego limitu |
| `Z_TAP_SPEED` | 0,8 mm/s domyślnie, zakres 0,2–1 mm/s | Prędkość podejścia w pojedynczej pionowej próbie |
| `RETRACT_SPEED` | 1,6 mm/s domyślnie, zakres 0,5–6 mm/s | Prędkość cofania filamentu podczas przygotowania |
| `XY_REPEAT_MM`, `Z_REPEAT_MM` | 0,08 i 0,05 mm domyślnie | Dopuszczalne przesunięcie powtórnego T0. Limit Z służy też porównaniu prób rozwarcia w środku pola |
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
| `measurements` | Środki i pomiary Z dla T0, T1 i powtórnego T0 |
| `sample_temperatures_c` | Zmierzona temperatura obu dysz na początku każdego skanu |
| `t1_offset_mm` | Względne przesunięcie końcówki T1 w XY wobec T0 |
| `t1_carriage_shift_mm` | Zmiana pozycji karetki w XY, aby T1 trafiła w punkt T0 |
| `t1_lower_by_mm` | Różnica wysokości dysz. Wartość dodatnia oznacza niżej wystającą T1 |
| `t0_reseat_mm` | Położenie T0 po powrocie pomniejszone o położenie w pierwszym pomiarze |

Po procedurze filament pozostaje cofnięty. Załaduj materiał nad koszem
zgodnie z [procedurą dla właściwego toru](FACTORY_LOAD_2026-11-10.md)
i potwierdź wypływ przed następnym drukiem.

Próbę na rzeczywistej drukarce zacznij od potwierdzenia działania
kontaktu PC14 dla T0 i zgodności zmierzonych granic z geometrią tacki.
Offset warto sprawdzić wydrukiem po wymianie dyszy lub hotendu,
serwisie selektora oraz wtedy, gdy ścieżki T0 i T1 przestaną się pokrywać.
