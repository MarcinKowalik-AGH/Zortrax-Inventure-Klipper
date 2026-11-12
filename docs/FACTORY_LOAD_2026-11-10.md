# Parametryzowane ładowanie kaset T0/T1

Host `RC8-CONTROL-R7-UIFAST-FACTORY-AUDIT` składa AUTO z tych samych modułów
co osobne przełączanie dyszy, purge, czyszczenie i podawanie ręczne. Profil
`SPEED_PROFILE=OEM` odtwarza żądane prędkości przejazdów i oscylacji r0323
dla T0 i T1. W obu torach używaj MCU T1-TIMING z poprawką czasu obsługi
kroków na rampie. Profil
serwisowy działa z MCU `RC8-CONTROL-R7-FACTORY-FEED`.
[Wspólne moduły i konfiguracja](MODULAR_PROCEDURES_2026-11-10.md).

`INVENTURE_PROCEDURE ACTION=AUTO ... SPEED_PROFILE=OEM` rozpoczyna od bazowania
serwisowego i parkowania, następnie grzeje, przełącza dyszę i wykonuje
próbę L1 zgodnie z r0323, również przy A=0 na wejściu. L2 uruchamia
kasetę i czeka na A. Po sygnale A
następuje przejazd do przodu, podawanie L3 i końcowy purge L4.
W starszym profilu SERVICE dla T1 stan A=0 na wejściu kieruje bezpośrednio
do L2. T1 korzysta z prawej, drugiej kasety, która ma numer kanału MCU 1.
Podawanie ma kierunek DIR1 zarówno w kasecie, jak i w E T1.
Wyładowanie obu napędów ma kierunek DIR0.
Poprzednie `ACTION=LOAD` i `ACTION=RETRACT` pozostają osobnymi próbami
stałego odcinka; `AUTO` nie przyjmuje ich parametrów `STEPS`, `HEAD_STEPS`,
`TUBE_SECONDS`, `PURGE_STEPS`, `SMOOTH` ani `RATE`.

Procedura SERVICE zakłada zamontowaną kasetę i podłączoną rurkę.
Operator wybiera `TOOL` według toru kasety i `MATERIAL` według
filamentu. Wspólny katalog materiałów modelowych i podporowych działa
dla obu torów w OEM i SERVICE. Obsługa kasety opiera się na tym
ręcznym wyborze. Zewnętrzna szpula ma osobną procedurę.
Testy programu sprawdzają parametry i żądane kroki. Czasy przejazdów,
pozycję karetki i reakcję mechaniki przy szybszym ruchu trzeba zweryfikować
na drukarce.

## Instalacja

Pierwsze MCU R7-FACTORY zgłaszało RUN podajnika w SERVICE przy zablokowanym
zegarze PE6 (TIM9 CH2). Wersja FEED włącza zegar w sesji FACTORY. Host
sprawdza tę obsługę przed startem procedur z napędem kasety.
MCU CASSETTE-POLICY udostępnia szybkie ruchy w sesji FACTORY obu
narzędzi i domyślnie wyłącza globalną opcję Door lock.

1. Zakończ próbę poleceniem `INVENTURE_STOP`. Po schłodzeniu skopiuj
   `MCU/INVENTUR.IVB` z paczki `INVENTURE_MCU_CONTROL_R7_CASSETTE_POLICY_BOOT17.zip`
   na kartę i zainstaluj aplikację przez InventureBoot 1.7.
2. Zaktualizuj host w terminalu Orange Pi przez SSH.

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
python3 "$HOME/inventure-orange-pi/install-orange-pi.py"
```

3. Wykonaj restart w konsoli Klippera we Fluidd.

```gcode
FIRMWARE_RESTART
```

Po ponownym połączeniu odczytaj wersje.

```gcode
INVENTURE_VERSION
```

Oczekiwane wersje to host `RC8-CONTROL-R7-UIFAST-CASSETTE-POLICY`
i MCU `RC8-CONTROL-R7-CASSETTE-POLICY`.
W raporcie powinny być też widoczne
`procedure_api: 1`, `factory_abi: 1`, `factory_feed_clock: 1`,
`factory_motion_abi: 1`, `factory_motion_max_hz: 125000`, `factory_motion_tools: 3`,
`door_policy_global: true`, `door_policy_default_enabled: false`,
`host_baud: 750000`, `p4_baud: 750000`, `baud_match: true`.
Bootloader i zapisany wybór UART pozostają bez zmiany. Instalator hosta
dodaje również `inventure_factory_core.py`, zachowuje poprzednie pliki
oraz informację o nieobecności tego modułu na starszej instalacji.

## Próby z prędkościami OEM

Polecenia w tym rozdziale wpisz w konsoli Klippera we Fluidd. Odczyt
PARAMS pozwala sprawdzić parametry i `factory_motion_available` przed ruchem.

```gcode
INVENTURE_PROCEDURE ACTION=PARAMS PROCEDURE=SWEEP TOOL=T0 SPEED_PROFILE=OEM
INVENTURE_PROCEDURE ACTION=PARAMS PROCEDURE=AUTO TOOL=T1 SPEED_PROFILE=OEM MATERIAL=Z-SUPPORT
```

Włącz SERVICE.

```gcode
INVENTURE_CONTROL MODE=SERVICE
```

Przy otwartych osłonach odczytaj bieżące ustawienie MCU.

```gcode
INVENTURE_DOOR_POLICY
```

Na CASSETTE-POLICY po restarcie zobaczysz `scope: global`,
`enabled: false` i `lock_command_mask: 0`. Jeżeli wcześniej włączyłeś
kontrolę osłon albo pracujesz na starszym MCU, wyłącz ją w bezczynnej
drukarce poleceniem poniżej. [Globalne działanie opcji](OEM_DOOR_POLICY.md).

```gcode
INVENTURE_DOOR_POLICY ENABLE=0
```

Pierwszą próbę wykonaj na zimno. SWEEP bazuje osie, parkuje nad koszem
i wykonuje trasę X jednej porcji L1 z wyłączonym grzaniem, E i podajnikiem.

```gcode
INVENTURE_PROCEDURE ACTION=SWEEP TOOL=T0 SPEED_PROFILE=OEM
```

Przed pierwszą gorącą próbą T1 sprawdź na zimno docisk selektora.
STATUS powinien pokazać przejazd X15 → X0 przy Y154,5. Obserwacja
fizycznego przełączenia dyszy jest częścią tej próby.

```gcode
INVENTURE_PROCEDURE ACTION=SELECT TOOL=T1 SPEED_PROFILE=OEM
```

Po zakończeniu SELECT uruchom SWEEP dla T1. Pierwsza oscylacja nad
koszem prowadzi od X72 do X90, po czym głowica zmienia kierunek co 36 mm.

```gcode
INVENTURE_PROCEDURE ACTION=SWEEP TOOL=T1 SPEED_PROFILE=OEM
```

Po sprawdzeniu przejazdów wybierz jedną operację. Poniżej lewa kaseta
z filamentem podporowym i prawa z modelowym. W obu przypadkach można
wybrać także `SPEED_PROFILE=SERVICE`. Podaj rzeczywisty `MATERIAL`.

```gcode
INVENTURE_PROCEDURE ACTION=AUTO TOOL=T0 SPEED_PROFILE=OEM MATERIAL=Z-SUPPORT MAX_POWER=40
```

```gcode
INVENTURE_PROCEDURE ACTION=UNLOAD TOOL=T0 SPEED_PROFILE=OEM MATERIAL=Z-SUPPORT MAX_POWER=40
```

Poniższe polecenia dotyczą prawej kasety T1 z Z-PLA. Z-SUPPORT wybiera
235°C i budżety wycofania 50/50 mm. Z-PLA wybiera 230°C i 30/70 mm.
`TARGET` pozwala nadpisać temperaturę.

```gcode
INVENTURE_PROCEDURE ACTION=AUTO TOOL=T1 SPEED_PROFILE=OEM MATERIAL=Z-PLA MAX_POWER=40
```

```gcode
INVENTURE_PROCEDURE ACTION=UNLOAD TOOL=T1 SPEED_PROFILE=OEM MATERIAL=Z-PLA MAX_POWER=40
```

Stan bieżącej próby pokazuje STATUS. STOP przerywa pracę.

```gcode
INVENTURE_PROCEDURE ACTION=STATUS
```

```gcode
INVENTURE_STOP
```

Zwykły profil wybiera `SPEED_PROFILE=SERVICE`, domyślny przy ustawieniach
wbudowanych. Przykład dla prawej kasety z dotychczasową geometrią to
`INVENTURE_PROCEDURE ACTION=AUTO TOOL=T1 SPEED_PROFILE=SERVICE TARGET=230 MAX_POWER=40`.

T0 oraz T1 w profilu OEM opuszczają stół do dolnej krańcówki, najeżdżają
na krańcówki Y i X i po każdym najeździe wyznaczają zero osi na zwolnionym
wejściu. Przed grzaniem T0 jedzie przez X32,8 do X82,8, a T1 przez X22 do
X72. Oba przejazdy parkują na Y152,5 w układzie od zwolnionych krańcówek.
Na zakończenie AUTO oraz po błędzie sterownik wyłącza grzanie i napędy.
Chłodzenie gorącej głowicy działa automatycznie.

## Etapy i sprzężenie zwrotne

| Etap | Domyślne działanie | Warunek wyjścia |
|---|---|---|
| L1 | T0 i T1 OEM wykonują porcję 20 mm z oscylacjami X przy stałym Y152,5 nad koszem. Porcja kończy się po pełnym ruchu X, domyślnie po 19 693 krokach E. Próg łączny wynosi 500 mm. | Świeży impuls o wieku do 3 s oraz co najmniej 6 startów podajnika kończą krótką ścieżkę. Brak świeżego impulsu wybiera L2. |
| L2 | Podajnik do przodu, oczekiwanie na wejście A | A musi pojawić się w ciągu 5 s; A już aktywne pozwala przejść dalej. |
| L3 | Głowica stoi z przodu w X150,3 Y2,5 dla T0 lub X4,5 Y2,5 dla T1 OEM. Kaseta i E podają filament do limitu 250 mm E (240 000 kroków). | Co najmniej 6 zaakceptowanych impulsów. Przerwa ponad 2 s resetuje podstawę liczenia. Po etapie głowica parkuje przez punkt boczny nad koszem. |
| L4 | AUTO kasety i oscylacje X nad koszem z budżetem 100 mm E. Domyślnie 96 394 kroki E i 3 przejazdy czyszczące. | Po co najmniej 3 startach podajnika głowica parkuje w punkcie środka. Brak startów uruchamia skonfigurowane wycofanie lub kończy błędem. |

Kod r0323 w `0x0801433C` obsługuje oba kanały przez rekordy
`0x08029A64` (T0) i `0x08029AA8` (T1). Pole +0x3C wybiera punkt
podawania L3, odpowiednio 12 i 13. Wspólny kod parkowania jest w
`0x0801D8FC`, a kod wyładowania U0–U3 w `0x0801402C`.

L1 sprawdza ciszę przed licznikiem startów i budżetem. Spełnienie progu
startów przy nieaktualnych impulsach nie oznacza sukcesu. Budżety 20/100 mm
są progami pętli: kończona jest pełna oscylacja, więc rzeczywisty licznik E
może przekroczyć próg o ostatni odcinek. 250/500 mm nie oznacza zawsze
podawanej długości ani odległości od zębatki do dyszy.

`starts` zlicza uruchomienia pomocniczego napędu w MCU. `pulses` zlicza
zaakceptowane zbocza transportu PB12 dla T0 lub PD14 dla T1. Odnowienie
sesji nie jest nowym startem napędu. Impulsy potwierdzają transport;
nie ma zidentyfikowanego osobnego czujnika wypływu z dyszy.

Wejście A to PD10/T0 albo PD15/T1; jego reakcję na wsunięcie i wyjęcie
filamentu potwierdzono fizycznie. B to PF1/T0 albo PG10/T1, używane przez
fabryczny automat jako warunek doładowania. Fizycznego przypisania B do
konkretnego przycisku/mechanizmu nadal nie należy uznawać za potwierdzone.
AUTO nie zastępuje B stałym wymuszeniem pracy kasety.

## Parametry procesu

Wszystkie poniższe nazwy są argumentami jednego polecenia AUTO lub PARAMS.
Wartości długości to mm, prędkości mm/s, czasy z przyrostkiem `_TIMEOUT`,
`_QUIET` i `PROCEDURE_TIMEOUT` to sekundy; `FEEDER_MS` jest w milisekundach.

| Parametry | Domyślne wartości | Znaczenie |
|---|---|---|
| `L1_PORTION`, `L1_BUDGET`, `L1_STARTS`, `L1_QUIET` | 20, 500, 6, 3 | Porcja, próg łącznego E, starty i dopuszczalna cisza L1 |
| `A_TIMEOUT` | 5 | Oczekiwanie na A w L2 |
| `LONG_MM`, `PULSE_COUNT`, `PULSE_QUIET` | 250, 6, 2 | Budżet E i potwierdzenie transportu L3 |
| `FINAL_MM`, `FINAL_STARTS` | 100, 3 | Końcowe wyciskanie i potwierdzenie L4 |
| `FEEDER_MS`, `FEEDER_K` | 1000, 1,75. Z-FLEX i Z-SEMIFLEX 1500, 3. | Czas pojedynczego doładowania AUTO i materiałowy współczynnik budżetu |
| `E_SPEED` | 1,6 | Prędkość osobnego E w długim podawaniu |
| `PURGE_SPEED`, `TRAVEL_SPEED`, `SWITCH_SPEED` | SERVICE T0 30, 30, 250/60. SERVICE T1 20, 20, 4. OEM T0/T1 50, 8000/60, 250/60. | Żądane prędkości oscylacji, przejazdów i powolnego docisku |
| `HOME_XY_MAX`, `HOME_Z_MAX` | 160, 250 | Budżety dojazdu do krańcówek |
| `HOME_SPEED`, `Z_SPEED` | T0 i T1 OEM 10, 2. T1 SERVICE 5, 2. | Prędkości najazdu XY i opuszczania stołu. Bazowanie do zwolnienia odjeżdża od każdej krańcówki XY z prędkością 1 mm/s, do 5000 kroków. |
| `TARGET`, `MAX_POWER` | Według materiału, 40% | Cel PID wybranej dyszy w zakresie 180–240°C i limit mocy |
| `MATERIAL` | UNKNOWN | Wybiera domyślny cel, budżety U1/U2 i parametry napędu kasety dla obu torów |
| `DURATION` | 900 | Ograniczenie czasu od pierwszej gotowości termicznej; AUTO kończy grzanie wcześniej po zakończeniu procedury |
| `PROCEDURE_TIMEOUT` | 1800 | Niezależny limit całej procedury, włącznie z przygotowaniem |

`FEEDER_K` nie jest regulatorem obrotów kasety. Określa budżet algorytmu
doładowania, z zaokrąglaniem float32 na etapach mnożenia:
`int(f32(f32(FEEDER_MS × f32(11.52)) × f32(FEEDER_K)))` w jednostkach E.
Przy 1000 ms i 1,75 wynik to 20160 jednostek E. To model sterownika,
nie zmierzona długość filamentu ani dowód równej prędkości obu napędów.

Profil serwisowy zachowuje limit 20 000 zdarzeń DDA/s. Host ogranicza
tempo każdego odcinka według przełożenia CoreXY. Odpowiada to maksymalnie
31,25 mm/s wzdłuż pojedynczej osi i około 22,1 mm/s na przekątnej 45°.

| Ruch T0 i T1 w profilu OEM | Nastawa r0323 | Żądana prędkość |
|---|---:|---:|
| Parkowanie, dojazdy, powrót selektora i przejazdy czyszczące | F8000 | 133,33 mm/s |
| Oscylacje L1/L4 i powrót po porcji L1 | F3000 | 50 mm/s |
| Docisk dźwigni selektora | F250 | 4,17 mm/s |

Sesja FACTORY w MCU obsługuje do 125 000 zdarzeń DDA/s w tym profilu.
Przejazd F8000 potrzebuje około 85 334 zdarzeń/s w osi i do około
120 680 zdarzeń/s na przekątnej. F3000 w osi X potrzebuje 32 000
zdarzeń/s. Prędkości XY można obniżyć parametrami z tabeli, z zachowaniem
fabrycznej granicy proporcji E do X. Rzeczywisty czas zależy od rozpędzania
i hamowania. Przy pełnym F3000 proporcja 2073 kroków E na 36 mm X daje
około 3 mm/s E.

Bazowanie zachowuje prędkości serwisowe. Osobny ekstruder w L3
(`E_SPEED=1.6`) i cofanie w U1–U3 używają dotychczasowych nastaw
serwisowych. r0323 realizuje te etapy bezpośrednimi impulsami z pętlami
opóźnienia CPU. Ich fizyczne prędkości wymagają pomiaru.

Przykład regulacji osobnego podawania i doładowania bez zmiany progów:

```gcode
INVENTURE_LOAD_TEST ACTION=AUTO TOOL=T0 TARGET=230 E_SPEED=1.6 FEEDER_MS=1000 FEEDER_K=1.75
```

## Geometria i przełączanie

R0323 zapisuje pozycję po zwolnieniu krańcówek X/Y. Nominalne współrzędne
zwolnienia w ramie T1 wynoszą X−4,5 Y−2,5. W ramie T0 firmware uwzględnia
również nominalne przesunięcie dysz X10,8 i korektę egzemplarza.
Dla punktów selektora 1–4 korekty narzędzia są częścią zarówno punktu,
jak i zera, więc znoszą się w układzie od zwolnionych krańcówek.
Stałe punkty T0 nad koszem i z przodu mają nominalne przesunięcie
X15,3 Y2,5, a stałe punkty T1 mają przesunięcie X4,5 Y2,5.
Przyjęto zerową korektę egzemplarza. T1 SERVICE używa współrzędnych
wcześniejszej procedury.

| Parametr | T0 | T1 OEM | T1 SERVICE |
|---|---:|---:|---:|
| `CENTER_X`, `CENTER_Y` | X82,8 Y152,5 | X72 Y152,5 | X67,5 Y150 |
| `APPROACH_X` | 139,5 | 15 | 10,5 |
| `SWITCH_X`, `SWITCH_Y` | X154,5 Y154,5 | X0 Y154,5 | X−4,5 Y152 |
| `SERVICE_X`, `SERVICE_Y` | X150,3 Y2,5 | X4,5 Y2,5 | X0 Y0 |
| `WIPE_Y` | 154,5 | 154,5 | 152 |
| `MIN_X`, `MAX_X`, `MIN_Y`, `MAX_Y` | −5; 155; 0; 155 | −5; 155; 0; 155 | −5; 155; 0; 155 |

Docisk OEM biegnie od X139,5 do X154,5 dla T0 i od X15 do X0
dla T1. Obie trasy mają stałe Y154,5 oraz żądaną prędkość F250/60,
czyli około 4,17 mm/s. Odpowiada to 9600 krokom każdego silnika A/B.
`factory.motion_legs` w STATUS pokazuje punkty,
prędkości i liczniki dla `selector_approach`, `selector_press`
i `selector_return`. Fizyczne trafienie dźwigni wymaga obserwacji na drukarce.

Parkowanie T0 wybiera bliższy z punktów X32,8 i X132,8, a T1 OEM
punkt X22 lub X122. Po dojeździe bocznym głowica jedzie do środka,
całość kończy na Y152,5. `PARK_SPAN` określa rozstaw punktów,
domyślnie 100 mm. `ACTION=PARK` pozwala sprawdzić ten moduł na zimno.
Przed mechanicznym wyborem głowicy używany jest nominalny punkt
parkowania dyszy docelowej. Fizyczny stan selektora przed ruchem
pozostaje niepotwierdzony. Przy domyślnym L4 czyszczenie T0 biegnie po
X132,8, X32,8 i X59,8, a T1 OEM po X22, X122 i X95, na Y154,5.
Pierwszy przejazd czyszczący jednocześnie zmienia X i Y.

Oscylacja T0 zaczyna od −18 mm w X, T1 od +18 mm. Następnie kierunek
zmienia się co 36 mm.
Ruchy E używają
fabrycznego zaokrąglenia: 1036 kroków dla pierwszego i 2073 dla kolejnych
odcinków. Koniec czyszczenia jest wybierany ze znaku następnej oscylacji,
więc zależy również od liczby wykonanych odcinków/budżetu E. Nie jest
sztywną, zawsze tą samą sekwencją X dla danej dyszy. W r0323 oscylacje
występują w L1 i L4 (`0x08012168`). L3 wykonuje dojazd i podawanie
przy nieruchomej głowicy (`0x08014694–0x08014746`).

T0 i T1 OEM przyjmują nadpisania współrzędnych domyślnie w układzie OEM
(`XY_INPUT_FRAME=OEM`) i przelicza je na układ od zwolnionych krańcówek.
`XY_INPUT_FRAME=RAW` dotyczy współrzędnych podanych w całym efektywnym
profilu, włącznie z odziedziczonymi. PARAMS pokazuje współrzędne po przeliczeniu.
MIN/MAX są zawsze granicami w układzie SERVICE.

## Profile materiałowe obu kaset

Poniższe nastawy dotyczą T0 i T1 w OEM i SERVICE. Źródłem celów OEM
i budżetów U1/U2 są rekordy kaset r0323 pod adresem `0x08029B30`
oraz tablica materiałów `0x08029DB8`.

| `MATERIAL` | Domyślny cel procedury | U1 / U2 |
|---|---:|---:|
| `Z-SUPPORT` | 235°C | 50 / 50 mm |
| `Z-SUPPORT-PLUS`, `Z-NYLON`, `Z-SEMIFLEX` | 240°C | 30 / 70 mm |
| `Z-SUPPORT-PREMIUM`, `BASF-ULTRAFUSE-BVOH`, `Z-PLA`, `Z-FLEX`, `Z-PHA`, `UNKNOWN` | 230°C | 30 / 70 mm |
| `Z-PLA-PRO` | 200°C | 30 / 70 mm |
| `Z-PETG` | Wymaga jawnego `TARGET`. Cel OEM 245°C. | 50 / 50 mm |
| `Z-ABS`, `Z-ABS-V2`, `Z-ASA-PRO`, `Z-ESD`, `Z-ESD-V2`, `Z-GLASS`, `Z-HIPS`, `Z-PCABS`, `Z-PEEK`, `Z-ULTRAT`, `Z-ULTRAT-PLUS` | Wymagają jawnego `TARGET`. Cele OEM powyżej 240°C. | 30 / 70 mm |

Warianty z celem OEM powyżej 240°C wymagają jawnego `TARGET` właściwego
dla filamentu i mieszczącego się w zakresie 180–240°C. `PARAMS` pokazuje
źródłowy `oem_material_target_c` oraz efektywny `target`, który jest
`null`, gdy brakuje wymaganego nadpisania.
Jawne `UNLOAD_L1`, `UNLOAD_L2`, `FEEDER_MS` i `FEEDER_K`
mają pierwszeństwo przed wartościami materiałowymi.

```gcode
INVENTURE_PROCEDURE ACTION=PARAMS PROCEDURE=UNLOAD TOOL=T0 MATERIAL=Z-SUPPORT
INVENTURE_PROCEDURE ACTION=PARAMS PROCEDURE=UNLOAD TOOL=T1 MATERIAL=Z-PETG
```

## Wyładowanie i cofanie po nieudanym L4

Przy wyładowaniu głowica stoi nad koszem w X82,8 Y152,5 dla T0 lub
X72 Y152,5 dla T1 OEM.

| Parametry | Domyślne wartości | Znaczenie |
|---|---|---|
| `FALLBACK` | 1 | Włącza odzyskanie po braku wymaganych startów L4; 0 kończy tę gałąź błędem |
| `UNLOAD_L1`, `UNLOAD_L2` | 30, 70 | Limity E dla większości profili. Z-PETG i Z-SUPPORT ustawiają 50, 50. |
| `UNLOAD_SPEED1`, `UNLOAD_SPEED2` | 1, 1 | Prędkości cofania, jawnie ustawiane do pomiaru |
| `UNLOAD_QUIET` | 4 | Cisza impulsów kończąca wcześniejsze cofanie |
| `UNLOAD_A_TIMEOUT` | 6 | Końcowy czas cofania z kontrolą zwolnienia A |

Przed tymi dwoma budżetami kaseta cofa do 10 s lub do warunku braku A /
aktywnego B. Następnie używany jest fabryczny tryb GATED. Po etapach E
następuje końcowe cofanie do A=0 albo do limitu 6 s. Gdy cisza zakończy
U1, T0 i T1 OEM wchodzą w U2 i wysyłają jeden krok przed ponownym
sprawdzeniem ciszy. T1 SERVICE zachowuje wcześniejsze przejście od
zakończenia U1 bezpośrednio do U3 przy ciszy.
Prędkości 1 mm/s są nastawą SERVICE. Dokładne fizyczne odpowiedniki
fabrycznych opóźnień CPU wymagają pomiaru.

Wycofanie po L4 raportuje błąd ładowania oraz informację o zwolnieniu A.
Samodzielne UNLOAD kończy grzanie i ruch oraz potwierdza opróżnienie
dopiero po A=0. Po 6 s z A=1 raportuje, że filament nadal jest przy
czujniku. Oryginalny ekran r0323 prosi operatora o wyjęcie kasety
i odcięcie końcówki. W SERVICE operator wykonuje te czynności po
sprawdzeniu stanu. Awaria temperatury, kabla,
świeżości danych, nadzoru sesji lub ręczny STOP zawsze przerywają pracę;
nie uruchamiają automatycznego cofania.

## Wynik i zakres potwierdzenia

STATUS zawiera fazę/historię, użyte parametry, kroki E w `e_steps_by_phase`,
liczniki MCU, stan wejść, pozycję obliczoną z ruchu i stan referencji.
Zakończenie krótką ścieżką L1 nie oznacza wykonania dodatkowego L4.
Raport rozróżnia zakończony transport i wykonane czyszczenie.
Wspólne `load_completed=false` i brak potwierdzenia wypływu zachowują
znaczenie: licznik impulsów nie jest czujnikiem wypływu z dyszy.

Sesja ma niezależny nadzór MCU i odnowienia właściciela. Odnowienie nie
powinno restartować ruchu ani doliczać fikcyjnego uruchomienia kasety.
Stan pozycji traci ważność po STOP/awarii/zwolnieniu napędów. Przed kolejną
próbą AUTO ponownie wykonuje przygotowanie i bazowanie.

`MONITOR=0` jest domyślnym ustawieniem fabrycznej procedury ładowania.
`MONITOR=1` włącza dodatkowy nadzór ponowień AUTO stosowany podczas pracy;
nie zmienia progów L1/L4 ani niezależnych zabezpieczeń temperatury i czasu.
