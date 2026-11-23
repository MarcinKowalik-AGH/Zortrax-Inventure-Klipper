# R38.5 selects and cleans T0 before operator homing

Home All and Home Z now execute the existing T0 CLEAN procedure before the native Z reference. This prepares the mechanical nozzle selector even when T1 is initially lowered. The 23 November printer observation showed that R38.4 could start measuring with T1 lowered. Its check for Klipper's active `extruder` identified the software heater and extrusion object. It did not operate the physical selector.

## Sequence from Fluidd and the OLED

The existing confirmation starts the complete operation. The prompt displays the target temperature and automatic T0 preparation. The host temporarily sets native heater targets to zero and waits for their PWM requests to stop. It passes control to the existing CLEAN owner, which homes its axes, parks over the waste bin, heats T0 to the requested temperature, executes the selector approach and slow T0 press, wipes the nozzle and returns to the bin.

Only a successful CLEAN result for T0, with both selection and cleaning completed, permits native homing to start. The host reacquires native control, confirms the lower Z endstop, homes Y and X, reaches the center pad, heats T0 to its homing target and runs the existing guarded PC14 refinement. It establishes the working zero and parks at Z10 by default. On successful completion the original native heater targets are restored. A failure leaves the targets at zero and records the failed result.

Preparation uses the existing service coordinate frame. Native motion then obtains its own reference. Consequently both operator Home Z and Home All repeat native Y/X homing after preparation, including when Home X and Home Y were run immediately beforehand. The individual cold Home X, Home Y and Home XY actions keep their existing route.

STOP, a failed limit release, interrupted preparation, a shutdown or an unsuccessful CLEAN result prevents the pad measurement. The shared service preflight admits the running homing object only for its own preparation. It continues to refuse a different owner and any already active PC14 refinement.

## Install on the Orange Pi

MCU R15 provides the heater handoff required after cleaning. Keep the installed MCU R15 and update the host through this tag.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-homing-r38.5 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-homing-r38.5" refs/tags/inventure-homing-r38.5 &&
python3 "$HOME/inventure-homing-r38.5/install-orange-pi.py" --configure-fluidd --organize-config
```

If this worktree already exists at the tag, rerun the final installer command. Once Fluidd reports Ready, enter `INVENTURE_VERSION`. The host should report `RC8-CONTROL-R7-HOMING-R38.5`, and the MCU should report `RC8-CONTROL-R7-HEATER-HANDOFF-R15`. The host checks the MCU's heater-handoff capability before accepting hot operator homing.

Enter `INVENTURE_HOME_ALL` in Fluidd and confirm the seated tray, clear pad and free platform travel. The console reports `T0 selection and cleaning`, then `T0 selected and cleaned` before native homing. Observe the T0 selector action and the following approach to the metal pad. Both the physical OLED and Fluidd use this entry.

## Verification

Regression tests start with T1 physically selected in the model and execute the production CLEAN planner. They require T0 selection and completed wiping before the reference. Additional tests execute the production factory host against its MCU wire model, including failure to release a homing limit. They cover cancelled preparation, refusal of older MCU capabilities, heater target preservation, independent service ownership and retained failed-reference diagnostics.

The host validation comprises 828 passing regression tests. All eight MCU register-test programs passed. The installed macros and their exact confirmation commands are exercised in the project's pinned Klipper for both the field upgrade and fresh configuration. The MCU register tests cover the existing R15 motion, guards and heater handoff. Software validation and the subsequent physical acceptance result are recorded separately. A complete R38.5 run on the printer remains pending at publication.

[Home Z safety audit](INVENTURE_HOME_Z_SAFETY_AUDIT_2026-11-22.md) · [MCU R15 heater handoff](INVENTURE_HEATER_HANDOFF_R15.md) · [Shared procedure modules](MODULAR_PROCEDURES_2026-11-10.md)
