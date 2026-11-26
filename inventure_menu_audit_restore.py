"""Presentation-only completion after the authenticated RC6 runtime."""
from hashlib import sha256

RELEASE = 'RC8-OPERATOR-MENU-R24.5'
FILES = {
    'inventure_menu_presentation.py': '87ecb0b3e89ec7be23addeedb78e205e5ba1f0583a3fea25ad60e31da6a8db12',
    'inventure_oem_menu_sprites.json': '59b77f4a183cb8e5a28a3abc444d8e0ed3364e37394df9e0c030e9613eab52d5',
}


def restored_menu_audit(repo, files):
    from inventure_operator_home_restore import replace
    updates = {}
    for name, digest in FILES.items():
        data = (repo/'restored/menu_audit_r24'/name).read_bytes()
        if sha256(data).hexdigest() != digest:
            raise ValueError('Reviewed menu presentation changed: '+name)
        updates['klippy/extras/'+name] = (data, 0o644)
    nav = 'klippy/extras/inventure_oem_menu.py'
    data = files[nav][0].decode()
    data = replace(data, 'def action_icon(action):\n', 'def _rc6_action_icon(action):\n')
    data += '''
# All owners/actions remain above. Complete artwork and inherited page icons.
from .inventure_menu_presentation import action_icon as _audit_action_icon, CHILD_ICONS
def action_icon(action):
    return _audit_action_icon(action) or _rc6_action_icon(action)
class OEMMenu(OEMMenu):
    def item_icon(self, item):
        if self.operational:
            return (action_icon(item.action) or CHILD_ICONS.get(item.child)
                    or item.icon or CHILD_ICONS.get(self.page)
                    or super().item_icon(item) or 'information')
        return super().item_icon(item)
'''
    updates[nav] = (data.encode(), 0o644)
    graphic = 'klippy/extras/inventure_oled_graphics.py'
    data = files[graphic][0].decode()
    data = replace(data, 'def _symbol(icon):\n',
        "@lru_cache(maxsize=1)\n"
        "def _menu_oem_symbols():\n"
        "    return json.loads(Path(__file__).with_name('inventure_oem_menu_sprites.json').read_bytes())['symbols']\n\n\n"
        "def _symbol(icon):\n"
        "    if icon and icon.startswith('oem:'):\n"
        "        return _menu_oem_symbols().get(icon[4:])\n")
    updates[graphic] = (data.encode(), 0o644)
    menu = 'klippy/extras/inventure_menu_service.py'
    data = files[menu][0].decode()
    data = replace(data,"'select_t0','select_t1','leveling',", "'select_t0','select_t1','leveling','alignment',")
    data = replace(data, "                if gcmd.get('ACTION') == 'leveling':\n",
        "                if gcmd.get('ACTION') == 'alignment':\n"
        "                    allowed.update(('T0_TARGET','T1_TARGET'))\n"
        "                if gcmd.get('ACTION') == 'leveling':\n")
    data += '''
from .inventure_menu_presentation import MenuPresentation
class ZortraxMenuService(MenuPresentation, ZortraxMenuService):
    pass
'''
    updates[menu] = (data.encode(), 0o644)
    oled = 'klippy/extras/inventure_oled.py'
    updates[oled] = (files[oled][0]+b'''
from .inventure_menu_presentation import OLEDMenuPresentation
class InventureOLED(OLEDMenuPresentation, InventureOLED):
    pass
''', 0o644)
    for name, (data, _) in updates.items():
        if name.endswith('.py'):
            compile(data, name, 'exec')
    return updates, dict(release=RELEASE, presentation_only=True, hardware_tested=False,
                         files={name: sha256(data).hexdigest() for name, (data, _) in updates.items()})
