#!/usr/bin/env python3
"""Read-only forensic analysis of Inventure Klipper logs (plain or ZIP).

Distinguishes MCU timer shutdown, MCU local safety faults and failed
NATIVE/SERVICE ownership receipts. Never sends G-code or resets the printer.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import re
import zipfile

MCU_SHUTDOWN = re.compile(r"MCU 'mcu' shutdown: (.+)")
COMMAND = re.compile(r'"script":"(INVENTURE_[A-Za-z0-9_]+)(?:[^"\\]*)')
UART = re.compile(r'bytes_write=(\d+) bytes_read=(\d+) bytes_retransmit=(\d+) bytes_invalid=(\d+)')
LAST_CLOCK = re.compile(r'shutdown clock=(\d+) static_string_id=(.+)')
PAIR = re.compile(r'\b([a-z_]+)=(\d+)')
EVENT = 'INVENTURE_OWNER_AUDIT '

def read_log(path):
    path=Path(path)
    if path.suffix.lower()=='.zip':
        with zipfile.ZipFile(path) as archive:
            files=[n for n in archive.namelist()
                   if n.lower().endswith('.log') and not n.startswith('__MACOSX/')]
            if len(files)!=1:
                raise ValueError('ZIP must contain exactly one Klipper .log file')
            data=archive.read(files[0])
    else:
        data=path.read_bytes()
    return data.replace(b'\0',b'').decode('utf-8','replace').splitlines()

def analyze(lines):
    incidents,violations,transfers,diagnostics=[],[],[],[]
    latest_command=latest_stats=latest_uart=None
    native_frame=None
    request=None
    verified=None
    last_shutdown=None
    for number,line in enumerate(lines,1):
        cm=COMMAND.search(line)
        if cm:
            latest_command=cm.group(1)
        if 'Stats ' in line and 'sysload=' in line:
            m=re.search(r'\bsysload=([0-9.]+)',line)
            if m:latest_stats={'sysload':float(m.group(1)),'line':number}
        u=UART.search(line)
        if u:
            fields=('bytes_write','bytes_read','bytes_retransmit','bytes_invalid')
            latest_uart=dict(zip(fields,map(int,u.groups())))
            written=latest_uart['bytes_write']
            latest_uart['retransmit_fraction']=round(
                latest_uart['bytes_retransmit']/written,4) if written else None
            latest_uart['line']=number
        if EVENT in line:
            raw=line.split(EVENT,1)[1].strip()
            try: record=json.loads(raw)
            except json.JSONDecodeError:
                violations.append(dict(line=number,kind='invalid_owner_audit_record'))
                continue
            phase=record.get('phase')
            transfers.append(dict(line=number,phase=phase,
                                  identity=[record.get(k) for k in ('owner','run','token')]))
            if phase=='klipper_xy_certified':
                native_frame=number;request=verified=None
            elif phase=='service_handoff_requested':
                if native_frame is None:
                    violations.append(dict(line=number,kind='service_without_klipper_xy'))
                if request is not None and verified is None:
                    violations.append(dict(line=number,kind='double_service_handoff_request'))
                request=tuple(record.get(k) for k in ('owner','run','token'))
                verified=None
            elif phase=='service_handoff_verified':
                keys=tuple(record.get(k) for k in ('owner','run','token'))
                if request is None or keys!=request:
                    violations.append(dict(line=number,kind='mcu_handoff_identity_mismatch'))
                if record.get('mode')!='NATIVE' or record.get('verified') is not True:
                    violations.append(dict(line=number,kind='mcu_handoff_not_native'))
                verified=keys if request==keys else None
            elif phase=='klipper_xy_imported':
                keys=tuple(record.get(k) for k in ('owner','run','token'))
                if verified is None or verified!=keys:
                    violations.append(dict(line=number,kind='klipper_pose_import_without_handoff'))
                native_frame=request=verified=None
        if 'inventure_scheduler_state ' in line or 'INVENTURE_TIMING {' in line:
            if 'INVENTURE_TIMING {' in line:
                try:sample=json.loads(line.split('INVENTURE_TIMING ',1)[1])
                except (ValueError,IndexError):continue
            else:
                sample={k:int(v) for k,v in PAIR.findall(
                    line.split('inventure_scheduler_state ',1)[1])}
            if 'resync' in sample:
                if diagnostics:
                    prev=diagnostics[-1]
                    changes={k:(int(sample[k])-int(prev[k]))&0xffffffff
                             for k in ('resync','skipped') if k in sample and k in prev}
                    if any(changes.values()):
                        violations.append(dict(line=number,kind='sampler_deadlines_missed',
                                               changes=changes))
                diagnostics.append(dict(sample,line=number))
        reason=MCU_SHUTDOWN.search(line)
        if reason:
            why=reason.group(1).strip()
            # Klipper prints the same event again in its receive-queue dump.
            if incidents and incidents[-1]['reason']==why and number-incidents[-1]['line']<250:
                continue
            kind=('mcu_scheduler_shutdown' if 'Timer too close' in why else
                  'mcu_safety_shutdown' if 'Inventure' in why else 'mcu_other_shutdown')
            incidents.append(dict(line=number,kind=kind,reason=why,
                                  last_home_command=latest_command,
                                  serial=dict(latest_uart) if latest_uart else None,
                                  host=dict(latest_stats) if latest_stats else None))
            last_shutdown=number
        clock=LAST_CLOCK.search(line)
        if clock and incidents and (
                incidents[-1].get('clock_at_shutdown') is None):
            if clock.group(2).strip()==incidents[-1]['reason']:
                incidents[-1]['clock_at_shutdown']=int(clock.group(1))
    if request is not None and verified is None and last_shutdown is not None:
        violations.append(dict(line=last_shutdown,kind='incomplete_service_handoff_at_shutdown'))
    types=Counter(x['kind'] for x in incidents)
    warnings=[]
    if latest_uart and latest_uart['retransmit_fraction'] is not None:
        if latest_uart['retransmit_fraction']>=.05:
            warnings.append('High UART retransmissions; correlation is NOT proof of timer root cause')
        if latest_uart['bytes_invalid']:
            warnings.append('UART invalid bytes were observed')
    if not transfers:
        warnings.append('No R21 ownership audit events found; historical R18/R20 ownership cannot be proven from this log')
    return dict(incidents=incidents,violations=violations,
                event_counts=dict(types),scheduler_samples=len(diagnostics),
                ownership_events=len(transfers),last_uart=latest_uart,
                warnings=warnings,
                outcome=('faults_detected' if incidents or violations else
                         'no_fault_observed_in_supplied_log'))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('log',type=Path,help='Klipper log or ZIP containing exactly one .log')
    parser.add_argument('--output',type=Path,help='Optional JSON report path')
    args=parser.parse_args()
    result=analyze(read_log(args.log))
    result['input_name']=args.log.name
    report=json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+'\n'
    if args.output:
        args.output.write_text(report)
    print(report,end='')
    return 0 # diagnostic read-only; an incident should not break unrelated automation
if __name__=='__main__':
    raise SystemExit(main())
