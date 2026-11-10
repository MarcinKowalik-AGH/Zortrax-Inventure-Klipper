# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure Zortrax action catalogue and one-use view authority; no printer I/O.

Sources: r0323 OLED/menu audit 2026-10-06 and Inventure/M300 port 2026-11-03.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile


MODEL_MATERIALS = ((0,'Z-ABS'),(26,'Z-ABS v2'),(12,'Z-ASA Pro'),(8,'Z-ESD'),
    (27,'Z-ESD v2'),(15,'Z-FLEX'),(2,'Z-GLASS'),(3,'Z-HIPS'),(16,'Z-NYLON'),
    (4,'Z-PCABS'),(5,'Z-PETG'),(10,'Z-PLA'),(11,'Z-PLA Pro'),(14,'Z-SEMIFLEX'),
    (1,'Z-ULTRAT'),(6,'Z-ULTRAT Plus'),(128,'External'))
SUPPORT_MATERIALS = ((7,'Z-SUPPORT'),(13,'Z-SUPPORT Plus'),
    (17,'Z-SUPPORT Premium'),(23,'BASF Ultrafuse BVOH'),(127,'External Support'))

READ_ACTIONS = frozenset(('version','printer_info','help','job_info','thermal_info',
    'settings_info','oled_info','mcu_info','uart_info','fans_info','adc_info','rfid_info',
    'save_print','motion_disabled','homing_disabled','heating_disabled'))
NATIVE_ACTIONS = {'leveling':'inventure_leveling', 'alignment':'inventure_alignment',
    'pause':'inventure_cycle','resume':'inventure_cycle','cancel_yes':'inventure_cycle',
    'print_model':'inventure_preflight','check_model':'inventure_preflight','filecheck_on':'inventure_preflight',
    'filecheck_off':'inventure_preflight','ignore_filament':'inventure_filament'}
SERVICE_ACTIONS = {
    'load_cartridge_t0':('AUTO',0),'load_cartridge_t1':('AUTO',1),
    'unload_t0':('UNLOAD',0),'unload_t1':('UNLOAD',1),
    'select_t0':('SELECT',0),'select_t1':('SELECT',1),
    'clean_t0':('CLEAN',0),'clean_t1':('CLEAN',1),
    'purge_t0':('PURGE',0),'purge_t1':('PURGE',1),
    'manual_load_t0':('MANUAL',0),'manual_load_t1':('MANUAL',1),
    'manual_unload_t0':('MANUAL',0),'manual_unload_t1':('MANUAL',1),
}
CHOICE_ACTIONS = frozenset(('sd_browser','spool_catalog_model','spool_catalog_support',
    'heat','maintenance_t0','maintenance_t1','move_platform','fan_test'))
SETTING_ACTIONS = frozenset(('sort_desc','sort_asc','sort_name','sort_size',
    'language_en','language_pl','door_lock','door_unlock'))
NAV_ACTIONS = frozenset(('back','go_status','cancel_no','question_no','question_yes'))
ALL_ACTIONS = READ_ACTIONS | set(NATIVE_ACTIONS) | set(SERVICE_ACTIONS) | CHOICE_ACTIONS | SETTING_ACTIONS | NAV_ACTIONS


class MenuError(ValueError):pass


class View:
    """Single-use token is tied to a view, arguments, live owner and deadline."""
    def __init__(self, now, kind, title, options, *, binding=None, timeout=120., data=None):
        self.token=secrets.token_hex(16);self.created=now;self.deadline=now+timeout
        self.kind=kind;self.title=title;self.options=tuple(options);self.selected=0
        self.binding=binding;self.data=dict(data or {});self.used=False;self.generation=0
        self.released=False

    def move(self, delta):
        if delta not in (-1,1):raise MenuError('Direction must be -1 or 1')
        selected=min(max(0,self.selected+delta),max(0,len(self.options)-1))
        if selected!=self.selected:
            self.selected=selected;self.generation+=1;self.token=secrets.token_hex(16);self.released=False

    def claim(self, token, now, binding):
        if self.used or token!=self.token or now>=self.deadline or binding!=self.binding:
            raise MenuError('Menu view expired or its procedure/job changed; open it again')
        self.used=True

    def render(self):
        # Keep Back in the first row, like the existing M300 navigation.
        window=max(1,self.selected-3) if self.selected else 1
        return dict(page='zortrax:'+self.token,title=self.title,
            labels=[str(option[0]) for option in self.options],selected=self.selected,
            window=window,icon=None,notice=None,language='en',generation=self.generation)

    def status(self):
        return dict(token=self.token,kind=self.kind,title=self.title,
            options=[x[0] for x in self.options],selected=self.selected,
            generation=self.generation,data=self.data,used=self.used)


class SettingsStore:
    """Host presentation preferences only; no factory Flash or boot journal."""
    DEFAULTS={'sorting':'desc','language':'en'}
    def __init__(self, filename=None):
        self.path=None if filename is None else Path(filename).expanduser()
        self.value=dict(self.DEFAULTS)
        if self.path is not None and self.path.exists():
            if self.path.is_symlink():raise MenuError('Menu settings must be a regular file')
            envelope=json.loads(self.path.read_text())
            value=envelope.get('settings')
            if envelope.get('schema')!=1 or not isinstance(value,dict):raise MenuError('Wrong menu settings schema')
            self._validate(value)
            if hashlib.sha256(self._bytes(value)).hexdigest()!=envelope.get('sha256'):
                raise MenuError('Menu settings checksum differs')
            self.value=dict(value)

    @staticmethod
    def _bytes(value):return json.dumps(value,sort_keys=True,separators=(',',':')).encode()

    @classmethod
    def _validate(cls,value):
        if set(value)!=set(cls.DEFAULTS) or value['sorting'] not in ('asc','desc','name','size') or value['language'] not in ('en','pl'):
            raise MenuError('Invalid menu presentation settings')

    def set(self, **updates):
        value=dict(self.value,**updates);self._validate(value)
        if value==self.value:return False
        if self.path is not None:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            if self.path.is_symlink():raise MenuError('Menu settings became a symlink')
            payload=json.dumps(dict(schema=1,settings=value,
                sha256=hashlib.sha256(self._bytes(value)).hexdigest()),indent=2)+'\n'
            fd,name=tempfile.mkstemp(prefix='.'+self.path.name,dir=self.path.parent)
            try:
                with os.fdopen(fd,'w') as f:f.write(payload);f.flush();os.fsync(f.fileno())
                os.replace(name,self.path)
                parent=os.open(str(self.path.parent),os.O_RDONLY)
                try:os.fsync(parent)
                finally:os.close(parent)
            finally:
                if os.path.exists(name):os.unlink(name)
        self.value=value
        return True


def source_identity(root, filename, maximum=16*1024*1024):
    """Read-only file identity for preview. The native P15 owner validates start."""
    root=Path(root).resolve()
    if not isinstance(filename,str) or not filename or any(ord(c)<32 for c in filename):
        raise MenuError('Invalid model filename')
    candidate=root/filename
    if Path(filename).is_absolute() or '..' in Path(filename).parts or candidate.is_symlink():
        raise MenuError('Model path is not a regular file below the model directory')
    path=candidate.resolve()
    if not path.is_relative_to(root) or not path.is_file():raise MenuError('Model is outside the file directory')
    digest=hashlib.sha256()
    with path.open('rb') as f:
        before=os.fstat(f.fileno())
        if before.st_size>maximum:raise MenuError('Model exceeds the preview size limit')
        count=0
        while True:
            data=f.read(65536)
            if not data:break
            count+=len(data)
            if count>maximum:raise MenuError('Model grew beyond the preview limit')
            digest.update(data)
        after=os.fstat(f.fileno())
    fields=lambda st:(st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)
    if fields(before)!=fields(after) or count!=after.st_size:raise MenuError('Model changed during preview')
    return dict(name=filename,size=count,sha256=digest.hexdigest(),identity=fields(after))
