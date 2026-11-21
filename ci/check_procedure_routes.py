"""Check the installed operator macros against the running Klipper command registry."""
from unittest.mock import patch


def exercise_home_confirmation(gcode):
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root), str(root/'tests')]
    from test_orange_pi_native_z import NativeHomeSequenceTests

    def extended(method):
        return lambda cmd: method(gcode._get_extended_params(cmd))

    cases = [('ALL', '', mode) for mode in ('OFF', 'SERVICE', 'NATIVE')]
    cases.extend([('Z', '', 'OFF'), ('Z', 'x', 'SERVICE'), ('Z', 'xy', 'NATIVE')])
    for button, homed, mode in cases:
        rig = NativeHomeSequenceTests()
        rig.setUp()
        rig.use_mcu_reference()
        rig.model.axes = homed
        rig.model.native_mode = mode == 'NATIVE'
        rig.model.inactive_mode = mode
        menu, lines = rig.operator_menu()
        handlers = dict(gcode.ready_gcode_handlers,
                        INVENTURE_HOME_REQUEST=extended(rig.adapter.cmd_request_home),
                        INVENTURE_ZORTRAX=extended(menu.cmd))
        with patch.object(gcode, 'gcode_handlers', handlers), patch.object(gcode, 'is_printer_ready', True):
            gcode.run_script_from_command('INVENTURE_HOME_' + button)
            assert rig.model.calls == [] and rig.model.mode_requests == []
            accept, = [line.split('|', 1)[1] for line in lines if 'Yes / Start|' in line]
            gcode.run_script_from_command(accept)
            assert menu.view.title == ('Home XYZ complete' if button == 'ALL' else 'Home Z complete')
            expected_axes = ['Z'] if button == 'Z' and homed == 'xy' else ['Z', 'Y', 'X']
            assert [entry[1] for entry in rig.model.calls if entry[0] == 'G28'] == expected_axes
            assert len([entry for entry in rig.model.calls if entry[0] == 'refine' and entry[2]]) == 1
            assert rig.adapter.last_result['coordinate_saved']
            assert rig.model.get_position()[2] == rig.adapter.park_z
            if button == 'Z' and not homed:
                # The preceding operation left valid XY. The physical menu uses that reference.
                rig.model.calls.clear()
                menu.open('home_z')
                menu.panel_move(1)
                gcode.run_script_from_command(menu.panel_select_command(acknowledged=True))
                assert menu.view.title == 'Home Z complete'
                assert [entry[1] for entry in rig.model.calls if entry[0] == 'G28'] == ['Z']
                assert rig.adapter.last_result['coordinate_saved']
    print('Home Z and All from installed macros through confirmation to STM32 refinement and park (simulated MCU)')


def audit_routes(printer):
    gcode = printer.lookup_object('gcode')
    handlers = gcode.ready_gcode_handlers
    now = printer.get_reactor().monotonic()
    config = printer.lookup_object('configfile').get_status(now)
    macros = {name.split(' ', 1)[1].upper(): obj for name, obj in printer.lookup_objects('gcode_macro')
              if name.startswith('gcode_macro ')}
    groups = {name for name in macros if not name.startswith('_')}
    targets = set(handlers)
    # Klipper installs rename_existing aliases at connect, after this config-only startup.
    targets.update(sec['rename_existing'].upper() for sec in config['config'].values()
                   if 'rename_existing' in sec)
    actions = 'RETRY CONFIRM BROKEN CANCEL UNLOAD MORE_OUT CLEARED LOAD MORE_IN FLOW WIPE CONTINUE SOURCE EXCHANGE'.split()
    machine = dict(
        configfile=dict(settings=config['settings'], save_config_pending_items={'extruder': dict(
            pid_kp='12', pid_ki='1', pid_kd='4')}),
        gcode=dict(commands={key: {} for key in targets}), print_stats=dict(state='printing'),
        heaters=dict(available_heaters=printer.lookup_object('heaters').get_all_heaters()),
        bed_mesh=dict(profiles={'audit': {}}),
        inventure_mcu=dict(material_service=dict(tool='T1', session=11, prompt=12),
            platform_level=dict(phase='candidate', run_id='level-run', prompt_id=2, awaiting_operator=True),
            filament_jam=dict(active=True, allowed_actions=actions, run_id='jam-run', prompt_id=3)))
    examples = dict(TOOL='T1', SOURCE='CASSETTE', TARGET='235', STEP='LOAD', TOKEN='audit', INDEX='1',
                    LOAD_TEMP='235', ACK='START_CHECKED_JOB', MANIFEST='audit', CHANNEL='1')
    routed = {}
    failures = []
    idle = dict(machine, print_stats={'state':'standby'}, configfile=dict(machine['configfile'],
        save_config_pending_items={}), inventure_mcu=dict(material_service=dict(tool=None, session=None, prompt=None),
        platform_level=dict(phase='idle', run_id=None, prompt_id=None, awaiting_operator=False),
        filament_jam=dict(active=False, allowed_actions=['SOURCE'], run_id=None, prompt_id=None)))
    idle_blocks = {}
    for name in sorted(groups):
        emitted = []
        context = macros[name].template.create_template_context()
        context.update(printer=machine, params=examples, rawparams='',
                       action_respond_info=lambda msg: emitted.append(msg) or '')
        try:
            script = macros[name].template.render(context)
        except Exception as exc:
            failures.append((name, str(exc)))
            continue
        cmds = [line.strip().split()[0].upper() for line in script.splitlines()
                if line.strip() and not line.lstrip().startswith(('#', ';'))]
        missing = set(cmds) - targets
        if missing or not (cmds or emitted):
            failures.append((name, sorted(missing) or 'empty route'))
        routed[name] = cmds
        reasons = []
        bare = macros[name].template.render(dict(context, printer=idle, params={}, rawparams='',
            action_raise_error=lambda message: reasons.append(message) or ''))
        if reasons:
            idle_blocks[name] = reasons
        else:
            idle_targets = [line.strip().split()[0].upper() for line in bare.splitlines()
                            if line.strip() and not line.lstrip().startswith(('#',';'))]
            if set(idle_targets) - targets:
                failures.append((name, 'idle', sorted(set(idle_targets) - targets)))
            if name == 'INVENTURE_CHANGE':
                assert bare.strip() == 'INVENTURE_ZORTRAX OP=EXCHANGE_STEPS'
    for name in ('INVENTURE_HOME_ALL','INVENTURE_LEVEL','INVENTURE_LOAD','INVENTURE_UNLOAD',
                 'INVENTURE_CHANGE','INVENTURE_HELP','INVENTURE_MODELS'):
        assert name in routed and name not in idle_blocks, name
    assert not failures, failures
    # Run the literal command sent by Fluidd through Klipper's tokenizer, macro
    # handler, Jinja engine and extended-command parser. Capture at the hardware boundary.
    seen = []
    operator = 'INVENTURE_HOME_REQUEST'
    assert operator in handlers
    def capture(command):
        seen.append(dict(gcode._get_extended_params(command).get_command_parameters()))
    with (patch.object(gcode, 'gcode_handlers', dict(handlers, **{operator: capture})),
          patch.object(gcode, 'is_printer_ready', True)):
        for button, axes in (('X','X'), ('Y','Y'), ('XY','XY'), ('Z','Z'), ('ALL','XYZ')):
            name = 'INVENTURE_HOME_'+button
            if name == 'INVENTURE_HOME_XY' and name not in macros:
                continue  # The optional motion module owns this command.
            gcode.run_script_from_command(name)
            assert seen[-1] == {'AXES':axes}, (name, seen)
        gcode.run_script_from_command('INVENTURE_HOME_ALL TARGET=205 PARK_Z=8')
        assert seen[-1] == {'AXES':'XYZ','TARGET':'205','PARK_Z':'8'}
    from extras.inventure_fluidd import bind_operator_heaters
    board = printer.lookup_object('inventure_mcu')
    heaters = printer.lookup_object('heaters')
    bind_operator_heaters(board)
    mux = gcode.mux_commands['SET_HEATER_TEMPERATURE'][1]
    wrapped = dict(mux)
    bind_operator_heaters(board)
    assert wrapped == mux
    healthy = dict(mode='NATIVE', active=1, armed=1, fault=0, latched=0, reject=None)
    with (patch.object(gcode, 'gcode_handlers', handlers),
          patch.object(board, '_control_query', return_value=healthy) as control,
          patch.object(heaters, 'set_temperature') as set_target):
        for key in ('extruder','inventure_t1','chamber_l','chamber_r'):
            heater = heaters.lookup_heater(key)
            gcode.run_script_from_command('SET_HEATER_TEMPERATURE HEATER='+key+' TARGET=40')
            set_target.assert_called_with(heater, 40.)
        assert control.call_count == 4
        control.side_effect = AssertionError('Turning off a heater must work in every control mode')
        gcode.run_script_from_command('SET_HEATER_TEMPERATURE HEATER=inventure_t1 TARGET=0')
        set_target.assert_called_with(heaters.lookup_heater('inventure_t1'), 0.)
    print('Operator routes:', len(routed), 'installed macros,',
          len({cmd for cmds in routed.values() for cmd in cmds}), 'distinct command targets')
    print('Idle template guards:', ', '.join(sorted(idle_blocks)))
    exercise_home_confirmation(gcode)
    from extras.inventure_oem_menu import MENU, OEMMenu
    nav = OEMMenu('en', root_page='launcher', bench=True, operational=True)
    actions = set()
    for page in MENU:
        nav.page = page
        for item in nav.items():
            if item.child:
                assert item.child in MENU, (page, item)
            if item.action:
                actions.add(item.action)
                if not item.action.startswith('bench_'):
                    info = board.zortrax.capability(item.action)
                    assert info['reason'] != 'Unknown menu action', (page, item)
                    assert info['available'] or info['reason'], (page, item)
    print('OLED menu:', len(MENU), 'pages,', len(actions), 'distinct actions including bench controls')
