# Zortrax Inventure V9 — Klipper (MCU RC8.0 LOCAL)

**Experimental, source-controlled STM32F207 build for the owner's printer.**  
Orange Pi Zero 3 / STM32F207 / InventureBoot 1.6-RC2 / P4-PERSIST-r3 / SSD1322 / Fluidd.  
This is **not** a stock Zortrax firmware or a physical-hardware qualification.

## Current release: autonomous cooling and display recovery

**MCU `P01-P17-RC8.0-LOCAL`** adds a local **50°C ON / 45°C OFF** thermostat
while unarmed or disconnected. All eight Inventure temperature inputs must be
fresh and plausible before cooling may stop. A diagnosed thermal/cable/tach
fault keeps protective cooling on. The model fan is off in local mode.
The normal armed printer retains its existing native fan policy.

The MCU now redraws its waiting page after OLED lease expiry/release, or a
stopped/cooling page after global shutdown. Host generation checks and the
actuator fault latch remain enforced. The local renderer uses **128 B** of
pixel buffer instead of 8192 B. ASCII glyph lookup and STEP/tach hot paths
are shortened; the 20 kHz sample rate and motion parameters are preserved.

This update needs a new **MCU application** and its matching host. The
bootloader is unchanged. Use [RC8 installation and audit](docs/MCU_RC8_LOCAL_SERVICES_PL.md).
The Actions artifact contains `MCU/INVENTURE_RC8_0_SD_BOOT16.zip` and a complete,
hash-verified host installer at `HOST/scripts/install_host.py`.
`INVENTURE_COOLING_STATUS` requests the new read-only MCU diagnostics.

## Earlier RC7.4 host-only OLED update

RC7.4 integrates the separate RC7.3 full-height, five-row menu into the actual
GitHub build and install path. The Klipper status page opens first; OK opens
the menu and its pinned Back row returns to status. Unchanged screens reuse
the rendered buffer, and moving the root cursor does not redraw a 50×50 icon.

Defaults are **`refresh_interval: 0.2` seconds** and
**`chunk_interval: 0.002` seconds**. These are 200 ms and 2 ms, respectively.
The refresh setting is a scheduling interval, not a promise that a complete
8192-byte frame transfers in 200 ms. Single-operation ACK pacing, generation
fences, retries and MCU shutdown behavior remain enforced. RC7.4 also avoids
500 reactor polls/s on a static screen, with immediate wake on panel events.

For an existing RC7.2/RC7.3 host, use the reversible host-only installer:
`python3 scripts/install_oled_rc74.py --klipper ~/klipper --printer-cfg ~/printer_data/config/printer.cfg`
(dry run). Details and validation are in [the audit](docs/OLED_RC7_4_AUDIT_PL.md).
That earlier host-only update keeps MCU RC7.1; the RC8 local services above
require the new application image.

## Automated GNU ARM compile

[Open Actions builds](../../actions/workflows/build-arm-inventure.yml) and select **Build Inventure STM32F207 GNU ARM** (or view its automatic run on source changes). Actions uses Arm GNU Toolchain **14.2.Rel1**, runs source/ABI/unit tests and packs an actual `INVENTUR.IVB` *only if* ELF, vector table, MCU dictionary and archive checks pass. The resulting downloadable Actions artifact includes `INVENTURE_RC8_0_SD_BOOT16.zip`, `INVENTUR.IVB`, `klipper.elf/.bin/.dict`, build reports and SHA-256.

**Bootloader is not built or modified.** The APP starts at **0x08010000**. Never flash raw `.bin` to address 0x08000000. The documented install format for the user's InventureBoot is **`INVENTUR.IVB`**.

## Source and scope

- `source/INVENTURE_RC7_2_PUBLIC_GNU_ARM_SOURCE.zip`: complete pinned **build snapshot**, Klipper baseline reduced to the F207 dependencies, 29 release overlays, host Zero 3 files, 29 diagnostic Fluidd macros + 19 opt-in procedure macros, and public offline tests.
- `mcu/`, `host/`, `host_core/`, `zero3/`, `fluidd/`: readable production overlays. The immutable source snapshot plus the four audited `ci/apply_*.py` steps define the build. `ci/rc74_manifest.json` pins the exact OLED inputs/outputs, and CI rejects readable/build MCU drift.
- `.github/workflows/build-arm-inventure.yml`: actual cross-compilation, package verification, and downloadable CI artifact.
- `docs/BUILD_AND_INSTALL_PL.md`: how to read results, install host, and choose speed.
- `docs/SOURCE_AND_LIMITATIONS.md`: exclusions, licenses, and hardware testing caveats.

**Privacy:** full original Zortrax application `.bin`, private Flash/NVM/OTP/UID dumps, device IDs, credentials and OEM decryption tools are NOT distributed. The original-firmware byte comparison test is explicitly `SKIPPED` in public CI; other tests are retained.

**Hardware limitations:** software tests cannot verify thermistor wiring, heater watchdogs under power, CoreXY directions, Z contact, material motion, stepping waveform, OLED pixels or 1–2 Mbaud UART on a physical Zero 3. Do not enable actuator/heater qualification solely on a green CI result.

Derived from project release RC7.1 (2026-11-02); `RC7.2` denotes the **GitHub GNU ARM publication/build packaging**, not newly verified printer behavior. The earlier build identified MCU RC7.1; the current application identifies RC8.0 and preserves board ABI3 / OLED ABI2.

To reconstruct a new local kit exactly as CI does, run
`python3 ci/prepare_kit.py --destination ../inventure-rc74-kit`.
Then run `python3 ../inventure-rc74-kit/scripts/verify_release.py`.
The current public suite is **591 passed + 1 private-reference skip**, plus
**28 acceptance** and **14 repository integration/installer/C-wrapper** tests.

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
