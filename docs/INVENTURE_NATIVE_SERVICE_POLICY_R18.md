# Inventure R18 — rozdzielenie zabezpieczeń SERVICE i NATIVE

Poprawka zachowuje Home R38.6, wybór T0, czyszczenie, pięciopunktowe poziomowanie
i OLED. Naprawia użycie progów procedur serwisowych w normalnym Klipperze.

| Reguła | SERVICE (rygorystyczne) | NATIVE (normalne działanie) |
|---|---|---|
| Maska / limity czujników | Pełne istniejące `control_guard_mask`, okna pomiarowe i odświeżanie 1 s | Nie są przenoszone do zwykłego X/Y/Z; niezwiązany, nieaktywny czujnik nie zatrzymuje bazowania |
| Maksimum bezwzględne | Zachowane | T0/T1 310°C, komora 150°C, PSU/płyta 100°C, PCB głowicy 190°C |
| Aktywna lub niedawno gorąca grzałka | Pełna kontrola serwisowa | Czujnik wymagany, również po wyłączeniu PWM aż do schłodzenia |
| PSU i płyta | Pełna kontrola serwisowa | Czujniki wymagane podczas grzania, nie blokują zimnego ruchu w razie utraty odczytu |
| Z: temperatury T0 | Serwisowe pomiary i zakresy pada bez zmian | Cel −20°C / +15°C zamiast −10°C / +8°C |
| PC13 platforma, PC14 styk, PD11 kabel, heartbeat | Obowiązkowe | Nadal obowiązkowe; utrata PC13 podczas ruchu Z musi zatrzymać STEP |
| Wentylatory i przegrzanie | Dotychczasowe reguły | Dotychczasowy watchdog tach 10 s i granice sprzętowe |
| NATIVE → SERVICE | Przejście tylko po zwolnieniu napędów i grzałek | Mechanizm R17 pozostaje |

MCU R18 podaje odrębny tekst przyczyny awarii w `klippy.log` zamiast
wspólnego `Inventure V9 local safety fault`: kabel, heartbeat, sensor
temperatury, wentylator, drzwi, PC13 oraz błąd lokalnego pomiaru Z.
Pierwszy zapis przyczyny pozostaje w pamięci RAM do odczytu komendą
`inventure_fault_detail_query` w stanie shutdown (znika po resecie MCU).
Zapisuje tryb, sygnały PC13/PC14/PE12/PD11, stan ADC i grzałek.

Ta gałąź jest kandydatem, wymaga walidacji kodu, obrazu IVB i testów na
drukarce. Nie zmienia automatycznie systemu na Orange Pi ani flash STM32.
