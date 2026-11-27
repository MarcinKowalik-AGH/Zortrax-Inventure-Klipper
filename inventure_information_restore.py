"""Add Info after the authenticated RC7 menu; never edit a movement engine."""
from hashlib import sha256

RELEASE = 'RC8-INFORMATION-R25.2'
PREDECESSOR_MANIFEST = '25a2219212e19c3b01d26e5888784b423185759c981b64d7b6e3da3fc009cb5e'
FILES = ('inventure_information.py', 'inventure_information_ui.py', 'inventure_info_store.py',
         'inventure_runtime.py', 'inventure_counter_sync.py', 'inventure_information_errors.py')


def restore(repo, files, *, revision='current'):
    if revision not in ('current', 'rc1'):
        raise ValueError('Unknown Information revision: '+str(revision))
    updates = {}
    for name in FILES:
        source = repo/'restored/information_r25'
        if revision == 'rc1' and name in ('inventure_information.py','inventure_information_ui.py'):
            source = source/'rc1'
        data = (source/name).read_bytes()
        compile(data, name, 'exec')
        updates['klippy/extras/'+name] = (data, 0o644)
    core = 'klippy/extras/inventure_menu_core.py'
    updates[core] = (files[core][0]+b"\nREAD_ACTIONS = READ_ACTIONS | frozenset(('information_errors',)) | frozenset('information_error_%d' % i for i in range(14))\nALL_ACTIONS = ALL_ACTIONS | READ_ACTIONS\n", 0o644)
    menu = 'klippy/extras/inventure_menu_service.py'
    text = files[menu][0].decode()
    anchor = "                show = gcmd.get_int('PROMPT', 0, minval=0, maxval=1)\n"
    if text.count(anchor) != 1:
        raise ValueError('Unexpected Info prompt integration anchor')
    text = text.replace(anchor,
        "                info_action = gcmd.get('ACTION') in ('version','printer_info','help','information_errors') or str(gcmd.get('ACTION','')).startswith('information_error_')\n"
        "                info_prompt = info_action and not getattr(getattr(self,'home_ui',None),'busy',False)\n"
        "                show = gcmd.get_int('PROMPT', int(info_prompt), minval=0, maxval=1)\n")
    anchor = "not in DISPLAY_ACTIONS | HOME_ACTIONS.keys() | {"
    if text.count(anchor) != 1:
        raise ValueError('Unexpected Info read-only prompt whitelist')
    text = text.replace(anchor, "not in READ_ACTIONS | DISPLAY_ACTIONS | HOME_ACTIONS.keys() | {")
    data = text.encode()+b'''\nfrom .inventure_information_ui import InformationMenu
class ZortraxMenuService(InformationMenu, ZortraxMenuService):
    pass
'''
    updates[menu] = (data, 0o644)
    return updates, dict(release=RELEASE if revision == 'current' else 'RC8-INFORMATION-R25.1', hardware_tested=False,
        m300_reference='1f9d77432cbbdc9ae69298b6e4815b4bd819e963',
        files={name:sha256(data).hexdigest() for name,(data,_) in updates.items()})
