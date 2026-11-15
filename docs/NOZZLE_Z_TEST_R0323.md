# Inventure — próba wysokości dysz T0 i T1

`ZORTRAX_NOZZLE_Z_TEST` jest makrem dla `INVENTURE_NOZZLE_Z_TEST`.
Polecenie porównuje wysokości rozgrzanych T0 i T1 na tym samym metalowym
polu tacki. `START` wymaga `[inventure_mcu]` z `allow_actuators: True`,
hosta `RC8-CONTROL-R7-UIFAST-NOZZLE-Z-DUAL` i aplikacji MCU
`RC8-CONTROL-R7-NOZZLE-Z-DUAL`. W `INVENTURE_VERSION` sprawdź,
czy `nozzle_z_test_abi` i `nozzle_z_dual_abi` mają wartość 1.
Jeśli makra brakuje, skopiuj `config/INVENTURE_HOME.cfg` do katalogu
konfiguracji drukarki i dodaj `[include INVENTURE_HOME.cfg]` w `printer.cfg`.
W konsoli Fluidd wybierz
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

1. Jeśli stół znajduje się poza dolną krańcówką PE12, opuszcza się o maksymalnie
   10 mm dla uzyskania prześwitu. Następuje jednokrotne bazowanie Y i X.
2. Głowica dociska selektor T0 w prawo od X139,5 do X154,5 przy Y154,5
   i jedzie nad pad.
3. Obie dysze zaczynają grzać do `TARGET`. W tym czasie stół bazuje na PE12,
   odjeżdża od krańcówki i szybko podjeżdża do początku pomiaru.
4. Gdy obie temperatury utrzymują się w granicach ±2°C od celu przez
   co najmniej 2 sekundy, STM32 wykonuje dwie próbki T0. Każda próbka
   składa się z powolnych zwarć i rozwarć PC14.
5. Stół opuszcza się o 10 mm od ostatniego rozwarcia T0. Głowica dociska
   selektor T1 w lewo od X15 do X0 przy Y154,5, po czym wraca nad ten sam
   fizyczny pad. Stół wraca do wspólnej wysokości początku pomiaru. Po
   sprawdzeniu obu temperatur STM32 wykonuje trzy próbki T1.
6. Stół opuszcza się o 10 mm od ostatniego rozwarcia T1, obie grzałki
   zostają wyłączone, a Klipper podaje różnicę wysokości.

Wszystkie próbki korzystają z jednej referencji PE12 i jednego bazowania XY.
`ZORTRAX_NOZZLE_Z_TEST ACTION=STATUS` pokazuje bieżący etap lub ostatni wynik.
Próbę przerywa `ZORTRAX_NOZZLE_Z_TEST ACTION=STOP` albo `INVENTURE_STOP`.
Podczas `START` ekran OLED wstrzymuje przesyłanie nowych klatek. Klipper
nadal odbiera odczyty i aktualizuje postęp w Fluidd. Odświeżanie OLED wraca
po zakończeniu próby. W razie utraty trybu SERVICE końcowy raport pokazuje
także ostatni stan sterowania, kabla głowicy i buforów UART.
W trakcie dokładnej próbki odczyt statusu PC14, potwierdzenie trybu
SERVICE i odnowienie sesji odbywają się z odstępem co najmniej 250 ms.
Kontrola aktualności czujników i PID działają dla obu grzałek.
Impulsy STEP i decyzje kontaktowe pozostają w timerze
STM32. STM32 pilnuje heartbeat 1000 ms i dzierżaw 1500 ms między odczytami.

| Parametr | Domyślnie | Działanie |
|---|---:|---|
| `TARGET` | 200°C | Cel pomiarowy dla każdej dyszy, 200–210°C |
| `MAX_POWER` | 40% | Limit mocy każdej grzałki, 1–40% |
| `Z_APPROACH_MM` | 137 mm | Zgrubny dojazd od dolnej krańcówki. Dobierz z rzeczywistej szczeliny, pozostawiając zapas przed kontaktem |
| `Z_APPROACH_SPEED` | 20 mm/s | Prędkość zgrubnego dojazdu przed pomiarem |
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
Skala pomiaru wynosi 802 kroki/mm. `measurement.t1_lower_by_mm` to wynik
diagnostyczny do wykorzystania przy późniejszym ustawianiu offsetu druku.
Raport zawiera `coordinate_saved: false`. Podczas pomiaru obowiązuje
aktualna globalna polityka osłon.

## Odpowiednik w aplikacji 1.6.1 r0323

| Fragment | Zachowanie fabryczne | Adresy |
|---|---|---|
| Przygotowanie | Fabryczne menu sprawdza m.in. tackę, ustawia T0 na 200°C, T1 na 160°C, bazuje dół i przechodzi do bazowania Y i X | `0x08018584`, `0x08018654–0x0801867C` |
| T0 | Wybór T0, wspólne pole 14, zadane Z=1604 kroki, czyli 2 mm, dwa niezależne pomiary | `0x08018714–0x08018742` |
| Przejście do T1 | Po ruchach wzorca firmware dodaje 2 mm do bieżącego Z. Wywołanie procedury XY omija dojazd do krańcówek, gdy osie mają już referencję | `0x0801882A–0x08018852`, `0x0801D45C–0x0801D45E`, `0x0801D500`, `0x0801D8FC` |
| T1 | Cel T1 200°C, wybór T1, zadane Z=1604 kroki, trzy niezależne pomiary | `0x080187D8–0x080187DC`, `0x08018864–0x08018890` |
| Próbka | Zakres 8020 kroków, wewnętrzny argument 200, powrót z rozwarcia do wysokości startowej przed następną próbką | `0x0801848C–0x080184E0`, `0x0801DC70` |
| Fabryczne OZ | Korekta resztowa T1 w kompensowanym układzie aplikacji, zapis rekordu 9 | `0x08018506–0x08018528` |

Fabryczny argument 200 oznacza zakończenie po przekroczeniu progu,
czyli po 201 kolejnych cyklach bez poprawy. Bazowanie Z używa osobnego
progu 100, co daje 101 takich cykli. W pełnym kreatorze drukarka przeplata
pomiary wysokości z drukowaniem wzorców X/Y. Próba Klippera wydziela pomiar
wysokości. Bazuje XY przed dojazdem do PE12, ustawia wspólny cel obu grzałek
od początku grzania i stosuje odjazd 10 mm przy zmianie dyszy oraz na końcu.
Utrata kontaktu w zakresie ruchu kończy próbkę błędem również po
wcześniejszym rozwarciu. Odstęp mikrokroków jest przybliżeniem pętli
czasowej aplikacji fabrycznej. Limit rozrzutu jest kontrolą jakości próby
Klippera.

Źródła projektu obejmują `application_r0323.bin`, pełny listing Thumb
i `08_calibration_safety.txt` w archiwum
`ZORTRAX_INVENTURE_KOMPLET_2026-10-02_CZESC_1_z_2.zip` oraz test
`test_alignment_binary_oracle.py` z archiwum źródłowego RC7.2.
[Audyt pomiaru PC14 i prędkości bazowania](Z_CONTACT_AND_HOMING_R0323.md#sekwencja-firmware-161-r0323).
