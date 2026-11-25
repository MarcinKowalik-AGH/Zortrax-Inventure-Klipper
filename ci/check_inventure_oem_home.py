#!/usr/bin/env python3
"""Load the OEM Home recovery with the field configuration, without an MCU."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import inventure_bootstrap as bootstrap
import inventure_oem_home_restore as restoration
from inventure_bootstrap_config import stage_config,_set_scalar


def check(destination):
    destination.mkdir(parents=True,exist_ok=False)
    spec=importlib.util.spec_from_file_location('inventure_oem_recovery_installer',ROOT/'install-new-host.py')
    installer=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    source=destination/'source'
    shutil.copytree(ROOT/'tests/fixtures/field_config_20261125',source)
    gcodes=destination/'gcodes';gcodes.mkdir()
    with bootstrap.procedure_profile(restoration.PROFILE):
        host=bootstrap.prepare_host(destination/'runtime')
        module=installer.updater.klipper_config_module(host)
        _set_scalar(module,source/'printer.cfg','virtual_sdcard','path',str(gcodes))
        plan=stage_config(host,destination/'config',source/'printer.cfg',
                          serial='/dev/ttyS5',baud=750000,gcodes_path=gcodes)
        config=Path(plan['config_path'])
        installer.require_identical_configuration(host,source/'printer.cfg',config)
        result=subprocess.run([sys.executable,str(ROOT/'ci/check_native_nozzle_startup.py'),
                               '--klipper',str(host),'--config',str(config)],
                              text=True,capture_output=True,timeout=90)
        (destination/'startup.log').write_text(result.stdout+result.stderr)
        if result.returncode:
            raise RuntimeError('OEM recovery startup failed: '+result.stdout+result.stderr)
        proof=bootstrap.verify_host(host)['oem_home_restoration']
    report=dict(status='PASS_OEM_HOME_RECOVERY',profile=restoration.PROFILE,
                reference_commit=restoration.REFERENCE_COMMIT,restoration=proof,
                configuration_values_unchanged=True,offline_startup=True,
                serial_opened=False,firmware_flashed=False,hardware_tested=False,
                macros=plan['macros'])
    (destination/'OEM_HOME_RECOVERY.json').write_text(json.dumps(report,indent=2)+'\n')
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--destination',type=Path)
    args=parser.parse_args()
    if args.destination:
        print(json.dumps(check(args.destination.absolute()),indent=2))
    else:
        with tempfile.TemporaryDirectory(prefix='inventure-oem-home-check-') as directory:
            print(json.dumps(check(Path(directory)/'verified'),indent=2))
