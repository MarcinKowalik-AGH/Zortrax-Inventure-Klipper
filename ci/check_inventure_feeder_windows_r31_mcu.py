"""Run the production cassette code with the existing MCU register mocks."""
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from apply_inventure_home_r24 import ROOT, overlay, replace
from apply_inventure_feeder_windows_r31 import patch_feed_windows


def check():
    with TemporaryDirectory(prefix='inventure-r31-registers-') as directory:
        root = Path(directory)
        source = overlay(root/'SOURCE_MCU')
        patch_feed_windows(source)
        shutil.copytree(ROOT/'service_mcu/control_r6/TESTS', root/'TESTS')
        factory = root/'TESTS/test_factory_session.c'
        factory.write_text(replace(factory.read_text(), 'int main(void) {', 'int factory_regression_main(void) {'))
        test = 'test_cassette_windows.c'
        shutil.copy2(ROOT/'restored/cassette_windows_r31'/test, root/'TESTS')
        subprocess.run([
            'gcc', '-std=c11', '-O2', '-Wno-pointer-to-int-cast',
            '-I', 'TESTS/mock', '-I', 'SOURCE_MCU', '-I', 'TESTS/support', '-I', 'TESTS',
            'TESTS/'+test, '-o', 'test-cassette-windows',
        ], cwd=root, check=True)
        subprocess.run(['./test-cassette-windows'], cwd=root, check=True)


if __name__ == '__main__':
    check()
