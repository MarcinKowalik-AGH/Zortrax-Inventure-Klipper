# Orange Pi Zero 3 (H618, aarch64) — Klipper Inventure RC7.1

**To nie jest starszy Orange Pi Zero H2+/H3.** Nie kopiuj `armbianEnv.txt`, numerów GPIO ani ścieżki UART z poprzedniej płytki. Przy Armbianie port UART5 na PH2/PH3 bywa dostępny jako `/dev/ttyS1` po włączeniu `uart5`, lecz zależy to od wersji DT/overlay. Dla konkretnej instalacji rozstrzygające są `/proc/device-tree/model`, `dmesg`, `/dev/tty*`, `armbian-config` i wykonana próba pętli UART. Skrypt nie modyfikuje overlay.

## Przygotowanie bez zmian systemowych

```
python3 zero3/check_zero3.py --printer-cfg "$HOME/printer_data/config/printer.cfg"
python3 zero3/stage_pinned_host.py --destination "$HOME/inventure-rc7_1/klipper-inventure"
```

Ostatnie polecenie jest **dry-run**. Aby stworzyć osobny nowy checkout **bez przełączania usług**, użyj:

```
python3 zero3/stage_pinned_host.py --destination "$HOME/inventure-rc7_1/klipper-inventure" --apply
```

Jeżeli masz już działający sprawdzony checkout RC6/RC7: użyj zamiast staging istniejącego `scripts/install_host.py`, po ręcznym zatrzymaniu usługi `klipper`. Installer wykonuje kontrolę SHA, tworzy backup i można go wycofać. Nigdy nie przepisuj ręcznie/na ślepo wszystkich plików Klippera.

**Pamiętaj o środowisku Pythona ARM64**: bibliotek nie instaluj do systemowego Pythona. Zachowaj istniejące działające `~/klippy-env` albo przygotuj nowy venv zgodnie z wymaganiami przypiętego Klippera; potrzebne m.in. greenlet i pyserial. Skrypt staging nie instaluje ani nie podnosi automatycznie zależności, nie przenosi usług i nie dotyka `printer.cfg`.

## UART

P4 Inventure: PG14=TX drukarki, PG9=RX, wspólna masa, **3,3 V TTL**. W Zero 3 wybierz rzeczywiście zmierzony TX/RX aktywnego portu, bez zakładania numeru Linux ani fizycznego pina na podstawie starszego Orange Pi. Dla bootloadera 1.6-RC2 menu daje 115200/250000/500000/1000000/2000000, `P4-PERSIST-r3` przechowywany w jego 256 B dzienniku. MCU RC7.1 w wariancie standardowym **odczytuje ostatni poprawny baud z dziennika**, natomiast Orange Pi potrzebuje tej samej jawnej wartości `baud` w istniejącej sekcji `[mcu]`. Nie ma autonegocjacji portu po stronie hosta.

Najpierw potwierdź UART i ekran przy **250000** z wyłączonym grzaniem i bez ruchu. 1M i 2M wdrażaj dopiero na podstawie licznika retransmisji/overrun, jittera, `INVENTURE_VERSION`, OLED ACK i stabilności portu H618.

## STEP 2 µs

`step_pulse_duration: 0.000002` należy umieścić **w każdej istniejącej sekcji silnika** `[stepper_x]`, `[stepper_y]`, `[stepper_z]`, `[extruder]` (gdy jest skonfigurowana). Nie dodawaj duplikatów sekcji i nie kopiuj pinów/`rotation_distance` M300. Sprawdzenie tego wykonuje `check_zero3.py`. 2 µs nie jest gwarancją fizycznej kompatybilności sterownika; przed ruchem potrzebny pomiar i kwalifikacja.

## Fluidd

Skopiuj tylko `fluidd/inventure_01_diagnostyka.cfg` do folderu konfiguracji i dodaj jednokrotnie `[include inventure_01_diagnostyka.cfg]` do `printer.cfg`, **po zgodnej instalacji modułów hosta RC7.1**. Kategoria w interfejsie Fluidd jest ustawieniem samego Fluidd — nazwy prefiksów ułatwiają grupowanie, ale samo `printer.cfg` nie tworzy kategorii UI. Wersja `inventure_02_procedury_TEST_OPTIN.cfg` wykonuje rzeczywiste G28, T0/T1 i procedury P09/P10/P13/P17, dlatego **nie** dołączaj jej do czasu zakończenia kwalifikacji sprzętowej odpowiednich modułów.

Zrób backup `printer.cfg`, `moonraker.conf`, instalacji hosta i obrazu APP; nie przywracaj stockowego `RESTORE.BIN` z instalatora BOOT, jeśli chcesz zachować Klippera. `RESTORE.BIN` zawiera fabryczną APP 1.6.1, a nie RC6/RC7.

## Automatyczna, opcjonalna korekta 2 µs

Dla prawdziwego CoreXY V9: `python3 zero3/set_step_2us.py ~/printer_data/config/printer.cfg` pokazuje plan bez zapisu. Po upewnieniu się, że `[printer]` i cztery sekcje napędów są w tym pliku oraz po zatrzymaniu Klippera można wykonać `python3 zero3/set_step_2us.py ~/printer_data/config/printer.cfg --apply --confirm-inventure-v9`. Narzędzie zachowuje oryginalną konfigurację w osobnym pliku backup, sprawdza odczyt, odmawia na nieznanych/zdublowanych sekcjach i nie dotyka żadnego innego parametru. Nie wykonuje ruchu ani restartu.
