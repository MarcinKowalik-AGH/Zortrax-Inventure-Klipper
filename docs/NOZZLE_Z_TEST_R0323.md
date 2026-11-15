# Inventure — próba wysokości dysz T0 i T1

`ZORTRAX_NOZZLE_Z_TEST` jest makrem dla `INVENTURE_NOZZLE_Z_TEST`.
Polecenie porównuje wysokości rozgrzanych T0 i T1 na tym samym metalowym
polu tacki. Wymaga `[inventure_mcu]` z `allow_actuators: True`,
zaktualizowanego hosta i aplikacji MCU `RC8-CONTROL-R7-NOZZLE-Z`.
Po aktualizacji sprawdź `nozzle_z_test_abi: 1` w `INVENTURE_VERSION`.
Makro znajduje się w pliku `config/INVENTURE_HOME.cfg`. Skopiuj jego
nową wersję do katalogu konfiguracji drukarki i użyj istniejącego
`[include INVENTURE_HOME.cfg]`. W konsoli Fluidd wybierz
`INVENTURE_CONTROL MODE=SERVICE`. Możesz też wywołać bezpośrednio
`INVENTURE_NOZZLE_Z_TEST` z tymi samymi argumentami.

## Ustawienie nad polem na zimno

Oczyść końcówki obu dysz i poczekaj, aż obie ostygną poniżej 50°C.
Osadź tackę i odsłoń metalowe pole
kontaktowe. Sprawdź przebieg ruchu osobno dla każdej dyszy. Po zakończeniu
każdego `PREPARE` stół stoi na dolnej krańcówce PE12, a wybrana dysza
powinna znajdować się dokładnie nad środkiem tego samego pola.

```gcode
ZORTRAX_NOZZLE_Z_TEST ACTION=PREPARE TOOL=T0
```

```gcode
ZORTRAX_NOZZLE_Z_TEST ACTION=PREPARE TOOL=T1
```

| Dysza | Przewidywana pozycja głowicy w układzie serwisowym R7 | Argumenty do korekty |
|---|---:|---|
| T0 | X82,8 / Y68,0 mm | `T0_PAD_X`, `T0_PAD_Y` |
| T1 | X72,0 / Y68,0 mm | `T1_PAD_X`, `T1_PAD_Y` |

Obie wartości wynikają z fabrycznego punktu 14 X67,5/Y65,5 i nominalnego
przesunięcia X dysz 10,8 mm. Log bazowania na tym egzemplarzu potwierdza
punkt T0. Punkt T1 wymaga sprawdzenia na zimno. Jeśli dysza nie jest nad
środkiem, podaj skorygowane współrzędne w `PREPARE`, a następnie użyj ich
także w `START`. `INVENTURE_INPUTS` odczytane na dolnej krańcówce powinno
pokazać osadzoną tackę (`tray=0`), zwolniony kontakt (`nozzle_contact=1`)
i aktywną dolną krańcówkę (`z_bottom=1`).

## Pomiar

Po sprawdzeniu obu pozycji oraz wolnej drogi stołu wywołaj `START`.
Podaj `T1_PAD_X` i `T1_PAD_Y`. Jeśli otrzymasz współrzędne inne niż
w przykładzie, podstaw wynik swojej próby.

```gcode
ZORTRAX_NOZZLE_Z_TEST ACTION=START T0_PAD_X=82.8 T0_PAD_Y=68 T1_PAD_X=72 T1_PAD_Y=68 ACK=BOTH_NOZZLES_OVER_SAME_PAD
```

Procedura dla każdej dyszy bazuje dolne Z oraz XY, nagrzewa i przełącza
selektor. T0 pracuje przed T1. Potem następuje szybki dojazd do początku pomiaru,
wolne zwarcia i rozwarcia PC14 w STM32 oraz odjazd. Każda próbka zaczyna
się z tego samego położenia w pionie względem PE12. Po grupie stół wraca
na PE12, a polecenie zatrzymania wyłącza grzanie.
`ZORTRAX_NOZZLE_Z_TEST ACTION=STATUS` pokazuje bieżący etap lub ostatni wynik.
Próbę przerywa `ZORTRAX_NOZZLE_Z_TEST ACTION=STOP` albo `INVENTURE_STOP`.

| Parametr | Domyślnie | Działanie |
|---|---:|---|
| `TARGET` | 200°C | Cel pomiarowy dla każdej dyszy, 200–210°C |
| `MAX_POWER` | 40% | Limit mocy grzania, 1–40% |
| `Z_APPROACH_MM` | 137 mm | Zgrubny dojazd od dolnej krańcówki. Dobierz z rzeczywistej szczeliny, pozostawiając zapas przed kontaktem |
| `Z_PROBE_MM` | 10 mm | Zakres dokładnego szukania rozwarcia od początku pomiaru |
| `Z_PROBE_SPEED` | ok. 0,312 mm/s | Prędkość mikrokroków w obu kierunkach. Odpowiada odstępowi ok. 4 ms |
| `Z_STABLE_CYCLES` | 201 | Liczba kolejnych cykli bez znalezienia lepszego rozwarcia w jednej próbce |
| `MAX_SPREAD_MM` | 0,15 mm | Dopuszczalny rozrzut próbek jednej dyszy, 0,005–0,5 mm |

`V9_NOZZLE_Z_TEST` pokazuje dwie próbki T0 i trzy T1. Porównanie używa
ostatniej próbki z każdej grupy, zgodnie z wyborem r0323. Dodatnia wartość
`t1_lower_by_mm` oznacza niżej wysuniętą T1, ponieważ stół pokonał
krótszą drogę z PE12 do rozwarcia T1.
Skala pomiaru wynosi 802 kroki/mm. Różnica trafia wyłącznie do raportu
diagnostycznego Klippera z bieżącej sesji. Podczas pomiaru obowiązuje
aktualna globalna polityka osłon.

## Odpowiednik w aplikacji 1.6.1 r0323

| Fragment | Zachowanie fabryczne | Adresy |
|---|---|---|
| Przygotowanie | Fabryczne menu sprawdza m.in. tackę, ustawia T0 na 200°C, T1 na 160°C w przygotowaniu i bazuje dół | `0x08018584`, `0x08018654–0x08018664` |
| T0 | Wybór T0, wspólne pole 14, zadane Z=1604 kroki, czyli 2 mm, dwa niezależne pomiary | `0x08018714–0x08018742` |
| T1 | Cel T1 200°C, wybór T1, zadane Z=1604 kroki, trzy niezależne pomiary | `0x080187D8–0x080187DC`, `0x08018864–0x08018890` |
| Próbka | Zakres 8020 kroków, wewnętrzny argument 200, powrót z rozwarcia do wysokości startowej przed następną próbką | `0x0801848C–0x080184E0`, `0x0801DC70` |
| Fabryczne OZ | Korekta resztowa T1 w kompensowanym układzie aplikacji, zapis rekordu 9 | `0x08018506–0x08018528` |

Fabryczny argument 200 oznacza zakończenie po przekroczeniu progu,
czyli po 201 kolejnych cyklach bez poprawy. Bazowanie Z używa osobnego
progu 100, co daje 101 takich cykli. W pełnym kreatorze drukarka przeplata
pomiary wysokości z drukowaniem wzorców X/Y. Próba Klippera wydziela sam
pomiar wysokości i nagrzewa dysze kolejno do 200°C, bez fabrycznego
jednoczesnego przygotowania T1 w temperaturze 160°C. Utrata kontaktu
w zakresie ruchu kończy próbkę błędem również po wcześniejszym rozwarciu.
Odstęp mikrokroków
jest przybliżeniem pętli czasowej aplikacji fabrycznej. Limit rozrzutu
jest kontrolą jakości próby Klippera.

Źródła projektu obejmują `application_r0323.bin`, pełny listing Thumb
i `08_calibration_safety.txt` w archiwum
`ZORTRAX_INVENTURE_KOMPLET_2026-10-02_CZESC_1_z_2.zip` oraz test
`test_alignment_binary_oracle.py` z archiwum źródłowego RC7.2.
[Audyt pomiaru PC14 i prędkości bazowania](Z_CONTACT_AND_HOMING_R0323.md#sekwencja-firmware-161-r0323).
