# Inventure R38.2 on Orange Pi

R38.2 installs the R38 operator procedures and fixes two errors in R38.1. It works with the existing `RC8-CONTROL-R7-DISPLAY-R14` controller. All printer procedures and their physical acceptance records are listed in the [operator procedure audit](INVENTURE_HOMING_R38.md).

## What happened on the printer

The R38.1 installer copied seven changed host modules. While setting up Fluidd, Klipper reported `Can not update MCU 'mcu' config as it is shutdown`. The updater treated that response as a failed software deployment and restored all seven original checksums. The adapter returned to the published R37 checksum. The second `INSTALLED` in that terminal transcript was a rollback. No homing command ran during the installation. The terminal transcript contains the MCU state during connection. The initial cause must be obtained from the printer log.

The R38.1 manifest also identified the host as R38.1 while the installed Python adapter reported R38. R38.2 checks the version returned by the running adapter during dashboard setup. The startup test calls the registered `INVENTURE_VERSION` command and compares it with the Moonraker status field and both release manifests.

The operator subsequently retrieved two shutdown transitions. The first records T0 failing the heater response check, followed by the MCU's `Command request`. The later transition records a loss of communication with the primary MCU. A repeated text search returned the same line numbers. Those lines alone do not identify the heater target, applied power, cause of link loss, or which incident coincided with the installation.

## Installation

Use the Orange Pi shell while the printer is idle.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-homing-r38.2 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-homing-r38.2" refs/tags/inventure-homing-r38.2 &&
python3 "$HOME/inventure-homing-r38.2/install-orange-pi.py" --configure-fluidd --organize-config
```

If that worktree already exists at the tag, repeat only the final command. Read the final installer status.

| Final status | What was verified | Operator action |
| --- | --- | --- |
| `HOST_UPDATED`, `HOST_RESTARTED` or `HOST_ALREADY_CURRENT` with `klipper_state: ready` | The running adapter reports R38.2, Klipper exposes the dashboard macros, and Moonraker holds the requested groups. | Check `INVENTURE_VERSION` in Fluidd. |
| `HOST_INSTALLED_MCU_SHUTDOWN` | Host files are installed. Klipper explicitly reported a previously shut-down primary MCU during configuration. The installer used Klipper's own include parser to get the installed macro names and completed the dashboard setup. | Inspect the original reason in `klippy.log`. After resolving it, enter `FIRMWARE_RESTART` once in Fluidd. Wait for Ready and then check `INVENTURE_VERSION`. |
| `UPDATE FAILED` | See the specific error. When the updater restores changed host modules, it reports `ROLLED_BACK` with the revision read from the restored file. | Resolve the reported error before retrying. |

The installer reports recent shutdown records in `shutdown_log` when it can read them. It checks up to 4 MiB of the active Klipper log and, when needed, its most recent uncompressed rotation. For each recorded transition it includes the available session header, up to three samples from the preceding 40 statistics lines, T0's target, reading and requested duty, and main MCU link counters. Multiple entries may belong to different sessions. To get the same report from an existing log on the Orange Pi:

```bash
python3 "$HOME/inventure-homing-r38.2/install-orange-pi.py" --shutdown-log "$HOME/printer_data/logs/klippy.log"
```

The explicit log command reads the entire specified file and prints up to the four most recent shutdown transitions. It runs without connecting to the printer. If the current log starts after the incident, select the preceding Klipper log. Once Ready, the expected identifiers are `RC8-CONTROL-R7-HOMING-R38.2` and `RC8-CONTROL-R7-DISPLAY-R14`. The operator test is **Tool → All** or `INVENTURE_HOME_ALL`. After checking the pad and physical clearance, accept **Yes / Start**. This is the first step that requests physical homing.

## Regression coverage

The field-message test exercises the systemd update with a temporary installation, the pinned Klipper include parser, the field printer configuration, a local wildcard macro, and simulated Moonraker responses. It checks that the new files stay installed across the MCU latch, that restarting only the installer leaves the host process running, and that Ready is reported only after the running adapter identifies itself as R38.2. A distinct invalid configuration exercises rollback and checks the restored module bytes. Other cases cover a different MCU, a sensor shutdown, missing macros and a stale running revision.

The release pipeline also loads the organized config in the pinned Klipper runtime, runs installed macros and OLED routes against simulated hardware, tests the compiled Fluidd dashboard and Moonraker connection, and checks the R14 image. Completion of homing on this printer after this update still requires an operator observation.
