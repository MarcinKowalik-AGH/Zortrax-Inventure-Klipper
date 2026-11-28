# Inventure R31 cassette feed phases

R31 runs the sensor-bounded portions of cassette load and unload on the
STM32F207. The existing Fluidd and OLED actions use the selected left T0
or right T1 cassette through the same factory procedure.

| Phase | Cassette motor | STM32 stop condition | Default limit |
| --- | --- | --- | ---: |
| L2, feed into the path | Forward | Selected inlet A is active | 5 s |
| U0, release | Reverse | Selected A is inactive or B is active | 10 s |
| U3, clear the inlet | Reverse | Selected inlet A is inactive | 6 s |

The STM32 checks the relevant inputs in the existing guard cycle and
reports whether it reached the condition or the time limit. It refuses
motion below the existing temperature limit. If the input is already in
the requested state, the selected motor stays off. A repeated request for
the same phase can recover a lost result without restarting the feeder.

The host sends one start command. It uses a short keepalive approximately
every 400 ms during feeder-only waits. The 1,500 ms factory lease still
stops the session on loss of the host. Thermal control and board
supervision continue throughout the procedure.

Klipper supplies the existing E segment direction, length and speed.
During U3 the cassette may stop independently. The host continues to
sample the inlet at the original E-control cadence during the E move.
The Z commands
use the same host motion path. L1, L3, U1, U2 and L4 retain their existing
factory conditions. R31 reuses the R30 CoreXY STEP ABI and settings.

## Install the paired build

Load the checked `INVENTUR.IVB` through InventureBoot 1.7 with the
existing FAT32 SD update procedure. At idle with heater targets off,
issue `FIRMWARE_RESTART` from Fluidd and wait for Ready before updating
the host.

For a host using the paired R30 CoreXY timers, run the following.

```bash
cd ~/inventure-src &&
git fetch origin codex/inventure-cassette-windows-r31 &&
git switch --detach FETCH_HEAD &&
python3 install-new-host.py --repair-home-procedures --with-xy-step-timers \
  --serial /dev/ttyS5 --baud 750000 --apply
```

The expected MCU release is `RC8-CONTROL-R7-HOME-R31-CASSETTE-WINDOWS`.
The installer selects the cassette profile for that version and preserves
the source configuration. Normal `INVENTURE_LOAD` and `INVENTURE_UNLOAD`
from Fluidd or the OLED exercise the profile. R30 rollback uses its
application image and the same paired host installer.

## Verification

Two builds of the application are byte-identical. The STM32 register
tests check both cassette selections, early sensor arrival, the local
deadline, loss of the host lease, explicit stop, and duplicate commands.
They exercise the original E and Z motion while checking that a cassette
can stop independently. The host tests execute full load and unload
sequences and check the original segment data, the paired installation
and the number of cassette commands.

The paired host passed the 800 Home cases and 184 interlock/refusal
checks. The linked ARM tests exercise the R30 STEP path and UART sampler.
Testing the cassette procedure on an Inventure is pending. R30's Flash
timeout behavior and the PA6 peak-rate hardware acceptance remain as
recorded in the R30 audit.
