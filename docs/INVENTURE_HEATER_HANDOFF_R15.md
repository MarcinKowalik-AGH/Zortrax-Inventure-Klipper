# Native heating after a T0 wipe

The November 22 printer log shows that T0 completed the 200 °C wipe, parked over the waste bin at X82.8 Y152.5 and switched its heater off. After the operator confirmed Home XYZ, Klipper reached the pad position at X82.8 Y68.0 with the table at the lower limit. Klipper requested 40% heater power for 200 °C. The logged T0 temperature fell from 121.4 °C to 104.6 °C in 21 seconds. Klipper's heater verification stopped homing before the PC14 approach and requested an MCU shutdown.

In R14 the service output code switches the active-low heater pin to GPIO HIGH at the end of cleaning. Klipper configures its timer output once at startup. After the switch back to native control, subsequent PWM commands updated the timer while the physical pin remained disconnected. R15 restores the timer connection for heaters configured by Klipper when native control is granted. It loads the OFF duty before reconnecting the pin. The next native heater command controls the output.

## Install on the Inventure

Download `INVENTURE_MCU_HEATER_HANDOFF_R15_BOOT17.zip` from this release. Extract `INVENTUR.IVB`, copy it to the root of the update SD card and use the InventureBoot 1.7 application update. The existing Orange Pi host R38.4 is compatible. After the application starts, run `INVENTURE_VERSION` in Fluidd and check that `mcu_release` is `RC8-CONTROL-R7-HEATER-HANDOFF-R15`.

## Check heating above the waste bin

With the printer idle, run `INVENTURE_CLEAN TOOL=T0 TARGET=200` and select **Yes / Start**. After the wipe finishes and T0 parks above the bin, let the nozzle cool below the target. Then enter these two lines in Fluidd.

```gcode
INVENTURE_CONTROL MODE=NATIVE
SET_HEATER_TEMPERATURE HEATER=extruder TARGET=200
```

Watch Extruder 0 in Thermals. Once it heats back to 200 °C, switch it off with `SET_HEATER_TEMPERATURE HEATER=extruder TARGET=0`. This checks the handoff while the table is stationary.

## Verification

The MCU register test reproduced the disconnected pin with the R14 source. With R15 it exercised T0, T1 and both chamber channels, including the stale duty left by the service timer, repeated mode selection, an active lease, a door interlock and latched stop. The test checks the GPIO mode as well as the timer compare register. The application build verifies the embedded dictionary and the SD image.

The previous run in the same field log reached PC14 and exhausted its 8020 upward steps with zero contact cycles. The run after cleaning stopped during heating. Successful heating after the handoff and physical Z contact on R15 still need verification on the printer.
