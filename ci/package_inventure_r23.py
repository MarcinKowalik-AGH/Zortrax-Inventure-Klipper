#!/usr/bin/env python3
"""Package the committed R23 MCU candidate with its exact software evidence."""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from ibtool_reference import inspect, LIMIT

RELEASE = 'RC8-CONTROL-R7-HOMING-R23-OLED'
SCREENS = ('startup', 'waiting', 'disconnected', 'timer_error',
           'cable_error', 'door_error', 'tray_error', 'INVENTURE_R23_SCREENS')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def package(build, previews, output, leveling_previews=None, menu_previews=None, operator_cycles=None):
    status = subprocess.check_output(
        ['git', 'status', '--porcelain'], cwd=ROOT, text=True)
    require(not status, 'Commit the candidate source before packaging')
    commit = subprocess.check_output(
        ['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    verify = json.loads((build/'THERMAL_TEST_VERIFY.json').read_text())
    report = json.loads((build/'BUILD_REPORT.json').read_text())
    dictionary = json.loads((build/'klipper.dict').read_text())
    conf = dictionary['config']
    require(verify['mcu_release'] == verify['motor_release'] == RELEASE
            == conf['INVENTURE_MCU_RELEASE'], 'Wrong firmware release')
    require(verify['tested_on_printer'] is False
            and report['physical_tests'] is False, 'Candidate status changed')
    for key in ('INVENTURE_DISPLAY_ERROR_ABI', 'INVENTURE_LOCAL_OLED_ANIMATION_ABI',
                'INVENTURE_LOCAL_OLED_NATIVE_GRAY4', 'INVENTURE_LOCAL_SPLASH_ABI',
                'INVENTURE_NATIVE_SERVICE_HANDOFF_ABI', 'INVENTURE_NATIVE_THERMAL_PROFILE_ABI',
                'INVENTURE_FAULT_REASON_ABI', 'INVENTURE_SERVICE_NATIVE_SPLIT_ABI',
                'INVENTURE_POLICY_STATUS_ABI', 'INVENTURE_SCHEDULER_ABI'):
        require(conf.get(key) == 1, 'Missing MCU feature: '+key)
    require(conf.get('INVENTURE_OLED_HOSTLINK_TIMEOUT_MS') == 1500,
            'Wrong display lease')
    source = ROOT/'service_mcu/control_r6/SOURCE_MCU'
    for name, expected in verify['sources_sha256'].items():
        require(digest((source/name).read_bytes()) == expected,
                'Build/source mismatch: '+name)
    ivb = (build/'INVENTUR.IVB').read_bytes()
    binary = (build/'klipper.bin').read_bytes()
    metadata = inspect(ivb)
    require(ivb[64:] == binary, 'IVB/BIN mismatch')
    require(digest(ivb) == verify['ivb_sha256'], 'IVB report mismatch')
    require(digest(binary) == verify['application_sha256'], 'BIN report mismatch')
    for name, expected in report['sha256'].items():
        require(digest((build/name).read_bytes()) == expected,
                'Build report mismatch: '+name)

    validation = ROOT/'docs/validation'
    evidence = json.loads((validation/'INVENTURE_R23_SOFTWARE_2026-11-24.json').read_text())
    require(evidence['ivb_sha256'] == digest(ivb), 'Evidence/IVB mismatch')
    files = {name: (build/name).read_bytes() for name in (
        'INVENTUR.IVB', 'klipper.bin', 'klipper.dict', 'BUILD_REPORT.json',
        'THERMAL_TEST_VERIFY.json', 'build.config', 'source_manifest.json')}
    files['README_PL.md'] = (ROOT/'docs/INVENTURE_R23_M300_DISPLAY_PARITY.md').read_bytes()
    files['HOST_UPDATE.md'] = (ROOT/'docs/INVENTURE_R23_OPERATOR_MENU.md').read_bytes()
    files['HOST_LEVELING_RC4.md'] = (ROOT/'docs/INVENTURE_R23_LEVELING_STAGES.md').read_bytes()
    files['HOST_LEVELING_RC3.md'] = (ROOT/'docs/INVENTURE_R23_LEVELING_OLED.md').read_bytes()
    files['HOST_SERVICE_ENTRY.md'] = (ROOT/'docs/INVENTURE_R23_LEVELING_SERVICE_ENTRY.md').read_bytes()
    for name in ('INVENTURE_R23_LEVELING_PHYSICAL_2026-11-25.json', 'INVENTURE_R23_LEVELING_UI.json'):
        files['validation/'+name] = (validation/name).read_bytes()
    files['validation/INVENTURE_R23_LEVELING_STAGES.json'] = (validation/'INVENTURE_R23_LEVELING_STAGES.json').read_bytes()
    for name in ('INVENTURE_OEM_MENU_R0323.json', 'INVENTURE_R23_OPERATOR_MENU.json'):
        files['validation/'+name] = (validation/name).read_bytes()
    for name in ('INVENTURE_R23_M300_DISPLAY_PROVENANCE.json',
                 'INVENTURE_R23_SOFTWARE_2026-11-24.json', 'INVENTURE_R23_RLE_BENCHMARK.json'):
        files['validation/'+name] = (validation/name).read_bytes()
    for name in SCREENS:
        data = (previews/(name+'.png')).read_bytes()
        require(data.startswith(b'\x89PNG\r\n\x1a\n'), 'Invalid preview: '+name)
        files['preview/'+name+'.png'] = data
    if leveling_previews is not None:
        proof_name = 'INVENTURE_R23_LEVELING_PREVIEW.json'
        proof = json.loads((leveling_previews/proof_name).read_text())
        require(proof['status'] == 'PASS_INSTALLED_LEVELING_OLED_RENDER'
                and proof['hardware_tested'] is False, 'Invalid leveling preview evidence')
        files['validation/'+proof_name] = (leveling_previews/proof_name).read_bytes()
        for name in proof['frames']:
            require(Path(name).name == name and name.endswith('.png'), 'Invalid leveling preview path')
            data = (leveling_previews/name).read_bytes()
            require(data.startswith(b'\x89PNG\r\n\x1a\n'), 'Invalid leveling preview: '+name)
            files['preview/leveling/'+name] = data
        gallery=proof.get('gallery')
        require(gallery == 'OLED_LEVELING_STAGES.png','Missing leveling stage gallery')
        files['preview/leveling/'+gallery]=(leveling_previews/gallery).read_bytes()
    if menu_previews is not None:
        proof_name = 'INVENTURE_R23_OPERATOR_MENU_PREVIEW.json'
        proof = json.loads((menu_previews/proof_name).read_text())
        require(proof['status'] == 'PASS_INSTALLED_OEM_OPERATOR_MENU_RENDER'
                and proof['hardware_tested'] is False
                and proof['original_record_count'] == 66
                and proof['launcher'] == ['Klipper', 'Zortrax Inventure'],
                'Invalid operator menu preview evidence')
        files['validation/'+proof_name] = (menu_previews/proof_name).read_bytes()
        for name in [*proof['frames'], proof['gallery']]:
            require(Path(name).name == name and name.endswith('.png'), 'Invalid menu preview path')
            data = (menu_previews/name).read_bytes()
            require(data.startswith(b'\x89PNG\r\n\x1a\n'), 'Invalid menu preview: '+name)
            files['preview/menu/'+name] = data
    if operator_cycles is not None:
        proof = json.loads(operator_cycles.read_text())
        require(proof['operator_menu']['status'] == 'PASS_INSTALLED_OLED_FLUIDD_MENU_PARITY'
                and proof['hardware_tested'] is False, 'Invalid installed macro acceptance evidence')
        files['validation/INVENTURE_R23_INSTALLED_OPERATOR_CYCLES.json'] = operator_cycles.read_bytes()
    source_archive = subprocess.check_output(
        ['git', 'archive', '--format=zip', '--prefix=INVENTURE_R23_SOURCE/', 'HEAD'], cwd=ROOT)
    with ZipFile(BytesIO(source_archive)) as archive:
        require(archive.testzip() is None, 'Source archive CRC mismatch')
        for name in verify['sources_sha256']:
            member = 'INVENTURE_R23_SOURCE/service_mcu/control_r6/SOURCE_MCU/'+name
            require(archive.read(member) == (source/name).read_bytes(),
                    'Archived source mismatch: '+name)
    files['SOURCE_R23.zip'] = source_archive
    files['CANDIDATE.json'] = (json.dumps({
        'status': 'SOFTWARE_VERIFIED_PHYSICAL_ACCEPTANCE_PENDING',
        'mcu_release': RELEASE, 'source_commit': commit,
        'source_repository': 'https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper',
        'source_archive': 'SOURCE_R23.zip',
        'application_file': 'INVENTUR.IVB', 'existing_bootloader': 'InventureBoot 1.7',
        'bootloader_update_included': False, 'installed_on_printer': False,
        'ivb_sha256': digest(ivb), 'application_sha256': digest(binary),
        'application_bytes': metadata['payload_bytes'],
        'application_slot_free_bytes': LIMIT-len(binary),
        'preview_source': 'Compiled MCU C code / simulated SPI / SSD1322 GDDRAM',
        'operator_ui_release': 'RC8-OPERATOR-MENU-R23.4',
        'operator_menu_hardware_tested': False,
        'prior_leveling_ui_operator_accepted': True,
        'mcu_update_required_from_r23': False,
    }, indent=2)+'\n').encode()
    files['SHA256SUMS'] = ''.join(
        f'{digest(data)}  {name}\n' for name, data in sorted(files.items())).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'x') as archive:
        for name, data in sorted(files.items()):
            entry = ZipInfo(name, (2026, 11, 24, 0, 0, 0))
            entry.external_attr = 0o100644 << 16
            archive.writestr(entry, data, compress_type=ZIP_DEFLATED, compresslevel=9)
    with ZipFile(output) as archive:
        require(archive.testzip() is None, 'Archive CRC mismatch')
        for name, data in files.items():
            require(archive.read(name) == data, 'Archive readback mismatch: '+name)
    return {'file': str(output), 'bytes': output.stat().st_size,
            'sha256': digest(output.read_bytes()), 'source_commit': commit}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build', type=Path)
    parser.add_argument('previews', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--leveling-previews', type=Path)
    parser.add_argument('--menu-previews', type=Path)
    parser.add_argument('--operator-cycles', type=Path)
    args = parser.parse_args()
    print(json.dumps(package(args.build.resolve(), args.previews.resolve(),
                             args.output.resolve(), args.leveling_previews,
                             args.menu_previews, args.operator_cycles), indent=2))
