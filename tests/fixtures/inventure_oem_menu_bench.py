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
    label_pl: str
    label_en: str
    child: Optional[str] = None
    action: Optional[str] = None
    icon: Optional[str] = None
    oem_ram: Optional[str] = None

# Exact OEM order for the five main branches; nested action labels follow
# the static r0323 audit. Discovered callbacks are NOT executed by this UI.
MENU = {
 # M300-style split root: Klipper / Zortrax / Service & Tests.
 # Never dispatch an unqualified printer action from this UI-only branch.
 'launcher':('Klipper','Klipper',None,[
    Item('Klipper','Klipper','klipper_main'),
    Item('Zortrax Inventure','Zortrax Inventure','root'),
    Item('Serwis / Testy','Service / Tests','service_tests')]),
 'klipper_main':('Klipper','Klipper',None,[
    Item('Status drukarki','Printer status',action='go_status'),
    Item('Druk / Zadanie','Print / Job',action='job_info'),
    Item('Temperatury','Temperatures',action='thermal_info'),
    Item('Sterowanie','Control','klipper_control'),
    Item('Ustawienia','Settings','klipper_settings'),
    Item('Informacje','Information','klipper_information')]),
 'klipper_control':('Sterowanie','Control',None,[
    Item('Ruch nieaktywny','Motion not qualified',action='motion_disabled'),
    Item('Bazowanie nieaktywne','Homing not qualified',action='homing_disabled'),
    Item('Grzanie nieaktywne','Heating not qualified',action='heating_disabled')]),
 'klipper_settings':('Ustawienia','Settings',None,[
    Item('Konfiguracja: Fluidd','Configuration: Fluidd',action='settings_info'),
    Item('Przyciski i OLED','Panel and OLED',action='oled_info')]),
 'klipper_information':('Informacje','Information',None,[
    Item('Wersja MCU','MCU version',action='version'),
    Item('Port UART P4','P4 serial port',action='uart_info'),
    Item('Stan drukarki','Printer status',action='go_status')]),
 'service_tests':('Serwis / Testy','Service / Tests',None,[
    Item('Stan MCU','MCU status',action='mcu_info'),
    Item('OLED i menu','OLED and menu',action='oled_info'),
    Item('UART P4','P4 UART',action='uart_info'),
    Item('Wentylatory','Fans',action='fans_info'),
    Item('Czujniki / ADC','Inputs / ADC',action='adc_info'),
    Item('RFID / filament','RFID / material',action='rfid_info')]),
 'root': ('Menu główne', 'Main Menu', None, [
   Item('Modele','Models','models',icon='models',oem_ram='0x20000448'),
   Item('Konserwacja','Maintenance','maintenance',icon='maintenance',oem_ram='0x20000A8C'),
   Item('Materiał','Material','material',icon='material',oem_ram='0x200002C0'),
   Item('Ustawienia','Settings','settings',icon='settings',oem_ram='0x200008E0'),
   Item('Informacje','Information','information',icon='information',oem_ram='0x200001F8')]),
 'models':('Modele','Models','models',[
   Item('Przeglądaj kartę SD','Browse SD card',action='sd_browser',icon='models')]),
 'maintenance':('Konserwacja','Maintenance','maintenance',[
   Item('Poziomowanie platformy','Platform leveling',action='leveling'),
   Item('Kalibracja położenia dysz','Nozzle alignment',action='alignment'),
   Item('Konserwacja głowic','Hotend maintenance','heads'),
   Item('Nagrzej ekstruder','Heat the extruder',action='heat'),
   Item('Zmiana położenia platformy','Move the platform',action='move_platform'),
   Item('Test wentylatorów','Fan test',action='fan_test')]),
 'heads':('Konserwacja głowic','Hotend maintenance','maintenance',[
   Item('Konserwacja lewej głowicy','Model hotend maintenance',action='maintenance_t0'),
   Item('Konserwacja prawej głowicy','Support hotend maintenance',action='maintenance_t1')]),
 'material':('Materiał','Material','material',[
   Item('Modelowy','Model','model_material'),Item('Podporowy','Support','support_material')]),
 'model_material':('Modelowy','Model','material',[
   Item('Wysuń materiał modelowy','Unload model material',action='unload_t0'),
   Item('Załaduj materiał modelowy','Load model material','load_model')]),
 'support_material':('Podporowy','Support','material',[
   Item('Wysuń materiał podporowy','Unload support material',action='unload_t1'),
   Item('Załaduj materiał podporowy','Load support material','load_support')]),
 'load_model':('Załaduj materiał','Load model material','material',[
   Item('Załaduj z kartridża','Load from cartridge',action='load_cartridge_t0'),
   Item('Załaduj ze szpuli','Load from spool','spool_model',icon='spool')]),
 'load_support':('Załaduj materiał','Load support material','material',[
   Item('Załaduj z kartridża','Load from cartridge',action='load_cartridge_t1'),
   Item('Załaduj ze szpuli','Load from spool','spool_support',icon='spool')]),
 'spool_model':('Model — szpula','Model spool','spool',[
   Item('Wybór materiału','Select material',action='spool_catalog_model')]),
 'spool_support':('Podpora — szpula','Support spool','spool',[
   Item('Wybór materiału','Select material',action='spool_catalog_support')]),
 'settings':('Ustawienia','Settings','settings',[
   Item('Język','Language','language',icon='language'),
   Item('Sortowanie modeli','Models sorting','sorting',icon='sorting'),
   Item('Blokada drzwiczek','Door lock','doorlock',icon='doorlock'),
   Item('Sprawdzanie plików','File checking','filecheck',icon='filecheck'),
   Item('Ignoruj endstopy materiału','Ignore material endstops',action='ignore_filament')]),
 'language':('Język','Language','language',[
   Item('English','English',action='language_en'),
   Item('Polski','Polski',action='language_pl')]),
 'sorting':('Sortowanie modeli','Models sorting','sorting',[
   Item('Data/czas malejąco','Date descending',action='sort_desc'),
   Item('Data/czas rosnąco','Date ascending',action='sort_asc'),
   Item('Nazwa pliku','File name',action='sort_name'),
   Item('Rozmiar pliku','File size',action='sort_size')]),
 'doorlock':('Blokada drzwiczek','Door lock','doorlock',[
   Item('Wyłącz blokadę','Disable door lock',action='door_unlock'),
   Item('Włącz blokadę','Enable door lock',action='door_lock')]),
 'filecheck':('Sprawdzanie plików','File checking','filecheck',[
   Item('Wyłącz sprawdzanie','Disable file checking',action='filecheck_off'),
   Item('Włącz sprawdzanie','Enable file checking',action='filecheck_on')]),
 'information':('Informacje','Information','information',[
   Item('Wersja firmware','Firmware version',action='version'),
   Item('Informacje o drukarce','Printer information',action='printer_info'),
   Item('Pomoc','Help',action='help')]),
 'pause':('Pauza','Pause','material',[
   Item('UWAGA: Gorący ekstruder','Warning: Hot extruder'),
   Item('Kontynuuj druk','Continue print',action='resume'),
   Item('Zatrzymaj druk','Stop print','stop_confirm'),
   Item('Materiał/Konserwacja','Material/Maintenance','pause_material')]),
 'stop_confirm':('Zatrzymaj druk?','Stop print?','material',[
   Item('Nie','No',action='cancel_no'),Item('Tak','Yes',action='cancel_yes')]),
 'pause_material':('Materiał/Konserwacja','Material/Maintenance','material',[
   Item('Materiał','Material','material'),Item('Zapisz wydruk','Save print',action='save_print'),
   Item('Konserwacja głowic','Hotend maintenance','heads')]),
 'cancel':('Anulować?','Cancel operation?','information',[
   Item('Nie','No',action='cancel_no'),Item('Tak','Yes',action='cancel_yes')]),
 'question':('Potwierdź','Confirm','information',[
   Item('Nie','No',action='question_no'),Item('Tak','Yes',action='question_yes')]),
 'model_details':('Szczegóły modelu','Model details','models',[
   Item('Drukuj','Print',action='print_model')]),
}

_FAN_LABELS = ('Head front', 'Model fan', 'Chamber L', 'Chamber R',
               'Rear fan', 'Transfer pair')
_HEAT_LABELS = (('T0', 'Nozzle T0'), ('T1', 'Nozzle T1'),
                ('LEFT', 'Chamber L'), ('RIGHT', 'Chamber R'))
MENU.update({
    'bench_fans': ('Wentylatory', 'Fans', None, [
        Item('Odczyt PWM', 'Fan readings', action='bench_fans_status'),
        Item('Stan próby ręcznej', 'Manual test status', action='bench_manual_status'),
        Item('Anuluj próbę', 'Cancel fan test', action='bench_manual_cancel'),
        *[Item(f'F{i} {label}', f'F{i} {label}', f'bench_fan_{i}')
          for i,label in enumerate(_FAN_LABELS)],
    ]),
    'bench_heat': ('Grzanie', 'Heating', None, [
        Item('Odczyt temperatur', 'Temperature readings', action='bench_heat_status'),
        Item('Stan próby', 'Test status', action='bench_result'),
        Item('Anuluj próbę', 'Cancel heat test', action='bench_heat_stop'),
        *[Item(label,label,'bench_heat_'+channel) for channel,label in _HEAT_LABELS],
    ]),
})
for i, label in enumerate(_FAN_LABELS):
    MENU[f'bench_fan_{i}'] = (label, label, None, [
        Item('Zimna drukarka? Włącz', 'Cold, heaters off? ON', action=f'bench_manual_{i}_on'),
        Item('Wyłącz', 'OFF', action=f'bench_manual_{i}_off'),
        Item('Potwierdź PASS', 'Observed PASS', action=f'bench_manual_{i}_pass'),
        Item('Potwierdź FAIL', 'Observed FAIL', action=f'bench_manual_{i}_fail'),
    ])
for channel, label in _HEAT_LABELS:
    action = 'bench_heat_' + channel
    MENU[action] = (label,label,None,[
        Item('Rozpocznij próbę', 'Start attended test', action=action)])

_BENCH_BRANCHES = {
    'thermal_info': Item('Temperatury','Temperatures','bench_heat'),
    'heating_disabled': Item('Grzanie','Heating','bench_heat'),
    'fans_info': Item('Wentylatory','Fans','bench_fans'),
    'heat': Item('Próba grzania','Heat test','bench_heat'),
    'fan_test': Item('Próba wentylatorów','Fan test','bench_fans'),
}


class OEMMenu:
    """Navigation and explicit dispatch results for attended bench choices."""
    def __init__(self, language='pl', root_back_to_status=True, root_page='root', bench=False):
        if language not in ('pl','en'): raise ValueError('Language must be pl/en')
        if root_page not in ('root','launcher'): raise ValueError('Unknown menu root page')
        self.root_page=root_page
        self.bench=bench
        self.language='en' if root_page=='launcher' else language
        self.root_back_to_status=bool(root_back_to_status)
        self.page=self.root_page;self.stack=[];self.selected=0;self.window=1 if self.root_back_to_status else 0
        self.notice=None;self.last_action=None;self.generation=0

    def _entry(self, node):
        return node.label_pl if self.language=='pl' else node.label_en

    def _title(self):
        n=MENU[self.page]
        return n[0] if self.language=='pl' else n[1]

    def items(self):
        nodes=MENU[self.page][3]
        if self.bench:
            nodes=[_BENCH_BRANCHES.get(node.action,node) for node in nodes]
            if self.page=='service_tests':
                nodes=nodes+[Item('Próba grzania','Heat test','bench_heat')]
        if self.root_page=='launcher' and self.page=='language':
            nodes=[node for node in nodes if node.action=='language_en']
        if self.page==self.root_page and not self.root_back_to_status:return list(nodes)
        return [Item('Powrót','Back',action='back')]+list(nodes)

    def view(self):
        items=self.items();selected=min(self.selected,max(0,len(items)-1))
        if selected!=self.selected:self.selected=selected
        # In a fast root menu, do not redraw a 50x50 bitmap for every UP/DOWN.
        # This is the largest source of 250000-baud navigation latency.
        # Keep the OEM category icon on a submenu page instead.
        icon=MENU[self.page][2]
        if self.page not in ('root','launcher') and items and items[selected].icon:
            icon=items[selected].icon
        return {'page':self.page,'title':self._title(),
                'labels':[self._entry(i) for i in items],
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
        if node.action in ('language_pl','language_en'):
            self.language='pl' if node.action=='language_pl' else 'en'
            self.notice='Polski' if self.language=='pl' else 'English'
            self.last_action=node.action;self.generation+=1
            return 'language'
        if self.bench and node.action and node.action.startswith('bench_'):
            self.last_action=node.action;self.generation+=1
            return 'bench'
        # Other uncertified operations remain view-only.
        self.notice='Wymaga kwalifikacji' if self.language=='pl' else 'Not qualified'
        if node.action in ('version','printer_info','help'):
            self.notice='Odczyt przez Fluidd' if self.language=='pl' else 'Query in Fluidd'
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
                'actuators_enabled_by_menu':False}
