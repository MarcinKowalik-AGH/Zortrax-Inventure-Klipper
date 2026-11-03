# INVENTURE RC7.4 OLED ACK-FAST — pakiet host-only

Data 2026-11-03. Wersja firmware STM32F207 pozostaje RC7.1 GNU ARM z GitHub Actions RC7.2.

W logu operatora z 03.11.2026 nadal widnieje `chunk_interval=0.020`, `refresh_interval=0.5`,
`oem_start_screen` nie występuje, a pełne odświeżenie 8192 B / 301 ACK zajmuje ok. 7,14 s.
Poprzednia aktualizacja RC7.3 nie została więc aktywowana w tej konfiguracji.

RC7.4 integruje RC7.3 (pełny ekran 5 wierszy, bez tytułu Zortrax, domyślnie status KLIPPER,
Back, glify r0323) i dodaje wzorowane na M300 natychmiastowe wznowienie pojedynczej
operacji OLED po zatwierdzonym ACK w callbacku reactora. Nie jest to M300 FAST DMA:
Inventure RC7.1 ma OLED ABI2, tylko 1 in-flight i max 32 bajty, a M300 FAST DMA używa
oddzielnego ABI i okna do czterech operacji z kompresją danych.

Wymagane: Orange Pi Zero 3, istniejący Klipper RC7.2 lub RC7.3,
`/dev/ttyS1` i dotychczasowy baud 250000.

Instalacja:
```bash
cd ~/inventure-rc74-oled
sha256sum -c SHA256SUMS
python3 install_rc74_oled.py --klipper ~/klipper --printer-cfg ~/printer_data/config/printer.cfg
# jeśli DRY RUN poprawny:
sudo systemctl stop klipper
python3 install_rc74_oled.py --klipper ~/klipper --printer-cfg ~/printer_data/config/printer.cfg --apply --confirm-service-stopped
sudo systemctl start klipper
```

Test: `INVENTURE_OLED_STATUS`, `INVENTURE_MENU_STATUS`, `INVENTURE_INPUTS`.
Sprawdź rzeczywistą szybkość i liczniki retransmisji; wyników offline nie utożsamiaj z hardware PASS.
NIE wykonuj G28, grzania, ani zmiany baud w ramach tej poprawki.
