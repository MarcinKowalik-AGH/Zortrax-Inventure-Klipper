#!/usr/bin/env python3
"""Build a new application from the verified RC8 source kit using GNU ARM."""
import argparse
import importlib
import json
import subprocess
import sys
import tempfile
import zlib
from pathlib import Path

def embedded_dictionary_matches(app, dictionary):
    for offset in range(len(app) - 2):
        if app[offset] != 0x78 or int.from_bytes(app[offset:offset + 2], 'big') % 31:
            continue
        try:
            if json.loads(zlib.decompress(app[offset:])) == dictionary:
                return True
        except (ValueError, zlib.error):
            pass
    return False


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('kit', type=Path, help='Kit prepared by the public RC8 workflow')
parser.add_argument('output', type=Path)
args = parser.parse_args()
kit = args.kit.resolve()
out = args.output.resolve()
source = Path(__file__).resolve().parent
sys.path.insert(0, str(kit / 'scripts'))
base = importlib.import_module('build_firmware_on_mac')
sys.path.insert(0, str(source))
base.validate = importlib.import_module('validate_control_build').validate
expected = json.loads(base.HIST.read_text())
env = base.doctor()
if subprocess.check_output([env['tools']['arm-none-eabi-gcc'], '-dumpfullversion'],
                           text=True).strip() != '14.2.1':
    sys.exit('Expected GNU ARM 14.2.1')
with tempfile.TemporaryDirectory(prefix='inventure-r7-') as tmp:
    root, original = base.extract_pinned(Path(tmp), expected)
    base.apply_rc7(root, original, expected)
    changes = {}
    for name in ('inventure_board.c', 'inventure_control.inc', 'inventure_transport.inc','inventure_transport.h', 'inventure_motor_test.inc', 'inventure_xy_seek.inc', 'inventure_cooling.inc', 'inventure_fan_manual.inc', 'inventure_heat_test.inc',
                 'inventure_panel.c', 'inventure_fans.inc', 'gpioperiph.c',
                 'inventure_p4_baud.c','inventure_p4_baud.h','serial.c','inventure_oled_local.inc'):
        data = (source / name).read_bytes()
        (root / 'src/stm32' / name).write_bytes(data)
        changes[name] = base.sha(data)
    provenance=json.loads((root/'.inventure-v9-patch.json').read_text())
    provenance['patched_sha256'].update({'src/stm32/'+name:digest for name,digest in changes.items()})
    provenance['p4_mode']='InventureBoot 1.7 journal; forced baud disabled'
    (root/'.inventure-v9-patch.json').write_text(json.dumps(provenance,indent=2,sort_keys=True)+'\n')
    cfg = root / '.config'
    config = cfg.read_text()
    assert config.count('CONFIG_SERIAL_BAUD=250000') == 1
    # 250000 is the unknown-boot fallback; runtime follows the saved BOOT menu.
    cfg.write_text(config)
    opts = [f'CPP={env["tools"]["arm-none-eabi-cpp"]}',
            f'PYTHON={sys.executable}', 'CROSS_PREFIX=arm-none-eabi-']
    make = env['tools']['make']
    base.run([make, 'olddefconfig', *opts], root)
    base.check_config(cfg)
    base.run([make, '-j8', *opts], root)
    base.validate(root, out)
    dictionary = json.loads((out / 'klipper.dict').read_text())
    c = dictionary['config']
    assert c['INVENTURE_MOTOR_TEST_ABI'] == 2
    assert c['INVENTURE_DOOR_POLICY_ABI'] == 1
    assert c['INVENTURE_DOOR_POLICY_DISABLE_BIT'] == 3
    assert c['INVENTURE_MOTOR_TEST_RELEASE'] == 'RC8-CONTROL-R7-FACTORY-DUAL'
    assert c['INVENTURE_CONTROL_ABI'] == 1
    assert c['INVENTURE_MOTION_ABI'] == 1
    assert c['INVENTURE_OUTPUT_ABI'] == 1
    assert c['INVENTURE_LOAD_STREAM_ABI'] == 1
    assert c['INVENTURE_FACTORY_ABI'] == 1
    assert c['INVENTURE_FACTORY_FEED_CLOCK'] == 1
    assert c['INVENTURE_FACTORY_MOTION_ABI'] == 1
    assert c['INVENTURE_FACTORY_MOTION_MAX_HZ'] == 125000
    assert c['INVENTURE_FACTORY_MOTION_TOOLS'] == 3
    assert c['INVENTURE_CONTROL_MAX_HZ'] == 20000
    assert 'inventure_factory_session action=%c tool=%c mode=%c monitor=%c duration_ms=%hu budget_steps=%u lease_ms=%hu min_c=%hi max_c=%hi token=%u sequence=%u' in dictionary['commands']
    assert 'inventure_factory_state token=%u active=%c reason=%c reject=%c tool=%c mode=%c running=%c a=%c b=%c pulses=%u pulse_age_ms=%u starts=%u fault=%c sequence=%u remaining_ms=%u' in dictionary['responses']
    assert 'inventure_load_stream action=%c steps=%u rate=%u max_ms=%u lease_ms=%hu min_c=%hi max_c=%hi token=%u sequence=%u tool=%c channel=%c e_direction=%c feeder_direction=%c' in dictionary['commands']
    assert c['INVENTURE_XY_REPEAT_ABI'] == 1
    assert c['INVENTURE_XY_REPEAT_BACKOFF_STEPS'] == 1280
    assert c['INVENTURE_XY_REPEAT_BACKOFF_HZ'] == 1280
    assert c['INVENTURE_XY_REPEAT_SLOW_STEPS'] == 2560
    assert c['INVENTURE_XY_REPEAT_SLOW_HZ'] == 640
    assert 'inventure_xy_repeat action=%c axis=%c token=%u' in dictionary['commands']
    assert c['INVENTURE_XY_SEEK_ABI'] == 1
    assert c['INVENTURE_XY_SEEK_MAX_STEPS'] == 102400
    assert c['INVENTURE_XY_SEEK_MAX_HZ'] == 3200
    assert c['INVENTURE_XY_SEEK_MAX_MS'] == 40000
    assert c['INVENTURE_XY_SEEK_X_DIR_MASK'] == 0
    assert c['INVENTURE_XY_SEEK_Y_DIR_MASK'] == 2
    assert 'inventure_xy_seek action=%c axis=%c token=%u' in dictionary['commands']
    assert 'inventure_door_policy action=%c enabled=%c' in dictionary['commands']
    assert c['INVENTURE_MOTOR_TEST_HZ'] == 400
    assert c['INVENTURE_MOTOR_TEST_PULSE_US'] == 100
    assert c['INVENTURE_MOTOR_ENABLE_ON'] == 1
    assert 'inventure_motor_test action=%c motor=%c direction=%c token=%u' in dictionary['commands']
    assert c['INVENTURE_FAN_MANUAL_ABI'] == 1
    assert c['INVENTURE_FAN_BENCH_ONLY'] == 0
    assert c['INVENTURE_HEAT_BENCH_ONLY'] == 0
    assert c['INVENTURE_HEAT_TEST_ABI'] == 3
    assert c["INVENTURE_OLED_FAST_ABI"] == 1
    assert c['INVENTURE_FAN_MANUAL_RELEASE'] == 'RC8-THERMAL-TEST-R2-20S'
    assert c['INVENTURE_HEAT_TEST_RELEASE'] == 'RC8-THERMAL-R3-CHAMBER75'
    assert c['INVENTURE_CHAMBER_DUTY_PCT'] == 75
    assert c['INVENTURE_CHAMBER_DURATION_MS'] == 180000
    assert c['INVENTURE_CHAMBER_OVERHEAT_C'] == 50
    assert 'inventure_heat_test action=%c heater=%c' in dictionary['commands']
    assert 'inventure_heat_sensor_query run=%u sensor=%c' in dictionary['commands']
    assert c['INVENTURE_P4_FORCED_BAUD'] == 0
    assert c['SERIAL_BAUD'] == 250000 and c['INVENTURE_P4_BOOT17_ABI'] == 1
    assert 'inventure_fan_manual action=%c channel=%c' in dictionary['commands']
    response, = [f for f in dictionary['responses'] if f.startswith('inventure_fan_manual_state ')]
    heat_response, = [f for f in dictionary['responses'] if f.startswith('inventure_heat_state ')]
    sensor_response, = [f for f in dictionary['responses'] if f.startswith('inventure_heat_sensor ')]
    app = (out / 'klipper.bin').read_bytes()
    ivb = (out / 'INVENTUR.IVB').read_bytes()
    assert app == ivb[64:]
    assert bytes.fromhex(base.sha(app)) == ivb[32:64]
    assert embedded_dictionary_matches(app, dictionary)
    report = dict(motor_release=c['INVENTURE_MOTOR_TEST_RELEASE'], motor_test_abi=2, xy_seek_abi=1,xy_repeat_abi=1,load_stream_abi=1,factory_abi=1,
                  factory_motion_abi=1,factory_motion_max_hz=c['INVENTURE_FACTORY_MOTION_MAX_HZ'],
                  factory_motion_tools=c['INVENTURE_FACTORY_MOTION_TOOLS'],
                  axis_motion_abi=c['INVENTURE_AXIS_MOTION_ABI'],
                  axis_motion_max_hz=c['INVENTURE_AXIS_MOTION_MAX_HZ'],
                  axis_motion_z_max_hz=c['INVENTURE_AXIS_MOTION_Z_MAX_HZ'],
                  axis_motion_e_max_hz=c['INVENTURE_AXIS_MOTION_E_MAX_HZ'],
                  door_policy_abi=1, oem_door_disable_bit=3,
                  release=c['INVENTURE_HEAT_TEST_RELEASE'], baud_mode='InventureBoot_journal',
                  fallback_baud=250000,forced_baud=0,boot17_abi=1,
                  compiler=env['gcc'], fan_manual_response=response,
                  heat_response=heat_response, sensor_response=sensor_response, sources_sha256=changes,
                  ivb_bytes=len(ivb), ivb_sha256=base.sha(ivb),
                  application_sha256=base.sha(app), tested_on_printer=False)
    (out / 'THERMAL_TEST_VERIFY.json').write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
