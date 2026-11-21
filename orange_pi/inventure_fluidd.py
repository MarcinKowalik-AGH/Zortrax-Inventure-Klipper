"""Compatibility names for scripts written before the English Inventure dashboard."""

LEGACY_COMMANDS = {
    'INVENTURE_HOME_XYZ': 'INVENTURE_HOME_ALL',
    'INV_C_ALIGNMENT': 'INVENTURE_CAL_ALIGNMENT',
    'INV_C_ALIGN_ANULUJ': 'INVENTURE_TEST_CAL_ALIGN_CANCEL',
    'INV_C_ALIGN_CANCEL': 'INVENTURE_TEST_CAL_ALIGN_CANCEL',
    'INV_C_ALIGN_START': 'INVENTURE_TEST_CAL_ALIGN_START',
    'INV_C_CALIBRATION': 'INVENTURE_CALIBRATION_STATE',
    'INV_C_KALIBRACJA': 'INVENTURE_CALIBRATION_STATE',
    'INV_C_LEVELING': 'INVENTURE_CAL_LEVELING',
    'INV_C_LEVEL_CANCEL': 'INVENTURE_TEST_CAL_LEVEL_CANCEL',
    'INV_C_LEVEL_NEXT': 'INVENTURE_TEST_CAL_LEVEL_NEXT',
    'INV_C_LEVEL_START': 'INVENTURE_TEST_CAL_LEVEL_START',
    'INV_C_POZIOMOWANIE': 'INVENTURE_CAL_LEVELING',
    'INV_C_POZIOMUJ_ANULUJ': 'INVENTURE_TEST_CAL_LEVEL_CANCEL',
    'INV_C_POZIOMUJ_DALEJ': 'INVENTURE_TEST_CAL_LEVEL_NEXT',
    'INV_C_POZIOMUJ_START': 'INVENTURE_TEST_CAL_LEVEL_START',
    'INV_C_TOOLFRAME': 'INVENTURE_CAL_TOOLFRAME',
    'INV_C_TOOLS': 'INVENTURE_CAL_TOOLS',
    'INV_C_WYROWNANIE': 'INVENTURE_CAL_ALIGNMENT',
    'INV_D_INPUTS': 'INVENTURE_DIAG_INPUTS',
    'INV_D_IO': 'INVENTURE_DIAG_IO',
    'INV_D_OPERACJE': 'INVENTURE_DIAG_OPERATIONS',
    'INV_D_OPERATIONS': 'INVENTURE_DIAG_OPERATIONS',
    'INV_D_PLANNER': 'INVENTURE_DIAG_PLANNER',
    'INV_D_RESOURCES': 'INVENTURE_DIAG_RESOURCES',
    'INV_D_RFID_CARTRIDGES': 'INVENTURE_DIAG_RFID_CARTRIDGES',
    'INV_D_RFID_KASETY': 'INVENTURE_DIAG_RFID_CARTRIDGES',
    'INV_D_RFID_UART': 'INVENTURE_DIAG_RFID_UART',
    'INV_D_STATUS': 'INVENTURE_DIAG_STATUS',
    'INV_D_VERSIONS': 'INVENTURE_DIAG_VERSIONS',
    'INV_D_WEJSCIA': 'INVENTURE_DIAG_INPUTS',
    'INV_D_WERSJE': 'INVENTURE_DIAG_VERSIONS',
    'INV_D_ZASOBY': 'INVENTURE_DIAG_RESOURCES',
    'INV_F_FANS': 'INVENTURE_FAN_POLICY',
    'INV_F_TEST': 'INVENTURE_TEST_FAN_TEST',
    'INV_F_TEST_STOP': 'INVENTURE_TEST_FAN_TEST_STOP',
    'INV_F_WENTYLATORY': 'INVENTURE_FAN_POLICY',
    'INV_M_CATALOG': 'INVENTURE_MATERIAL_CATALOG',
    'INV_M_CONSUMPTION': 'INVENTURE_MATERIAL_CONSUMPTION',
    'INV_M_FILAMENT': 'INVENTURE_MATERIAL_FILAMENT',
    'INV_M_FILAMENT_CANCEL': 'INVENTURE_TEST_MATERIAL_FILAMENT_CANCEL',
    'INV_M_FILAMENT_LOAD': 'INVENTURE_TEST_MATERIAL_FILAMENT_LOAD',
    'INV_M_FILAMENT_UNLOAD': 'INVENTURE_TEST_MATERIAL_FILAMENT_UNLOAD',
    'INV_M_KATALOG': 'INVENTURE_MATERIAL_CATALOG',
    'INV_M_MATERIALS': 'INVENTURE_MATERIAL_SELECTION',
    'INV_M_MATERIALY': 'INVENTURE_MATERIAL_SELECTION',
    'INV_M_ZUZYCIE': 'INVENTURE_MATERIAL_CONSUMPTION',
    'INV_O_MENU': 'INVENTURE_DISPLAY_MENU',
    'INV_O_REINIT': 'INVENTURE_DISPLAY_REINIT',
    'INV_O_STATUS': 'INVENTURE_DISPLAY_STATUS',
    'INV_O_TEST_TEKST': 'INVENTURE_DISPLAY_TEXT_TEST',
    'INV_O_TEXT_TEST': 'INVENTURE_DISPLAY_TEXT_TEST',
    'INV_P_CHECK': 'INVENTURE_PRINT_CHECK',
    'INV_P_CYCLE': 'INVENTURE_PRINT_CYCLE',
    'INV_P_CYCLE_CANCEL': 'INVENTURE_TEST_PRINT_CYCLE_CANCEL',
    'INV_P_CYCLE_PAUSE': 'INVENTURE_TEST_PRINT_CYCLE_PAUSE',
    'INV_P_CYCLE_RESUME': 'INVENTURE_TEST_PRINT_CYCLE_RESUME',
    'INV_P_CYKL': 'INVENTURE_PRINT_CYCLE',
    'INV_P_INTERLOCKS': 'INVENTURE_PRINT_INTERLOCKS',
    'INV_P_PREFLIGHT': 'INVENTURE_PRINT_PREFLIGHT',
    'INV_P_WERYFIKUJ': 'INVENTURE_PRINT_CHECK',
    'INV_R_HOME_X': 'INVENTURE_TEST_MOTION_HOME_X',
    'INV_R_HOME_XY': 'INVENTURE_TEST_MOTION_HOME_XY',
    'INV_R_HOME_Y': 'INVENTURE_TEST_MOTION_HOME_Y',
    'INV_R_HOME_Z': 'INVENTURE_TEST_MOTION_HOME_Z',
    'INV_R_REFERENCES': 'INVENTURE_MOTION_REFERENCES',
    'INV_R_REFERENCJE': 'INVENTURE_MOTION_REFERENCES',
    'INV_R_SELECT_T0': 'INVENTURE_TEST_MOTION_SELECT_T0',
    'INV_R_SELECT_T1': 'INVENTURE_TEST_MOTION_SELECT_T1',
    'INV_R_WYBIERZ_T0': 'INVENTURE_TEST_MOTION_SELECT_T0',
    'INV_R_WYBIERZ_T1': 'INVENTURE_TEST_MOTION_SELECT_T1',
    'INV_T_TERMIKA': 'INVENTURE_THERMAL_READINGS',
    'INV_T_THERMAL': 'INVENTURE_THERMAL_READINGS',
    'ZORTRAX_DOOR_LOCK': 'INVENTURE_DOOR_LOCK',
    'ZORTRAX_FEED_MORE': 'INVENTURE_FEED_MORE',
    'ZORTRAX_FILAMENT_FINISH': 'INVENTURE_FILAMENT_FINISH',
    'ZORTRAX_FILAMENT_JOG': 'INVENTURE_CASSETTE_JOG',
    'ZORTRAX_FILAMENT_SAVE': 'INVENTURE_FILAMENT_SAVE',
    'ZORTRAX_FILAMENT_STATUS': 'INVENTURE_CASSETTE_STATUS',
    'ZORTRAX_JAM_BROKEN': 'INVENTURE_JAM_BROKEN',
    'ZORTRAX_JAM_CANCEL': 'INVENTURE_JAM_CANCEL',
    'ZORTRAX_JAM_CLEARED': 'INVENTURE_JAM_CLEARED',
    'ZORTRAX_JAM_CONFIRM_FLOW': 'INVENTURE_JAM_CONFIRM_FLOW',
    'ZORTRAX_JAM_CONTINUE': 'INVENTURE_JAM_CONTINUE',
    'ZORTRAX_JAM_EXCHANGE': 'INVENTURE_JAM_EXCHANGE',
    'ZORTRAX_JAM_FLOW': 'INVENTURE_JAM_FLOW',
    'ZORTRAX_JAM_LOAD': 'INVENTURE_JAM_LOAD',
    'ZORTRAX_JAM_MISSING': 'INVENTURE_JAM_MISSING',
    'ZORTRAX_JAM_MORE_IN': 'INVENTURE_JAM_MORE_IN',
    'ZORTRAX_JAM_MORE_OUT': 'INVENTURE_JAM_MORE_OUT',
    'ZORTRAX_JAM_REPORT': 'INVENTURE_JAM_REPORT',
    'ZORTRAX_JAM_RETRY': 'INVENTURE_JAM_RETRY',
    'ZORTRAX_JAM_SERVICE_STEP': 'INVENTURE_JAM_SERVICE_STEP',
    'ZORTRAX_JAM_SERVICE_TEST': 'INVENTURE_JAM_SERVICE_TEST',
    'ZORTRAX_JAM_SOURCE': 'INVENTURE_JAM_SOURCE',
    'ZORTRAX_JAM_STATUS': 'INVENTURE_JAM_STATUS',
    'ZORTRAX_JAM_UNLOAD': 'INVENTURE_JAM_UNLOAD',
    'ZORTRAX_JAM_WIPE': 'INVENTURE_JAM_WIPE',
    'ZORTRAX_LEVEL': 'INVENTURE_LEVEL',
    'ZORTRAX_LEVEL_CANCEL': 'INVENTURE_LEVEL_STOP',
    'ZORTRAX_LEVEL_NEXT': 'INVENTURE_LEVEL_NEXT',
    'ZORTRAX_LEVEL_SAVE': 'INVENTURE_LEVEL_SAVE',
    'ZORTRAX_LEVEL_STATUS': 'INVENTURE_LEVEL_PROGRESS',
    'ZORTRAX_LOAD': 'INVENTURE_LOAD',
    'ZORTRAX_NOZZLE_PAD_CALIBRATE': 'INVENTURE_PAD_CALIBRATE',
    'ZORTRAX_NOZZLE_XY_TEST': 'INVENTURE_NOZZLE_XY',
    'ZORTRAX_NOZZLE_Z_TEST': 'INVENTURE_NOZZLE_Z',
    'ZORTRAX_RETRACT_MORE': 'INVENTURE_RETRACT_MORE',
    'ZORTRAX_UNLOAD': 'INVENTURE_UNLOAD',
}


def register_legacy_commands(printer, gcode):
    """Forward old commands without creating duplicate buttons or overriding a custom macro."""
    grouped = {}
    for old, current in LEGACY_COMMANDS.items():
        grouped.setdefault(current, []).append(old)
    for current, older in grouped.items():
        names = [current, *older]
        for name in names:
            macro = printer.lookup_object('gcode_macro ' + name, None)
            if macro is not None:
                for alias in names:
                    if alias not in gcode.ready_gcode_handlers:
                        gcode.register_command(alias, macro.cmd)
                break


def require_idle(board):
    now = board.reactor.monotonic()
    sd = board.printer.lookup_object('virtual_sdcard', None)
    stats = board.printer.lookup_object('print_stats', None)
    if ((sd is not None and sd.is_active()) or
            (stats is not None and stats.get_status(now).get('state') in ('printing', 'paused'))):
        raise ValueError('This action requires an idle printer')
    ops = board.printer.lookup_object('inventure_operations', None)
    state = getattr(ops, 'state', None)
    if state is not None and (state.owner is not None or state.fatal is not None):
        raise ValueError('The native operation controller is busy or faulted')
    if any(getattr(board, name, None) is not None for name in
           ('load_test_context', 'feeder_run_context', 'nozzle_z_context')):
        raise ValueError('An Inventure service procedure is active')
    jam = getattr(board, 'filament_jam', None)
    if jam is not None and jam.active:
        raise ValueError('Finish the current filament recovery first')


def native_control_state(board):
    state = board._control_query()
    if state['fault'] or state['latched'] or state.get('reject'):
        raise ValueError('MCU blocked native control: fault=%s latched=%s reject=%s'
                         % (state['fault'], state['latched'], state.get('reject')))
    if state['mode'] == 'NATIVE' and state['armed']:
        return state
    require_idle(board)
    if state['mode'] not in ('OFF', 'SERVICE') or state['active']:
        raise ValueError('NATIVE control requires an idle motion controller')
    return state


def select_native_control(board, state):
    if state['mode'] == 'NATIVE':
        return
    gcode = board.printer.lookup_object('gcode')
    command = gcode.create_gcode_command('INVENTURE_CONTROL', 'INVENTURE_CONTROL MODE=NATIVE',
                                          {'MODE': 'NATIVE'})
    board.cmd_control(command)
    current = board._control_query()
    if (current['mode'] != 'NATIVE' or not current['armed'] or current['fault']
            or current['latched'] or current.get('reject')):
        raise ValueError('The MCU did not confirm NATIVE control')


def bind_operator_heaters(board):
    """Select NATIVE on an explicit Fluidd target change for this board's R5 heaters."""
    from functools import partial
    key, handlers = board.gcode.mux_commands.get('SET_HEATER_TEMPERATURE', (None, {}))
    if key != 'HEATER':
        return
    for name, handler in list(handlers.items()):
        heater = getattr(handler, '__self__', None)
        pin = getattr(heater, 'mcu_pwm', None)
        if (getattr(pin, 'board', None) is board and
                pin.name in ('HEATER_T0', 'HEATER_T1', 'HEATER_PC8', 'HEATER_PC9')):
            handlers[name] = partial(_operator_heater_target, board, heater, handler)


def _operator_heater_target(board, heater, handler, gcmd):
    import math
    target = gcmd.get_float('TARGET', 0.)
    if target:
        if not math.isfinite(target) or not heater.min_temp <= target <= heater.max_temp:
            raise gcmd.error('Target is outside the configured heater range')
        try:
            select_native_control(board, native_control_state(board))
        except ValueError as exc:
            raise gcmd.error(str(exc)) from exc
    return handler(gcmd)


def platform_available(board):
    lookup = board.printer.lookup_object
    movement = lookup('inventure_motion', None)
    ops = lookup('inventure_operations', None)
    if ops is not None or movement is not None:
        return bool(getattr(movement, 'enabled', False) and callable(getattr(ops, 'procedure', None)))
    return lookup('inventure_native_z', None) is not None


def move_platform(board, direction, distance, speed):
    import math
    from contextlib import nullcontext
    if direction not in ('up', 'down'):
        raise ValueError('Choose platform up or down')
    if not (math.isfinite(distance) and .1 <= distance <= 5 and math.isfinite(speed) and .1 <= speed <= 5):
        raise ValueError('Platform DISTANCE and SPEED must be 0.1..5 mm and mm/s')
    require_idle(board)
    lookup = board.printer.lookup_object
    head = lookup('toolhead')
    if 'z' not in head.get_status(board.reactor.monotonic()).get('homed_axes', '') or not platform_available(board):
        raise ValueError('Platform movement requires a qualified native homed Z')
    ops = lookup('inventure_operations', None)
    native = lookup('inventure_native_z', None)
    if native is not None and (native.running or getattr(native, 'sequence_running', False)):
        raise ValueError('Wait for native homing to finish before moving the platform')
    if ops is not None:
        ops.require_motion()
        ops.require_position('z')
        scope = ops.procedure('calibration')
    else:
        if not (native.last_result or {}).get('reference_established'):
            raise ValueError('Establish a native pad Z reference before moving the platform')
        native._require_native()
        scope = nullcontext()
    with scope:
        head.wait_moves()
        board._z_home_inputs({'nozzle_contact': 1} if direction == 'up' else {'z_bottom': 0})
        target = list(head.get_position())
        target[2] += -distance if direction == 'up' else distance
        limits = head.get_status(board.reactor.monotonic())
        if not max(0., limits['axis_minimum'][2]) <= target[2] <= limits['axis_maximum'][2]:
            raise ValueError('Platform target exceeds the referenced native Z range')
        token = board._control_next_token() if native is not None else None
        try:
            if token is not None:
                native._verify_guard(1, token, True)
            head.move(target, speed)
            head.wait_moves()
            if token is not None:
                native._verify_guard(2, token, False)
        except Exception:
            if token is not None and not board.printer.is_shutdown():
                board._control_stop_outputs()
            raise
        lookup('gcode_move').reset_last_position()


def calibrate_nozzle_pid(board, gcmd):
    """Use the actual R5 nozzle heater and Klipper's native PID controller."""
    import math
    name = gcmd.get('TOOL', 'T0').upper()
    if name not in ('T0', 'T1'):
        raise ValueError('Choose TOOL=T0 or T1')
    channel = int(name[1])
    heaters = board.printer.lookup_object('heaters')
    matches = []
    for item in heaters.get_all_heaters():
        short = item.split()[-1]
        heater = heaters.lookup_heater(short)
        if getattr(heater.sensor, 'channel', None) == channel and getattr(heater.mcu_pwm, 'name', None) == 'HEATER_'+name:
            matches.append(short)
    if len(matches) != 1:
        raise ValueError('Configure the native R5 heater for '+name+' before tuning')
    target = gcmd.get_float('TARGET', 200., above=0.)
    if not math.isfinite(target):
        raise ValueError('TARGET must be finite')
    board._nozzle_service_cutoff(channel, target)
    require_idle(board)
    select_native_control(board, native_control_state(board))
    board.gcode.run_script_from_command('PID_CALIBRATE HEATER=%s TARGET=%g' % (matches[0], target))
