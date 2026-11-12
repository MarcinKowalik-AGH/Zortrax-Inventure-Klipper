# Parametryzowane ładowanie kaset T0/T1 — CONTROL-R7-FACTORY-FEED

Host `RC8-CONTROL-R7-UIFAST-FEED-T0-MOTION` składa AUTO z tych samych modułów
co osobne przełączanie dyszy, purge, czyszczenie i podawanie ręczne.
Procedury używające kasety wymagają MCU `RC8-CONTROL-R7-FACTORY-FEED`.
[Wspólne moduły i konfiguracja](MODULAR_PROCEDURES_2026-11-10.md).

`INVENTURE_PROCEDURE ACTION=AUTO TOOL=T0` rozpoczyna od bazowania
serwisowego i parkowania, następnie grzeje, przełącza dyszę i wykonuje
próbę L1 zgodnie z r0323. L2 uruchamia kasetę i czeka na A. Po sygnale A
następuje przejazd do przodu, podawanie L3 i końcowy purge L4.
W T1 stan A=0 na wejściu kieruje bezpośrednio do L2.
Poprzednie `ACTION=LOAD` i `ACTION=RETRACT` pozostają osobnymi próbami
stałego odcinka; `AUTO` nie przyjmuje ich parametrów `STEPS`, `HEAD_STEPS`,
`TUBE_SECONDS`, `PURGE_STEPS`, `SMOOTH` ani `RATE`.

To procedura SERVICE dla już zamontowanej kasety i podłączonej rurki.
Nie wykonuje autoryzacji RFID ani zapisu salda kasety. External/szpula
pozostaje osobną procedurą. Fizyczne potwierdzenie poprawionego docisku
i trasy pozostaje do wykonania na drukarce. Testy programu sprawdzają
żądane kroki. Pozycję karetki, długość filamentu i zadziałanie dźwigni
weryfikuje próba mechaniczna.

## Instalacja

Pierwsze MCU R7-FACTORY zgłaszało RUN podajnika w SERVICE przy zablokowanym
zegarze PE6 (TIM9 CH2). Wersja FEED włącza zegar w sesji FACTORY. Host
sprawdza tę obsługę przed startem procedur z napędem kasety.
Drukarka z już zainstalowanym MCU FACTORY-FEED wymaga tylko aktualizacji hosta.

1. Zakończ próbę poleceniem `INVENTURE_STOP`. Po schłodzeniu skopiuj
   `MCU/INVENTUR.IVB` z paczki `INVENTURE_MCU_CONTROL_R7_FACTORY_FEED_BOOT17.zip`
   na kartę i zainstaluj aplikację przez InventureBoot 1.7.
2. W terminalu Orange Pi zaktualizuj host:

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only && python3 "$HOME/inventure-orange-pi/install-orange-pi.py"
```

3. W konsoli Klippera wykonaj:

```gcode
FIRMWARE_RESTART
```

Po ponownym połączeniu:

```gcode
INVENTURE_VERSION
```

Oczekiwane wersje to host `RC8-CONTROL-R7-UIFAST-FEED-T0-MOTION` i MCU `RC8-CONTROL-R7-FACTORY-FEED`.
W raporcie powinny być też widoczne
`procedure_api: 1`, `factory_abi: 1`, `factory_feed_clock: 1`,
`host_baud: 750000`, `p4_baud: 750000`, `baud_match: true`.
Bootloader i zapisany wybór UART pozostają bez zmiany. Instalator hosta
dodaje również `inventure_factory_core.py`, zachowuje poprzednie pliki
oraz informację o nieobecności tego modułu na starszej instalacji.

## Polecenia do próby

Najpierw można odczytać i zwalidować parametry bez uruchamiania urządzenia:

```gcode
INVENTURE_LOAD_TEST ACTION=PARAMS TOOL=T0
INVENTURE_LOAD_TEST ACTION=PARAMS TOOL=T1
```

PARAMS pokazuje parametry planera dla podanych wartości/wartości domyślnych.
STATUS podczas pracy pokazuje wartości rzeczywiście używane przez próbę.

T0 można najpierw sprawdzić na zimno. W trybie SERVICE wykonaj osobno SELECT,
a po jego zakończeniu SWEEP. SELECT bazuje osie, wykonuje docisk selektora
i wraca nad kosz. SWEEP bazuje osie, parkuje nad koszem i wykonuje trasę X
jednej porcji L1 z wyłączonym grzaniem, E i podajnikiem.

```gcode
INVENTURE_PROCEDURE ACTION=SELECT TOOL=T0
```

```gcode
INVENTURE_PROCEDURE ACTION=SWEEP TOOL=T0
```

Wariant dla lewej kasety i T0:

```gcode
INVENTURE_STOP
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_DOOR_POLICY ENABLE=0
INVENTURE_PROCEDURE ACTION=AUTO TOOL=T0 MATERIAL=Z-PLA TARGET=230 MAX_POWER=40
```

Wariant dla prawej kasety i T1, po zakończeniu lub zatrzymaniu poprzedniej próby:

```gcode
INVENTURE_STOP
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_DOOR_POLICY ENABLE=0
INVENTURE_LOAD_TEST ACTION=AUTO TOOL=T1 TARGET=230 MAX_POWER=40 DURATION=900 PROCEDURE_TIMEOUT=1800
```

Stan i przerwanie:

```gcode
INVENTURE_LOAD_TEST ACTION=STATUS
```

```gcode
INVENTURE_STOP
```

T0 opuszcza stół do dolnej krańcówki, najeżdża na krańcówki Y i X i po
każdym najeździe wyznacza zero osi na zwolnionym wejściu. Przed grzaniem
jedzie przez X32,8 Y152,5 do X82,8 Y152,5 w układzie SERVICE.
Na normalnym zakończeniu AUTO oraz po błędzie
wyłącza grzanie i napędy; chłodzenie gorącej głowicy pozostaje automatyczne.

## Etapy i sprzężenie zwrotne

| Etap | Domyślne działanie | Warunek wyjścia |
|---|---|---|
| L1 | T0 wykonuje porcję 20 mm z oscylacjami X przy stałym Y152,5 nad koszem. Porcja kończy się po pełnym ruchu X, domyślnie po 19 693 krokach E. Próg łączny wynosi 500 mm. | Dla T0 świeży impuls o wieku do 3 s oraz co najmniej 6 startów podajnika kończą krótką ścieżkę. Brak świeżego impulsu wybiera L2. |
| L2 | Podajnik do przodu, oczekiwanie na wejście A | A musi pojawić się w ciągu 5 s; A już aktywne pozwala przejść dalej. |
| L3 | Głowica T0 stoi w X150,3 Y2,5 podczas podawania kasetą i E do 250 mm E (240 000 kroków). | Co najmniej 6 zaakceptowanych impulsów. Przerwa ponad 2 s resetuje podstawę liczenia. T0 wraca przez X132,8 Y152,5 do X82,8 Y152,5. |
| L4 | AUTO kasety i oscylacje X nad koszem z budżetem 100 mm E. Domyślnie 96 394 kroki E i 3 przejazdy czyszczące dla T0. | Po co najmniej 3 startach podajnika T0 parkuje w X82,8 Y152,5. Brak startów uruchamia skonfigurowane wycofanie lub kończy błędem. |

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
| `FEEDER_MS`, `FEEDER_K` | 1000, 1,75 | Czas pojedynczego doładowania AUTO i materiałowy współczynnik budżetu |
| `E_SPEED` | 1,6 | Prędkość osobnego E w długim podawaniu |
| `PURGE_SPEED`, `TRAVEL_SPEED`, `SWITCH_SPEED` | T0 30, 30, 250/60. T1 20, 20, 4 | Żądane prędkości oscylacji, przejazdów i powolnego docisku |
| `HOME_XY_MAX`, `HOME_Z_MAX` | 160, 250 | Budżety dojazdu do krańcówek |
| `HOME_SPEED`, `Z_SPEED` | T0 10, 2. T1 5, 2 | Prędkości najazdu XY i opuszczania stołu. T0 odjeżdża od każdej krańcówki XY z prędkością 1 mm/s, do 5000 kroków. |
| `TARGET`, `MAX_POWER` | 230°C, 40% | Cel PID wybranej dyszy i limit mocy |
| `MATERIAL` | UNKNOWN | Profil transportu kasety T0 z r0323. Ustawia budżety U1/U2 i parametry napędu kasety. Temperaturę dobiera `TARGET`. |
| `DURATION` | 900 | Ograniczenie czasu od pierwszej gotowości termicznej; AUTO kończy grzanie wcześniej po zakończeniu procedury |
| `PROCEDURE_TIMEOUT` | 1800 | Niezależny limit całej procedury, włącznie z przygotowaniem |

`FEEDER_K` nie jest regulatorem obrotów kasety. Określa budżet algorytmu
doładowania, z zaokrąglaniem float32 na etapach mnożenia:
`int(f32(f32(FEEDER_MS × f32(11.52)) × f32(FEEDER_K)))` w jednostkach E.
Przy 1000 ms i 1,75 wynik to 20160 jednostek E. To model sterownika,
nie zmierzona długość filamentu ani dowód równej prędkości obu napędów.

R7 zachowuje limit 20 000 zdarzeń DDA/s. Host ogranicza tempo każdego
odcinka według przełożenia CoreXY. Odpowiada to maksymalnie 31,25 mm/s
wzdłuż pojedynczej osi i około 22,1 mm/s na przekątnej 45°. Przy żądanych
30 mm/s pełne oscylacje T0 w osi X są szybsze od poprzednich 20 mm/s,
a nadal wolniejsze od fabrycznego F3000 (50 mm/s). Fabryczne przejazdy
F8000 również przekraczają limit SERVICE. Podczas oscylacji prędkość E
wynika z ruchu XY i proporcji około 2,159 mm E na pełny odcinek 36 mm X.
`E_SPEED` steruje osobnym podawaniem ekstrudera.

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
X15,3 Y2,5. Poniższe wartości T0 są podane dla zerowej korekty egzemplarza.
Wartości T1 są w dotychczasowym układzie SERVICE.

| Parametr | T0 | T1 |
|---|---:|---:|
| `CENTER_X`, `CENTER_Y` | X82,8 Y152,5 | X67,5 Y150 |
| `APPROACH_X` | 139,5 | 10,5 |
| `SWITCH_X`, `SWITCH_Y` | X154,5 Y154,5 | X−4,5 Y152 |
| `SERVICE_X`, `SERVICE_Y` | X150,3 Y2,5 | X0 Y0 |
| `MIN_X`, `MAX_X`, `MIN_Y`, `MAX_Y` | −5; 155; 0; 155 | −5; 155; 0; 155 |

Docisk T0 biegnie od X139,5 do X154,5 przy stałym Y154,5 i żądanej
prędkości F250/60, czyli około 4,17 mm/s. Odpowiada to 9600 krokom
każdego silnika A/B. `factory.motion_legs` w STATUS pokazuje punkty,
prędkości i liczniki dla `selector_approach`, `selector_press`
i `selector_return`. Fizyczne trafienie dźwigni wymaga obserwacji na drukarce.

Parkowanie T0 wybiera najbliższy z punktów X32,8 i X132,8 na Y152,5,
po czym jedzie do X82,8 Y152,5. `PARK_SPAN` określa rozstaw punktów,
domyślnie 100 mm. `ACTION=PARK TOOL=T0` pozwala sprawdzić ten moduł na zimno.
Przed dociskiem parkowanie także używa nominalnego punktu docelowej dyszy
T0, ponieważ aktualne położenie mechanizmu przed wyborem jest nieznane.
Przy domyślnym L4 czyszczenie T0 biegnie po X132,8, X32,8 i X59,8 na Y154,5.
Pierwszy przejazd jednocześnie zmienia X i Y.

Oscylacja T0 zaczyna od −18 mm w X, następnie zmienia kierunek co 36 mm.
Ruchy E używają
fabrycznego zaokrąglenia: 1036 kroków dla pierwszego i 2073 dla kolejnych
odcinków. Koniec czyszczenia jest wybierany ze znaku następnej oscylacji,
więc zależy również od liczby wykonanych odcinków/budżetu E. Nie jest
sztywną, zawsze tą samą sekwencją X dla danej dyszy. W r0323 oscylacje
występują w L1 i L4 (`0x08012168`). L3 wykonuje dojazd i podawanie
przy nieruchomej głowicy (`0x08014694–0x08014746`).

T0 przyjmuje nadpisania współrzędnych domyślnie w układzie OEM
(`XY_INPUT_FRAME=OEM`) i przelicza je na układ od zwolnionych krańcówek.
`XY_INPUT_FRAME=RAW` dotyczy współrzędnych podanych w całym efektywnym
profilu, włącznie z odziedziczonymi. PARAMS pokazuje współrzędne po przeliczeniu.
MIN/MAX są zawsze granicami w układzie SERVICE.

## Wyładowanie i cofanie po nieudanym L4

Głowica T0 stoi nad koszem w X82,8 Y152,5 w układzie SERVICE. W r0323 Z-PLA i profil
Unknown mają limity U1/U2 30 i 70 mm, a Z-PETG 50 i 50 mm. W SERVICE
wybierz profil transportu przez `MATERIAL`.

```gcode
INVENTURE_PROCEDURE ACTION=UNLOAD TOOL=T0 MATERIAL=Z-PLA TARGET=230 MAX_POWER=40
INVENTURE_PROCEDURE ACTION=PARAMS PROCEDURE=UNLOAD TOOL=T0 MATERIAL=Z-PETG
```

| Parametry | Domyślne wartości | Znaczenie |
|---|---|---|
| `FALLBACK` | 1 | Włącza odzyskanie po braku wymaganych startów L4; 0 kończy tę gałąź błędem |
| `UNLOAD_L1`, `UNLOAD_L2` | 30, 70 | Limity E dla Z-PLA/Unknown. Z-PETG ustawia 50, 50. |
| `UNLOAD_SPEED1`, `UNLOAD_SPEED2` | 1, 1 | Prędkości cofania, jawnie ustawiane do pomiaru |
| `UNLOAD_QUIET` | 4 | Cisza impulsów kończąca wcześniejsze cofanie |
| `UNLOAD_A_TIMEOUT` | 6 | Końcowy czas cofania z kontrolą zwolnienia A |

Przed tymi dwoma budżetami kaseta cofa do 10 s lub do warunku braku A /
aktywnego B. Następnie używany jest fabryczny tryb GATED. Po etapach E
następuje końcowe cofanie do A=0 albo do limitu 6 s. Gdy cisza zakończy
U1, T0 wchodzi w U2 i wysyła jeden krok przed ponownym sprawdzeniem ciszy.
Prędkości 1 mm/s są nastawą SERVICE. Dokładne fizyczne odpowiedniki
fabrycznych opóźnień CPU wymagają pomiaru.

Wycofanie po L4 nie zamienia wyniku ładowania w sukces. Wynik informuje,
czy A zostało zwolnione, i kończy grzanie/ruch. Awaria temperatury, kabla,
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
