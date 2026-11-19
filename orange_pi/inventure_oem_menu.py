# SPDX-License-Identifier: GPL-3.0-or-later
"""Inventure r0323 menu reconstruction: M300 M11-style optional UI-only layer.

A menu action without an explicit Inventure Pxx route is view-only.
The panel adapter routes attended thermal bench actions to P06.
Source: V9 r0323 menu graph 2026-10-06 audit, five OEM top-level branches.
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
 # M300-style split root: Klipper / Zortrax / Service & Tests.
 # Never dispatch an unqualified printer action from this UI-only branch.
 'launcher':('Klipper',None,[
    Item('Klipper','klipper_main'),
    Item('Zortrax Inventure','root'),
    Item('Service / Tests','service_tests')]),
 'klipper_main':('Klipper',None,[
    Item('Printer status',action='go_status'),
    Item('Print / Job',action='job_info'),
    Item('Temperatures',action='thermal_info'),
    Item('Control','klipper_control'),
    Item('Settings','klipper_settings'),
    Item('Information','klipper_information')]),
 'klipper_control':('Control',None,[
    Item('Motion not qualified',action='motion_disabled'),
    Item('Homing not qualified',action='homing_disabled'),
    Item('Heating not qualified',action='heating_disabled')]),
 'klipper_settings':('Settings',None,[
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
 'root': ('Main Menu', None, [
   Item('Models','models',icon='models',oem_ram='0x20000448'),
   Item('Maintenance','maintenance',icon='maintenance',oem_ram='0x20000A8C'),
   Item('Material','material',icon='material',oem_ram='0x200002C0'),
   Item('Settings','settings',icon='settings',oem_ram='0x200008E0'),
   Item('Information','information',icon='information',oem_ram='0x200001F8')]),
 'models':('Models','models',[
   Item('Browse SD card',action='sd_browser',icon='models')]),
 'maintenance':('Maintenance','maintenance',[
   Item('Platform leveling',action='leveling'),
   Item('Nozzle alignment',action='alignment'),
   Item('Hotend maintenance','heads'),
   Item('Heat the extruder',action='heat'),
   Item('Move the platform',action='move_platform'),
   Item('Fan test',action='fan_test')]),
 'heads':('Hotend maintenance','maintenance',[
   Item('Model hotend maintenance',action='maintenance_t0'),
   Item('Support hotend maintenance',action='maintenance_t1')]),
 'material':('Material','material',[
   Item('Model','model_material'),Item('Support','support_material'),
   Item('Filament recovery',action='filament_jam')]),
 'model_material':('Model','material',[
   Item('Unload model material',action='unload_t0'),
   Item('Load model material','load_model')]),
 'support_material':('Support','material',[
   Item('Unload support material',action='unload_t1'),
   Item('Load support material','load_support')]),
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
   Item('English',action='language_en')]),
 'sorting':('Models sorting','sorting',[
   Item('Date descending',action='sort_desc'),
   Item('Date ascending',action='sort_asc'),
   Item('File name',action='sort_name'),
   Item('File size',action='sort_size')]),
 'doorlock':('Door lock','doorlock',[
   Item('Disable door lock',action='door_unlock'),
   Item('Enable door lock',action='door_lock')]),
 'filecheck':('File checking','filecheck',[
   Item('Disable file checking',action='filecheck_off'),
   Item('Enable file checking',action='filecheck_on')]),
 'information':('Information','information',[
   Item('Firmware version',action='version'),
   Item('Printer information',action='printer_info'),
   Item('Help',action='help')]),
 'pause':('Pause','material',[
   Item('Warning: Hot extruder'),
   Item('Continue print',action='resume'),
   Item('Stop print','stop_confirm'),
   Item('Material/Maintenance','pause_material')]),
 'stop_confirm':('Stop print?','material',[
   Item('No',action='cancel_no'),Item('Yes',action='cancel_yes')]),
 'pause_material':('Material/Maintenance','material',[
   Item('Material','material'),Item('Save print',action='save_print'),
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
        nodes=MENU[self.page][2]
        if self.bench:
            if not self.operational or self.page=='service_tests':
                nodes=[_BENCH_BRANCHES.get(node.action,node) for node in nodes]
            if self.page=='service_tests':
                nodes=nodes+[Item('Heat test','bench_heat')]
        if self.page==self.root_page and not self.root_back_to_status:return list(nodes)
        return [Item('Back',action='back')]+list(nodes)

    def view(self):
        items=self.items();selected=min(self.selected,max(0,len(items)-1))
        if selected!=self.selected:self.selected=selected
        # In a fast root menu, do not redraw a 50x50 bitmap for every UP/DOWN.
        # This is the largest source of 250000-baud navigation latency.
        # Keep the OEM category icon on a submenu page instead.
        icon=MENU[self.page][1]
        if self.page not in ('root','launcher') and items and items[selected].icon:
            icon=items[selected].icon
        return {'page':self.page,'title':MENU[self.page][0],
                'labels':[item.label for item in items],
                'selected':self.selected,'window':self.window,
                'icon':icon,'language':self.language,'notice':self.notice,
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
        # Other uncertified operations remain view-only.
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
                'root_page':self.root_page,'navigation_profile':'M300_LAUNCHER',
                'ui_only':not self.bench,
                'thermal_bench_menu':self.bench,
                'actuators_enabled_by_menu':False,'procedure_router':self.operational}
