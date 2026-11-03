# Inventure RC7.5 — ekran startowy Klipper / menu M300 + etapowe UART P4

**3 listopada 2026** | HOST Orange Pi Zero 3 H618 | aplikacja MCU pozostaje `P01-P17-RC7.1-ZERO3` (GitHub GNU ARM RC7.2/RC7.4) | InventureBoot 1.6-RC2 `P4-PERSIST-r3`.

## Zdiagnozowano na podstawie przesłanych logów i printer.cfg

- Aktualny `printer.cfg` zawiera `oem_start_screen: klipper`, `chunk_interval: 0.002`, `refresh_interval: 0.2`, `baud: 250000`. To nie jest problem z brakującą opcją ekranu.
- Host RC7.4 wywołuje **bezpośrednio OEMMenu(root)** po OK. M300 Classic ma osobny launcher: `Klipper`, `Zortrax M300`, `Service / Tests`. W RC7.5 Inventure ma odpowiednik.
- W logu po `FIRMWARE_RESTART` występowały powtarzane `No communication with MCU` przy 250000, a późniejsza sesja była stabilna (0 nowych retransmisji w podanym oknie). Najpierw rozstrzygnij stan `Ready` i status ekranu, nie próbuj losowo zmieniać UART.
- Gdy MCU jest jeszcze w bootloaderze lub czeka na hosta, ekran lokalny **nie może** wyświetlić hostowego statusu Klipper. Docelowy status pojawia się po `klippy:ready` i potwierdzonym odświeżeniu OLED. `INVENTURE_OLED_STATUS` potwierdza transmisję SPI, a nie bezpośrednio świecenie pikseli.

## Nowy układ menu

Po uzyskaniu stanu `Ready`, ekran zaczyna od hostowego statusu **KLIPPER**, pokazującego wersję MCU i bieżący P4; nie jest to jeszcze natywny szablon Klipper `_default_16x4` z temperaturami grzałek, ponieważ obecna diagnostyczna konfiguracja ma `kinematics: none`, bez skonfigurowanych grzałek.

Po OK:

1. `Powrót` — status Klipper
2. `Klipper` — status drukarki, job, temperatury, kontrola, ustawienia, informacje (w trybie diagnostycznym bez aktorów)
3. `Zortrax Inventure` — dotychczasowe pięć gałęzi OEM r0323
4. `Serwis / Testy` — odczyt stanu MCU, OLED, P4, wentylatorów, ADC, RFID

Po podmenu `Powrót` wraca do nadrzędnego launchera, a z launchera na status. Lista nie zawija się, pięć wierszy zajmuje pełne 256x64 pikseli. Brak operacji grzania, ruchu, zapisu NVM, SD lub RFID w menu UI-only. Rzeczywiste procedury są osobnym etapem projektu P01-P17.

Host RC7.5 dodatkowo chroni ACK-pump przed rekurencyjnym wejściem, gdy ACK jest dostarczany synchronicznie; poprawka nie zwiększa liczby komend OLED równolegle oczekujących na ACK (nadal 1 przy ABI2 i 32 B).

### Instalacja z GitHub (tylko host, na początek nadal 250000)

```bash
cd ~/inventure-rc72-repo
git pull --ff-only
git rev-parse --short HEAD
python3 install_rc75_oled.py --klipper ~/klipper --printer-cfg ~/printer_data/config/printer.cfg
# TYLKO JEŚLI raport DRY_RUN nie zawiera REFUSED:
sudo systemctl stop klipper
python3 install_rc75_oled.py --klipper ~/klipper --printer-cfg ~/printer_data/config/printer.cfg --apply --confirm-service-stopped
sudo systemctl start klipper
```

Instalator sprawdza SHA-256 starego i nowego modułu, robi backup dwóch plików oraz `printer.cfg`, ustawia `menu_profile: m300` i zachowuje `oem_start_screen: klipper`. W razie niezgodności przerywa bez zapisu. `BACKUP` z komunikatu potrzebny jest do ewentualnego wycofania wersji.

W Fluidd przy `Ready` wywołaj:

```gcode
INVENTURE_MENU_STATUS
INVENTURE_OLED_STATUS
INVENTURE_VERSION
INVENTURE_STATUS
```

Oczekuj `menu_profile=m300`, `root_page=launcher`, `display_mode=status`, `rendered_screen=klipper_status`, `host_frame_acknowledged=true`, `baud_match=true` oraz `p4_boot.revision=3` (jeśli nadal Boot 1.6-RC2). Bez hardware nie twierdzimy, że realne piksele OLED są poprawne.

**Jeśli nie ma stanu Ready**: nie ponawiaj wielokrotnie `FIRMWARE_RESTART`; w podanym logu po tej komendzie połączenie MCU bywało utracone. Wykonaj kontrolę portu, P4 i rozważ fizyczne zimne uruchomienie STM32 przy zatrzymanej usłudze, następnie `sudo systemctl start klipper`. Nie przechodź do 1M/2M, jeśli stabilność przy 250k nie została potwierdzona.

### Etap 1 — 250000 -> 1000000 (tylko po stabilnym UI/MCU)

Dokumentacja właściciela bootloadera 1.6-RC2 identyfikuje **Diagnostics → UART → P4 baud rate**, wartości 115200, 250000, 500000, 1000000 i 2000000. **UART on bootwait = OFF**. Aplikacja RC7.1 z `INVENTURE_P4_FORCED_BAUD=0` czyta ostatnią poprawną prędkość z dziennika Flash `0x08007F00`, więc NIE ma autonegocjacji. Orange Pi Zero 3: `/dev/ttyS1` na `overlays=uart5`, **3,3 V TTL**.

1. W Fluidd zapisz bazowy stan `INVENTURE_STATUS`, `INVENTURE_VERSION` i log z `bytes_retransmit`, `bytes_invalid`.
2. **Na Orange Pi** zatrzymaj hosta: `sudo systemctl stop klipper`.
3. Zrób staging hosta (kod NIE dotyka MCU/BOOT):

```bash
cd ~/inventure-rc72-repo
python3 scripts/inventure_p4_baud_stage.py --printer-cfg ~/printer_data/config/printer.cfg --baud 1000000
python3 scripts/inventure_p4_baud_stage.py --printer-cfg ~/printer_data/config/printer.cfg --baud 1000000 --apply --confirm-klipper-stopped
```

4. **Przy drukarce** uruchom bootloader fizycznie, zatrzymując 5-sekundowy autostart przyciskiem. Wybierz `Diagnostics → UART → P4 baud rate → 1000000` i zatwierdź zapis. Pozostaw `UART on bootwait → OFF`. Dziennik ma 256 B, a odczyt przy zapisie jest weryfikowany przez bootloader.
5. Uruchom aplikację z menu albo wykonaj pełny restart zasilania STM32 (bez restartu konfiguracji całego Orange Pi). Dopiero po wyborze 1000000 po stronie BOOT i uruchomieniu MCU włącz hosta: `sudo systemctl start klipper`.
6. W Fluidd oczekuj `Ready`, uruchom `INVENTURE_VERSION`, `INVENTURE_STATUS`, `INVENTURE_OLED_STATUS` i testuj bez grzania oraz ruchu. `p4_boot.baud=1000000`, `host_baud=1000000`, `baud_match=true`, `p4_boot.revision=3`.
7. Porównaj przyrosty `bytes_retransmit`, `bytes_invalid`, OLED `ignored_acks`, `recovery_attempts`, `uart_buffer_stats.dropped` i fizyczną reakcję na UP/DOWN/OK. Przy błędach wróć do 250000.

**Rollback hosta**: zatrzymaj Klippera, ustaw w bootloaderze znów 250000, uruchom MCU. Następnie użyj ścieżki `BACKUP:` z etapu stagingu:

```bash
python3 ~/inventure-rc72-repo/scripts/inventure_p4_baud_stage.py --printer-cfg ~/printer_data/config/printer.cfg --rollback 'TUTAJ_DOKLADNA_SCIEZKA_BACKUP' --apply --confirm-klipper-stopped
sudo systemctl start klipper
```

Jeśli nie masz zapisanego backupu, zmień ręcznie tylko `baud: 250000` w `[mcu]` przy zatrzymanym Klipperze i po przywróceniu 250000 w BOOT. Bootloader ma także procedurę awaryjnego 250000 przez `UP+DOWN` przy starcie, opisaną w dokumentacji RC2; nie zakładamy, że samo jej wywołanie zastępuje trwałą zmianę ustawień.

### Etap 2 — 1000000 -> 2000000

Dopiero po stabilnym teście 1M (kilka minut sesji `Ready`, kilkanaście zmian menu, brak narastających retransmisji i bez powtarzających się restartów) ponów procedurę z `--baud 2000000` i ustaw identyczne `2000000` w BOOT. **Nie wykonuj 250000 -> 2000000 bez testu 1M.** APB2 aplikacji STM32F207 to 60 MHz; 2M jest obsługiwane odczytem dziennika r3. BOOT używa innego zegara i konfiguracji oversamplingu; nie kopiuj UART parametrów M300.

**Uwaga:** wzrost UART z 250k do 1M i 2M nie daje automatycznie odpowiednio cztery lub osiem razy szybszego menu. ABI2 ma 32-bajtowe operacje, ACK i ograniczenia renderowania, a fizycznie długie przewody 3,3 V mogą pogorszyć niezawodność. Nie zwiększaj `spi_speed` powyżej zatwierdzonego 2 MHz w trakcie testu P4.

### Zgodność i ograniczenia

- Inventure V9 STM32F207 + Orange Pi Zero 3 H618, **nie M300 MCU**.
- Aplikacja MCU GNU ARM RC7.1 już wgrana: bez ponownego firmware i bez zmiany InventureBoot.
- Brak przełączania 1M/2M przez samo `FIRMWARE_RESTART` lub ustawienie tylko hosta.
- Brak kwalifikacji sprzętowej renderowania, homingu, termiki i napędów. `kinematics: none` i `allow_actuators: False` bez zmian.
