#!/usr/bin/env python3
"""Read the running host identity, G28 configuration and source on disk.

Only Moonraker GET requests and ordinary source-file reads are used. This
tool never imports printer modules, sends G-code or controls a service.
"""
import ast
from hashlib import sha1,sha256
import json
from pathlib import Path
from urllib.request import urlopen

BASE='http://127.0.0.1:7125'


def get(endpoint):
    with urlopen(BASE+endpoint,timeout=5.) as response:
        return json.load(response)


def source_record(path,owner,methods,read_file):
    try:
        data=read_file(path)
        text=data.decode('utf-8')
        tree=ast.parse(text)
        cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name==owner)
        functions={n.name:n for n in cls.body if isinstance(n,ast.FunctionDef)}
        off=[]
        for name,node in functions.items():
            for item in ast.walk(node):
                if (isinstance(item,ast.Call) and isinstance(item.func,ast.Attribute)
                        and item.func.attr=='motor_off'):
                    off.append(dict(method=name,line=item.lineno))
        return dict(path=str(path),sha256=sha256(data).hexdigest(),
                    git_blob=sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest(),
                    methods={name:ast.get_source_segment(text,functions[name])
                             for name in methods if name in functions},motor_off_calls=off)
    except (OSError,UnicodeError,SyntaxError,StopIteration) as exc:
        return dict(path=str(path),error=str(exc))


def audit(request=get,read_file=lambda path:Path(path).read_bytes()):
    info=request('/printer/info')['result']
    status=request('/printer/objects/query?configfile&inventure_native_z&inventure_mcu')['result']['status']
    try:
        help_result=request('/printer/gcode/help')
        help_text=help_result.get('result',{}).get('G28')
        help_error=help_result.get('error')
    except (OSError,ValueError) as exc:
        help_text,help_error=None,str(exc)
    config=status.get('configfile',{})
    settings=config.get('settings',{})
    raw=config.get('config',settings)
    overrides={name:values for name,values in raw.items()
               if name.lower() in ('gcode_macro g28','homing_override','safe_z_home')}
    macros={name:values.get('gcode') for name,values in raw.items()
            if name.lower() in ('gcode_macro inventure_home_x','gcode_macro inventure_home_y',
                               'gcode_macro inventure_home_xy','gcode_macro inventure_home_z',
                               'gcode_macro inventure_home_all')}
    root=Path(info['klipper_path'])
    native=status.get('inventure_native_z',{})
    native_file=Path(native.get('host_module') or root/'klippy/extras/inventure_native_z.py')
    axes={name:values for name,values in settings.items()
          if name in ('stepper_x','stepper_y','stepper_z','idle_timeout','inventure_native_z')}
    return dict(status='READ_ONLY_G28_AUDIT',
        printer={key:info.get(key) for key in ('state','state_message','process_id','software_version',
                                              'klipper_path','config_file')},
        loaded_home={key:native.get(key) for key in ('homing_safety_release','procedure_restore_release',
                        'procedure_baseline_blob','home_procedure_profile','running')},
        configured_overrides=overrides,home_macros=macros,homing_settings=axes,
        g28_help=help_text,g28_help_error=help_error,
        source_on_disk=dict(
            core=source_record(root/'klippy/extras/homing.py','PrinterHoming',('cmd_G28',),read_file),
            native=source_record(native_file,'InventureNativeZ',('_home_limit','_prepare_t0'),read_file)),
        source_note='File hashes describe disk bytes; live identity above comes from Moonraker. No runtime modules were imported.',
        gcode_sent=False,service_changed=False,configuration_written=False)


if __name__=='__main__':
    try:
        result=audit()
    except (OSError,ValueError,KeyError) as exc:
        result=dict(status='READ_ONLY_G28_AUDIT_UNAVAILABLE',error=str(exc),
                    gcode_sent=False,service_changed=False,configuration_written=False)
    print(json.dumps(result,indent=2,ensure_ascii=False))
