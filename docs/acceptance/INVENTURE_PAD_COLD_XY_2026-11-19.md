# Inventure — pomiar środka pada na zimno, 19 listopada 2026

Archiwum `klippy(19).log` zawiera dwa osobne wyniki XY z tej samej drukarki. Na R24 T1 ukończyła
`CENTER` z pionową weryfikacją obu osi. Po restarcie Klippera o 13:37:48
T0 ukończyła pomiar od pierwszego punktu pionowymi dotknięciami. W tej
sesji zapisano start MCU R13 i przebieg zgodny z `CENTER SCAN=TAP` z R25.
Ostatni jawny odczyt wersji hosta w pliku pochodzi sprzed restartu i podaje R24.
W późniejszym zapisie konsoli operator potwierdził także wynik T1 z polecenia
`CENTER SCAN=TAP`.

## Wyniki z archiwum Klippera

Wartości obliczono z końców przedziałów potwierdzonego styku i rozwarcia,
przed zaokrągleniem komunikatu w konsoli. Środki i szerokości podano w mm.

| Dysza i oś | Krawędź ujemna | Krawędź dodatnia | Środek | Szerokość styku |
|---|---:|---:|---:|---:|
| T0 X | 77,8328 | 85,4891 | 81,6609 | 7,65625 |
| T0 Y | 64,4891 | 72,1453 | 68,3172 | 7,65625 |
| T1 X | 67,0109 | 74,5109 | 70,7609 | 7,5000 |
| T1 Y | 64,9891 | 72,5672 | 68,7781 | 7,5781 |

Wszystkie osiem przedziałów krawędzi miało szerokość 0,078125 mm.
T0 sprawdziła 30 punktów po dwa razy. W 15 punktach styk powtórzył się
przy obu dojazdach. W pozostałych 15 oba dojazdy zakończyły się bez styku.
Konsola podała dla T0 X = 81,7 i Y = 68,3 mm, a dla T1 X = 70,8 i Y = 68,8 mm.

Różnica położenia T0 (pomiar zimny minus drugi zapisany pomiar przy
nastawie 100°C) wyniosła ΔX = −0,0391 mm i ΔY = −0,0266 mm.
Różnica pozycji karetki T1 minus T0 z osobnych zimnych pomiarów
wyniosła X −10,9000 i Y +0,4609 mm. Zapisany offset XYZ pochodzi z pełnego
przebiegu T0 → T1 → T0. [Dane obu prób przy 100°C](INVENTURE_PAD_R23_2026-11-19.md).

## T1 z jawnym wyborem TAP o 13:52:36

Operator uruchomił pomiar poniższym poleceniem. Konsola zgłosiła wynik
o 13:59:03. Od wydania polecenia do wyniku upłynęło 6 min 27 s.

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=CENTER TOOL=T1 SCAN=TAP T1_PAD_X=70.80 T1_PAD_Y=68.70 T1_Z_MAX=137.95 Z_CLEARANCE_MM=1 EDGE_TOLERANCE_MM=0.1
```

| Oś | Krawędź ujemna [mm] | Krawędź dodatnia [mm] | Środek [mm] |
|---|---:|---:|---:|
| X | 67,0 | 74,5 | 70,8 |
| Y | 65,0 | 72,6 | 68,8 |

Środek i wszystkie cztery krawędzie T1 zgadzają się po zaokrągleniu do
0,1 mm z wcześniejszą zimną próbą R24 oraz z obiema zapisanymi próbami
R23 przy nastawie 100°C. Współrzędne nowej próby są dostępne z jednym
miejscem po przecinku. W tym fragmencie konsoli brakuje dokładnych
przedziałów styku oraz odczytu `INVENTURE_VERSION` po restarcie.

Obie dysze ukończyły na drukarce zimny pomiar środka XY. Pomiar wysokości
i zapis offsetu XYZ wykonano w osobnych pełnych przebiegach przy 100°C.

## Dwa zatrzymania T0 przed restartem

- O 12:46:44 MCU zatrzymał sesję podczas szukania Y+, po potwierdzeniu
  obu krawędzi X.
- O 13:22:04 kolejna próba zakończyła się już przy pierwszej krawędzi X+,
  około 1 min 50 s po wejściu w tryb SERVICE. T0 miała wówczas 34,97°C,
  a T1 miała 35,30°C.

W obu przypadkach MCU zgłosił kod 5 (`motion_or_interlock_guard`). Log
R24 nie zawierał stanu zatrzymanego ruchu, który pozwoliłby wskazać
konkretne wejście lub warunek. Dla T0 archiwum dokumentuje jeden
ukończony zimny pomiar XY wykonany od pierwszego punktu pionowymi dotknięciami.

Zapis konsoli operatora zawiera polecenie i wynik próby T1 z 13:52:36.
W pliku `klippy(19).log` wiersze 22546–22547 i 22612–22613 rejestrują
zatrzymania T0, wiersz 22599 wynik T1,
a wiersze 22844–22884 pionowe pomiary i wynik T0. SHA-256 pliku logu
po rozpakowaniu wynosi `93a4ea083e52ea0c7459155eef9afbfb5ab6d067fe2af8607eb7c8843f890881`.
