"""Install the authenticated shared Fluidd/OLED leveling presentation."""
from hashlib import sha1, sha256

RELEASE = 'RC8-LEVELING-UI-R23.3'

FILES = {'klippy/extras/inventure_menu_service.py': {'source': 'restored/leveling_stages_r23/inventure_menu_service.py',
                                             'prior_blob': '527efc9db3e58549647208a2fea36207d36431f1',
                                             'installed_blob': '5a08da3154254f9ef478ebfcd35dfad7d531762b',
                                             'sha256': '80035a7efc6b9cb388efadc62da76665652584d8b685684b851356aeb1344baa'},
 'klippy/extras/inventure_oled_graphics.py': {'source': 'restored/leveling_stages_r23/inventure_oled_graphics.py',
                                              'prior_blob': 'a965cf2ec3a0508af326518b904ebabcbaaffe10',
                                              'installed_blob': '145c34679ffd56c6a3b9ea31d9ee3cb705bd7389',
                                              'sha256': '68eadc4896924d14442e14b553c7e90c17b702b89d62674feb18626031babb99'},
 'klippy/extras/inventure_oled.py': {'source': 'restored/leveling_stages_r23/inventure_oled.py',
                                     'prior_blob': '1a7c46e04bbf61c8fe2e34559d50dcd7df2ef526',
                                     'installed_blob': 'b360bbfc2239141b75ec5f41bf580f9b519771fa',
                                     'sha256': '7b45f5768081288ee08424c85de089ba9b8e0b39c8e818067633d90870072468'}}

def restored_leveling_stages(repo, files):
    updates = {}
    for target, record in FILES.items():
        before = files[target][0]
        path = repo/record['source']
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or non-regular leveling UI source: '+str(path))
        after = path.read_bytes()
        blob = lambda data: sha1(b'blob '+str(len(data)).encode('ascii')+b'\0'+data).hexdigest()
        if blob(before) != record['prior_blob'] or blob(after) != record['installed_blob']:
            raise ValueError('Leveling stages restoration integrity mismatch: '+target)
        compile(after, target, 'exec')
        updates[target] = (after, 0o644)
    return updates, dict(release=RELEASE, files=FILES,
                         mcu_update_required=False, hardware_qualified=False)
