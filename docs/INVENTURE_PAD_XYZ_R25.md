# Inventure R25 — pionowy pomiar środka zimną dyszą

R25 umożliwia wywołanie `ACTION=CENTER SCAN=TAP` dla T0 i T1.
R24 przyjmował parametr `SCAN` w poleceniu `CENTER`, lecz zawsze rozpoczynał
od przejazdu ślizgowego `SWEEP`. Dotychczasowe polecenia bez wyboru metody
nadal rozpoczynają od `SWEEP`.

## Co zarejestrowała drukarka na R24

19 listopada 2026 pomiar zimną T0 rozpoczął się o 12:42:34.
Pionowe dotknięcia potwierdziły X− = 77,9109 i X+ = 85,4891 mm.
O 12:46:44 MCU zatrzymał sesję przy szukaniu Y+ z kodem 5
(`motion_or_interlock_guard`). T0 miała 35,46°C, T1 miała 35,30°C,
a licznik utraconych danych UART wynosił zero. W rekordzie brakowało
stanu ostatniego ruchu, więc nie da się wskazać konkretnego wejścia lub
warunku ruchu. W osobnej próbie T1 wyznaczyła obie osie, korzystając
z pionowych dotknięć. Konsola podała X = 70,8 i Y = 68,8 mm.

## Jak zmierzyć T0

Zainstaluj host z tagu `inventure-pad-xyz-r25` po zakończeniu bieżącego
ruchu. Drukarka z MCU `RC8-CONTROL-R7-PAD-THERMAL-R13` potrzebuje
tylko aktualizacji na Orange Pi. Po zgłoszeniu gotowości Klippera
sprawdź `INVENTURE_VERSION`. Na tej drukarce, z obiema dyszami poniżej
50°C, użyj w konsoli Klippera następującego polecenia.

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=CENTER TOOL=T0 SCAN=TAP T0_PAD_X=81.70 T0_PAD_Y=68.20 T0_Z_MAX=139.50 Z_CLEARANCE_MM=1 EDGE_TOLERANCE_MM=0.1
```

Procedura korzysta z tego samego pionowego pomiaru, który w R24 wyznaczył
obie krawędzie X dla T0. Przy każdej osi zaczyna od dwóch potwierdzonych
dotknięć wewnątrz pola. Przy pomiarze jego brzegów dojazd Z ogranicza
zarówno podany limit dyszy, jak i wysokość potwierdzonego styku powiększona
o najwyżej 0,05 mm. Stół cofa się po każdym dotknięciu. Pomiar kończy
się jednowierszowym wynikiem XY. Zapis offsetu XYZ jest osobną operacją
po pełnym przebiegu obu głowic.

Przy kodzie 5 host zapisuje oczekującą czynność. Gdy brakuje stanu ruchu
związanego z błędem, host odczytuje ostatni stan MCU po zatrzymaniu
w polu `last_motion_after_stop`. Może on dotyczyć poprzedniego ruchu.

Testy z modelem obu dysz obejmują pionowe pomiary czterech krawędzi,
brak ruchu ślizgowego, limity Z i zatrzymanie po kodzie 5 podczas Y+.
Potwierdzenie R25 na fizycznej T0 wymaga ukończenia powyższego pomiaru.
