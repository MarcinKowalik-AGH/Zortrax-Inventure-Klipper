# R38.6 single XY homing and MCU R16 position handoff

Home All and Home Z execute one composed preparation followed by the native Z contact reference. The physical OLED and Fluidd use the same confirmation and host entry. R38.5 invoked a complete CLEAN and then homed native Y/X again. R38.6 transfers the measured XY reference directly to Klipper.

## Motion sequence

1. The existing bounded initial clearance module lowers the table by up to 10 mm when PE12 is inactive. PE12 can stop this clearance early. PC14 must be open before lateral movement. This initial clearance establishes space for XY travel. The full bottom reference is established later over the center pad.
2. Home Y and X once, using the existing MCU limit seek and release modules. Record the actual completed release strokes of both CoreXY drivers.
3. Park above the T0 waste-bin position and heat T0 to the requested target, normally 200 degrees C.
4. Run the existing T0 selector module, including the slow 15 mm press from service X139.5 to X154.5 at Y154.5. Complete the existing wipe module and leave the brush for the waste bin.
5. Move to the configured center pad. The final service move compensates the measured release-to-trigger offsets so it lands at the native pad coordinates, normally X82.8 Y68.0.
6. MCU R16 releases service heater outputs, restores native heater PWM ownership and native STEP/DIR direction levels, and switches to NATIVE while holding the shared PE10 enable continuously. It accepts only the current completed XY trajectory and its factory owner. Klipper adopts the confirmed XY position.
7. Home the table downward to PE12 at the center pad. Release PE12 slowly and complete the existing bottom clearance. Rise quickly to the fine-search height, normally nominal Z2. The field configuration uses 8 mm/s coarse travel. The fresh commissioning profile uses its configured 20 mm/s.
8. Run the existing local PC14 contact/release loop at its configured fine speed, normally about 0.312 mm/s. Retain the measured best electrical release and its existing completion criteria.
9. The MCU immediately lowers the table 802 additional steps, exactly 1 mm, before reporting the completed result. These steps are reported separately from the contact measurement. A smaller requested final park distance limits this first retreat to that distance, with step quantization. The host receives the result at this gap, releases local ownership, records the correct working coordinate and completes the downward park to Z10 by default.

Heater targets saved before preparation are restored after successful completion. Failure clears the targets and prevents publication of a valid Z reference.

## XY reference transfer

For each axis, native position equals the configured native endstop position plus the measured positive release stroke plus the final service coordinate. The two release strokes must stop on their selected endstop transition, use equal CoreXY step counts with the correct directions, and contain no Z or E steps. The native wiring and 640 steps/mm are verified during MCU identification.

The host calculates the service pad endpoint from this measured translation. The MCU handoff requires the matching factory token, motion run and motion token, a complete XY move, open PC14, seated PC13, healthy sensors and the current global door policy. It preserves enable and restores the direction levels requested by the native stepper API. Replayed handoff requests acknowledge the completed transfer without re-enabling a stopped controller.

## Contact result handoff

The final R38.5 log completed 184 contact/release cycles and reported DONE before a local guard shutdown during host verification. The retained record reported `guard dropped`. Its exact input cause was unavailable in that record. MCU R16 removes the pause at the electrical release edge by executing the requested downward gap locally. Register tests reproduce reclosure during the first clearance steps and require an open circuit at the completed gap. The release height and the additional clearance have separate counters, so the working zero remains the measured release.

## Install

Install MCU R16 and host R38.6 together. Download `INVENTURE_MCU_HOMING_R16_BOOT17.zip` from this release. Extract `INVENTUR.IVB` and install it through the existing InventureBoot 1.7 application-update procedure.

Update the Orange Pi while the printer is idle.

```bash
git -C "$HOME/inventure-orange-pi" fetch origin tag inventure-homing-r38.6 &&
git -C "$HOME/inventure-orange-pi" worktree add --detach "$HOME/inventure-homing-r38.6" refs/tags/inventure-homing-r38.6 &&
python3 "$HOME/inventure-homing-r38.6/install-orange-pi.py" --configure-fluidd --organize-config
```

The host installer handles the Orange Pi files. MCU installation uses the separate application package above. After restart and Ready, run the following in the Klipper console.

```gcode
INVENTURE_VERSION
INVENTURE_HOME_ALL
```

Confirm the existing tray, clear pad and free travel prompt. The version must report host `RC8-CONTROL-R7-HOMING-R38.6`, MCU `RC8-CONTROL-R7-HOMING-R16`, `native_xy_handoff_abi=1` and `native_z_clearance_abi=1`. The default homing target is 200 degrees C. An explicit example is `INVENTURE_HOME_ALL TARGET=200 PARK_Z=10`.

## Verification and physical acceptance

The host regression and pinned Klipper startup checks exercise the literal installed Fluidd macros and OLED confirmation. The model begins with T1 physically selected, checks one Y/X seek and release, heating at the bin, the T0 selector press, completed wiping, the translated pad endpoint, the later PE12 reference, local contact measurement and final downward park. The production factory host also runs against the separate endstop/motion wire model. Failed homing release, interrupted preparation, invalid handoff, incomplete clearance and MCU shutdown prevent a valid Z result.

MCU register tests exercise continuous enable and restored native DIR during XY handoff, owner and run checks, retries, sensor/interlock rejection, exact separate clearance counts, contact reclosure during downward clearance, heartbeat loss, tray loss and overlapping native STEP requests. The release build verifies the embedded dictionary, capability flags and InventureBoot container.

Validation passed 830 Python regression tests, all eight MCU register-test programs, and the installed field-upgrade and fresh-profile startup checks in the pinned Klipper. GNU Arm 14.2.1 produced a 107948-byte InventureBoot application container.

Physical acceptance of this composed R38.6 and R16 sequence remains pending on the printer. The software tests distinguish commanded driver pulses from mechanical selector engagement.
