#!/usr/bin/env python3
"""Read the migrated files with Moonraker's deployed configuration parser."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from moonraker.confighelper import FileSourceWrapper

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


if __name__ == '__main__':
    check()
