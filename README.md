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

Obecny host: **RC8-MOTOR-R4-XY-REPEAT**. Z wcześniejszym MCU termicznym pozostają
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
zmienia jego ustawienia UART. Nowy host i aplikacja MOTOR-R4 współpracują
przy 750000 baud; zgodność sprawdza `INVENTURE_VERSION`.

## Dojazd do krańcówek XY — MOTOR-R3

Po aktualizacji MCU `INVENTURE_VERSION` powinno pokazać `xy_seek_abi: 1`.
Włączona polityka drzwi nadal wymaga zamkniętych osłon; dla obserwacji użyj
istniejącej opcji Door lock lub `INVENTURE_DOOR_POLICY ENABLE=0`.

Najpierw pojedynczy dojazd X:

```gcode
INVENTURE_XY_SEEK AXIS=X ACTION=START
```

Po zatrzymaniu i raporcie `phase: hit`, `reason: endstop_hit` wykonaj Y:

```gcode
INVENTURE_XY_SEEK AXIS=Y ACTION=START
```

Oba STEP są zsynchronizowane zgodnie z homingiem CoreXY OEM. X monitoruje
PB11, Y monitoruje PC15. Na pierwszy aktywny poziom wejścia MCU wyłącza
oba STEP i wspólne ENABLE. Nie ma automatycznego cofania ani drugiego najazdu.
Krańcówka X może pozostać aktywna podczas późniejszego dojazdu Y.
Nowe zadziałanie drugiej krańcówki przerywa przejazd jako błąd.

Rampa od 400 do maksymalnie 3200 par impulsów/s daje nominalnie do 5 mm/s
przy skali OEM 640 kroków/mm. MCU ogranicza przejazd do 102400 par impulsów
(160 mm według skali OEM) oraz 40 s. Brak krańcówki kończy się błędem,
nie stanem sukcesu. Utrata heartbeat, shutdown lub STOP również przerywają ruch.

```gcode
INVENTURE_XY_SEEK_STOP
INVENTURE_XY_SEEK ACTION=STATUS
```

`INVENTURE_MOTOR_TEST_STOP` także przerywa dojazd XY. Startu nie ponawia się
automatycznie. Test nie ustawia pozycji Klippera ani flagi bazowania.
Użytkownik określił robocze zero z przodu po lewej; pomiar dotknięcia
krańcówki potwierdza mechaniczny punkt odniesienia. Offset roboczego zera,
skala rzeczywista i pełny zakres osi pozostają do kalibracji.
Źródło limitu 160 mm i kierunków:
[audyt dojazdu XY](docs/XY_SEEK_OEM_EVIDENCE.md).

## Odjazd 2 mm i wolny ponowny najazd — MOTOR-R4

R3 został potwierdzony na drukarce: oba dojazdy zakończyły się na właściwych
krańcówkach i zwolniły napędy. R4 zachowuje tę komendę oraz dodaje ograniczoną
procedurę z `xy_repeat_abi: 1`:

```gcode
INVENTURE_DOOR_POLICY ENABLE=0
INVENTURE_XY_REPEAT AXIS=X ACTION=START
```

Jedna komenda wykonuje pierwszy dojazd, odjazd 1280 par impulsów (nominalnie
2 mm), potwierdza co najmniej 5 ms zwolnienia krańcówki i wraca do niej
z limitem 640 par impulsów/s (nominalnie 1 mm/s). Napędy pozostają włączone
między etapami. Jeśli wejście jest aktywne już na starcie, pierwszy dojazd
jest pomijany i rozpoczyna się ograniczony odjazd.

Po zakończeniu X oczekiwane są `phase: hit`, `reason: endstop_hit`,
`stage: slow_approach`, `switch_release_confirmed: true`, `backoff_steps: 1280`
i `driver_enabled: false`. Wtedy wykonaj osobno Y:

```gcode
INVENTURE_XY_REPEAT AXIS=Y ACTION=START
```

Odczyt oraz STOP:

```gcode
INVENTURE_XY_REPEAT ACTION=STATUS
INVENTURE_XY_REPEAT_STOP
```

Dotychczasowe `INVENTURE_XY_SEEK_STOP` i `INVENTURE_MOTOR_TEST_STOP` też
zatrzymują tę procedurę. Po końcowym kontakcie napędy są zwalniane.
Całość ma limit 40 s. Pierwszy najazd zachowuje limit R3 102400 par impulsów;
odjazd ma dokładnie 1280, a wolny najazd maksymalnie 2560 (nominalnie 4 mm).
Brak zwolnienia wejścia, zbyt wczesny kontakt, brak ponownego kontaktu,
nowe zadziałanie innej krańcówki, utrata heartbeat oraz shutdown przerywają próbę.

R4 nie ustawia pozycji ani flagi homed w Klipperze. Podane milimetry nadal
korzystają ze skali OEM 640 kroków/mm; fizyczną skalę i offset zera potwierdza
pomiar. Dopiero końcowe raporty kolejnych prób pozwolą ocenić powtarzalność.

## Weryfikacja

```bash
python3 -m unittest discover -s tests -p 'test_orange_pi*.py' -v
```

Próby obejmują aktywny katalog RC72, przejście z CHAMBER75-R3, kopie,
rollback po błędzie startu, przerwanie kopiowania, odmowę nadpisania
nieznanych zmian, przejście adaptera i OLED z MOTOR-R1, ustawienie polityki
drzwi i zachowanie konfiguracji. Główne źródła i wcześniejszy
sposób kompilacji są w gałęzi `main`.
