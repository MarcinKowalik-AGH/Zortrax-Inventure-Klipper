"""Literal Fluidd macros and physical OLED choices after accepted R24 Home."""
import importlib


def inspect_shared_menu(rig, panel, package, render_running):
    from check_inventure_full_cycles import require
    material = importlib.import_module(package+'.inventure_material_service')
    board, menu = rig.board, rig.menu
    board.nozzle_service_target_max = 310.
    board.material_service = material.MaterialService(board)
    board.material_service.profiles = {
        'T0': dict(load_temp=215., unload_temp=220., load_speed=2., unload_speed=3.),
        'T1': dict(load_temp=225., unload_temp=230., load_speed=4., unload_speed=5.)}
    for name, handler in (('INVENTURE_MATERIAL', board.material_service.command),
                          ('INVENTURE_PROCEDURE', board.cmd_procedure),
                          ('INVENTURE_STOP', board.cmd_control_stop)):
        rig.gcode.register_command(name, None)
        rig.gcode.register_command(name, handler)

    def choose(label):
        view = menu.view
        view.selected = next(i for i, (text, _) in enumerate(view.options) if text == label)
        view.released = True
        rig.run(menu.panel_select_command(acknowledged=True))

    def physical(page, label):
        panel.oem_menu.open_page(page); panel.oem_display_mode = 'menu'
        panel.oem_menu.selected = next(i for i, node in enumerate(panel.oem_menu.items()) if node.label == label)
        panel._select(panel.reactor.monotonic())

    cases = []
    commands = {'SELECT': 'INVENTURE_NOZZLE_SELECT', 'HEAT': 'INVENTURE_HEAT',
                'CLEAN': 'INVENTURE_CLEAN', 'PURGE': 'INVENTURE_NOZZLE_PURGE',
                'LOAD': 'INVENTURE_LOAD', 'UNLOAD': 'INVENTURE_UNLOAD'}
    labels = {'SELECT': 'Select nozzle', 'HEAT': 'Heat / hold', 'CLEAN': 'Clean nozzle',
              'PURGE': 'Purge and clean', 'LOAD': 'Load from cartridge'}
    for tool in (0, 1):
        for operation, command in commands.items():
            signatures = []
            for origin in ('fluidd', 'oled'):
                menu.view = None; menu.operator_page_open = menu.menu_dialog_requested = False
                # Independent attempts begin with a cooled physical plant.
                # This fixture has no passive cooling model between attempts.
                board.temperatures[:2] = [25., 25.]
                rig.model.heater_target = 0.
                for heater in rig.model.heaters.values():
                    heater.set_temp(0.)
                rig.run('INVENTURE_HOME_ALL')
                require(rig.native.last_result.get('coordinate_saved'), 'R24 Home did not precede the menu procedure')
                if origin == 'fluidd':
                    rig.run(command+' TOOL=T%d' % tool)
                elif operation == 'LOAD':
                    physical('load_support' if tool else 'load_model', 'Load from cartridge')
                elif operation == 'UNLOAD':
                    physical('support_material' if tool else 'model_material',
                             'Unload the %s material' % ('support' if tool else 'model'))
                else:
                    physical('heads', ('Right hotend maintenance (support)' if tool else
                                       'Left hotend maintenance (model)'))
                    choose(labels[operation])
                if menu.view is not None and menu.view.kind == 'confirm':
                    choose('Yes / Start')
                context = board.load_test_context
                require(context is not None, 'The shared menu did not acquire its service owner')
                factory = context.get('factory') or {}
                signatures.append(dict(tool=context['tool'], target=context['target'],
                                       operation=factory.get('operation', 'HEAT'), parameters=factory.get('parameters'),
                                       interactive=bool(factory.get('interactive_material')),
                                       manual_speeds=factory.get('manual_speeds')))
                require(board._control_query()['mode'] == 'SERVICE', 'Ordinary service did not use its original handoff')
                before = len(board.device.packets)
                board.reactor.pause(board.reactor.monotonic()+.25)
                require(len(board.device.packets) > before, 'The owner never reached the MCU interface')
                menu._tick(board.reactor.monotonic())
                render_running()
                old_token = menu.view.token
                if origin == 'fluidd':
                    rig.run('INVENTURE_STOP'); menu._tick(board.reactor.monotonic())
                else:
                    menu.panel_back(); choose('Yes / stop')
                require(board.load_test_context is None and not board.device.active(), 'Cancel retained the actuator owner')
                try:
                    menu.choose(old_token, confirm=True)
                except ValueError:
                    pass
                else:
                    raise AssertionError('A cancelled menu token was accepted')
                cases.append(dict(operation=operation, tool=tool, origin=origin,
                                  after_r24_home=True, mcu_interface_reached=True, cancellation=True, stale_token_refused=True))
            require(signatures[0] == signatures[1], 'Fluidd/OLED owner parameters differ: '+repr((operation, tool)))
    # Compare the new alignment macro with the corresponding physical entry.
    # Stop at review: printing/contact engines have their independent tests.
    board.nozzle_xy_available = lambda: True
    views = []
    for origin in ('fluidd', 'oled'):
        menu.view = None
        if origin == 'fluidd':
            rig.run('INVENTURE_NOZZLE_ALIGNMENT')
        else:
            physical('maintenance', 'Nozzle alignment calibration')
        require(menu.view is not None and menu.view.kind == 'confirm', 'Alignment entry lost its review')
        views.append(dict(title=menu.view.title, options=[x[0] for x in menu.view.options],
                          parameters=menu.view.data['parameters']))
        choose('No / Back')
    require(views[0] == views[1], 'Alignment macro and OLED review differ')
    return dict(status='PASS_R24_SHARED_MENU_OWNERS', starts=len(cases), paired_cases=len(cases)//2,
                cases=cases, alignment_review_same=True, oled_during_thermal_hold=len(cases),
                physical_interfaces='simulated', hardware_tested=False)
