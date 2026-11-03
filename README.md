# Zortrax Inventure V9 — Klipper (RC7.2 GitHub GNU ARM build)

**Experimental, source-controlled STM32F207 build for the owner's printer.**  
Orange Pi Zero 3 / STM32F207 / InventureBoot 1.6-RC2 / P4-PERSIST-r3 / SSD1322 / Fluidd.  
This is **not** a stock Zortrax firmware or a physical-hardware qualification.

## Automated GNU ARM compile

[Open Actions builds](../../actions/workflows/build-arm-inventure.yml) and select **Build Inventure STM32F207 GNU ARM** (or view its automatic run on source changes). Actions uses Arm GNU Toolchain **14.2.Rel1**, runs source/ABI/unit tests and packs an actual `INVENTUR.IVB` *only if* ELF, vector table, MCU dictionary and archive checks pass. The resulting downloadable Actions artifact includes `INVENTURE_RC7_1_SD_BOOT16.zip`, `INVENTUR.IVB`, `klipper.elf/.bin/.dict`, build reports and SHA-256.

**Bootloader is not built or modified.** The APP starts at **0x08010000**. Never flash raw `.bin` to address 0x08000000. The documented install format for the user's InventureBoot is **`INVENTUR.IVB`**.

## Source and scope

- `source/INVENTURE_RC7_2_PUBLIC_GNU_ARM_SOURCE.zip`: complete pinned **build snapshot**, Klipper baseline reduced to the F207 dependencies, 26 RC7.1 overlays, host Zero 3 files, 29 diagnostic Fluidd macros + 19 opt-in procedure macros, and public offline tests.
- `mcu/`, `host/`, `host_core/`, `zero3/`, `fluidd/`: readable copies of production/host overlays (the CI source snapshot is authoritative; checksums verified before build).
- `.github/workflows/build-arm-inventure.yml`: actual cross-compilation, package verification, and downloadable CI artifact.
- `docs/BUILD_AND_INSTALL_PL.md`: how to read results, install host, and choose speed.
- `docs/SOURCE_AND_LIMITATIONS.md`: exclusions, licenses, and hardware testing caveats.

**Privacy:** full original Zortrax application `.bin`, private Flash/NVM/OTP/UID dumps, device IDs, credentials and OEM decryption tools are NOT distributed. The original-firmware byte comparison test is explicitly `SKIPPED` in public CI; other tests are retained.

**Hardware limitations:** software tests cannot verify thermistor wiring, heater watchdogs under power, CoreXY directions, Z contact, material motion, stepping waveform, OLED pixels or 1–2 Mbaud UART on a physical Zero 3. Do not enable actuator/heater qualification solely on a green CI result.

Derived from project release RC7.1 (2026-11-02); `RC7.2` denotes the **GitHub GNU ARM publication/build packaging**, not newly verified printer behavior. The firmware still identifies the RC7.1 ABI and release.

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
