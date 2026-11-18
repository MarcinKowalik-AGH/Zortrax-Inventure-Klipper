# Inventure — host R18 / MCU R12: tolerancja temperatury pada ±10°C

W pełnej kalibracji pada temperatura zadana obu dysz wynosi 100°C,
a dopuszczalny zakres wynosi **90–110°C włącznie**. Obowiązuje w hoście
i w MCU. Wersja obejmuje przygotowanie `EMPTY` i `RETRACT`, pomiar
`SWEEP` i `TAP` oraz kolejność T0 → T1 → powtórne T0.

## Przyczyna zatrzymania z klippy(14).log

Host R17 zatrzymał T0 w etapie `X+_TOUCH` przy temperaturze
105,084249°C. Było to przekroczenie starego pasma 95–105°C
o 0,084249°C. MCU pozostawało w trybie SERVICE, bez fault i latch.
Końcowy `operator_stop` był skutkiem automatycznego STOP hosta.

Użytkownik nie wykonał jeszcze strojenia PID głowic i zlecił tolerancję
±10°C. Ta wersja rozszerza ją w obu częściach oprogramowania. Wahania
w tym zakresie nie przerywają procedury.

## Zmiany

- Jeden zakres 90–110°C dla obu dysz podczas całego pomiaru, także
  podczas ruchów Z, weryfikacji styku i przełączania narzędzi.
- Gotowość po 2 sekundach ciągłego przebywania obu dysz w tym paśmie.
  Podczas chłodzenia procedura czeka na wejście w zakres.
- Podczas pomiaru usunięte konkurujące warunki 95–105°C, 98–102°C
  oraz przerwanie powyżej 108°C. Pozostałe procedury zachowują swoje
  wcześniejsze okna temperatury.
- MCU sprawdza granice bezpośrednio na ułamkowej wartości wynikającej
  z ADC. Odczyt 110,08°C nie przechodzi przez obcięcie do 110°C.
  Odczyt diagnostyczny hosta interpretuje tę samą próbkę.
- Host sprawdza pasmo zgłaszane przez MCU przed rozpoczęciem ruchu
  i grzania. Pełna kalibracja R18 wymaga MCU R12.

Tor ruchu i rozdzielczość krawędzi 0,05 mm pozostają takie jak wcześniej.
Gorące oczekiwanie odbywa się nad koszem. Zachowane są maksymalne
nastawy 310°C, timeout utraty komunikacji 5 sekund, kontrola świeżości
ADC i wentylatora oraz globalna polityka drzwi ustawiana przez użytkownika.
Wyjście poza 90–110°C podczas pomiaru zatrzymuje procedurę.

## Sprawdzenie oprogramowania

- 531 testów hosta, w tym pełny T0 → T1 → T0 przy wahaniach blisko
  90°C i 110°C oraz odczycie 105,084249°C z logu drukarki.
- Kontrola wszystkich 4096 kodów ADC względem granic 90–110°C.
- Osiem zestawów testów C z rzeczywistymi źródłami MCU na modelu
  rejestrów. Granice sprawdzane dla obu dysz oraz obu źródeł próbek
  ADC; przekroczenie nadal zatrzymuje ruch i obie grzałki.
- Sprawdzenie uruchamiania konfiguracji przez przypiętą wersję Klippera.
- Kompilacja aplikacji ARM dla InventureBoot 1.7 z kontrolą słownika,
  rozmiaru obrazu i sum SHA256.

Są to testy programowe. Pełny przebieg tej wersji na drukarce wymaga
potwierdzenia. Nie wykonano strojenia PID głowic.

## Aktualizacja

SSH na Orange Pi:

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
python3 "$HOME/inventure-orange-pi/install-orange-pi.py" --configure-nozzles
```

Rozpakuj `INVENTURE_MCU_PAD_100C_R12_BOOT17.zip`, sprawdź zawarte
`SHA256SUMS` i skopiuj `INVENTUR.IVB` do głównego katalogu karty SD FAT32.
Wgraj aplikację przez InventureBoot 1.7. Dotychczasowe MCU R11 nadal
pilnuje starego pasma, dlatego potrzebna jest również ta aktualizacja.

W Fluidd:

```gcode
FIRMWARE_RESTART
INVENTURE_VERSION
```

Sprawdź host `...PAD-XYZ-R18`, MCU `...PAD-THERMAL-R12`,
`nozzle_pad_measurement_band_c: [90, 110]` i `heartbeat_timeout_s: 5`.

## Próba na pustych dyszach

Powtórz tę samą geometrię i limity Z użyte wcześniej na tej drukarce:

```gcode
INVENTURE_CONTROL MODE=SERVICE
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=FULL SCAN=SWEEP PREP=EMPTY ACK=PAD_GEOMETRY_MEASURED T0_PAD_X=81.70 T0_PAD_Y=68.20 T1_PAD_X=70.80 T1_PAD_Y=68.70 T0_Z_MAX=139.50 T1_Z_MAX=137.95 PROCEDURE_TIMEOUT=3600
```

`PREP=EMPTY` służy pustym dyszom i nie wykonuje retrakcji. Dla dysz
z filamentem `PREP=RETRACT` nadal wymaga jawnych temperatur i długości
cofania osobno dla obu materiałów.

Odczyt wyniku:

```gcode
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=STATUS
```

Oczekiwany etap końcowy to `candidate`, z pomiarami `T0`, `T1`
i `T0_RETURN`. Zapis wyniku wymaga osobnego `SAVE`; automatyczne
przeniesienie tego wyniku do offsetów drukowania nadal nie jest wdrożone.
