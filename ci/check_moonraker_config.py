#!/usr/bin/env python3
"""Read the migrated files with Moonraker's deployed configuration parser."""
from pathlib import Path
import importlib.util
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from moonraker.confighelper import FileSourceWrapper
from moonraker.components.file_manager.file_manager import FileManager

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from inventure_config_layout import organize_moonraker


def check():
    with TemporaryDirectory() as temp:
        folder = Path(temp)
        web = folder/'fluidd'
        main = folder/'moonraker.conf'
        access = folder/'access.conf'
        main.write_text('[server]\nhost: 127.0.0.1\nport: 7125\n[include access.conf]\n'
                        '[update_manager fluidd]\ntype: web\nrepo: fluidd-core/fluidd\npath: '+str(web)+'\n'
                        '[update_manager operator]\ntype: git_repo\norigin: https://example.test/project.git\n')
        access.write_text('[authorization]\ntrusted_clients:\n    127.0.0.1\n    127.0.0.1\n'
                          '    192.168.1.0/24\ncors_domains:\n    http://printer.local\n')
        src = FileSourceWrapper(SimpleNamespace())
        src.read_file(main)
        orig = {name: dict(src.config.items(name)) for name in src.config.sections()}
        changes = {}
        assert organize_moonraker(main, [], web, changes) == ['update_manager fluidd']
        for path, (_, data) in changes.items():
            path.write_bytes(data)
        src.read_file(main)
        after = {name: dict(src.config.items(name)) for name in src.config.sections()}
        assert orig.pop('update_manager fluidd')['path'] == str(web)
        assert orig['authorization']['trusted_clients'].split() == ['127.0.0.1','127.0.0.1','192.168.1.0/24']
        assert after['authorization']['trusted_clients'].split() == ['127.0.0.1','192.168.1.0/24']
        orig['authorization']['trusted_clients'] = after['authorization']['trusted_clients']
        assert after == orig
        assert set(src.get_files()) == {main, access}
        print('PASS Moonraker readback: server, CORS, effective trusted clients, custom updater and includes')

        spec = importlib.util.spec_from_file_location('inventure_update', Path(__file__).resolve().parents[1]/'install-orange-pi.py')
        updater = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(updater)
        warnings, handlers = [], {}
        manager = FileManager.__new__(FileManager)
        manager.file_paths = {}
        manager.full_access_roots = set()
        manager.server = SimpleNamespace(add_warning=warnings.append, is_running=lambda:False,
                                         register_static_file_handler=handlers.__setitem__)
        docs = folder/'docs'
        assert not manager.register_directory('docs', str(docs)) and len(warnings) == 1
        plan = {}
        guide = updater.plan_klipper_docs(folder, plan)
        assert not docs.exists()
        guide.parent.mkdir()
        updater.atomic_config_write(guide, plan[guide][1], create=True)
        warnings.clear()
        assert manager.register_directory('docs', str(docs)) and not warnings
        assert handlers['docs'] == str(docs) and 'docs' not in manager.full_access_roots
        assert guide.read_bytes() == (updater.ROOT/'docs/Inventure_Display.md').read_bytes()
        assert updater.plan_klipper_docs(folder, {}) is None
        print('PASS missing docs reproduced and read-only root registered with the Inventure guide')


if __name__ == '__main__':
    check()
