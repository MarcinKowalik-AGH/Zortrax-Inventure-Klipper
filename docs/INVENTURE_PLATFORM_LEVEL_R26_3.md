# Inventure R26.3 – five-point platform contact measurement

R26.3 updates the Orange Pi host for an Inventure running MCU R13. Start five-point platform leveling with `ZORTRAX_LEVEL` in Fluidd or **Maintenance → Platform leveling** on the OLED. The printer selects T0 automatically. [Pad order, heater targets and knob instructions](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/blob/inventure-platform-level-r26.3/docs/INVENTURE_PLATFORM_LEVEL_R26_2.md#run-the-procedure)

## Why the previous run stopped

The field recording shows that T0 completed the selector stroke and reached X141.70, Y15.84375 mm, the front-right pad. The host was looking for 151 consecutive contact-and-release cycles without a new minimum release height. Each lower minimum restarts that count.

| Completed cycles reported by the host | Cycles since the last new minimum | Best release, Z steps from the bottom endstop |
|---:|---:|---:|
| 471 | 43 | 111851 |
| 493 | 65 | 111851 |
| 511 | 83 | 111851 |

At cycle 511, another 68 cycles without a new minimum would have satisfied the rule at cycle 579. The host stopped at its 512-cycle ceiling with `z_contact_cycle_limit`. The final T0 reading was 200.01 °C against a 200 °C target. The trace ends at the failed probe. It contains no saved leveling result or `INVENTURE_VERSION` output identifying the installed host revision.

## What R26.3 changes

For five-point leveling, the host now continues past 512 contact-and-release pairs while the pad reading settles. It accepts the initial center reference after 101 consecutive cycles without a new minimum, and each subsequent reading after 151. Movement stays within the 10 mm search span for the initial center or the 15 mm span for later readings. The MCU checks each contact and release movement, and the entire procedure has a 3,600-second deadline. [Inventure r0323 firmware audit](https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/blob/inventure-platform-level-r26.3/docs/INVENTURE_PLATFORM_LEVEL_R26.md)

In Fluidd, run `ZORTRAX_LEVEL_STATUS` to inspect `contact_progress`. The `point` field identifies the pad, `qualified_cycles` counts completed contact-and-release pairs, and `best_release_steps` records the lowest release. Compare `non_improvements` with `required_non_improvements` to see how close that pad is to acceptance. The counters start again at each pad. The latest probe progress remains available after a stop.

The regression case models a last improvement at cycle 428, as indicated by the reported counters. Its synthetic continuation holds the same minimum through cycle 579 to exercise the full 151-cycle acceptance rule. The printer recording ends at the cycle-512 stop.

## Install and check R26.3

Run these commands in the Orange Pi shell while the printer is idle.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-platform-level-r26.3 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-platform-level-r26.3" refs/tags/inventure-platform-level-r26.3 &&
python3 "$HOME/inventure-platform-level-r26.3/install-orange-pi.py" --configure-leveling
```

If the tagged worktree already exists, run its installer command again. When Klipper is ready, run `INVENTURE_VERSION` in Fluidd and check for host `RC8-CONTROL-R7-PLATFORM-LEVEL-R26.3` and MCU `RC8-CONTROL-R7-PAD-THERMAL-R13`. Run `ZORTRAX_LEVEL_STATUS` to check that leveling is idle.

Fit the original tray with its five clean contact pads, then run `ZORTRAX_LEVEL`. Confirm each requested knob adjustment with `ZORTRAX_LEVEL_NEXT`. After a completed run, inspect the center deviation in `ZORTRAX_LEVEL_STATUS` and use `ZORTRAX_LEVEL_SAVE` to save an accepted candidate. A complete R26.3 run on the physical printer remains to be verified.
