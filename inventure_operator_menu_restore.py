"""Install the authenticated Klipper / Zortrax Inventure operator menu."""
from hashlib import sha1, sha256

RELEASE = 'RC8-OPERATOR-MENU-R23.4'

FILES = {'klippy/extras/inventure_menu_service.py': {'source': 'restored/operator_menu_r23/inventure_menu_service.py',
                                             'prior_blob': '5a08da3154254f9ef478ebfcd35dfad7d531762b',
                                             'installed_blob': 'ff5ef37239b3754b489a390ae953bbfb92f69532',
                                             'sha256': '51b4158a25fb143a73d31fabe25333c05b2d83d90a65790072ec2f81d92c50c3'},
 'klippy/extras/inventure_oled_graphics.py': {'source': 'restored/operator_menu_r23/inventure_oled_graphics.py',
                                              'prior_blob': '145c34679ffd56c6a3b9ea31d9ee3cb705bd7389',
                                              'installed_blob': '7461f7703cf810a058d4213757daf378b85e5c0d',
                                              'sha256': 'f14f1e2048219eea6b4697e51122621cf66b220f677bb213f0131e1d3488b0d1'},
 'klippy/extras/inventure_oled.py': {'source': 'restored/operator_menu_r23/inventure_oled.py',
                                     'prior_blob': 'b360bbfc2239141b75ec5f41bf580f9b519771fa',
                                     'installed_blob': 'ed947efd5c4e1c6ac6e54747a1d27f1121fa932a',
                                     'sha256': '79a8a83252b2baeb707d658f830d11a4abb8349044ecbcc915b4f1283bba6779'},
 'klippy/extras/inventure_menu_core.py': {'source': 'restored/operator_menu_r23/inventure_menu_core.py',
                                          'prior_blob': '87c547008ba56c44e8a43c6e4b1d444ad027e3d6',
                                          'installed_blob': '287e74b1f07cf078f813754ae46d8467d5e10d0f',
                                          'sha256': 'b383aff2947e7eb77d90d0a12b1f99b92769409142f3bcd97c695b3259829748'},
 'klippy/extras/inventure_oem_menu.py': {'source': 'restored/operator_menu_r23/inventure_oem_menu.py',
                                         'prior_blob': 'c9cf76d5198aad8eed81f3083a86ac2f4f27fdc7',
                                         'installed_blob': '37334e6ecefb9f2da44b7904d4b74d1386878988',
                                         'sha256': 'd32af9743fce67fa026985c87467cbcc6cc3017814bf52e7f0a27df7cdf5d0b4'}}

def restored_operator_menu(repo, files):
    updates = {}
    for target, record in FILES.items():
        before = files[target][0]
        path = repo/record['source']
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or non-regular operator menu source: '+str(path))
        after = path.read_bytes()
        blob = lambda data: sha1(b'blob '+str(len(data)).encode('ascii')+b'\0'+data).hexdigest()
        if blob(before) != record['prior_blob'] or blob(after) != record['installed_blob']:
            raise ValueError('Operator menu restoration integrity mismatch: '+target)
        compile(after, target, 'exec')
        updates[target] = (after, 0o644)
    return updates, dict(release=RELEASE, files=FILES,
                         mcu_update_required=False, hardware_qualified=False)
