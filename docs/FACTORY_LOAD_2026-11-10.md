# Parametryzowane ładowanie kaset T0/T1 — CONTROL-R7-FACTORY-FEED

Host `RC8-CONTROL-R7-UIFAST-FEED-T0` składa AUTO z tych samych modułów
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
pozostaje osobną procedurą. Pierwsze wykonanie całego R7 na drukarce
pozostaje próbą fizyczną; zgodność programu i testów nie jest pomiarem
geometrii, długości filamentu ani działania mechanizmu.

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

Oczekiwane host `RC8-CONTROL-R7-UIFAST-FEED-T0`, MCU `RC8-CONTROL-R7-FACTORY-FEED`,
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

T0 opuszcza stół do dolnej krańcówki, bazuje Y i X i jedzie przez
X17,5 Y150 do X67,5 Y150 przed grzaniem. Punkt startu SERVICE wynika
z krańcówek i nominalnych nastaw. Na normalnym zakończeniu AUTO oraz po błędzie
wyłącza grzanie i napędy; chłodzenie gorącej głowicy pozostaje automatyczne.

## Etapy i sprzężenie zwrotne

| Etap | Domyślne działanie | Warunek wyjścia |
|---|---|---|
| L1 | T0 wykonuje porcję 20 mm przy każdym wejściu. Porcja kończy się po pełnym ruchu X, domyślnie po 19 693 krokach E. Próg łączny wynosi 500 mm. | Dla T0 świeży impuls o wieku do 3 s oraz co najmniej 6 startów podajnika kończą krótką ścieżkę. Brak świeżego impulsu wybiera L2. |
| L2 | Podajnik do przodu, oczekiwanie na wejście A | A musi pojawić się w ciągu 5 s; A już aktywne pozwala przejść dalej. |
| L3 | Głowica T0 stoi w X135 Y0. Kaseta i E podają do 250 mm E (240 000 kroków). | Co najmniej 6 zaakceptowanych impulsów. Przerwa ponad 2 s resetuje podstawę liczenia. T0 wraca przez X117,5 Y150 do X67,5 Y150. |
| L4 | AUTO kasety i budżet 100 mm E. Domyślnie 96 394 kroki E i 3 przejazdy czyszczące dla T0. | Po co najmniej 3 startach podajnika T0 parkuje w X67,5 Y150. Brak startów uruchamia skonfigurowane wycofanie lub kończy błędem. |

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
| `PURGE_SPEED`, `TRAVEL_SPEED`, `SWITCH_SPEED` | 20, 20, 4 | Ruch XY z wyciskaniem, przejazdy i powolny docisk przełączający |
| `HOME_XY_MAX`, `HOME_Z_MAX` | 160, 250 | Budżety dojazdu do krańcówek |
| `HOME_SPEED`, `Z_SPEED` | 5, 2 | Prędkości bazowania XY i opuszczania stołu |
| `TARGET`, `MAX_POWER` | 230°C, 40% | Cel PID wybranej dyszy i limit mocy |
| `MATERIAL` | UNKNOWN | Profil transportu kasety T0 z r0323. Ustawia budżety U1/U2 i parametry napędu kasety. Temperaturę dobiera `TARGET`. |
| `DURATION` | 900 | Ograniczenie czasu od pierwszej gotowości termicznej; AUTO kończy grzanie wcześniej po zakończeniu procedury |
| `PROCEDURE_TIMEOUT` | 1800 | Niezależny limit całej procedury, włącznie z przygotowaniem |

`FEEDER_K` nie jest regulatorem obrotów kasety. Określa budżet algorytmu
doładowania, z zaokrąglaniem float32 na etapach mnożenia:
`int(f32(f32(FEEDER_MS × f32(11.52)) × f32(FEEDER_K)))` w jednostkach E.
Przy 1000 ms i 1,75 wynik to 20160 jednostek E. To model sterownika,
nie zmierzona długość filamentu ani dowód równej prędkości obu napędów.

R7 zachowuje limit DDA 20000 zdarzeń głównych/s. Dlatego domyślne
przejazdy i oscylacje są wolniejsze od fabrycznych. Prędkości XY można
ustawiać do 22 mm/s, przy czym host przelicza również przekątne CoreXY
i odmawia trajektorii przekraczającej limit. `E_SPEED` nie ustawia
prędkości E podczas oscylacji: wtedy wynika ona z ruchu XY i proporcji
fabrycznej, około 2,159 mm E na pełny odcinek 36 mm X.

Przykład regulacji osobnego podawania i doładowania bez zmiany progów:

```gcode
INVENTURE_LOAD_TEST ACTION=AUTO TOOL=T0 TARGET=230 E_SPEED=1.6 FEEDER_MS=1000 FEEDER_K=1.75
```

## Geometria i przełączanie

Wartości są nominalne z audytu, przed indywidualną korektą drukarki.
Zmiana parametrów jest walidowana przed pierwszym żądaniem ruchu/grzania.
Pierwsza próba sprawdza fizycznie tę geometrię, w szczególności docisk T1
przy ujemnym X. `ACTION=AUTO` wykonuje przejazd przełączający; stare LOAD
pozostaje próbą podawania bez przełączania.

| Parametr | T0 | T1 |
|---|---:|---:|
| `CENTER_X`, `CENTER_Y` | 67,5; 150 | 67,5; 150 |
| `APPROACH_X` | 135 | 10,5 |
| `SWITCH_X`, `SWITCH_Y` | 150; 152 | −4,5; 152 |
| `SERVICE_X`, `SERVICE_Y` | 135; 0 | 0; 0 |
| `MIN_X`, `MAX_X`, `MIN_Y`, `MAX_Y` | −5; 155; 0; 155 | −5; 155; 0; 155 |

Parkowanie T0 wybiera najbliższy z punktów X17,5 i X117,5 na Y150,
po czym jedzie do X67,5 Y150. `PARK_SPAN` określa rozstaw punktów,
domyślnie 100 mm. `ACTION=PARK TOOL=T0` pozwala sprawdzić ten moduł na zimno.
Przy domyślnym L4 czyszczenie T0 biegnie po X117,5, X17,5 i X44,5 na Y152.
Pierwszy przejazd jednocześnie zmienia X i Y.

Oscylacje X mają pierwszy odcinek ±18 mm, kolejne ±36 mm. Ruchy E używają
fabrycznego zaokrąglenia: 1036 kroków dla pierwszego i 2073 dla kolejnych
odcinków. Koniec czyszczenia jest wybierany ze znaku następnej oscylacji,
więc zależy również od liczby wykonanych odcinków/budżetu E. Nie jest
sztywną, zawsze tą samą sekwencją X dla danej dyszy.

## Wyładowanie i cofanie po nieudanym L4

Głowica T0 stoi nad koszem w X67,5 Y150. W r0323 Z-PLA i profil
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
