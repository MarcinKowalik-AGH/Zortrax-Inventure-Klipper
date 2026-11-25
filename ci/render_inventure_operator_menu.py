"""Render every installed Inventure operator page and its selected-row icons."""
import argparse
from hashlib import sha256
import importlib
import json
from pathlib import Path
import sys
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import inventure_bootstrap as bootstrap
from render_inventure_leveling_preview import png


def render(destination):
    _, files, metadata = bootstrap._plan(ROOT)
    with tempfile.TemporaryDirectory(prefix='inventure-operator-menu-preview-') as temp:
        extras = Path(temp)/'extras'
        for name, (data, _) in files.items():
            if name.startswith('klippy/extras/'):
                path = Path(temp)/name.removeprefix('klippy/')
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        package = types.ModuleType('operator_menu_preview')
        package.__path__ = [str(extras)]
        sys.modules[package.__name__] = package
        graphics = importlib.import_module(package.__name__+'.inventure_oled_graphics')
        catalog = importlib.import_module(package.__name__+'.inventure_oem_menu')
        nav = catalog.OEMMenu(root_page='launcher', root_back_to_status=False, operational=True)
        destination.mkdir(parents=True, exist_ok=True)
        pages, frames, captures, pending = {}, {}, {}, ['launcher', 'pause', 'pause_material', 'stop_confirm']
        while pending:
            page = pending.pop(0)
            if page in pages:
                continue
            nav.open_page(page)
            items = nav.items()
            pages[page] = dict(title=nav.view()['title'], rows=[dict(label=n.label, child=n.child,
                action=n.action, icon=nav.item_icon(n)) for n in items])
            pending.extend(node.child for node in items if node.child)
            for index in range(len(items)):
                nav.selected = index
                view = nav.view()
                wire = graphics.menu_wire(view)
                if len(wire) != 8192:
                    raise ValueError('Invalid physical framebuffer for '+page)
                name = '%s_%02d.png' % (page, index)
                (destination/name).write_bytes(png(wire))
                captures[(page, index)] = wire
                frames[name] = dict(wire_bytes=len(wire), wire_sha256=sha256(wire).hexdigest())
        selected = [('launcher', 0), ('launcher', 1), ('root', 1), ('root', 2),
                    ('maintenance', 1), ('heads', 2), ('material', 1), ('load_support', 1),
                    ('settings', 1), ('doorlock', 1), ('language', 1), ('sorting', 1),
                    ('information', 1), ('klipper_main', 4), ('pause', 1), ('pause_material', 1)]
        rows = []
        for offset in range(0, len(selected), 2):
            for y in range(64):
                rows.append(b''.join(captures[key][y*128:(y+1)*128]
                                     for key in selected[offset:offset+2]))
        gallery = 'OLED_ZORTRAX_INVENTURE_MENU.png'
        (destination/gallery).write_bytes(png(b''.join(rows), scale=2, width=512, height=512))
        original = json.loads((ROOT/'docs/validation/INVENTURE_OEM_MENU_R0323.json').read_text())
        if set(catalog.OEM_RECORD_BINDINGS) != {row['address'] for row in original}:
            raise ValueError('The original firmware menu inventory is incomplete')
        proof = dict(status='PASS_INSTALLED_OEM_OPERATOR_MENU_RENDER',
                     ui_release=graphics.LEVELING_RENDERER_RELEASE,
                     launcher=[n.label for n in catalog.MENU['launcher'][2]],
                     zortrax_title=catalog.MENU['root'][0],
                     original_record_count=len(original), original_bindings=catalog.OEM_RECORD_BINDINGS,
                     restoration=metadata['runtime_restorations']['configuration_audit_r23'],
                     pages=pages, frames=frames, gallery=gallery,
                     physical_interfaces='not_accessed', hardware_tested=False)
        (destination/'INVENTURE_R23_OPERATOR_MENU_PREVIEW.json').write_text(json.dumps(proof, indent=2)+'\n')
        return proof


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    proof = render(parser.parse_args().destination)
    print(proof['status'], len(proof['pages']), 'pages,', len(proof['frames']),
          'selections;', proof['original_record_count'], 'original records; no hardware accessed')
