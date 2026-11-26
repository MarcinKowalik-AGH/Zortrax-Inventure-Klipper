#!/usr/bin/env python3
"""Compile the isolated held-home overlay with the existing pinned R23 builder."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from apply_inventure_home_r24 import ROOT,RELEASE,overlay,replace

def build(kit,output):
    with tempfile.TemporaryDirectory(prefix='inventure-r24-build-source-') as temp:
        root=Path(temp)
        source=overlay(root/'service_mcu/control_r6/SOURCE_MCU')
        (root/'orange_pi').symlink_to(ROOT/'orange_pi',target_is_directory=True)
        script=source/'build_thermal_test.py'
        text=script.read_text()
        text=replace(text,'    base.apply_rc7(root, original, expected)\n',
            '    base.apply_rc7(root, original, expected)\n'
            f'    sys.path.insert(0, {str(ROOT/"ci")!r})\n'
            '    from apply_inventure_home_r24 import patch_stepper\n'
            '    patch_stepper(root)\n')
        text=replace(text,"'inventure_board.c', 'inventure_control.inc', 'inventure_z_home.inc',",
            "'inventure_board.c', 'inventure_control.inc', 'inventure_held_service.inc', 'inventure_z_home.inc',")
        text=replace(text,"    provenance['version'] = MCU_RELEASE\n",
            "    provenance['version'] = MCU_RELEASE\n"
            "    provenance['patched_sha256']['src/stepper.c']=base.sha((root/'src/stepper.c').read_bytes())\n")
        script.write_text(text)
        subprocess.run([sys.executable,str(script),str(kit),str(output)],check=True)
    dictionary=json.loads((output/'klipper.dict').read_text())
    if dictionary['config'].get('INVENTURE_NATIVE_HELD_SERVICE_ABI')!=1:
        raise RuntimeError('Compiled firmware lacks the held-service ABI')
    if dictionary['config']['INVENTURE_MCU_RELEASE']!=RELEASE:
        raise RuntimeError('Compiled R24 firmware identity differs')
    if 'inventure_native_held_service owner=%u' not in dictionary['commands']:
        raise RuntimeError('Held-service command missing from compiled dictionary')
    (output/'R24_HOME_BUILD.json').write_text(json.dumps(dict(
        status='PASS_R24_ARM_BUILD',mcu_release=RELEASE,native_held_service_abi=1,
        native_step_queue_admission_checked=True,bootloader='InventureBoot 1.7',
        uart='saved P4 journal; unchanged from R23',hardware_tested=False),indent=2)+'\n')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('kit',type=Path);parser.add_argument('output',type=Path)
    args=parser.parse_args();build(args.kit.resolve(),args.output.resolve())
