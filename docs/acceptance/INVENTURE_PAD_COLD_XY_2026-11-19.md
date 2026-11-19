# Inventure — pomiar środka pada na zimno, 19 listopada 2026

Na tej samej drukarce uzyskano dwa osobne wyniki XY. Na R24 T1 ukończyła
`CENTER` z pionową weryfikacją obu osi. Po restarcie Klippera o 13:37:48
T0 ukończyła pomiar od pierwszego punktu pionowymi dotknięciami. W tej
sesji zapisano start MCU R13 i przebieg zgodny z `CENTER SCAN=TAP` z R25.
Ostatni jawny odczyt wersji hosta w pliku pochodzi sprzed restartu i podaje R24.

## Wyniki na drukarce

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

## Dwa zatrzymania T0 przed restartem

- O 12:46:44 MCU zatrzymał sesję podczas szukania Y+, po potwierdzeniu
  obu krawędzi X.
- O 13:22:04 kolejna próba zakończyła się już przy pierwszej krawędzi X+,
  około 1 min 50 s po wejściu w tryb SERVICE. T0 miała wówczas 34,97°C,
  a T1 miała 35,30°C.

W obu przypadkach MCU zgłosił kod 5 (`motion_or_interlock_guard`). Log
R24 nie zawierał stanu zatrzymanego ruchu, który pozwoliłby wskazać
konkretne wejście lub warunek. Późniejszy dodatni wynik dotyczy fizycznego
pomiaru środka XY zimną T0. Powtarzalność samodzielnego zimnego wariantu
TAP wymaga osobnych przebiegów.

Źródłem jest `klippy(19).log` z archiwum użytkownika. Wiersze 22546–22547
i 22612–22613 rejestrują zatrzymania T0, wiersz 22599 wynik T1,
a wiersze 22844–22884 pionowe pomiary i wynik T0. SHA-256 pliku logu
po rozpakowaniu wynosi `93a4ea083e52ea0c7459155eef9afbfb5ab6d067fe2af8607eb7c8843f890881`.
