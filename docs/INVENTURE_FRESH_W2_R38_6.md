# Inventure R38.6 na nowym Orange Pi Zero 2W

Ta ścieżka przenosi host ze starszej instalacji diagnostycznej RC7/RC8 do
kompletnego zestawu hosta i konfiguracji potrzebnego dla Home R38.6.
Przykład dotyczy użytkownika `klipper`, UART `/dev/ttyS5` i prędkości
`750000`, zgodnej z ustawieniem działającego MCU.

| Element | Wymagana wersja lub ścieżka |
|---|---|
| Źródła hosta R38.6 | `ee9cd2f3dc2c1f71d930b0b6825ed08233b72a2f` |
| Wersja hosta w `INVENTURE_VERSION` | `RC8-CONTROL-R7-HOMING-R38.6` |
| Wersja MCU dla Home R38.6 | `RC8-CONTROL-R7-HOMING-R16` |
| Bootloader dla pakietu aplikacji | istniejący InventureBoot 1.7 |
| Docelowy katalog działającego Klippera | `~/klipper-inventure-r38.6` |
| Aktywna konfiguracja | dotychczasowy `~/printer_data/config/printer.cfg` |

## Gdzie jest ostatni opublikowany stan projektu

Bieżąca linia projektu Inventure jest na gałęzi `orange-pi-all-axis`.
Tag `inventure-homing-r38.6` wskazuje kod procedur z commitu
`ee9cd2f3dc2c1f71d930b0b6825ed08233b72a2f`: nowe Home Z oraz zachowane
pięciopunktowe poziomowanie platformy. Kolejne commity instalatora na tej
gałęzi nie zmieniają przypiętego kodu tych procedur. Gałąź `main` zawiera
starszy zestaw źródłowy RC8 i nie jest źródłem bieżącej instalacji.

Instalacja korzysta z opublikowanych plików repozytorium. Dostęp do starego
Orange Pi nie jest wymagany do przeniesienia kodu ani zaakceptowanej
kalibracji pada zapisanej w repozytorium.

## Kiedy użyć nowego instalatora

Starszy katalog `klipper-inventure-candidate` może zawierać dodatki Inventure,
a mimo to uruchamiać konfigurację z `kinematics: none` i
`allow_actuators: False`. Taki zestaw służył do diagnostyki. Zwykły
`install-orange-pi.py` aktualizuje istniejący port kontrolny i nie odtwarza
brakującej geometrii ani konfiguracji natywnego CoreXY.

Użyj `install-new-host.py` z checkoutu zawierającego ten plik i niniejszą
instrukcję. Stary tag źródeł R38.6 sam w sobie nie zawiera nowego instalatora.
Przygotowanie dla działającego wcześniej wariantu diagnostycznego wymaga
jawnego `--restore-field-profile`.

Na nowym hoście bez katalogu `~/inventure-src` pobierz projekt zwykłym
`git clone`, wskazując bieżącą gałąź:

```bash
git clone --branch orange-pi-all-axis \
  https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper.git \
  "$HOME/inventure-src" &&
cd "$HOME/inventure-src"
```

Jeżeli `~/inventure-src` już istnieje jako klon tego repozytorium, zamiast
ponownego klonowania wykonaj:

```bash
git -C "$HOME/inventure-src" fetch origin &&
git -C "$HOME/inventure-src" switch orange-pi-all-axis &&
git -C "$HOME/inventure-src" merge --ff-only origin/orange-pi-all-axis &&
cd "$HOME/inventure-src"
```

Git odmówi przełączenia, jeśli nadpisałoby ono lokalne zmiany. Ten katalog
zawiera źródła i narzędzia instalacyjne. Działający runtime znajduje się
pod stałą ścieżką wskazaną w tabeli; instalator ponownie wykorzystuje
istniejący runtime po sprawdzeniu wszystkich przypiętych plików. Źródła
procedur R38.6 pozostają przypięte do `ee9cd2f`; dodatkowe commity
dostarczają instalator migracji. Żadna z tych ścieżek nie wymaga worktree.

## 1. Przygotowanie i weryfikacja bez przełączenia usługi

Na W2, jako użytkownik `klipper`, przejdź do katalogu aktualnego instalatora
i uruchom polecenie bez `sudo`. Instalator wymaga działającego Klippera
z odpowiedzią MCU przez Moonrakera oraz istniejącego katalogu
`~/printer_data/gcodes`:

```bash
python3 install-new-host.py \
  --restore-field-profile \
  --serial /dev/ttyS5 \
  --baud 750000
```

Bez `--apply` instalator przygotowuje osobny, nieaktywny runtime i kopię
konfiguracji. Sprawdza źródła oraz ładuje konfigurację przypiętym Klipperem
w trybie offline. Ten test nie otwiera UART, nie uruchamia pętli sterowania
ani nie zapisuje próbnych wartości PID przez `SAVE_CONFIG`.

Raport pokazuje przygotowane pliki, brakujące pliki stanu operatora i
ścieżki użyte do instalacji. Działająca usługa pozostaje w dotychczasowej
instalacji. Konfiguracja sieci i Armbiana nie jest częścią tej operacji.
Przygotowanie jest możliwe również z aktualnie podłączonym MCU R15:
wtedy raport `firmware_requirement` wskazuje konieczną aktualizację MCU.

### Co odtwarza profil

`config/INVENTURE_R38_FIELD_RECOVERY.cfg` zawiera ostatnie jawne parametry
maszyny odzyskane z logu z 23 listopada 2026 r. Odtwarza m.in. CoreXY,
mapowanie wyjść i czujników, obie dysze, obie strefy komory, chłodzenie,
native Z i preferencje ekranu zapisane w konfiguracji. Zachowuje terenowe
ustawienia Z: dolną pozycję `139.35`, szybkie podejście `8 mm/s`, wolne
podejście około `0.312 mm/s` i `repeat_count: 101`.

Właściwe makra Home, materiału, diagnostyki, OLED i Fluidd pochodzą z
bieżącego zestawu R38.6. Instalator rozdziela ustawienia tym samym
organizatorem, którego używa aktualizator istniejącego hosta: pliki
`inventure_base.cfg`, `inventure_display.cfg`, `inventure_services.cfg`,
`inventure_operations.cfg`, `klipper_calibration.cfg` oraz osobne pliki
czujników, grzania i chłodzenia.

Port UART, baud i katalog G-code są ustawiane na podstawie bieżącej
instalacji i jawnych argumentów. Historyczny port `/dev/ttyS1` nie jest
kopiowany na W2.

### Zaakceptowana kalibracja opublikowana w repozytorium

Przy jawnym `--restore-field-profile` instalator uzupełnia brakujący
`inventure_nozzle_pad.json` z zatwierdzonej drugiej próby FULL zapisanej w
`docs/acceptance/INVENTURE_PAD_R23_2026-11-19_PROBA_2.json`.
Dotyczy to także już uruchomionego profilu CoreXY na nowym Orange Pi.
Istniejący plik kalibracji ma pierwszeństwo i pozostaje bez zmian.

To zapis próby `4eed5e30be704a6ca57440e1e7f27b44`, wskazanej w wydaniu
R38.6 jako `last_saved_hardware_run`. Instalator weryfikuje sumę źródła
`75669f6ea0156dd500abe91de78007433889a852a8c4bc9b747ae17942c1afcf`
i odtwarza format pliku dokładnie według produkcyjnego polecenia SAVE.
Nie zmienia pomiarów ani wartości kandydata. Raport `restored_state_files`
wskazuje źródło, identyfikator próby i sumy kontrolne.

Ta kalibracja jest wymagana przez opublikowaną procedurę poziomowania.
Jej brak w instalatorze `772ce750` stanowił brak przeniesienia danych
dostępnych na GitHubie. Sam kod poziomowania był obecny.

### Pozostałe dane operatora

Zachowane pliki JSON z dotychczasowego katalogu konfiguracji są kopiowane
bez zmiany ich zawartości. Instalator raportuje brakujące pliki:

- `inventure_menu_settings.json` — zapisane wybory menu i ekranu;
- `inventure_filament_settings.json` — ustawienia materiału T0/T1;
- `inventure_nozzle_xy.json` — zapisana korekta XY;
- `inventure_nozzle_pad.json` — zapisany wynik pomiaru padów/dysz;
- `inventure_platform_level.json` — zapisany raport poziomowania.

Poza jawnym odtworzeniem opublikowanej próby FULL nie tworzy zastępczych
wyników kalibracji. Brak pliku korekty XY oznacza
domyślny punkt wyjścia `X=0`, `Y=0` w procedurze kalibracji, a brak profili
materiału oznacza użycie wartości domyślnych procedur. Sam log nie pozwala
odtworzyć tych wyborów. Profil odzyskany z logu nie jest pełną kopią danych
starego hosta ani całej bazy Moonrakera.

### Zgłoszone błędy wydania R38.6/R16

Odtworzenie kalibracji uzupełnia instalację. Nie zmienia procedury Home
ani firmware. W próbie z 24 listopada 2026 r. potwierdzono osobno:

- MCU R16 odrzuca przejście z NATIVE do SERVICE po Home X/Y, ponieważ
  ogólny warunek `motor_test_busy()` uznaje sam tryb NATIVE za zajętość.
  Komunikat to `another_owner_is_busy`. Ten sam warunek występuje w
  niezmienionym opublikowanym kodzie MCU; ponowne sklonowanie tagu go
  nie usuwa. STOP/OFF nie jest obejściem, ponieważ celowo zatrzaskuje
  wyjście z natywnego sterowania.
- W osobnej próbie Home Y MCU zgłosił `Timer too close`; przed zdarzeniem
  wzrastała liczba retransmisji UART. Sam ten wyciąg nie rozstrzyga źródła
  opóźnienia.

Stan `ready` i poprawna identyfikacja wersji są sprawdzeniem uruchomienia
usługi, nie potwierdzeniem wykonania tych procedur na drukarce.

## 2. Aplikacja MCU R16 dla istniejącego InventureBoot 1.7

Pobierz [INVENTURE_MCU_HOMING_R16_BOOT17.zip](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/releases/download/inventure-homing-r38.6/INVENTURE_MCU_HOMING_R16_BOOT17.zip)
z [wydania `inventure-homing-r38.6`](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/releases/tag/inventure-homing-r38.6).

Poniższe sumy zostały sprawdzone na rzeczywiście pobranym archiwum i jego
zawartości. Wszystkie wpisy wewnętrznego `SHA256SUMS` również się zgadzają.

| Plik | Bajty | SHA-256 |
|---|---:|---|
| `INVENTURE_MCU_HOMING_R16_BOOT17.zip` | 91363 | `9cec15d54705bd282fefa04d05aa4c95b1eda8b114c2ed387352d3428c598e08` |
| `INVENTUR.IVB` wewnątrz ZIP | 107948 | `24ad949263b47358f67bbb7d32e63234c3b71950af733078997fe643ba6fb324` |

W katalogu pobrania można sprawdzić archiwum poleceniem:

```bash
sha256sum INVENTURE_MCU_HOMING_R16_BOOT17.zip
```

Na macOS odpowiednikiem jest `shasum -a 256 INVENTURE_MCU_HOMING_R16_BOOT17.zip`.

Instrukcja `README.md` wewnątrz tego ZIP-u wymaga wypakowania
`INVENTUR.IVB` i instalacji przez istniejącą procedurę aktualizacji
aplikacji InventureBoot 1.7. Nie wymienia kolejnych nazw pozycji menu.
Stosuj używaną już na tej drukarce procedurę:

1. Zatrzymaj host poleceniem `sudo systemctl stop klipper`. Wypakuj
   `INVENTUR.IVB` z podanego pakietu. Zachowaj poprzednią aplikację.
2. Umieść plik w głównym katalogu używanej karty FAT32, zgodnie z
   [opisaną w projekcie procedurą aplikacji InventureBoot](MCU_RC8_LOCAL_SERVICES_PL.md).
3. W istniejącym InventureBoot 1.7 sprawdź wybrany plik i wgraj samą
   aplikację. Wykonaj sprawdzenie Flash dostępne w tej procedurze.
4. Uruchom aplikację MCU. Zachowaj ustawienie P4 odpowiadające hostowi,
   w tym przypadku `750000`.
5. Wznów host poleceniem `sudo systemctl start klipper` i poczekaj na
   Ready w Fluidd. Teraz instalator może odczytać identyfikację R16 przez
   działające połączenie.

Pakiet zawiera aplikację `INVENTUR.IVB`; nie zawiera obrazu aktualizacji
bootloadera. Raport wydania potwierdza `baud_mode: InventureBoot_journal`
i `forced_baud: 0`, więc prędkość pochodzi z zapisanych ustawień InventureBoot.
Wiersz `P4: ... 8N1` na lokalnym ekranie oczekiwania pozwala sprawdzić tę
wartość. Sam napis `MCU: RC8.0` na tym ekranie nie identyfikuje wydania R15/R16.

W zweryfikowanym `klipper.dict` tego pakietu są:

```text
version = RC8-CONTROL-R7-HOMING-R16-fae8ae93a812
INVENTURE_MCU_RELEASE = RC8-CONTROL-R7-HOMING-R16
INVENTURE_NATIVE_HEATER_HANDOFF_ABI = 1
INVENTURE_NATIVE_XY_HANDOFF_ABI = 1
INVENTURE_NATIVE_Z_CLEARANCE_ABI = 1
```

Archiwalny log użyty do odzyskania konfiguracji identyfikował R15. Ten
historyczny wpis nie potwierdza aktualizacji obecnie podłączonego MCU.

## 3. Aktywacja na W2

Gdy przygotowanie zakończy się poprawnie i MCU ma odpowiadające wydanie
R16, uruchom ten sam instalator z `--apply` przy bezczynnej drukarce:

```bash
python3 install-new-host.py \
  --restore-field-profile \
  --serial /dev/ttyS5 \
  --baud 750000 \
  --apply
```

Instalator wykonuje prywatną kopię całego dotychczasowego katalogu
konfiguracji (`before-config`) oraz kopie plików zmienianych w transakcji.
Sprawdza, czy pliki źródłowe nie
zmieniły się od przygotowania. Włącza runtime
`~/klipper-inventure-r38.6`, zachowując dotychczasową ścieżkę aktywnego
`~/printer_data/config/printer.cfg`. Zapisz wyświetloną ścieżkę kopii.
Raport po uruchomieniu identyfikuje aktywne wersje. Przygotowanie i
aktywacja nie wywołują procedury Home ani grzania testowego.

`--apply` sprawdza przez działające połączenie, czy MCU zgłasza R16 oraz
obie cechy `INVENTURE_NATIVE_XY_HANDOFF_ABI=1` i
`INVENTURE_NATIVE_Z_CLEARANCE_ABI=1`. Kontrola odbywa się przed
przygotowaniem i ponownie bezpośrednio przed przełączeniem usługi.
R15 nie przejdzie aktywacji. Uprawnień `sudo` instalator żąda osobno
tylko w wariancie `--apply`, do przełączenia usług.

Jeżeli wynik to `INSTALLED_MCU_RESTART_REQUIRED`, nowa konfiguracja jest
zainstalowana, lecz MCU zachował stan shutdown z poprzedniej sesji.
Sprawdź wypisany powód, następnie wykonaj raz `FIRMWARE_RESTART` w Fluidd
i poczekaj na Ready. Instalator sam nie wysyła tej komendy.

Po Ready w konsoli Fluidd sprawdź:

```gcode
INVENTURE_VERSION
INVENTURE_OLED_STATUS
```

Oczekiwane są host `RC8-CONTROL-R7-HOMING-R38.6`, MCU
`RC8-CONTROL-R7-HOMING-R16`, `native_xy_handoff_abi=1` i
`native_z_clearance_abi=1`. Przy błędzie komunikacji lub innej wersji MCU
najpierw wyjaśnij wynik identyfikacji.

## Pierwszy Home i zakres potwierdzenia

Po identyfikacji zgodnej pary i Ready można wywołać w konsoli Fluidd:

```gcode
INVENTURE_HOME_ALL TARGET=200 PARK_Z=10
```

Potwierdź istniejący komunikat o tacy, czystym padzie i swobodnym ruchu.
Ta komenda uruchamia rzeczywiste grzanie i ruch. R38.6 wykonuje jedno
bazowanie XY, przygotowanie T0 nad koszem, przejście na pad, odniesienie
dolne Z, szybkie i wolne podejście do kontaktu oraz odjazd. Pełna kolejność
i warunki przejęcia sterowania są opisane w
[instrukcji Home R38.6/MCU R16](INVENTURE_HOME_SEQUENCE_R38_6.md).

Testy oprogramowania i odczyt konfiguracji offline sprawdzają instalację
oraz wywołania makr. Nie potwierdzają mechanicznego przełączenia dyszy ani
ukończenia tej sekwencji na fizycznej drukarce. Raport pakietu R16 ma
`tested_on_printer: false`; fizyczna akceptacja pary R38.6/R16 pozostaje
do wykonania i zapisania.

## Weryfikacja instalatora z 24 listopada 2026 r.

Cały zestaw `python -m unittest discover -s tests -q` przeszedł **871
testów**. W tym poprawka instalacji wnosi 41 testów: 13 budowy i
pochodzenia runtime, 6 migracji konfiguracji, 3 rzeczywistego loadera
Klippera i 19 transakcji aktywacji.

Testy awarii obejmują odtworzenie plików, jednostki systemd, ustawień
Fluidd i jego katalogu web. Potwierdzają również odmowę aktywacji na MCU
R15, zachowanie równoległych edycji, odrzucenie starego procesu zgłaszającego
Ready i niewznawianie usługi po niepełnym rollbacku. Usługi i HTTP są
symulowane; konfiguracje, parser Klippera, kopie i atomowe operacje plikowe
są rzeczywiste. Weryfikacja nie wykonuje ruchu na drukarce.
