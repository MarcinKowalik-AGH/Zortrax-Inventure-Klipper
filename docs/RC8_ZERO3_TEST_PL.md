# Test Klippera na Orange Pi Zero 3

Pakiet `INVENTURE_RC8_0_ORANGE_PI_ZERO3_HOST.zip` zawiera przypięte źródła
Klippera z hostem RC7.6, konfigurację `printer.test.cfg` i instalator
nakładek z kontrolą SHA-256. Pasuje do aplikacji MCU `P01-P17-RC8.0-LOCAL`.
Dla pierwszego uruchomienia testowego używa portu `/dev/ttyS1`, 250000 baud,
`kinematics: none` i `allow_actuators: False`.

## Test w osobnym katalogu

Rozpakuj ZIP i przejdź do jego katalogu `HOST`. Uruchom jako użytkownik
`klipper`, z istniejącym środowiskiem `~/klippy-env` na Armbian ARM64.

```sh
python3 zero3/check_zero3.py --printer-cfg "$PWD/printer.test.cfg"
stage="$HOME/inventure-rc8-test"
mkdir "$stage"
tar -xzf klipper-inventure.tar.gz -C "$stage"
cp printer.test.cfg "$stage/"
```

Po wgraniu aplikacji RC8.0 z paczki SD, zweryfikowaniu jej w InventureBoot
i potwierdzeniu 250000 baud w ustawieniach P4, zatrzymaj usługę, aby zwolnić
UART. Uruchom test ręcznie w terminalu.

```sh
sudo systemctl stop klipper
cd "$stage"
"$HOME/klippy-env/bin/python" "$stage/klipper-inventure/klippy/klippy.py" \
    "$stage/printer.test.cfg" -l "$stage/klippy.log" \
    -a "$stage/klippy.sock" -I "$stage/printer"
```

Obserwuj OLED i log. Najpierw MCU pokazuje `Waiting for Klipper`.
Po `Ready` pierwszą klatką hosta jest graficzny ekran Klippera `_default_16x4`,
jak w M300. Pokazuje ikony temperatur i wentylatora, feed rate, postęp i czas.
W trybie diagnostycznym wyświetla świeży odczyt T0. Dla brakujących pomiarów
pokazuje `--`. OK otwiera angielskie menu `Klipper / Zortrax Inventure / Service / Tests`. Wybierz `Zortrax Inventure`
i sprawdź pięć sekcji OEM. Zatrzymaj test przez Ctrl-C i sprawdź powrót
lokalnego statusu MCU. Test zachowuje bieżące dowiązanie `~/klipper`,
`printer.cfg`, usługę systemd i konfigurację Moonrakera. Przed powrotem do
wcześniejszego zestawu sprawdź zgodność jego aplikacji MCU i hosta.

## Aktualizacja istniejącego hosta

Do współpracy z istniejącym Fluidd użyj `scripts/install_host.py ~/klipper`
z tego katalogu, najpierw w trybie podglądu. Kroki instalacji i rollbacku
są w dołączonej instrukcji `MCU_RC8_LOCAL_SERVICES_PL.md`.
W istniejącej sekcji `[inventure_oled]` użyj opcji z
`OLED_FAST_RC7_5.cfg`. Zachowaj pojedynczą sekcję i istniejące `[mcu]`.

Po Ready polecenia `INVENTURE_MENU_STATUS` i `INVENTURE_OLED_STATUS`
powinny pokazać `rendered_screen=klipper_status`, `menu_profile=m300`
oraz `language=en` i `ui_transport.status_layout=_default_16x4`. Po
przesłaniu klatki pojawi się `host_frame_acknowledged=true`. Z kolei
`INVENTURE_COOLING_STATUS` odczytuje lokalny stan chłodzenia MCU.

Odświeżanie statusu ma okres 0,2 s. Kolejne porcje OLED wysyłane są po
prawidłowym ACK, przy pojedynczej operacji ABI2 w locie. 2 ms to krok
timera podczas transferu, a przewijanie wysyła zmienione obszary.
Rzeczywisty czas pokazania pełnego obrazu zależy od UART, SPI i panelu.

Jeśli host RC7.5 współpracuje już z MCU RC8.0, zaktualizuj klon GitHub
i uruchom `install_rc75_oled.py` w trybie podglądu. Po zatrzymaniu Klippera
uruchom go z `--apply --confirm-service-stopped`. Instalator sprawdza SHA
czterech modułów i ustawia `oem_language: en` w istniejącej sekcji OLED.
