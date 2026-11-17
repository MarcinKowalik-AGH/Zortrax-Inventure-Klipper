#!/usr/bin/env python3
"""Package the verified Inventure host and MCU from one committed release."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo


def package(build, output):
    root = Path(__file__).resolve().parents[1]
    if subprocess.check_output(
            ['git', 'status', '--porcelain', '--untracked-files=no'], cwd=root, text=True):
        raise ValueError('Release packaging requires committed source files')
    release = json.loads((root/'orange_pi/RELEASE.json').read_text())
    pad = release['nozzle_pad_calibration']
    report = json.loads((build/'THERMAL_TEST_VERIFY.json').read_text())
    dictionary = json.loads((build/'klipper.dict').read_text())
    ivb = (build/'INVENTUR.IVB').read_bytes()
    assert report['mcu_release'] == pad['mcu_release']
    assert report['heartbeat_timeout_s'] == 5
    assert dictionary['config']['INVENTURE_HEARTBEAT_TIMEOUT_MS'] == 5000
    assert report['nozzle_pad_measurement_band_c'] == pad['measurement_band_c'] == [95, 105]
    assert report['nozzle_service_target_max_c'] == 310
    assert report['boot17_abi'] == 1 and report['forced_baud'] == 0
    assert ivb[:7] == b'INVB001' and len(ivb) == report['ivb_bytes']
    assert sha256(ivb).hexdigest() == report['ivb_sha256'] == pad['ivb_sha256']
    assert sha256(ivb[64:]).hexdigest() == report['application_sha256']
    assert ivb[32:64] == sha256(ivb[64:]).digest()
    for name, expected in report['sources_sha256'].items():
        source = root/'service_mcu/control_r6/SOURCE_MCU'/name
        assert sha256(source.read_bytes()).hexdigest() == expected, name

    output.mkdir(parents=True, exist_ok=True)
    mcu_zip = output/pad['package_filename']
    names = ('INVENTUR.IVB', 'THERMAL_TEST_VERIFY.json', 'klipper.dict')
    files = {name: (build/name).read_bytes() for name in names}
    files['README_PL.md'] = (root/'docs/INVENTURE_PAD_XYZ_R14.md').read_bytes()
    files['SHA256SUMS'] = ''.join(
        f'{sha256(data).hexdigest()}  {name}\n' for name, data in files.items()).encode('ascii')
    with ZipFile(mcu_zip, 'w') as archive:
        for name, data in files.items():
            entry = ZipInfo(name, (2026, 11, 17, 0, 0, 0))
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data, compress_type=ZIP_DEFLATED, compresslevel=9)
    with ZipFile(mcu_zip) as archive:
        assert archive.testzip() is None
    if pad.get('zip_sha256'):
        assert sha256(mcu_zip.read_bytes()).hexdigest() == pad['zip_sha256']

    host_zip = output/pad['host_package_filename']
    subprocess.run(['git', 'archive', '--format=zip', '--prefix=inventure-orange-pi/',
                    '--output='+str(host_zip), 'HEAD'], cwd=root, check=True)
    with ZipFile(host_zip) as archive:
        assert archive.testzip() is None
        prefix = 'inventure-orange-pi/'
        assert archive.read(prefix+'orange_pi/RELEASE.json') == (root/'orange_pi/RELEASE.json').read_bytes()
        assert archive.read(prefix+'install-orange-pi.py') == (root/'install-orange-pi.py').read_bytes()
    assets = (mcu_zip, host_zip)
    digests = {asset.name: sha256(asset.read_bytes()).hexdigest() for asset in assets}
    (output/'SHA256SUMS').write_text(''.join(
        f'{digest}  {name}\n' for name, digest in digests.items()))
    print(json.dumps(dict(tag=pad['release_tag'], assets=digests), indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    package(args.build.resolve(), args.output.resolve())
