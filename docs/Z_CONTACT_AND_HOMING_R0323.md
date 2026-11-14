# Inventure V9 — kontakt dyszy i odniesienie Z

## Próba styku na postoju

Drukarka ma być w bezruchu, a dysza i tacka ostudzone. Użyj czystego metalowego
pola kalibracyjnego tacki i czystej końcówki wysuniętej dyszy. Bez poruszania
stołem połącz te dwie powierzchnie czystym przewodzącym łącznikiem. Łącznik
dotyka wyłącznie końcówki dyszy i pola na tacce.

Wykonaj `INVENTURE_INPUTS` osobno przed połączeniem, przy utrzymanym
połączeniu i po zabraniu łącznika. Polecenie pobiera świeży odczyt z MCU.

| Warunek | `raw.nozzle_contact` | Bit 3 w `raw_mask` |
|---|---:|---:|
| Rozwarte | 1 | 1 |
| Połączone | 0 | 0 |
| Ponownie rozwarte | 1 | 1 |

PC14 to kontakt dyszy aktywny w stanie niskim. PE12 to dolna krańcówka Z,
aktywna w stanie wysokim. PC13 to detektor osadzenia tacki, aktywny w stanie
niskim. Zmiana PC14 przy nieruchomym PE12 potwierdza niezależność torów.

## Ograniczona próba zatrzymania

Po uzyskaniu `1 → 0 → 1` można sprawdzić zatrzymanie w MCU. Początek próby
wymaga ustawienia wysuniętej dyszy nad metalowym polem z widoczną szczeliną
mieszczącą się w zadanym zakresie. Łącznik użyty podczas próby na postoju
ma być już usunięty. Przykład ogranicza ruch do 80 kroków, nominalnie 0,10 mm.

```gcode
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_MOVE AXIS=Z STEPS=80 DIR=1 RATE=160 MAX_MS=2000 TIMER=1
```

`DIR=1` podnosi stół. Host i MCU dodają maskę `8` i poziom `0` dla PC14.
Po zatrzymaniu odczytaj `INVENTURE_MOTION ACTION=STATUS`. Dotknięcie pola
powinno dać `reason: stop_input`, `hit: 8` i `hit_inputs: [nozzle_contact]`.
Aktywny styk przy starcie daje zero kroków. Wykonanie wszystkich kroków
z `hit: 0` oznacza koniec zadanego zakresu. Odczyt bez kontaktu oraz kontakt
przy starcie pozostawiają próbę zatrzymania w ruchu do sprawdzenia.
`INVENTURE_STOP` przerywa ruch. Ruch serwisowy nie ustanawia współrzędnej Z.

## Rozgrzana próba odniesienia Z w SERVICE

Wykonuj ją po potwierdzeniu przełączania PC14 i przy osadzonej tacce
(`raw.tray: 0`). Obsługiwane jest T0. Domyślna zadana temperatura to
200°C, zgodnie z przygotowaniem T0 w kreatorze poziomowania Inventure.
Można zmienić ją przez `TARGET`. Procedura utrzymuje temperaturę dyszy
podczas pomiaru i wyłącza grzałkę po zakończeniu. Profil `OEM` wymaga
aplikacji MCU `RC8-CONTROL-R7-Z-NATIVE` z `z_home_abi: 1`.

Najpierw przygotuj drukarkę i sprawdź ustawienie dyszy nad środkowym
srebrnym polem. Ten etap kończy się z platformą na dolnej krańcówce.

```gcode
INVENTURE_CONTROL MODE=SERVICE
INVENTURE_PROCEDURE ACTION=LIST
INVENTURE_PROCEDURE ACTION=PARAMS PROCEDURE=HOME_Z TOOL=T0 SPEED_PROFILE=OEM
INVENTURE_PROCEDURE ACTION=Z_PREPARE TOOL=T0 TARGET=200 SPEED_PROFILE=OEM
```

Po zakończeniu sprawdź `INVENTURE_PROCEDURE ACTION=STATUS`, osadzenie tacki
i ustawienie T0. Nominalny punkt 14 to X67,5/Y65,5 w układzie r0323, co po
przeliczeniu T0 daje X82,8/Y68,0 w układzie R7 liczonym od zwolnionych
krańcówek. `Z_PROBE_X` i `Z_PROBE_Y` pozwalają podać zmierzone współrzędne
w tym układzie. W razie korekty użyj tych samych wartości w `Z_PREPARE`
i `HOME_Z`.

`HOME_Z` ponownie wykonuje przygotowanie. Przy platformie na PE12
zmierz odstęp w pionie od metalowego pola do końcówki dyszy. W
`Z_APPROACH_MM` podaj ten odstęp pomniejszony o zapas większy od
niepewności pomiaru. Pozostała szczelina musi mieścić się w
`Z_PROBE_MM`. Profil OEM ma domyślnie zakres 10 mm w każdą stronę
od punktu wejścia w pomiar. MCU zachowuje ten zakres przez cały przebieg.
W próbie 53 odczyt z MCU wykazał `hit=8` po 3471 krokach precyzyjnego
dojazdu, na wysokości 139,327930 mm od PE12. Operator potwierdził dotknięcie.
Przy kolejnym odczycie PC14 był rozwarty, więc próba nie zmierzyła jeszcze
punktu rozwarcia. Dla tej samej geometrii i ustawienia T0 użyj sprawdzonego
dojazdu 135 mm oraz fabrycznego budżetu pomiarowego 10 mm.

Kolejna próba z profilem OEM doszła do potwierdzonego zwarcia na
139,362843 mm od PE12. MCU zatrzymał odjazd (ruch 15) na chwilowym
rozwarciu PC14 przed pierwszym krokiem. Świeży odczyt znów pokazywał
zwarcie. W tej próbie nie ukończono żadnego pełnego cyklu pomiarowego.

Próba `HOME-Z-RELEASE` potwierdziła trzy pełne cykle i najniższe zmierzone
rozwarcie na 111 762 krokach (139,354115 mm). Przy czwartym zwarciu
(ruch 35, trzy kroki w górę) PC14 zmienił stan między świeżym odczytem
końcowym a sprawdzeniem przed odjazdem. Przebieg przerwano przed pomiarem
stabilności. `HOME-Z-RETRY` obsługuje również tę zmianę stanu.

Kolejny log z `HOME-Z-RETRY` potwierdził 3 pełne cykle i pośrednie minimum
111 759 kroków (139,350374 mm). Próba zakończyła się na limicie ponowień.
Ostatni ruch 67 zatrzymał się na rozwarciu bez kroku w dół i ze świeżym
odczytem PC14=1. `HOME-Z-VERIFY` sprawdza taki odczyt ponownie na postoju.

W dwóch próbach `HOME-Z-VERIFY` uzyskano odpowiednio pięć i jeden pełny
cykl. Najlepsze rozwarcia wyniosły 111 761 i 111 767 kroków od PE12.
Obie próby przerwano, gdy PC14 rozwarł się przed pierwszym krokiem w dół
i ponownie zwarł przy świeżym odczycie. Różnica minimów to 0,007481 mm.

```gcode
INVENTURE_PROCEDURE ACTION=HOME_Z TOOL=T0 TARGET=200 SPEED_PROFILE=OEM Z_APPROACH_MM=135 Z_PROBE_MM=10 ACK=PAD_AND_CLEARANCE_CHECKED
```

| Parametr | Domyślna wartość | Znaczenie |
|---|---:|---|
| `TARGET` | 200°C | Temperatura T0 podczas pomiaru, 180–240°C |
| `SPEED_PROFILE` | `SERVICE` | `OEM` stosuje F8000 na przejeździe do pola oraz parametry Z z kolejnych wierszy |
| `HOME_Z_MAX` | 250 mm SERVICE / 150 mm OEM | Maksymalna droga szukania PE12 |
| `Z_BOTTOM_SPEED` | 2 mm/s SERVICE / 8,285 mm/s OEM | Zjazd do PE12. W SERVICE wartość dziedziczy `Z_SPEED` |
| `Z_BOTTOM_RELEASE_SPEED` | 1 mm/s SERVICE / 1,246 mm/s OEM | Odjazd do rozwarcia PE12 |
| `Z_BACKOFF_SPEED` | 1 mm/s SERVICE / 20 mm/s OEM | Dodatkowy odjazd o 1 mm po rozwarciu PE12 |
| `Z_PROBE_X`, `Z_PROBE_Y` | 82,8 / 68,0 mm | Środek w układzie R7 dla T0 |
| `Z_APPROACH_MM` | Wymagany dla `HOME_Z` | Odległość od zadziałania PE12 do początku precyzyjnego pomiaru. Co najmniej 2 mm, a z `Z_PROBE_MM` najwyżej `HOME_Z_MAX` i 250 mm |
| `Z_APPROACH_SPEED` | 1 mm/s SERVICE / 20 mm/s OEM | Prędkość dojazdu w górę przed precyzyjnym pomiarem |
| `Z_PROBE_MM` | 10 mm OEM / 1 mm SERVICE | Zakres precyzyjny wokół początku pomiaru, do 10 mm w każdą stronę |
| `Z_RELEASE_MM` | 1 mm | Budżet odjazdu serwisowego i odjazdu przy styku na zgrubnym najazdzie |
| `Z_PROBE_SPEED`, `Z_RELEASE_SPEED` | ok. 0,312 mm/s OEM / 0,2 mm/s SERVICE | OEM używa tego samego odstępu kroków w obu kierunkach. Domyślne 4000 µs to przybliżenie pętli r0323 |
| `Z_STABLE_CYCLES` | 101 | Kolejne pełne cykle bez znalezienia niższego rozwarcia, 1–101 |
| `Z_MAX_CYCLES` | 512 | Stały limit bezpieczeństwa w OEM. W SERVICE od `Z_STABLE_CYCLES + 1` do 512 |
| `Z_TRANSIENT_BACKOFF_MM` | 0,2 mm | Ograniczony odjazd przed ponownym podejściem w SERVICE, 0,05–0,5 mm |
| `Z_TRANSIENT_RETRIES`, `Z_TRANSIENT_TOTAL` | 3 / 10 | Wspólne limity ponowień zwarcia i rozwarcia w SERVICE |
| `Z_RELEASE_RECOVERY_STEPS` | 16 | Maksymalny dodatkowy ruch w dół po niepotwierdzonym rozwarciu w SERVICE |
| `Z_SAMPLE_CLEARANCE_MM` | 0,05 mm | Dodatkowy odjazd między pomiarami w SERVICE |

Po zwolnieniu PE12 procedura robi dodatkowy odjazd o 1 mm w górę. Podczas
podejścia MCU zatrzymuje ruch na PC14 lub przy utracie osadzenia tacki.
Kontakt przed osiągnięciem początku precyzyjnego pomiaru przerywa pomiar.
W profilu OEM host uruchamia jeden pomiar. MCU zmienia kierunek po
zwarciu, bada PC14 przed pierwszym krokiem w dół i zapisuje rozwarcie także
wtedy, gdy nastąpiło bez kroku. MCU zachowuje najniższe rozwarcie i wraca
do tej pozycji przy końcu pomiaru. Po 101 kolejnych cyklach bez poprawy
normalne kryterium r0323 kończy pętlę. Firmware ogranicza też drogę, czas
i liczbę cykli oraz sprawdza PE12, PC13 i ważność sesji oraz temperatury.
Klipper odczytuje postęp bez sterowania zmianami kierunku przez UART.

Profil `SERVICE` zachowuje diagnostykę sterowaną z hosta z dodatkowymi
potwierdzeniami PC14, odjazdami i ograniczonymi ponowieniami. W obu
profilach `factory.result.z_home_result` zawiera pozycję od PE12 i liczniki
cykli. `coordinate_saved: false` opisuje wynik serwisowy. Układ roboczy
Klippera i fabryczny rekord EEPROM nie są zapisywane.
`INVENTURE_STOP` przerywa próbę.

## Sekwencja firmware 1.6.1 r0323

| Etap | Zachowanie r0323 | Ślad kodu |
|---|---|---|
| Początek | Opróżnienie kolejki, zapewnienie bazowania Y, potem X. Stan bez referencji uruchamia pomocnika dolnego Z na PE12. | `0x0801DD34`, `0x0801D5A2`, `0x0801D59C`, `0x0801D6C0` |
| Dolna referencja | Dojazd do PE12 do 120 300 kroków. Odjazd do rozwarcia do 5000 kroków i osobny przejazd w górę o 1 mm. | `0x0801D720`, `0x0801D76E`, `0x0801D6C0` |
| Podejście | Ruch XY z zadanym F8000 do punktu 14, X67,5 / Y65,5 mm w fabrycznym układzie. Następnie zadane Z = 1604 kroki, czyli 2 mm w tym układzie. | `0x0801DD62–0x0801DD92` |
| Pomiar | Szukanie kontaktu na PC14, odjazd do rozwarcia i ponowienia. Zakres wynosi 8020 kroków względem pozycji początkowej. Kod zachowuje najlepsze rozwarcie i kończy normalny cykl po 101 kolejnych cyklach bez poprawy. | `0x0801DD9E–0x0801DDB0`, `0x0801DC70`, `0x0801D368` |
| Wynik | Ustanowienie Z w pozycji rozwarcia i zapis głębokości w rekordzie 52. Silnik rozróżnia brak pierwszego kontaktu (`INT_MAX`) i niezwalniający kontakt (`INT_MIN`). | `0x0801DDB4–0x0801DDEA`, `0x0801DE0C`, `0x0801DE3E` |

TIM6 otrzymuje zegar 60 MHz i preskaler 29. Zjazd i rozwarcie PE12
używają wartości ARR odpowiednio 300 i 2000. Daje to 6644,52 i 999,50 kroków/s,
czyli 8,285 i 1,246 mm/s przy 802 krokach/mm. Odczytane F8000 jest w mm/min.
Planer zachowuje je podczas zadanego Z=2 mm i odjazdu o 1 mm, z limitem
ruchu Z 20 mm/s. Prędkości w profilu `OEM` to wartości zadane, z rampą MCU.

W silniku pomiarowym argument `100` steruje licznikiem powtórzeń. Trzeci
argument `800` pozostaje nieodczytany. Kroki pomiarowe uruchamia TIM10,
a odstępy wyznacza pętla procesora o 60 000 iteracji. Wartość 0,2 mm/s
pochodzi z próby serwisowej. Pomiar czasów STEP w oryginalnej aplikacji
pozwoli ustalić rzeczywistą prędkość precyzyjnego pomiaru. SEEK i RELEASE
krańcówek XY również używają programowych opóźnień. Zachowują serwisowe
nastawy, a przejazd do pola korzysta z wartości fabrycznej.

Handler homingu Z nie zadaje wprost temperatury dyszy. Interaktywny kreator
poziomowania i kalibracja wysokości dysz mają własne przygotowanie cieplne.
R7 SERVICE opuszcza stół przed ruchem XY, co daje prześwit na wybór T0.
OEM dopuszcza próbkę z zerowym odjazdem przy natychmiastowym rozwarciu.
Profil OEM zachowuje fabryczną kolejność odczytów PC14 w pętli MCU.
Profil SERVICE potwierdza taki przypadek dodatkowym odczytem i umożliwia
krótkie odjazdy diagnostyczne. Oryginał przy braku rozwarcia wykonywał
jeszcze jeden krok poniżej granicy. MCU `Z-NATIVE` zatrzymuje się na granicy.
MCU sprawdza PC14 również bezpośrednio przed krokiem w górę i po powrocie
do minimum. Przy odjeździe krok w dół wybrany przy zwartym PC14 jest
wykonywany po odczekaniu, jeśli lokalne zabezpieczenia nadal pozwalają
na ruch. `completion_basis` odróżnia stabilne powtórzenia od osiągnięcia
górnej granicy z wcześniej zarejestrowanym rozwarciem.
Powtórzenia `HOME_Z`
dotyczą jednego pola. Pomiar kilku pól i regulacja płaszczyzny należą do
osobnej procedury. Fabryczne bazowanie `.zcode` po pomiarze może wywołać
`0x0801D604` (Z=1 mm, XY=0,0, Z=0 w układzie roboczym). Osobny etap
przygotowania poziomowania wywołuje `0x0801DBD0`, które zadaje Z=40 mm.

Przy pustym rekordzie głębokości r0323 przyjmuje 135 000 µm jako datum
obliczeniowe dolnego Z. Po zwolnieniu PE12 i dodatkowym odjeździe o 1 mm
T0 osiąga logiczne Z=2 mm po nominalnym ruchu o 133 mm od pozycji, której
przypisano datum. Ta stała służy do odtworzenia rachunku OEM. Parametr
`Z_APPROACH_MM` w SERVICE pochodzi z pomiaru danej drukarki.

## Implementacje Klippera

R7 SERVICE używa PE12 dla dolnej referencji oraz PC14 jako lokalnego
warunku zatrzymania przy podnoszeniu stołu. Wynik fizycznej próby dolnej
krańcówki jest zapisany osobno. `INVENTURE_PROCEDURE ACTION=HOME` w R7
wyznacza referencję dolną i XY do ruchów serwisowych. [Wynik próby PE12](Z_LOWER_LIMIT_TEST_2026-11-09.md)

Repozytorium zawiera wcześniejszą implementację P03 w archiwum
`INVENTURE_RC7_2_PUBLIC_GNU_ARM_SOURCE.zip` z części `source/parts/part*.bin`.
W `regression_tests/overlay/klippy/extras/` pliki `inventure_motion.py`,
`inventure_motion_core.py` i `inventure_contact.py` mają lokalne wyzwalanie
`HomingMove` na PC14, ograniczone podejście i rozwarcie, `ReleaseTracker`
oraz `INVENTURE_REFERENCE_Z`. Konfiguracja `regression_tests/config/printer.cfg`
ma P03 domyślnie wyłączone. Natywne P09 korzysta ze wspólnego pomiaru PC14
do regulacji pięciu pól. Bieżąca aktualizacja R7 nie instaluje tych modułów.
Pełne odniesienie Z wymaga potwierdzenia styku, położenia pola w bieżącym
układzie XY, drogi kontaktu i odjazdu, powtarzalności oraz warunków cieplnych.

Źródła projektu obejmują pełny listing Thumb i aplikację r0323 z
`ZORTRAX_INVENTURE_KOMPLET_2026-10-02_CZESC_1_z_2.zip`, sekcję 12 raportu
o ruchu z `Audyt firmware.zip`, audyt wysokości dysz z 23.10.2026 oraz
kontrakt P09 z 25.10.2026.
