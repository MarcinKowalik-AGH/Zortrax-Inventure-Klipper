#!/usr/bin/env python3
"""Compile and execute the actual MCU sources against register/timer mocks."""
from pathlib import Path
import json
import shutil
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
PROGRAMS=('test_control_board.c','test_t0_heat_board.c','test_motor_board.c','test_load_stream.c',
          'test_factory_session.c','test_z_home.c')
def main():
    compiler=shutil.which('gcc')
    if not compiler:raise SystemExit('A C11 host compiler (gcc) is required for register mocks')
    rows=[]
    with tempfile.TemporaryDirectory(prefix='inventure-r6-register-tests-') as tmp:
        for name in PROGRAMS:
            binary=Path(tmp)/Path(name).stem
            command=[compiler,'-std=c11','-O2','-Wno-pointer-to-int-cast',
                '-I','TESTS/mock','-I','SOURCE_MCU','-I','TESTS/support','-I','TESTS',
                str(Path('TESTS')/name),'-o',str(binary)]
            subprocess.run(command,cwd=ROOT,check=True)
            result=subprocess.run([str(binary)],cwd=ROOT,check=True,capture_output=True,text=True)
            print(result.stdout,end='');rows.append(dict(program=name,status='PASS',output=result.stdout))
    print(json.dumps(dict(status='PASS_CONTROL_R6_REGISTER_TESTS',programs=rows),indent=2))
if __name__=='__main__':main()
