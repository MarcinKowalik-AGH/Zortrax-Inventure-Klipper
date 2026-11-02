# SPDX-License-Identifier: GPL-3.0-or-later
"""Inventure r0323 menu reconstruction: M300 M11-style optional UI-only layer.

A menu action without a certified Inventure Pxx executor is *view-only*.
No G-code, MCU commands, pins, heater, motor, NVM or SD side effects.
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

class OEMMenu:
    """Navigation only: all unqualified hardware-affecting choices are inert."""
    def __init__(self, language='pl'):
        if language not in ('pl','en'): raise ValueError('Language must be pl/en')
        self.language=language
        self.page='root';self.stack=[];self.selected=0;self.window=0
        self.notice=None;self.last_action=None;self.generation=0

    def _entry(self, node):
        return node.label_pl if self.language=='pl' else node.label_en

    def _title(self):
        n=MENU[self.page]
        return n[0] if self.language=='pl' else n[1]

    def items(self):
        nodes=MENU[self.page][3]
        if self.page=='root':return list(nodes)
        return [Item('Powrót','Back',action='back')]+list(nodes)

    def view(self):
        items=self.items();selected=min(self.selected,max(0,len(items)-1))
        if selected!=self.selected:self.selected=selected
        icon=MENU[self.page][2]
        if items and items[selected].icon:icon=items[selected].icon
        return {'page':self.page,'title':self._title(),
                'labels':[self._entry(i) for i in items],
                'selected':self.selected,'window':self.window,
                'icon':icon,'language':self.language,'notice':self.notice,
                'generation':self.generation}

    def move(self, direction):
        if direction not in (-1,1):raise ValueError('direction -1/+1')
        prev=self.selected
        self.selected=min(len(self.items())-1,max(0,self.selected+direction))
        if prev!=self.selected:self.notice=None;self.generation+=1
        # OEM main menu has 5 branches: maintain a 4-row viewport without
        # hiding the selected first row. Subs use pinned Back plus 3 rows.
        if self.page=='root' and len(self.items())>4:
            if self.selected<self.window:self.window=self.selected
            elif self.selected>self.window+3:self.window=self.selected-3
        if self.page!='root' and len(self.items())>4:
            if self.selected==0:self.window=1
            elif self.selected<self.window:self.window=self.selected
            elif self.selected>self.window+2:self.window=self.selected-2
        return self.selected

    def back(self):
        if not self.stack:return False
        self.page,self.selected,self.window=self.stack.pop()
        self.notice=None;self.generation+=1
        return True

    def select(self):
        items=self.items()
        if not items:return 'empty'
        node=items[self.selected]
        if node.action=='back':self.back();return 'back'
        if node.child:
            self.stack.append((self.page,self.selected,self.window))
            self.page=node.child;self.selected=0;self.window=1;self.notice=None
            self.generation+=1;return 'submenu'
        if node.action in ('language_pl','language_en'):
            self.language='pl' if node.action=='language_pl' else 'en'
            self.notice='Polski' if self.language=='pl' else 'English'
            self.last_action=node.action;self.generation+=1
            return 'language'
        # UI cannot execute motion, heat, calibration, NVM, SD or print.
        self.notice='Wymaga kwalifikacji' if self.language=='pl' else 'Not qualified'
        if node.action in ('version','printer_info','help'):
            self.notice='Odczyt przez Fluidd' if self.language=='pl' else 'Query in Fluidd'
        self.last_action=node.action;self.generation+=1
        return 'inert'

    def status(self):
        return {'page':self.page,'selected':self.selected,'window':self.window,
                'stack_depth':len(self.stack),'language':self.language,
                'notice':self.notice,'last_action':self.last_action,
                'generation':self.generation, 'ui_only':True,
                'actuators_enabled_by_menu':False}
