# Zortrax Inventure — aktualizacje Orange Pi

Gałąź `orange-pi` udostępnia aktualizacje działającego hosta Klippera.
Instalator korzysta z tego samego schematu co projekt M300:
`git pull --ff-only`, następnie `python3 install-orange-pi.py`.

## Pierwsze pobranie na Orange Pi

```bash
git clone --depth 1 --single-branch --no-tags --branch orange-pi https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper.git "$HOME/inventure-orange-pi" && python3 "$HOME/inventure-orange-pi/install-orange-pi.py"
```

## Kolejne aktualizacje

```bash
git -C "$HOME/inventure-orange-pi" pull --ff-only && python3 "$HOME/inventure-orange-pi/install-orange-pi.py"
```

Skrypt wykrywa aktywny katalog `klippy.py` przez działającą usługę, zamiast
zakładać, że jest to `~/klipper`. Weryfikuje sumy modułów, zachowuje ich kopie,
zatrzymuje usługę, instaluje zgodne pliki i uruchamia Klippera ponownie.
Zmienia tylko adapter Inventure, OLED i zgodny moduł transportu UART.
Zachowuje ustawienia `printer.cfg`, w tym bieżące 750000 baud.
Ponowne wykonanie dla już zainstalowanej wersji zachowuje pliki i restartuje
usługę, aby działający Python wczytał aktualny moduł.

Sam odczyt i sprawdzenie planu:

```bash
python3 "$HOME/inventure-orange-pi/install-orange-pi.py" --check
```

Po instalacji w konsoli Klippera:

```gcode
INVENTURE_VERSION
```

Obecny host: **RC8-MOTOR-R1**. Z wcześniejszym MCU termicznym pozostają
odczyty i dotychczasowe testy termiczne. Testy silników wymagają MCU z
`motor_test_abi: 1`. Komenda nie rozpoczyna żadnego ruchu ani grzania.

## Aplikacja MCU

Aktualizacje MCU użytkownik dostaje osobno w czacie jako ZIP z plikiem
`INVENTUR.IVB`, sumami i instrukcją instalacji przez InventureBoot 1.7.
Instalator hosta nie programuje MCU, nie aktualizuje bootloadera i nie
zmienia jego ustawienia UART. Nowy host i aplikacja MOTOR-R1 współpracują
przy 750000 baud; zgodność sprawdza `INVENTURE_VERSION`.

## Weryfikacja

```bash
python3 -m unittest discover -s tests -p test_orange_pi_update.py -v
```

Próby obejmują aktywny katalog RC72, przejście z CHAMBER75-R3, kopie,
rollback po błędzie startu, przerwanie kopiowania, odmowę nadpisania
nieznanych zmian i zachowanie konfiguracji. Główne źródła i wcześniejszy
sposób kompilacji są w gałęzi `main`.
