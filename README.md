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

Obecny host: **RC8-MOTOR-R2-DOOR-POLICY**. Z wcześniejszym MCU termicznym pozostają
odczyty i dotychczasowe testy termiczne. Testy silników wymagają MCU z
`motor_test_abi: 1` lub `2`. Nowe ustawienie drzwi wymaga `motor_test_abi: 2`
i `door_policy_abi: 1`. `INVENTURE_VERSION` nie rozpoczyna ruchu ani grzania.

## Fabryczna opcja Door lock — testy napędów

MOTOR-R2 uwzględnia odnalezioną w audycie r0323 opcję **Settings → Door lock**.
Ustawienie dotyczy jednocześnie przednich drzwi PB10 i górnej klapy PE15.
Po aktualizacji hosta i MCU można użyć menu **Zortrax Inventure → Settings →
Door lock → Disable door lock** albo konsoli:

```gcode
INVENTURE_DOOR_POLICY ENABLE=0
INVENTURE_MOTOR_TEST MOTOR=A ACTION=START
```

Po wyłączeniu kontroli można obserwować krótki ruch przy otwartych osłonach.
Trzymaj ręce poza obszarem ruchu. Odczyty obu czujników zachowują rzeczywisty
stan; opcja nie zmienia kontroli krańcówek, STOP, limitu impulsów ani heartbeat.
W tej diagnostycznej aplikacji cewki zamków pozostają zwolnione w obu
ustawieniach. `ENABLE=1` przywraca wymóg zamknięcia osłon dla testu.

```gcode
INVENTURE_DOOR_POLICY
INVENTURE_DOOR_POLICY ENABLE=1
```

Zmiana jest przyjmowana między testami. Restart MCU lub jego shutdown
przywraca `ENABLE=1`. Ustawienie nie zapisuje pamięci oryginalnego firmware.
Przeniesiony warunek to odpowiednik bitu systemowego 3 OEM; dokładne adresy
i zakres potwierdzenia są opisane w [dokumencie audytu](docs/OEM_DOOR_POLICY.md).
To wydanie obsługuje ograniczone testy napędów; pełny wydruk i bazowanie nadal
nie są aktywowane przez tę opcję.

## Aplikacja MCU

Aktualizacje MCU użytkownik dostaje osobno w czacie jako ZIP z plikiem
`INVENTUR.IVB`, sumami i instrukcją instalacji przez InventureBoot 1.7.
Instalator hosta nie programuje MCU, nie aktualizuje bootloadera i nie
zmienia jego ustawienia UART. Nowy host i aplikacja MOTOR-R2 współpracują
przy 750000 baud; zgodność sprawdza `INVENTURE_VERSION`.

## Weryfikacja

```bash
python3 -m unittest discover -s tests -p 'test_orange_pi*.py' -v
```

Próby obejmują aktywny katalog RC72, przejście z CHAMBER75-R3, kopie,
rollback po błędzie startu, przerwanie kopiowania, odmowę nadpisania
nieznanych zmian, przejście adaptera i OLED z MOTOR-R1, ustawienie polityki
drzwi i zachowanie konfiguracji. Główne źródła i wcześniejszy
sposób kompilacji są w gałęzi `main`.
