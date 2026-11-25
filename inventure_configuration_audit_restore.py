"""Install the authenticated Klipper / Zortrax Inventure configuration audit."""
from hashlib import sha1, sha256

RELEASE = 'RC8-CONFIG-AUDIT-R23.5'

FILES = {'klippy/extras/inventure_native_z.py': {'source': 'restored/configuration_audit_r23/inventure_native_z.py',
                                         'prior_blob': '3fc954974a4a375e003b50c8624304301457f1de',
                                         'installed_blob': 'eb92dd3eb7bb54aee5b710b34957494250a547e9',
                                         'sha256': 'bee99afaba08671a654c904772ec4bd4a682094e6fbcb1a5da49d66f32882ca5'},
 'klippy/extras/inventure_menu_service.py': {'source': 'restored/configuration_audit_r23/inventure_menu_service.py',
                                             'prior_blob': 'ff5ef37239b3754b489a390ae953bbfb92f69532',
                                             'installed_blob': '57c875460fef361076c9cf0420ed16a7e788c34c',
                                             'sha256': 'c8dbfc7043046251efb61446ddc72d85938a0f3e6addf3963562289f1416c01e'},
 'klippy/extras/inventure_oled_graphics.py': {'source': 'restored/configuration_audit_r23/inventure_oled_graphics.py',
                                              'prior_blob': '7461f7703cf810a058d4213757daf378b85e5c0d',
                                              'installed_blob': 'fccc9a78f3912b26f9e733d83f8481208b427fff',
                                              'sha256': '0963f500585ef19b39e33459e2fc8de45596a422dba9cdd10daa0df65ef9f790'}}

def restored_configuration_audit(repo, files):
    updates = {}
    for target, record in FILES.items():
        before = files[target][0]
        path = repo/record['source']
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing or non-regular configuration audit source: '+str(path))
        after = path.read_bytes()
        blob = lambda data: sha1(b'blob '+str(len(data)).encode('ascii')+b'\0'+data).hexdigest()
        if blob(before) != record['prior_blob'] or blob(after) != record['installed_blob']:
            raise ValueError('Configuration audit restoration integrity mismatch: '+target)
        compile(after, target, 'exec')
        updates[target] = (after, 0o644)
    return updates, dict(release=RELEASE, files=FILES,
                         mcu_update_required=False, hardware_qualified=False)
