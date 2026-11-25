# SPDX-License-Identifier: GPL-3.0-or-later
"""Inventure r0323 menu tree and shared operator navigation.

The five original OEM branches sit beneath the Klipper / Zortrax Inventure
launcher. The operator service resolves action identifiers through existing
Inventure owners and their capability guards. Original callback addresses
are read-only provenance and are never executed.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class Item:
    label: str
    child: Optional[str] = None
    action: Optional[str] = None
    icon: Optional[str] = None
    oem_ram: Optional[str] = None

# Exact OEM order for the five main branches; nested action labels follow
# the static r0323 audit. Discovered callbacks are NOT executed by this UI.
MENU = {
 'launcher':('Main menu',None,[
    Item('Klipper','klipper_main',icon='project:project-klipper'),
    Item('Zortrax Inventure','root',icon='project:project-zortrax')]),
 'klipper_main':('Klipper',None,[
    Item('Printer status',action='go_status'),
    Item('Print / Job',action='job_info'),
    Item('Temperatures',action='thermal_info'),
    Item('Control','klipper_control'),
    Item('Fans',action='fan_test'),
    Item('Settings','klipper_settings'),
    Item('Information','klipper_information'),
    Item('Diagnostics','service_tests')]),
 'klipper_control':('Control',None,[
    *[Item('Home '+axes,action='home_'+axes.lower()) for axes in ('X','Y','Z','XY','XYZ')],
    Item('Move the platform',action='move_platform'),
    Item('Heat the extruder',action='heat'),
    Item('Turn off heaters',action='heaters_off')]),
 'klipper_settings':('Settings',None,[
    Item('Light',action='light'),
    Item('Sleep mode',action='oled_sleep_mode'),
    Item('OLED brightness',action='oled_brightness'),
    Item('OLED timers',action='oled_timers'),
    Item('Configuration: Fluidd',action='settings_info'),
    Item('Panel and OLED',action='oled_info')]),
 'klipper_information':('Information',None,[
    Item('MCU version',action='version'),
    Item('P4 serial port',action='uart_info'),
    Item('Printer status',action='go_status')]),
 'service_tests':('Service / Tests',None,[
    Item('MCU status',action='mcu_info'),
    Item('OLED and menu',action='oled_info'),
    Item('P4 UART',action='uart_info'),
    Item('Fans',action='fans_info'),
    Item('Inputs / ADC',action='adc_info'),
    Item('RFID / material',action='rfid_info')]),
 'root': ('Zortrax Inventure', None, [
   Item('Models','models',icon='models',oem_ram='0x20000448'),
   Item('Maintenance','maintenance',icon='maintenance',oem_ram='0x20000A8C'),
   Item('Material','material',icon='material',oem_ram='0x200002C0'),
   Item('Settings','settings',icon='settings',oem_ram='0x200008E0'),
   Item('Information','information',icon='information',oem_ram='0x200001F8')]),
 'models':('Models','models',[
   Item('Browse SD card',action='sd_browser',icon='models')]),
 'maintenance':('Maintenance','maintenance',[
   Item('Platform leveling',action='leveling'),
   Item('Nozzle alignment calibration',action='alignment',oem_ram='0x200009E4'),
   Item('Hotend maintenance','heads'),
   Item('Heat the extruder',action='heat'),
   Item('Move the platform',action='move_platform'),
   Item('Fan test',action='fan_test')]),
 'heads':('Hotend maintenance','maintenance',[
   Item('Left hotend maintenance (model)',action='maintenance_t0',oem_ram='0x20000A44'),
   Item('Right hotend maintenance (support)',action='maintenance_t1',oem_ram='0x20000A64')]),
 'material':('Material','material',[
   Item('Model','model_material'),Item('Support','support_material'),
   Item('Filament recovery',action='filament_jam')]),
 'model_material':('Model','material',[
   Item('Unload the model material',action='unload_t0'),
   Item('Load the model material','load_model')]),
 'support_material':('Support','material',[
   Item('Unload the support material',action='unload_t1'),
   Item('Load the support material','load_support')]),
 'load_model':('Load model material','material',[
   Item('Load from cartridge',action='load_cartridge_t0'),
   Item('Load from spool','spool_model',icon='spool')]),
 'load_support':('Load support material','material',[
   Item('Load from cartridge',action='load_cartridge_t1'),
   Item('Load from spool','spool_support',icon='spool')]),
 'spool_model':('Model spool','spool',[
   Item('Select material',action='spool_catalog_model')]),
 'spool_support':('Support spool','spool',[
   Item('Select material',action='spool_catalog_support')]),
 'settings':('Settings','settings',[
   Item('Language','language',icon='language'),
   Item('Models sorting','sorting',icon='sorting'),
   Item('Door lock','doorlock',icon='doorlock'),
   Item('File checking','filecheck',icon='filecheck'),
   Item('Ignore material endstops',action='ignore_filament')]),
 'language':('Language','language',[
   Item('English',action='language_en'),
   Item('Polski',action='language_pl')]),
 'sorting':('Models sorting','sorting',[
   Item('Date/time ↓',action='sort_desc'),
   Item('Date/time ↑',action='sort_asc'),
   Item('File name',action='sort_name'),
   Item('File size',action='sort_size')]),
 'doorlock':('Door lock','doorlock',[
   Item('Door lock enable',action='door_lock'),
   Item('Door lock disable',action='door_unlock')]),
 'filecheck':('File checking','filecheck',[
   Item('Enable .zcode file checking',action='filecheck_on'),
   Item('Disable .zcode file checking',action='filecheck_off')]),
 'information':('Information','information',[
   Item('Firmware version',action='version'),
   Item('Printer information',action='printer_info'),
   Item('Help',action='help')]),
 'pause':('Pause','material',[
   Item('WARNING: Hot extruder'),
   Item('Resume the print job',action='resume'),
   Item('Stop the print job','stop_confirm'),
   Item('Material/Maintenance','pause_material')]),
 'stop_confirm':('Stop print?','material',[
   Item('Cancel the print job?'),
   Item('No',action='cancel_no'),Item('Yes',action='cancel_yes')]),
 'pause_material':('Material/Maintenance','material',[
   Item('Material','material'),Item('Save the print',action='save_print'),
   Item('Hotend maintenance','heads')]),
 'cancel':('Cancel operation?','information',[
   Item('No',action='cancel_no'),Item('Yes',action='cancel_yes')]),
 'question':('Confirm','information',[
   Item('No',action='question_no'),Item('Yes',action='question_yes')]),
 'model_details':('Model details','models',[
   Item('Print',action='print_model')]),
}

_FAN_LABELS = ('Head front', 'Model fan', 'Chamber L', 'Chamber R',
               'Rear fan', 'Transfer pair')
_HEAT_LABELS = (('T0', 'Nozzle T0'), ('T1', 'Nozzle T1'),
                ('LEFT', 'Chamber L'), ('RIGHT', 'Chamber R'))
MENU.update({
    'bench_fans': ('Fans', None, [
        Item('Fan readings', action='bench_fans_status'),
        Item('Manual test status', action='bench_manual_status'),
        Item('Cancel fan test', action='bench_manual_cancel'),
        *[Item(f'F{i} {label}', f'bench_fan_{i}')
          for i,label in enumerate(_FAN_LABELS)],
    ]),
    'bench_heat': ('Heating', None, [
        Item('Temperature readings', action='bench_heat_status'),
        Item('Test status', action='bench_result'),
        Item('Cancel heat test', action='bench_heat_stop'),
        *[Item(label,'bench_heat_'+channel) for channel,label in _HEAT_LABELS],
    ]),
})
for i, label in enumerate(_FAN_LABELS):
    MENU[f'bench_fan_{i}'] = (label, None, [
        Item('Cold, heaters off? ON', action=f'bench_manual_{i}_on'),
        Item('OFF', action=f'bench_manual_{i}_off'),
        Item('Observed PASS', action=f'bench_manual_{i}_pass'),
        Item('Observed FAIL', action=f'bench_manual_{i}_fail'),
    ])
for channel, label in _HEAT_LABELS:
    action = 'bench_heat_' + channel
    MENU[action] = (label,None,[
        Item('Start attended test', action=action)])

_BENCH_BRANCHES = {
    'thermal_info': Item('Temperatures','bench_heat'),
    'heating_disabled': Item('Heating','bench_heat'),
    'fans_info': Item('Fans','bench_fans'),
    'heat': Item('Heat test','bench_heat'),
    'fan_test': Item('Fan test','bench_fans'),
}


def _operator_rows():
    language, sorting, *native_settings = MENU['settings'][2]
    leveling, alignment, hotends, heat, _, fans = MENU['maintenance'][2]
    homes = [Item('Home ' + axes, action='home_' + axes.lower()) for axes in ('X', 'Y', 'Z', 'XY', 'XYZ')]
    return {
        'settings': [language, sorting, *native_settings,
                     Item('Light', action='light'), Item('Sleep mode', action='oled_sleep_mode'),
                     Item('OLED brightness', action='oled_brightness'),
                     Item('OLED dim time', action='oled_dim'), Item('OLED sleep time', action='oled_sleep'),
                     Item('OLED timers', action='oled_timers'),
                     Item('Sleep lighting', action='oled_sleep_light'), Item('OLED icons', action='oled_icons')],
        'maintenance': [leveling, alignment, hotends, *homes, heat,
                        Item('Move the platform',action='move_platform'), fans,
                        Item('Current procedure', action='current_procedure')],
    }


OPERATOR_ROWS = _operator_rows()
PROJECT_ACTION_ICONS = {'home_xy': 'homing-xy', 'home_z': 'homing-z', 'home_xyz': 'homing-all',
                        'light': 'led_brightness'}


# The renderer resolves each operation to an audited Inventure symbol.
_ACTION_ICONS = {
    'heat': 'heat_nozzle', 'heat_t0': 'heat_nozzle', 'heat_t1': 'heat_nozzle',
    'thermal_info': 'heat_nozzle', 'clean_t0': 'clean_nozzle', 'clean_t1': 'clean_nozzle',
    'filament_jam': 'filament_jam', 'oled_brightness': 'oled_brightness',
    'oled_dim': 'oled_brightness', 'oled_sleep': 'oled_brightness', 'oled_timers': 'oled_brightness',
    'uart_info': 'link_status', 'sd_browser': 'sd_read',
}


def action_icon(action):
    if action == 'leveling':return 'leveling:0x0802A218'
    shared = {'heat':'heat_nozzle','heat_t0':'heat_nozzle','heat_t1':'heat_nozzle',
              'maintenance_t0':'heat_nozzle','maintenance_t1':'heat_nozzle',
              'clean_t0':'clean_nozzle','clean_t1':'clean_nozzle',
              'purge_t0':'heat_nozzle','purge_t1':'heat_nozzle',
              'fan_test':'fan_upper','fans_info':'fan_upper',
              'heaters_off':'cooling','sd_browser':'sd_read'}
    if action in shared:return 'project:'+shared[action]
    if action in PROJECT_ACTION_ICONS:
        return 'project:' + PROJECT_ACTION_ICONS[action]
    name = _ACTION_ICONS.get(action)
    return 'activity:' + name if name else None


class OEMMenu:
    """Navigation and explicit dispatch results for attended bench choices."""
    def __init__(self, language='en', root_back_to_status=True, root_page='root', bench=False, operational=False):
        if language not in ('pl','en'): raise ValueError('Unsupported oem_language')
        if root_page not in ('root','launcher'): raise ValueError('Unknown menu root page')
        self.root_page=root_page
        self.bench=bench
        self.operational=operational
        # Legacy printer.cfg files can still contain oem_language: pl.
        self.language='en'
        self.root_back_to_status=bool(root_back_to_status)
        self.page=self.root_page;self.stack=[];self.selected=0;self.window=1 if self.root_back_to_status else 0
        self.notice=None;self.last_action=None;self.generation=0

    def items(self):
        nodes = MENU[self.page][2]
        if self.operational:
            nodes = OPERATOR_ROWS.get(self.page, nodes)
        if self.bench:
            if not self.operational or self.page=='service_tests':
                nodes=[_BENCH_BRANCHES.get(node.action,node) for node in nodes]
            if self.page=='service_tests':
                nodes=nodes+[Item('Heat test','bench_heat')]
        if self.page==self.root_page and not self.root_back_to_status:return list(nodes)
        return [Item('Back',action='back')]+list(nodes)

    def item_icon(self, item):
        if item.icon and (self.operational or self.page not in ('root', 'launcher')):
            return item.icon
        return (action_icon(item.action) if self.operational else None) or MENU[self.page][1]

    def view(self):
        items=self.items();selected=min(self.selected,max(0,len(items)-1))
        if selected!=self.selected:self.selected=selected
        icon=self.item_icon(items[selected]) if items else None
        return {'page':self.page,'title':MENU[self.page][0],
                'labels':[item.label for item in items],
                'selected':self.selected,'window':self.window,
                'icon':icon,'animated':bool(icon and icon.startswith('project:') and not self.notice),
                'language':self.language,'notice':self.notice,
                'generation':self.generation}

    def move(self, direction):
        if direction not in (-1,1):raise ValueError('direction -1/+1')
        prev=self.selected
        self.selected=min(len(self.items())-1,max(0,self.selected+direction))
        if prev!=self.selected:
            self.notice=None;self.generation+=1
        # Five physical 12-pixel rows, top Back pinned when present.
        # Never wrap; move the cursor in its existing window first.
        entries=self.items()
        has_back=bool(entries and entries[0].action=='back')
        visible=4 if has_back else 5
        if len(entries)>(visible+int(has_back)):
            if has_back:
                self.window=max(1,self.window)
                if self.selected and self.selected<self.window:
                    self.window=self.selected
                elif self.selected>self.window+visible-1:
                    self.window=self.selected-visible+1
            else:
                if self.selected<self.window:self.window=self.selected
                elif self.selected>self.window+visible-1:
                    self.window=self.selected-visible+1
        return self.selected

    def home(self):
        self.page=self.root_page;self.stack=[];self.selected=0
        self.window=1 if self.root_back_to_status else 0
        self.notice=None;self.last_action=None;self.generation+=1

    def open_page(self,page):
        """Enter a shared page with the same parents as physical navigation."""
        if page not in MENU:raise ValueError('Unknown Inventure menu page')
        start='klipper_main' if page.startswith('klipper_') or page=='service_tests' else 'root'
        queue=[([self.root_page],self.root_page)]
        if self.root_page=='launcher' and page not in ('launcher','klipper_main'):
            queue=[(['launcher',start],start)]
        found=None
        while queue:
            path,current=queue.pop(0)
            if current==page:found=path;break
            nodes=OPERATOR_ROWS.get(current,MENU[current][2]) if self.operational else MENU[current][2]
            queue.extend((path+[n.child],n.child) for n in nodes if n.child and n.child not in path)
        if found is None:
            # Pause is an OEM contextual root, not a child of the idle tree.
            found=[self.root_page,'pause']+([] if page=='pause' else [page])
        self.home()
        for child in found[1:]:
            index=next((i for i,n in enumerate(self.items()) if n.child==child),0)
            self.stack.append((self.page,index,self.window))
            self.page=child;self.selected=0;self.window=1;self.generation+=1

    def back(self):
        if not self.stack:return False
        self.page,self.selected,self.window=self.stack.pop()
        self.notice=None;self.generation+=1
        return True

    def select(self):
        items=self.items()
        if not items:return 'empty'
        node=items[self.selected]
        if node.action=='back':
            if self.back():return 'back'
            return 'exit_to_status'
        if node.action in ('cancel_no','question_no'):
            return 'back' if self.back() else 'exit_to_status'
        if node.child:
            self.stack.append((self.page,self.selected,self.window))
            self.page=node.child;self.selected=0;self.window=1;self.notice=None
            self.generation+=1;return 'submenu'
        if node.action=='go_status':
            self.last_action=node.action;self.generation+=1
            return 'exit_to_status'
        if node.action=='language_en':
            self.notice='English'
            self.last_action=node.action;self.generation+=1
            return 'language'
        if self.bench and node.action and node.action.startswith('bench_'):
            self.last_action=node.action;self.generation+=1
            return 'bench'
        if node.action is None:
            self.last_action=None
            return 'caption'
        # Return the action identifier to the operator service for its owner
        # and capability checks. Standalone reconstruction stays view-only.
        self.notice='Not qualified'
        if node.action in ('version','printer_info','help'):
            self.notice='Query in Fluidd'
        self.last_action=node.action;self.generation+=1
        return 'inert'

    def status(self):
        return {'page':self.page,'selected':self.selected,'window':self.window,
                'stack_depth':len(self.stack),'language':self.language,
                'notice':self.notice,'last_action':self.last_action,
                'generation':self.generation, 'root_back_to_status':self.root_back_to_status,
                'root_page':self.root_page,'navigation_profile':'INVENTURE_LAUNCHER' if self.root_page=='launcher' else 'INVENTURE_OEM',
                'ui_only':not self.operational,
                'thermal_bench_menu':self.bench,
                'actuators_enabled_by_menu':False,'procedure_router':self.operational}


# Read-only r0323 provenance; callback addresses are never dispatched.
OEM_RECORD_BINDINGS = {'0x2000028c': {'kind': 'page', 'target': 'root', 'source_label': 'Main menu'},
 '0x20000448': {'kind': 'page', 'target': 'models', 'source_label': 'Models'},
 '0x20000a8c': {'kind': 'page', 'target': 'maintenance', 'source_label': 'Maintenance'},
 '0x20000ae4': {'kind': 'action', 'target': 'leveling', 'source_label': 'Platform leveling'},
 '0x200009e4': {'kind': 'action', 'target': 'alignment', 'source_label': 'Nozzle alignment calibration'},
 '0x20000a24': {'kind': 'page', 'target': 'heads', 'source_label': 'Hotend maintenance'},
 '0x20000a44': {'kind': 'action',
                'target': 'maintenance_t0',
                'source_label': 'Left hotend maintenance (model)'},
 '0x20000a64': {'kind': 'action',
                'target': 'maintenance_t1',
                'source_label': 'Right hotend maintenance (support)'},
 '0x20000a04': {'kind': 'action', 'target': 'heat', 'source_label': 'Heat the extruder'},
 '0x20000ac4': {'kind': 'action', 'target': 'move_platform', 'source_label': 'Move the platform'},
 '0x200009c4': {'kind': 'action', 'target': 'fan_test', 'source_label': 'Fan Test'},
 '0x200002c0': {'kind': 'page', 'target': 'material', 'source_label': 'Material'},
 '0x200003b0': {'kind': 'page', 'target': 'model_material', 'source_label': 'Model'},
 '0x200003f0': {'kind': 'action', 'target': 'unload_t0', 'source_label': 'Unload the model material'},
 '0x200002e0': {'kind': 'page', 'target': 'load_model', 'source_label': 'Load the model material'},
 '0x20000300': {'kind': 'action', 'target': 'load_cartridge_t0', 'source_label': 'Load from cartridge'},
 '0x20000320': {'kind': 'page', 'target': 'spool_model', 'source_label': 'Load from spool'},
 '0x200003d0': {'kind': 'page', 'target': 'support_material', 'source_label': 'Support'},
 '0x20000410': {'kind': 'action', 'target': 'unload_t1', 'source_label': 'Unload the support material'},
 '0x20000348': {'kind': 'page', 'target': 'load_support', 'source_label': 'Load the support material'},
 '0x20000368': {'kind': 'action', 'target': 'load_cartridge_t1', 'source_label': 'Load from cartridge'},
 '0x20000388': {'kind': 'page', 'target': 'spool_support', 'source_label': 'Load from spool'},
 '0x200008e0': {'kind': 'page', 'target': 'settings', 'source_label': 'Settings'},
 '0x20000224': {'kind': 'page', 'target': 'language', 'source_label': 'Language'},
 '0x2000024c': {'kind': 'action', 'target': 'language_en', 'source_label': 'English'},
 '0x2000026c': {'kind': 'action', 'target': 'language_pl', 'source_label': 'Polski'},
 '0x20000468': {'kind': 'page', 'target': 'sorting', 'source_label': 'Models sorting'},
 '0x20000498': {'kind': 'action', 'target': 'sort_desc', 'source_label': 'Date/time ↓'},
 '0x200004b8': {'kind': 'action', 'target': 'sort_asc', 'source_label': 'Date/time ↑'},
 '0x200004d8': {'kind': 'action', 'target': 'sort_name', 'source_label': 'File name'},
 '0x200004f8': {'kind': 'action', 'target': 'sort_size', 'source_label': 'File size'},
 '0x200000a8': {'kind': 'page', 'target': 'doorlock', 'source_label': 'Door lock'},
 '0x200000e8': {'kind': 'action', 'target': 'door_lock', 'source_label': 'Door lock enable'},
 '0x200000c8': {'kind': 'action', 'target': 'door_unlock', 'source_label': 'Door lock disable'},
 '0x20000110': {'kind': 'page', 'target': 'filecheck', 'source_label': 'File checking'},
 '0x20000150': {'kind': 'action', 'target': 'filecheck_on', 'source_label': 'Enable .zcode file checking'},
 '0x20000130': {'kind': 'action', 'target': 'filecheck_off', 'source_label': 'Disable .zcode file checking'},
 '0x200001d8': {'kind': 'action', 'target': 'ignore_filament', 'source_label': 'Ignore material endstops'},
 '0x200001f8': {'kind': 'page', 'target': 'information', 'source_label': 'Information'},
 '0x20000178': {'kind': 'action', 'target': 'version', 'source_label': 'Firmware version'},
 '0x2000087c': {'kind': 'action', 'target': 'printer_info', 'source_label': 'Printer information'},
 '0x200001b8': {'kind': 'action', 'target': 'help', 'source_label': 'Help'},
 '0x20000518': {'kind': 'page', 'target': 'pause', 'source_label': '--- UNUSED ---'},
 '0x2000085c': {'kind': 'caption',
                'target': 'WARNING: Hot extruder',
                'source_label': 'WARNING: Hot extruder'},
 '0x200007a8': {'kind': 'spacer', 'target': None, 'source_label': ''},
 '0x20000768': {'kind': 'action', 'target': 'resume', 'source_label': 'Resume the print job'},
 '0x200007c8': {'kind': 'page', 'target': 'stop_confirm', 'source_label': 'Stop the print job'},
 '0x200007e8': {'kind': 'caption',
                'target': 'Cancel the print job?',
                'source_label': 'Are you sure you want to cancel\nthe print job?'},
 '0x2000081c': {'kind': 'action', 'target': 'cancel_no', 'source_label': 'No'},
 '0x2000083c': {'kind': 'action', 'target': 'cancel_yes', 'source_label': 'Yes'},
 '0x200006a4': {'kind': 'page', 'target': 'pause_material', 'source_label': 'Material/Maintenance'},
 '0x200005b4': {'kind': 'page', 'target': 'material', 'source_label': 'Material'},
 '0x200006d0': {'kind': 'page', 'target': 'model_material', 'source_label': 'Model'},
 '0x20000710': {'kind': 'action', 'target': 'unload_t0', 'source_label': 'Unload the model material'},
 '0x200005d4': {'kind': 'page', 'target': 'load_model', 'source_label': 'Load the model material'},
 '0x200005f4': {'kind': 'action', 'target': 'load_cartridge_t0', 'source_label': 'Load from cartridge'},
 '0x20000614': {'kind': 'page', 'target': 'spool_model', 'source_label': 'Load from spool'},
 '0x200006f0': {'kind': 'page', 'target': 'support_material', 'source_label': 'Support'},
 '0x20000730': {'kind': 'action', 'target': 'unload_t1', 'source_label': 'Unload the support material'},
 '0x2000063c': {'kind': 'page', 'target': 'load_support', 'source_label': 'Load the support material'},
 '0x2000065c': {'kind': 'action', 'target': 'load_cartridge_t1', 'source_label': 'Load from cartridge'},
 '0x2000067c': {'kind': 'page', 'target': 'spool_support', 'source_label': 'Load from spool'},
 '0x20000788': {'kind': 'action', 'target': 'save_print', 'source_label': 'Save the print'},
 '0x2000054c': {'kind': 'page', 'target': 'heads', 'source_label': 'Hotend maintenance'},
 '0x2000056c': {'kind': 'action',
                'target': 'maintenance_t0',
                'source_label': 'Left hotend maintenance (model)'},
 '0x2000058c': {'kind': 'action',
                'target': 'maintenance_t1',
                'source_label': 'Right hotend maintenance (support)'}}
