# Zortrax Inventure V9 — MCU RC8.0 i host RC7.6

Wydanie testowe dla Inventure V9 STM32F207 z InventureBoot 1.6-RC2 i Orange Pi
Zero 3 (Armbian ARM64). Pobierz oba pakiety z zakończonego sukcesem przebiegu
[Build Inventure STM32F207 GNU ARM](../../actions/workflows/build-arm-inventure.yml).

| Pakiet | Zawartość |
|---|---|
| `MCU/INVENTURE_RC8_0_SD_BOOT16.zip` | Aplikacja `INVENTUR.IVB` dla istniejącego InventureBoot |
| `INVENTURE_RC8_0_ORANGE_PI_ZERO3_HOST.zip` | Przypięty Klipper, konfiguracja diagnostyczna, instalator i rollback |
| `REPORTS/` | Wyniki regresji, sprawdzenie IVB i kontrakty źródłowe |

MCU `P01-P17-RC8.0-LOCAL` utrzymuje chłodzenie przy utracie hosta lub
nieuzbrojonej drukarce. Włącza je od 50°C, wyłącza przy najwyżej 45°C
przy świeżych i poprawnych pomiarach wszystkich ośmiu czujników.
OLED wraca do strony oczekiwania lub pokazuje status zatrzymania
i chłodzenia. Renderer lokalny używa 128 B na wiersz zamiast bufora 8192 B.
Obraz APP zaczyna się pod adresem `0x08010000`.

Host po Ready pokazuje graficzny ekran Klippera `_default_16x4` z M300.
OK otwiera angielski launcher. Sterownik zachowuje tło menu przy ruchu
kursora, wysyła zmienione obszary po ACK i odświeża status co 0,2 s.
Rzeczywisty czas wyświetlania zależy od UART, SPI i panelu.

[Instalacja MCU i rollback](docs/MCU_RC8_LOCAL_SERVICES_PL.md) ·
[Test Orange Pi Zero 3](docs/RC8_ZERO3_TEST_PL.md) ·
[Zegary P4 i test 1,5 Mbaud](docs/P4_ZERO3_CLOCK_AND_1500K_PL.md) ·
[Zakres źródeł](docs/SOURCE_AND_LIMITATIONS.md)

## Źródła i kompilacja

`python3 ci/prepare_kit.py --destination <nowy-katalog>` odtwarza
snapshot z `source/parts`, sprawdza SHA-256 i nakłada wersjonowane
zmiany ze źródeł w `mcu/`, `host/` i `host_core/`. GitHub Actions
kompiluje rzeczywistą aplikację GNU ARM 14.2.Rel1 i weryfikuje wektory,
słownik MCU, granice pamięci oraz wynikowe IVB. Pełny fabryczny obraz
Zortrax i prywatne zrzuty pamięci są wyłączone z dystrybucji.

## Historia wydań RC7

### GNU ARM follow-up (02.11.2026)

First GitHub Actions attempt passed public regression but GCC correctly
rejected a duplicate `DECL_CTR` identifier produced by `__LINE__` in
`inventure_oled_local.inc` versus `inventure_panel.c`. The CI now applies
`ci/apply_gnu_ctr_fix.py`: a small, hash-pinned source-only `#line`
change, before tests and build. MCU protocol and safety logic are unchanged.
The original 3.3 MB source snapshot remains hash-verified and immutable.

The second audited CI-only patch, `ci/apply_rc7_dict_fix.py`, checks
compile-time markers in the **actual built `klipper.dict`** instead of a
shortened report. `validate_build.py` still verifies all values/schemas
before the IVB can be published. No safety gate is disabled.


## OLED RC7.4 / 2026-11-03 — M300-style, Inventure ABI2

The Inventure host UI now has full-height 256x64 menu rows without the fixed
Zortrax title, an English KLIPPER home/status page, pinned Back, and original
Inventure glyphs. OLED frame updates are pumped immediately from valid MCU ACK
callbacks rather than waiting for every 20 ms host timer tick. Unlike the M300
FAST DMA driver, RC7.4 deliberately retains one outstanding 32-byte ABI2
operation; DMA window=4 and RLE would require a new compatible MCU ABI and
separate firmware qualification.

**Current operator log (03 Nov 2026):** The loaded printer.cfg still showed
chunk_interval=0.020 / refresh_interval=0.5, proving the earlier RC7.3 installer
settings had not become active. An 8192-byte full frame took about 7.14 seconds
with 301 ACKed operations. RC7.4 changes those defaults to 0.002 / 0.2 using
the SHA-pinned `install_rc74_oled.py` and adds ACK-driven pumping, with the
actual OLED speed still requiring hardware validation.

[Polish install and limitations](docs/RC7_4_OLED_ACK_FAST_PL.md).
**MCU APP and InventureBoot unchanged.** CI builds and validates the pinned
RC7.1 STM32F207 APP and now explicitly packages the current tested RC7.4 host
files, instead of republishing the old kit/host snapshot.


## RC7.5 — M300-like launcher / P4 stage (03 November 2026)

The diagnostic OLED defaults to the KLIPPER host status **after** MCU
connection and OLED handover. OK opens a three-way launcher:
`Klipper`, `Zortrax Inventure`, and `Service / Tests`; the five
r0323 OEM branches live under Zortrax Inventure, not immediately under OK.
The M300-like launcher is explicitly display-only for unqualified
movement, heating, RFID and calibration. `INVENTURE_MENU_STATUS` and
`INVENTURE_OLED_STATUS` expose the requested and rendered screen,
current menu root and whether the host frame was ACKed. Before host Ready,
the MCU may display only its local `Waiting for Klipper` screen.

The ACK-driven OLED transport now also guards against synchronous,
recursive MCU ACK delivery. Hardware-independent lifecycle and host
integration tests are included in GitHub Actions.

The matching InventureBoot 1.6-RC2 P4 journal supports both
**1000000** and **2000000** baud; baud is *not auto-negotiated*. Change
bootloader P4 baud and host `printer.cfg` together while the Klipper
service is stopped; test 1M before 2M. The `scripts/inventure_p4_baud_stage.py`
helper performs only an offline hash-backed host config edit. No
bootloader, MCU or motor/heater action is performed by this commit.

[RC7.5 installation, verification and rollback (Polish)](docs/RC7_5_UI_M300_AND_P4_PL.md).
