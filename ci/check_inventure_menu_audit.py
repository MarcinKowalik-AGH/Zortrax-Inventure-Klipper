#!/usr/bin/env python3
"""Render the deployed R24 menu and compare OLED/Fluidd presentation authority."""
import argparse
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT/'tests'), str(ROOT/'ci')]
import inventure_bootstrap as bootstrap
from render_inventure_leveling_preview import png


def check(destination):
    with bootstrap.procedure_profile('operator-home-r24'):
        _, files, metadata = bootstrap._plan(ROOT)
    with tempfile.TemporaryDirectory(prefix='inventure-menu-audit-') as temporary:
        extras = Path(temporary)/'extras'
        for name, (data, mode) in files.items():
            if name.startswith('klippy/extras/'):
                path = Path(temporary)/name.removeprefix('klippy/')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data); path.chmod(mode)
        package = types.ModuleType('audited_menu_runtime')
        package.__path__ = [str(extras)]
        sys.modules[package.__name__] = package
        catalog = importlib.import_module(package.__name__+'.inventure_oem_menu')
        graphics = importlib.import_module(package.__name__+'.inventure_oled_graphics')
        service_module = importlib.import_module(package.__name__+'.inventure_menu_service')
        panel_module = importlib.import_module(package.__name__+'.inventure_oled')
        from test_orange_pi_zortrax_menu import MenuFixture, Config
        from test_orange_pi_oled_fast import Printer, Config as PanelConfig
        board, _ = MenuFixture().host()
        board.gcode.register_command('INVENTURE_ZORTRAX', None)
        service = service_module.ZortraxMenuService(board, Config())
        board.zortrax = service
        panel = panel_module.InventureOLED(PanelConfig(Printer()))
        panel._build_config(); panel.board = board
        board.printer.objects['inventure_oled'] = panel
        nav = panel.oem_menu = catalog.OEMMenu(root_page='launcher', root_back_to_status=False, operational=True)
        board.connected = True
        board.commands['rgb'] = types.SimpleNamespace(send=lambda *a, **k: None)
        board.commands['oled_sleep_light'] = types.SimpleNamespace(send=lambda *a, **k: None)
        board.nozzle_xy_available = lambda: True
        rows, dynamic, captures, seen = [], [], {}, set()

        def inspect(view, label):
            icon = view.get('icon')
            assert icon, 'Missing icon: '+label
            record = graphics._symbol(icon)
            count = len(record['frames']) if record else int(icon in graphics.native.get_oem_assets().icons)
            assert count, 'Unknown icon: '+repr((label, icon))
            frames = [graphics.menu_wire(view, index=i) for i in range(count)]
            assert all(len(frame) == 8192 for frame in frames), label
            if icon.startswith(('project:', 'home:')):
                assert view['animated'], 'Animation is disabled: '+label
                assert len(set(frames)) == 4, label
            captures[label] = frames[0]
            return dict(label=label, icon=icon, frames=count, animated=bool(view.get('animated') and count>1),
                        frame_sha256=[sha256(frame).hexdigest() for frame in frames])

        pending = ['launcher', 'pause', 'pause_material', 'stop_confirm', 'cancel', 'question']
        while pending:
            page = pending.pop(0)
            if page in seen:
                continue
            seen.add(page); nav.open_page(page)
            for index, node in enumerate(nav.items()):
                if node.child:
                    pending.append(node.child)
                if index:
                    nav.move(1)
                physical = nav.view()
                result = inspect(physical, page+' / '+node.label)
                service.open_operator_page(); service.view.selected = index
                shared = service.view_status()
                assert shared['labels'] == physical['labels'], (page, node.label)
                assert shared['icon'] == physical['icon'], (page, node.label)
                assert graphics.menu_wire(shared) == graphics.menu_wire(physical), (page, node.label)
                result.update(page=page, action=node.action, child=node.child, oled_fluidd_same=True)
                rows.append(result)
            # Encoder navigation of an already shared page must move the same
            # cursor/window and replace its token, including around a wrap.
            nav.selected = nav.window = 0
            service.open_operator_page()
            for _ in range(len(nav.items())+2):
                previous_token = service.view.token
                service.panel_move(1)
                physical, shared = nav.view(), service.view_status()
                assert service.view.token != previous_token, page
                assert graphics.menu_wire(shared) == graphics.menu_wire(physical), page
        original = json.loads((ROOT/'docs/validation/INVENTURE_OEM_MENU_R0323.json').read_bytes())
        assert set(catalog.OEM_RECORD_BINDINGS) == {x['address'] for x in original}
        # Every original child remains present in its original relative order.
        for entry in original:
            binding = catalog.OEM_RECORD_BINDINGS[entry['address']]
            if binding['kind'] != 'page':
                continue
            keys = [('page', n.child) if n.child else ('action', n.action) if n.action else ('caption', n.label)
                    for n in catalog.MENU[binding['target']][2]]
            position = 0
            for child in entry['children']:
                item = catalog.OEM_RECORD_BINDINGS[child]
                if item['kind'] == 'spacer':
                    continue
                position = keys.index((item['kind'], item['target']), position)+1
        service.operator_page_open = service.menu_dialog_requested = False
        for action in ('maintenance_t0', 'maintenance_t1', 'heat', 'move_platform', 'fan_test',
                       'spool_catalog_model', 'spool_catalog_support', 'light', 'oled_timers',
                       'oled_brightness', 'oled_sleep_mode', 'oled_sleep_light', 'help', 'version',
                       'printer_info', 'language_pl', 'clean_t0', 'purge_t0', 'select_t0', 'heat_t0',
                       'clean_t1', 'purge_t1', 'select_t1', 'heat_t1', 'alignment'):
            service.open(action)
            for index, (label, _) in enumerate(service.view.options):
                service.view.selected = index
                dynamic.append(inspect(service.view_status(), action+' / '+label))
        for channel in range(6):
            service._fan_page(channel)
            for index, (label, _) in enumerate(service.view.options):
                service.view.selected = index
                dynamic.append(inspect(service.view_status(), 'fan_%d / %s' % (channel, label)))
        assert board.load_test_context is None, 'A menu audit started an actuator owner'
        destination.mkdir(parents=True, exist_ok=True)
        selected = ['root / Maintenance', 'maintenance / Home XY', 'model_material / Unload the model material',
                    'support_material / Unload the support material', 'load_support / Load from cartridge',
                    'pause / Resume the print job', 'maintenance_t0 / Clean nozzle', 'heat / Model nozzle T0',
                    'fan_test / F0 '+board.FAN_NAMES[0], 'light / White', 'version / Back', 'alignment / Yes / Start']
        gallery = []
        for offset in range(0, len(selected), 2):
            for y in range(64):
                gallery.append(b''.join(captures[key][y*128:(y+1)*128] for key in selected[offset:offset+2]))
        (destination/'INVENTURE_MENU_AUDIT.png').write_bytes(png(b''.join(gallery), scale=2, width=512, height=384))
        report = dict(status='PASS_INSTALLED_MENU_AUDIT', original_records=len(original), pages=len(seen),
                      static_positions=len(rows), dynamic_positions=len(dynamic), rows=rows, dynamic=dynamic,
                      missing_icons=0, disabled_project_animations=0,
                      menu_audit_release=service.get_status(0.)['menu_audit_release'],
                      runtime_restoration=metadata['runtime_restorations']['menu_audit_r24'],
                      hardware_tested=False, serial_opened=False)
        (destination/'INVENTURE_MENU_AUDIT.json').write_text(json.dumps(report, indent=2)+'\n')
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    report = check(parser.parse_args().destination)
    print(json.dumps({k: report[k] for k in ('status', 'original_records', 'pages', 'static_positions',
                                           'dynamic_positions', 'missing_icons', 'disabled_project_animations')}))
