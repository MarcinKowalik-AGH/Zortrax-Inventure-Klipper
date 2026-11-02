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
