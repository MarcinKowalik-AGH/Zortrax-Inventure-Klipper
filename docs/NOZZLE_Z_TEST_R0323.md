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

## Pozycje nad padem

| Dysza | Domyślna pozycja głowicy w układzie serwisowym R7 | Argumenty do korekty |
|---|---:|---|
| T0 | X82,8 / Y68,0 mm | `T0_PAD_X`, `T0_PAD_Y` |
| T1 | X72,0 / Y68,0 mm | `T1_PAD_X`, `T1_PAD_Y` |

Pomiar PC14 na tym egzemplarzu potwierdził punkt T0 X82,8/Y68,0.
Fabryczne przesunięcie dysz 10,8 mm daje `X_T1 = X_T0 − 10,8`, przy
tym samym Y. Zmiana pary `T0_PAD_X/Y` przesuwa również wyliczony punkt T1.
Para `T1_PAD_X/Y` pozwala uwzględnić indywidualną korektę egzemplarza.
Dla kontroli położenia można wywołać `ACTION=PREPARE TOOL=T1` przy obu
dyszach poniżej 50°C. Podgląd wybiera dyszę mechanicznie i dojeżdża nad pad
ze stołem na PE12. Dolna krańcówka odpowiada odczytom `tray=0`,
`nozzle_contact=1`, `z_bottom=1` w `INVENTURE_INPUTS`.

## Pomiar

Oczyść dysze, osadź tackę i odsłoń pad. Po potwierdzeniu położeń i wolnej
drogi stołu wywołaj `START` z fabryczną geometrią lub parametrami korekty.

```gcode
ZORTRAX_NOZZLE_Z_TEST ACTION=START ACK=BOTH_NOZZLES_OVER_SAME_PAD
```

Procedura dla każdej dyszy bazuje dolne Z oraz XY i nagrzewa dyszę. T0
dociska selektor w prawo od X139,5 do X154,5. T1 dociska go w lewo od
X15 do X0. Oba ruchy odbywają się przy Y154,5. Po ukończeniu docisku
głowica wraca z selektora i jedzie nad pad. Potem następuje szybki dojazd do początku pomiaru,
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
Pole `selector_press` pokazuje punkty docisku i wynik ruchu zgłoszony przez MCU.
Gdy STM32 zatrzyma próbkę, `details.nozzle_z_state` pokazuje końcowy
stan pomiaru, w tym liczbę cykli i kroków. `z_contact_cycle_limit` oznacza
osiągnięcie 512 cykli bez ukończenia próbki. Wcześniejsze ukończone serie
pozostają w `tools`, a obliczenie różnicy wymaga pełnego wyniku obu dysz.
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
