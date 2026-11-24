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

Jeżeli masz już repozytorium `~/inventure-src` i pobrany worktree
`~/inventure-homing-r38.6`, uzupełnij ten worktree o poprawiony instalator:

```bash
git -C "$HOME/inventure-src" fetch origin \
  refs/heads/fix/w2-r38.6-installer:refs/remotes/origin/fix/w2-r38.6-installer &&
git -C "$HOME/inventure-homing-r38.6" switch --detach \
  refs/remotes/origin/fix/w2-r38.6-installer &&
cd "$HOME/inventure-homing-r38.6"
```

Git odmówi przełączenia, jeśli nadpisałoby ono lokalne zmiany. Ten worktree
zawiera źródła i narzędzia instalacyjne; nowy działający runtime będzie
osobnym katalogiem wskazanym w tabeli. Źródła procedur R38.6 pozostają
przypięte do `ee9cd2f`; dodatkowy commit dostarcza instalator migracji.

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

### Dane, których nie ma w logu

Zachowane pliki JSON z dotychczasowego katalogu konfiguracji są kopiowane
bez zmiany ich zawartości. Instalator raportuje brakujące pliki:

- `inventure_menu_settings.json` — zapisane wybory menu i ekranu;
- `inventure_filament_settings.json` — ustawienia materiału T0/T1;
- `inventure_nozzle_xy.json` — zapisana korekta XY;
- `inventure_nozzle_pad.json` — zapisany wynik pomiaru padów/dysz;
- `inventure_platform_level.json` — zapisany raport poziomowania.

Nie tworzy zastępczych wyników kalibracji. Brak pliku korekty XY oznacza
domyślny punkt wyjścia `X=0`, `Y=0` w procedurze kalibracji, a brak profili
materiału oznacza użycie wartości domyślnych procedur. Sam log nie pozwala
odtworzyć tych wyborów. Profil odzyskany z logu nie jest pełną kopią danych
starego hosta ani całej bazy Moonrakera.

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
