"""Install the authenticated shared Fluidd/OLED leveling presentation."""
from hashlib import sha1, sha256

RELEASE = 'RC8-LEVELING-UI-R23.2'

FILES = {'klippy/extras/inventure_menu_service.py': {'source': 'restored/leveling_ui_r23/inventure_menu_service.py', 'prior_blob': '4d40f4408c37a5499633a3789634832c492794ac', 'installed_blob': '527efc9db3e58549647208a2fea36207d36431f1', 'sha256': '06781bfd70059ea8d0303ebad1e6edef8677d851ad352e17696d16ea66a7390b'}, 'klippy/extras/inventure_oled_graphics.py': {'source': 'restored/leveling_ui_r23/inventure_oled_graphics.py', 'prior_blob': 'ed25fca215f31ea94d2b4b4581ce5833c78013c6', 'installed_blob': 'a965cf2ec3a0508af326518b904ebabcbaaffe10', 'sha256': 'bba681f06e8576d6d25054c9052926a7c704ee688137620a8f7e420d861eb193'}}

def restored_leveling_ui(repo, files):
    updates = {}
    for target, record in FILES.items():
        before = files[target][0]
        path = repo/record['source']
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or non-regular leveling UI source: '+str(path))
        after = path.read_bytes()
        blob = lambda data: sha1(b'blob '+str(len(data)).encode('ascii')+b'\0'+data).hexdigest()
        if blob(before) != record['prior_blob'] or blob(after) != record['installed_blob']:
            raise ValueError('Leveling UI restoration integrity mismatch: '+target)
        compile(after, target, 'exec')
        updates[target] = (after, 0o644)
    return updates, dict(release=RELEASE, files=FILES,
                         mcu_update_required=False, hardware_qualified=False)
