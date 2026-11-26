#!/usr/bin/env python3
"""Run the R24 ownership overlay against production C and register mocks."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
from apply_inventure_home_r24 import ROOT,overlay

def check():
    with tempfile.TemporaryDirectory(prefix='inventure-r24-held-registers-') as temp:
        root=Path(temp)
        overlay(root/'SOURCE_MCU')
        shutil.copytree(ROOT/'service_mcu/control_r6/TESTS',root/'TESTS')
        factory=root/'TESTS/test_factory_session.c'
        factory.write_text(factory.read_text().replace('int main(void) {','int factory_regression_main(void) {'))
        shutil.copy2(ROOT/'restored/operator_home_r24/test_held_service.c',root/'TESTS')
        command=['gcc','-std=c11','-O2','-Wno-pointer-to-int-cast',
                 '-I','TESTS/mock','-I','SOURCE_MCU','-I','TESTS/support','-I','TESTS',
                 'TESTS/test_held_service.c','-o',str(root/'held-service')]
        subprocess.run(command,cwd=root,check=True)
        result=subprocess.run([str(root/'held-service')],cwd=root,capture_output=True,text=True)
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)
        return dict(status='PASS_R24_HELD_SERVICE_REGISTER_TESTS',output=result.stdout,
                    firmware_flashed=False,hardware_tested=False)

if __name__=='__main__':print(json.dumps(check(),indent=2))
