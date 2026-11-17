# Zortrax Inventure R14 / MCU R10

Wydanie ustawia limit braku heartbeat na 5 sekund w SERVICE i NATIVE.
MCU wyłącza wtedy grzałki i prowadzi lokalne chłodzenie. `INVENTURE_VERSION`
raportuje `heartbeat_timeout_s: 5` z parametrów aplikacji MCU.

T0 i T1 przyjmują cele do 310°C w SERVICE i natywnym Klipperze. Odczyt
310°C uruchamia odcięcie temperaturowe MCU. Nastawa na samej granicy może
zakończyć się odcięciem podczas dojścia do celu.

Pomiar XYZ pada przebiega przy 100°C, w paśmie 95–105°C dla obu dysz.
Wycofanie filamentu korzysta z indywidualnych temperatur materiałów,
a głowica czeka na nagrzanie i schłodzenie nad koszem. Przedział
potwierdzenia krawędzi w SWEEP wynosi do 0,05 mm. Drzwi korzystają ze
wspólnego ustawienia użytkownika.

1. Na Orange Pi wykonaj przez SSH poniższe polecenia. Instalator skonfiguruje
   obie grzałki, limity 310°C i powiązania wentylatorów z T0 oraz T1.

   ```bash
   git -C "$HOME/inventure-orange-pi" pull --ff-only origin orange-pi-all-axis &&
   python3 "$HOME/inventure-orange-pi/install-orange-pi.py" --configure-nozzles
   ```

2. Pobierz `INVENTURE_MCU_PAD_100C_R10_BOOT17.zip` z tego wydania, rozpakuj
   i sprawdź `SHA256SUMS`. Skopiuj `INVENTUR.IVB` do głównego katalogu karty
   FAT32 i wgraj aplikację przez InventureBoot 1.7.

3. W konsoli Fluidd wykonaj `FIRMWARE_RESTART`, a po połączeniu
   `INVENTURE_VERSION`. Oczekiwane wartości są podane w tabeli.

| Pole | Wartość |
|---|---|
| `host_revision` | `RC8-CONTROL-R7-UIFAST-PAD-XYZ-R14` |
| `mcu_release` | `RC8-CONTROL-R7-PAD-THERMAL-R10` |
| `heartbeat_timeout_s` | `5` |
| `nozzle_service_target_max_c` | `310` |
| `native_nozzle_max_temp_c` | `T0` i `T1` po `310` |
| `nozzle_pad_measurement_band_c` | `[95, 105]` |
| `baud_match` | `true` |

Przy dwóch pustych dyszach rozpocznij pełny pomiar na wcześniej sprawdzonym
padzie. Poniższe pozycje i limity Z pochodzą z prób tego egzemplarza.

```gcode
INVENTURE_CONTROL MODE=SERVICE
ZORTRAX_NOZZLE_PAD_CALIBRATE ACTION=START MODE=FULL SCAN=SWEEP PREP=EMPTY ACK=PAD_GEOMETRY_MEASURED T0_PAD_X=81.70 T0_PAD_Y=68.20 T1_PAD_X=70.80 T1_PAD_Y=68.70 T0_Z_MAX=139.50 T1_Z_MAX=137.95 PROCEDURE_TIMEOUT=3600
```

Przy załadowanych dyszach wybierz `PREP=RETRACT` i podaj indywidualne
`T0_TARGET`, `T1_TARGET`, `T0_RETRACT_MM`, `T1_RETRACT_MM`.

Załączony `INVENTURE_ORANGE_PI_PAD_XYZ_R14.zip` zawiera kompletny host
z tego samego commita. Używana wcześniej kopia Git aktualizuje się
poleceniami podanymi powyżej.

Wydanie przechodzi testy hosta, testy MCU na modelu rejestrów i walidację
skompilowanej aplikacji. Pełny przebieg R14/R10 wymaga sprawdzenia na drukarce.
