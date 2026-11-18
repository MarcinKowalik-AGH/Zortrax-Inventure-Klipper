# Inventure — host R20: rozwarcie PC14 i odjazd pada 1 mm

Host R20 dodaje ograniczoną obsługę ponownego zwarcia PC14 przy
końcowym pomiarze Z na padzie. Domyślne `Z_CLEARANCE_MM` wynosi teraz
**1 mm**, zgodnie z dyspozycją użytkownika. Aktualizacja dotyczy hosta.
Dotychczasowe MCU R12 i jego obraz pozostają takie same.

## Co wydarzyło się w klippy(16).log

Ostatnia próba ma identyfikator `739790fb878841d39b3bb5da259ccf76`.
T0 zakończyła dwa przejścia wyznaczania krawędzi XY:

| Wynik T0 | Wartość [mm] |
|---|---:|
| X− | 77,8914 |
| X+ | 85,5086 |
| Y− | 64,6602 |
| Y+ | 72,3031 |
| Środek po zaokrągleniu do kroku XY | X81,7000 Y68,48125 |
| Efektywna szerokość styku X / Y | 7,6172 / 7,6430 |
| Największa różnica środka między dwoma przejściami | 0,009375 |

Są to częściowe wyniki XY. Nie stanowią kompletnej kalibracji XYZ
ani offsetu obu głowic. T1 nie rozpoczęła jeszcze skanu.

Następnie procedura zatrzymała się przy pierwszym końcowym pomiarze
rozwarcia Z: `PC14 release was not repeatable at pad center`.
Dotychczasowa etykieta `Y-_TOUCH` pozostała po ostatniej krawędzi XY
i nie opisywała już faktycznie wykonywanego pomiaru środka Z.

Temperatury T0/T1 wynosiły 102,344/100,733°C. Wiek statusu płyty
wynosił 0,221 s, `fault=0`, `latched=0`, `cable=0`.
Host zakończył sesję STOP; MCU nie zgłosiło guard termicznego.

Log potwierdza brak kwalifikacji rozwarcia. Nie zapisuje poziomów PC14
bezpośrednio po pierwszym zatrzymaniu i po jego potwierdzeniu, więc
nie pozwala ustalić, w którym z tych odczytów styk znów był zwarty.
Wcześniejsze próby rozpoczęcia SWEEP również kilkukrotnie wymagały
pionowej weryfikacji kontaktu.

## Zmiana pomiaru Z

Dotychczasowy końcowy pomiar pada używał pojedynczego sprawdzenia
rozwarcia. Homing Z miał już ograniczoną obsługę takiego ponownego
zwarcia, której pomiar pada nie wykorzystywał.

- Przed pomiarem Z kontakt zostaje potwierdzony świeżym odczytem
  przy nieruchomym stole.
- Jeżeli PC14 ponownie się zewrze po zatrzymaniu na rozwarciu, program
  wywołuje istniejące `_z_recover_release` z homingu Z. Wykonuje ono
  ruchy wyłącznie w dół, po 4 kroki, maksymalnie 16 kroków łącznie
  (0,01995 mm), w dotychczasowym zakresie zwalniania kontaktu.
- Każdy zaakceptowany punkt musi mieć świeżo potwierdzone rozwarcie
  na postoju. Zapis Z pochodzi z potwierdzonej pozycji.
- Nieudana próba wraca na zweryfikowaną płaszczyznę przejazdu.
  Obowiązuje limit 4 prób na próbkę przy dotychczasowym ustawieniu
  `z_transient_retries=3`.
- Nadal wymagane są dwie poprawne próbki Z, ich zgodność do 0,05 mm
  oraz zgodność powrotnego T0. Brak potwierdzenia kończy procedurę
  bez kandydata do zapisania.

Odjazd między dotknięciami określa `Z_CLEARANCE_MM=1`. Początkowa
płaszczyzna przejazdu leży 1 mm poniżej podanego limitu Z; jej
położenie nadal podlega kontroli względem zmierzonego kontaktu.
Zachowane są kwalifikowane limity T0/T1 oraz warunki zmiany narzędzia.

## Wyniki i diagnostyka

`STATUS` zachowuje `xy_measurements` po zakończeniu części XY,
także jeżeli późniejszy pomiar Z się nie powiedzie. Nowe etapy mają
nazwy takie jak `Z_PAD_T0_SAMPLE_1_ATTEMPT_1`.

`z_release_diagnostics` zawiera próbkę, numer próby, wysokość styku,
liczbę kroków i latch rozwarcia, świeży poziom PC14, dodatkowy ruch
w dół oraz informację o zaakceptowaniu próbki. Przy wyczerpaniu prób
te same dane trafiają do `details.attempts`. Po świadomym `SAVE`
diagnostyka jest zachowywana w pliku kalibracji.

## Weryfikacja i ograniczenia

539 testów hosta obejmuje pełny T0 → T1 → T0 z ponownym zwarciem
PC14 dla obu dysz oraz przypadek trwałej niestabilności. W drugim
przypadku program kończy dokładnie cztery próby, nie przekracza
16 kroków dodatkowego ruchu na próbę, zachowuje XY, wyłącza grzanie
i nie przechodzi do T1. Sprawdzone są też zapis diagnostyki i przebieg
z domyślnym odjazdem 1 mm.

Pozostałe testy obejmują homing, procedury filamentowe, konfigurację,
instalator oraz dotychczasowe zabezpieczenia. MCU R12 ma ten sam SHA256:
`5c890be7cccac852df31cab32b2db94d544ba34464377d61c34c798743828067`.

Pasmo 90–110°C, nastawy do 310°C, heartbeat 5 s, globalna polityka
drzwi i tolerancja krawędzi XY 0,05 mm są takie jak w R19/R12.
Pełny fizyczny pomiar XYZ na tej drukarce nadal wymaga potwierdzenia.

## Instalacja i próba

SSH na Orange Pi:

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
python3 "$HOME/inventure-orange-pi/install-orange-pi.py" --configure-nozzles
```

MCU R12 pozostaw bez ponownego wgrywania. Po aktualizacji sprawdź
`INVENTURE_VERSION`: host `...PAD-XYZ-R20`, MCU `...PAD-THERMAL-R12`.

Przy pustych dyszach:

```gcode
INVENTURE_CONTROL MODE=SERVICE
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=FULL SCAN=SWEEP PREP=EMPTY ACK=PAD_GEOMETRY_MEASURED T0_PAD_X=81.70 T0_PAD_Y=68.20 T1_PAD_X=70.80 T1_PAD_Y=68.70 T0_Z_MAX=139.50 T1_Z_MAX=137.95 Z_CLEARANCE_MM=1 PROCEDURE_TIMEOUT=3600
```

Odczyt:

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=STATUS
```

Oczekiwany wynik: `candidate` z pomiarami `T0`, `T1`, `T0_RETURN`.
Zapis wyniku nadal wymaga osobnego `SAVE` po ocenie pomiarów.
