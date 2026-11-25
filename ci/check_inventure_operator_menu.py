"""Compare literal installed Fluidd macros with physical OLED selections.

The staged Klipper parser, macros, menu and procedure owners are real. Panel,
MCU, motion and heater interfaces are deterministic fixtures; no serial opens.
"""
import importlib
from pathlib import Path
import sys


def inspect_operator_menu(printer, klipper):
    from check_inventure_full_cycles import InstalledRig, require
    from test_orange_pi_oled_fast import Printer as PanelPrinter, Config, ready_stream
    package = type(printer.lookup_object('inventure_mcu')).__module__.rsplit('.', 1)[0]
    panel_module = importlib.import_module(package+'.inventure_oled')
    catalog = importlib.import_module(package+'.inventure_oem_menu')
    material_module = importlib.import_module(package+'.inventure_material_service')

    def rig():
        result = InstalledRig(printer, klipper)
        board = result.board
        board.nozzle_service_target_max = 310.
        board.material_service = material_module.MaterialService(board)
        # Exercise persisted per-tool choices without writing calibration state.
        board.material_service.profiles = {
            'T0': dict(load_temp=215., unload_temp=220., load_speed=2., unload_speed=3.),
            'T1': dict(load_temp=225., unload_temp=230., load_speed=4., unload_speed=5.)}
        panel = panel_module.InventureOLED(Config(PanelPrinter()))
        panel._build_config()
        require(Path(sys.modules[type(panel).__module__].__file__).resolve()
                == klipper/'klippy/extras/inventure_oled.py', 'OLED is not the installed module')
        panel.board, panel.printer, panel.reactor = board, board.printer, board.reactor
        panel.connected, panel.stream = True, ready_stream()
        panel._script = result.run
        panel.oem_display_mode = 'status'
        panel.oem_menu = catalog.OEMMenu(root_page='launcher', root_back_to_status=False,
                                        operational=True)
        result.panel = panel
        board.printer.objects['inventure_oled'] = panel
        for command, handler in (
                ('INVENTURE_DISPLAY_CONTROL', panel.cmd_display_control),
                ('INVENTURE_MATERIAL', board.material_service.command),
                ('INVENTURE_PROCEDURE', board.cmd_procedure),
                ('INVENTURE_STOP', board.cmd_control_stop)):
            result.gcode.register_command(command, None)
            result.gcode.register_command(command, handler)
        return result

    def choose(result, label, origin):
        view = result.menu.view
        index = next(i for i, (name, _) in enumerate(view.options) if name == label)
        if origin == 'fluidd':
            command = next(line.split('|', 1)[1].split('|', 1)[0]
                           for line in reversed(result.lines)
                           if ('action:prompt_button '+label+'|') in line)
        else:
            view.selected, view.released = index, True
            command = result.menu.panel_select_command(acknowledged=True)
        result.run(command)

    def physical(result, page, label):
        panel = result.panel
        panel.oem_menu.open_page(page)
        panel.oem_display_mode = 'menu'
        panel.oem_menu.selected = next(i for i, node in enumerate(panel.oem_menu.items())
                                      if node.label == label)
        panel._select(panel.reactor.monotonic())

    def signature(result):
        context = result.board.load_test_context
        require(context is not None, 'The selected procedure did not acquire its owner')
        factory = context.get('factory') or {}
        return dict(tool=context['tool'], target=context['target'],
                    operation=factory.get('operation', 'HEAT'),
                    parameters=factory.get('parameters'),
                    interactive=bool(factory.get('interactive_material')),
                    manual_speeds=factory.get('manual_speeds'))

    pages = []
    for macro, page in (('INVENTURE_MAIN_MENU', 'root'),
                        ('INVENTURE_KLIPPER_MENU', 'klipper_main'),
                        ('INVENTURE_MAINTENANCE', 'maintenance'),
                        ('INVENTURE_MATERIAL_MENU', 'material'),
                        ('INVENTURE_SETTINGS', 'settings'),
                        ('INVENTURE_INFORMATION', 'information'),
                        ('INVENTURE_LANGUAGE', 'language'),
                        ('INVENTURE_MODELS_SORT', 'sorting')):
        result = rig()
        result.run(macro)
        require(result.panel.oem_menu.page == page, macro+' opened another OLED page')
        require(result.menu.view.data['operator_page'] == page,
                macro+' opened a different Fluidd prompt')
        require([name for name, _ in result.menu.view.options]
                == result.panel.oem_menu.view()['labels'], macro+' changed the shared rows')
        require(not result.board.device.packets, macro+' actuated a device')
        pages.append(dict(macro=macro, page=page, labels=result.panel.oem_menu.view()['labels']))

    # Both paths start on a fresh host state and again after a completed Home.
    procedures = []
    for after_home in (False, True):
        for tool in (0, 1):
            for verb, label in (('SELECT', 'Select nozzle'), ('HEAT', 'Heat / hold'),
                                ('CLEAN', 'Clean nozzle'), ('PURGE', 'Purge and clean'),
                                ('LOAD', 'Load from cartridge'), ('UNLOAD', 'Unload')):
                pairs = []
                for origin in ('fluidd', 'oled'):
                    result = rig()
                    if after_home:
                        result.home('XYZ', 'fluidd')
                        result.menu.view = None
                        result.model.heater_target = 0.
                        for heater in result.model.heaters.values():
                            heater.set_temp(0.)
                    before = len(result.board.device.packets)
                    result.lines.clear()
                    if origin == 'fluidd':
                        macro = {'SELECT': 'INVENTURE_NOZZLE_SELECT', 'HEAT': 'INVENTURE_HEAT',
                                 'CLEAN': 'INVENTURE_CLEAN', 'PURGE': 'INVENTURE_NOZZLE_PURGE',
                                 'LOAD': 'INVENTURE_LOAD', 'UNLOAD': 'INVENTURE_UNLOAD'}[verb]
                        result.run(macro+' TOOL=T%d' % tool)
                    elif verb == 'LOAD':
                        physical(result, 'load_support' if tool else 'load_model', label)
                    elif verb == 'UNLOAD':
                        physical(result, 'support_material' if tool else 'model_material',
                                 'Unload the %s material' % ('support' if tool else 'model'))
                    else:
                        physical(result, 'heads',
                                 ('Right hotend maintenance (support)' if tool else
                                  'Left hotend maintenance (model)'))
                        choose(result, label, 'oled')
                    if result.menu.view is not None and result.menu.view.kind == 'confirm':
                        require(len(result.board.device.packets) == before,
                                'A menu action actuated before confirmation')
                        choose(result, 'Yes / Start', origin)
                    pairs.append(signature(result))
                    require(result.board._control_query()['mode'] == 'SERVICE',
                            'The procedure did not use the existing SERVICE owner')
                    # Run one real owner tick into the simulated MCU interface.
                    # Fluidd has the installed STOP button; OLED uses its
                    # token-bearing cancellation confirmation.
                    result.board.reactor.pause(result.board.reactor.monotonic()+.25)
                    require(len(result.board.device.packets) > before,
                            'The procedure never reached the simulated MCU interface')
                    result.menu._tick(result.board.reactor.monotonic())
                    if origin == 'fluidd':
                        result.run('INVENTURE_STOP')
                    else:
                        result.menu.panel_back()
                        choose(result, 'Yes / stop', origin)
                    require(result.board.load_test_context is None,
                            'Cancellation retained the service owner')
                    require(not result.board.device.active(), 'Cancellation left an active output')
                require(pairs[0] == pairs[1], '%s T%d differs between Fluidd and OLED' % (verb, tool))
                procedures.append(dict(operation=verb, tool=tool, after_home=after_home,
                                       status='PASS', signature=pairs[0],
                                       simulated_mcu_reached=True, cancel_verified=True))

    refusals = []
    for origin in ('fluidd', 'oled'):
        result = rig()
        result.model.print_state = 'printing'
        before = list(result.board.device.packets)
        if origin == 'fluidd':
            result.run('INVENTURE_CLEAN TOOL=T0')
        else:
            physical(result, 'heads', 'Left hotend maintenance (model)')
            choose(result, 'Clean nozzle', origin)
        refusal = None
        try:
            choose(result, 'Yes / Start', origin)
        except Exception as exc:
            refusal = str(exc)
        require(refusal and 'idle' in refusal.lower(), 'Printing interlock was not refused')
        require(result.board.device.packets == before, 'Refusal reached an actuator')
        refusals.append(dict(origin=origin, condition='printing', status='PASS', reason=refusal))

    # Old buttons cannot target a newly opened page after navigation changes.
    result = rig()
    result.run('INVENTURE_MATERIAL_MENU')
    old = next(line.split('|', 1)[1] for line in result.lines
               if 'action:prompt_button Model|' in line)
    result.run('INVENTURE_MAINTENANCE')
    rejection = None
    try:
        result.run(old)
    except Exception as exc:
        rejection = str(exc)
    require(rejection and any(s in rejection.lower() for s in ('expired', 'token', 'stale')),
            'A stale Fluidd page button was accepted')
    require(not result.board.device.packets, 'A stale page button actuated a device')

    return dict(status='PASS_INSTALLED_OLED_FLUIDD_MENU_PARITY', pages=pages,
                procedures=procedures, refusal_cases=refusals, stale_button_rejected=True,
                original_records=66, launcher=['Klipper', 'Zortrax Inventure'],
                interface_language='en', physical_interfaces='simulated', hardware_tested=False)
