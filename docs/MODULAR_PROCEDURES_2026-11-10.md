# Wspólne procedury T0/T1 — host CONTROL-R7-MODULAR

Host `RC8-CONTROL-R7-UIFAST-FEED-T0-MOTION` udostępnia moduły w konsoli i
[w menu Zortrax](ZORTRAX_MENU_PROCEDURES_2026-11-10.md).
AUTO, UNLOAD i próby z `FEEDER=1` wymagają
[instalacji MCU R7-FACTORY-FEED](FACTORY_LOAD_2026-11-10.md).

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only && python3 "$HOME/inventure-orange-pi/install-orange-pi.py"
```

Po aktualizacji obu części `INVENTURE_VERSION` pokazuje host
`RC8-CONTROL-R7-UIFAST-FEED-T0-MOTION`, MCU `RC8-CONTROL-R7-FACTORY-FEED`,
`procedure_api=1`, `factory_abi=1` i `factory_feed_clock=1`.
Zainstalowane MCU FACTORY-FEED obsługuje tę wersję hosta.

## Podział programu

`inventure_factory_core.py` zawiera wspólne moduły geometrii i procesu.
`inventure_mcu.py` wykonuje ich żądania: pilnuje właściciela operacji,
nadzoru MCU, temperatur, rzeczywistych kroków i świeżych liczników.
Zmiana samej sekwencji lub jej parametrów nie zmienia protokołu MCU.

| Moduł | Wspólne zastosowanie |
|---|---|
| `home` | Opuszczenie stołu do dolnej krańcówki, bazowanie X/Y i referencja używana do końca jednej procedury. W T0 zera X/Y wyznacza zwolnienie odpowiedniego wejścia. |
| `heat` | PID wybranej dyszy, gotowość temperaturowa, limit mocy i czasu |
| `select_tool` | Dojazd, powolny docisk przełączający T0/T1 i powrót nad bin |
| `position_bin` | Dojazd do konfigurowalnego środka strefy odpadu |
| `park` | Przejazd przez bliższy boczny punkt do środka kosza, używany w T0 przed grzaniem i po podawaniu |
| `purge` | Wyciskanie połączone z oscylacjami XY; używane w L1, L4 i osobnym PURGE |
| `sweep` | Zimna próba oscylacji X jednej porcji L1. Używa tej samej trajektorii bez E, grzania i podajnika. |
| `clean` | Wspólny przejazd czyszczący, bez E; używany po purge i w osobnym CLEAN |
| `manual_feed` | Odmierzony odcinek E w kierunku LOAD/UNLOAD, opcjonalnie z kasetą |
| `load_l1`…`load_l4` | Warunki krótkiego i długiego ładowania z r0323 |
| `unload` | U0 cofanie kasetą, U1/U2 odcinki E i U3 zwolnienie A, używane samodzielnie i przy nieudanym L4 |

AUTO składa moduły pod jednym właścicielem i zachowuje napędy/referencję
pomiędzy etapami. Nie uruchamia bazowania ani osobnej sesji między purge
a czyszczeniem. T0 przelicza punkty r0323 na współrzędne od zwolnionych
krańcówek i zaczyna od L1. T1 przy A=0 na wejściu przechodzi do L2.

## Osobne polecenia serwisowe

Na początku pracy wybierz SERVICE:

```gcode
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_DOOR_POLICY ENABLE=0
```

Każdy poniższy wiersz jest osobną procedurą. Wykonuj je pojedynczo,
po zakończeniu poprzedniego. Samodzielne wywołanie zaczyna od bazowania
Z do dołu i X/Y, ponieważ zakończenie poprzedniej procedury zwalnia
napędy. Referencja serwisowa nie jest bazowaniem Z na styku dyszy ani
potwierdzoną geometrią druku. Te polecenia są serwisowe; nie są jeszcze
implementacją makr T0/T1 wywoływanych podczas wydruku.

| Polecenie | Działanie |
|---|---|
| `INVENTURE_PROCEDURE ACTION=HOME TOOL=T0` | Samo bazowanie serwisowe, bez grzania i E |
| `INVENTURE_PROCEDURE ACTION=PARK TOOL=T0` | Bazowanie Z/Y/X i przejazd przez boczny punkt do kosza, bez grzania i E |
| `INVENTURE_PROCEDURE ACTION=SELECT TOOL=T1` | Mechaniczne przełączenie na T1, bez grzania i E |
| `INVENTURE_PROCEDURE ACTION=SELECT TOOL=T0` | Mechaniczne przełączenie na T0, bez grzania i E |
| `INVENTURE_PROCEDURE ACTION=SWEEP TOOL=T0` | Bazowanie i jedna porcja ruchu X z L1 nad koszem, bez grzania, E i podajnika |
| `INVENTURE_PROCEDURE ACTION=CLEAN TOOL=T0 TARGET=230` | Nagrzanie, wybór T0 i przetarcie, bez wyciskania |
| `INVENTURE_PROCEDURE ACTION=PURGE TOOL=T1 PURGE_MM=20 CLEAN_AFTER=1 TARGET=230` | Wyciskanie z oscylacjami nad bin i czyszczenie |
| `INVENTURE_PROCEDURE ACTION=MANUAL TOOL=T0 MANUAL_DIR=LOAD MANUAL_MM=20 E_SPEED=1.6 TARGET=230` | Ręczne podanie odcinka przez E, nad bin |
| `INVENTURE_PROCEDURE ACTION=MANUAL TOOL=T1 MANUAL_DIR=UNLOAD MANUAL_MM=20 E_SPEED=1.6 TARGET=230` | Ręczne cofnięcie odcinka przez E |
| `INVENTURE_PROCEDURE ACTION=UNLOAD TOOL=T0 MATERIAL=Z-PLA TARGET=230` | Wycofanie U0–U3, z budżetami E profilu Z-PLA |
| `INVENTURE_PROCEDURE ACTION=AUTO TOOL=T1 TARGET=230` | Pełna procedura ładowania L1–L4 |

`INVENTURE_LOAD_TEST ACTION=AUTO` wywołuje ten sam nowy planer.
Dotychczasowe HEAT/JOG oraz stałoodcinkowe LOAD/RETRACT zachowują swój
interfejs. Parametry `procedure_*` dotyczą wspólnego planera AUTO i
INVENTURE_PROCEDURE; nie zmieniają nastaw starszych HEAT/JOG/LOAD/RETRACT.

SELECT wybiera dyszę przez docisk mechanizmu. Wartość `TOOL` oznacza dyszę
docelową. Dla T0 docisk od X139,5 do X154,5 przy Y154,5 zaczyna się po
potwierdzonym zakończeniu dojazdu. STATUS pokazuje w `factory.motion_legs`
osobne wpisy `selector_approach`, `selector_press` i `selector_return`.
Każdy zawiera cele, żądaną i obliczoną prędkość oraz liczniki kroków MCU.
`selection_commanded=true` potwierdza wykonanie przejazdu według liczników.
Trafienie dźwigni sprawdza się fizycznie.

PURGE i MANUAL domyślnie mają `FEEDER=0`: pracuje E. `FEEDER=1` włącza
podajnik wybranej kasety: w PURGE używa AUTO, w MANUAL kierunku zgodnego
z LOAD/UNLOAD. Logiczny LOAD zawsze podaje materiał, niezależnie od
odwrotnej polaryzacji E dla T0 i T1. AUTO zachowuje własne tryby kasety i
warunki L1–L4, niezależnie od tej nastawy prób ręcznych.

Każda zakończona procedura wyłącza grzanie i napędy. MANUAL/PURGE nie
ustawiają wyniku pełnego ładowania ani potwierdzenia wypływu. UNLOAD
kończy sukcesem dopiero po świeżym A=0. Nieudane L4 po wykonaniu tego
samego modułu wycofania nadal pozostaje nieudanym ładowaniem.

## Konfiguracja i kolejność nadpisywania

Kolejność: wartości wbudowane → `procedure_defaults` → `procedure_t0`
lub `procedure_t1` → parametry konkretnego polecenia. Na początku operacji
tworzona jest kopia nastaw, obowiązująca do jej końca.

Opcjonalnie dodaj poniższe klucze do istniejącej sekcji `[inventure_mcu]`
w konfiguracji drukarki; nie twórz drugiej sekcji o tej samej nazwie:

```ini
procedure_defaults: {"TARGET":230,"MAX_POWER":40,"E_SPEED":1.6,"CENTER_X":67.5,"CENTER_Y":150,"WIPE_Y":152}
procedure_t0: {"APPROACH_X":135,"SWITCH_X":150,"SERVICE_X":135}
procedure_t1: {"APPROACH_X":10.5,"SWITCH_X":-4.5,"SERVICE_X":0}
```

Po edycji konfiguracji użyj `RESTART`. Przykład podaje nominalne wartości
z audytu. Dla T0 domyślny `XY_INPUT_FRAME=OEM` przelicza je na układ od
zwolnionych krańcówek. `XY_INPUT_FRAME=RAW` stosuje układ SERVICE do
współrzędnych w całym efektywnym profilu, także w odziedziczonych nastawach.
Granice MIN/MAX pozostają zawsze w układzie SERVICE. Nazwy parametrów
w JSON mogą być wielkimi lub małymi literami. Nieznany klucz lub niewłaściwa
wartość powoduje błąd konfiguracji.

| Parametry wspólnych modułów | Domyślnie | Znaczenie |
|---|---|---|
| `TARGET`, `MAX_POWER`, `DURATION`, `PROCEDURE_TIMEOUT` | 230, 40, 900, 1800 | Temperatura °C, limit mocy %, czas od gotowości i całej procedury w s |
| `CENTER_X`, `CENTER_Y` | T0 X82,8 Y152,5. T1 X67,5 Y150 | Środek oscylacji nad pojemnikiem w układzie SERVICE |
| `PURGE_SPAN` | 36 mm | Pełna szerokość oscylacji, pierwszy odcinek to połowa |
| `PURGE_E_STEPS` | 2073 | Kroki E na pełną oscylację; pierwszy odcinek używa 1036 |
| `PURGE_MM`, `CLEAN_AFTER` | 20 mm, 1 | Budżet osobnego PURGE i czyszczenie po nim |
| `WIPE_Y`, `WIPE_SPAN`, `WIPE_EXIT` | T0 Y154,5. T1 Y152. Szerokość 100 mm, odsunięcie 23 mm. | Y czyszczenia w układzie SERVICE, pełna szerokość i końcowe odsunięcie od środka |
| `APPROACH_X`, `SWITCH_X`, `SWITCH_Y`, `SWITCH_SPEED` | T0 X139,5 → X154,5 przy Y154,5, 250/60 mm/s. T1 X10,5 → X−4,5 przy Y152, 4 mm/s. | Mechaniczne przełączanie w układzie SERVICE |
| `MANUAL_MM`, `MANUAL_DIR`, `FEEDER` | 20 mm, LOAD, 0 | Długość i kierunek ręcznego E oraz pomoc kasety |
| `E_SPEED`, `PURGE_SPEED`, `TRAVEL_SPEED` | T0 1,6, 30, 30 mm/s. T1 1,6, 20, 20 mm/s. | Osobny E oraz żądane tempo oscylacji i przejazdów |

Parametry `L1_*`, `LONG_MM`, `FINAL_MM`, `PULSE_*`, `FEEDER_MS/K`,
`UNLOAD_*` i bazowania zachowują opis w
[instrukcji L1–L4](FACTORY_LOAD_2026-11-10.md). PURGE_MM jest progiem pętli:
ostatnia oscylacja jest kończona, więc E może przekroczyć zadany budżet.
MANUAL_MM jest odcinkiem E wyrażonym według nominalnych 960 kroków/mm.
Nie oznacza pomiaru rzeczywistej długości filamentu. Podczas purge prędkość
E wynika z `PURGE_SPEED`, `PURGE_SPAN` i `PURGE_E_STEPS`, a nie z E_SPEED.

Konfiguracja i polecenie są walidowane przed pierwszym ruchem lub grzaniem.
Obowiązuje także limit 20 000 zdarzeń DDA/s i 600 s na jedną trajektorię.
Host dopasowuje tempo każdego przejazdu do limitu CoreXY. Przykładowo
przekątna 45° pozwala osiągnąć około 22,1 mm/s. Przy zwiększaniu
wyciskania i zmniejszaniu szerokości oscylacji planer sprawdza limit E
przed rozpoczęciem procedury.

## Odczyt, diagnostyka i przerwanie

```gcode
INVENTURE_PROCEDURE ACTION=LIST
INVENTURE_PROCEDURE ACTION=PARAMS TOOL=T0 PROCEDURE=PURGE
INVENTURE_PROCEDURE ACTION=PARAMS TOOL=T1 PROCEDURE=SELECT
```

LIST i PARAMS nie poruszają drukarką i nie grzeją. PARAMS pokazuje wynik
połączenia wszystkich warstw konfiguracji i przeliczone cele XY w układzie
SERVICE. Podczas procedury:

```gcode
INVENTURE_PROCEDURE ACTION=STATUS
```

Raport zawiera rodzaj procedury, aktywny stos modułów (`module_stack`),
ostatnie 64 wejścia/wyjścia modułów (`module_history`), fazy OEM,
parametry sesji, kroki E oraz świeże liczniki MCU. Wspólne przerwanie:

```gcode
INVENTURE_STOP
```

lub `INVENTURE_PROCEDURE ACTION=STOP`. STOP unieważnia właściciela i
referencję przed dalszą komunikacją. Nie uruchamia nowego ruchu ani
automatycznego wycofania. Zabezpieczenia temperatury, kabla, danych,
wentylatora i nadzoru MCU nadal obowiązują dla wszystkich modułów.

Geometria i całe przebiegi nowych poleceń pozostają do pierwszego
potwierdzenia na drukarce. Testy programu i symulacji protokołu nie
potwierdzają położenia dysz ani trafienia odpadu do pojemnika.
