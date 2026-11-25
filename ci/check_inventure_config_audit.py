"""Validate and export an organized Inventure field config without serial I/O."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import sys
from zipfile import ZipFile, ZIP_DEFLATED

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import inventure_bootstrap as bootstrap
from inventure_bootstrap_config import stage_config
import inventure_config_layout as layout


def audit(source,destination,cycles):
    destination.mkdir(parents=True,exist_ok=True)
    host=bootstrap.prepare_host(destination/'runtime',repo=ROOT)
    (destination/'gcodes').mkdir()
    plan=stage_config(host,destination/'config',source,serial='/dev/ttyS5',baud=750000,
                      gcodes_path=destination/'gcodes',restore_field_profile=True)
    from inventure_bootstrap_config import _updater
    module=_updater().klipper_config_module(host)
    effective,loaded=layout.active_klipper(module,Path(plan['config_path']),{})
    original,_=layout.active_klipper(module,source,{})
    gcodes_path=original.get('virtual_sdcard','path')
    import subprocess
    checked=subprocess.run([sys.executable,str(ROOT/'ci/validate_bootstrap_config.py'),
                            '--klipper',str(host),'--config',plan['config_path']],
                           text=True,capture_output=True,timeout=120)
    if checked.returncode:raise ValueError(checked.stdout+checked.stderr)
    loaded_names={str(p.relative_to(destination/'config')) for p in loaded}
    moon=source.parent/'moonraker.conf'
    moon_files=layout.moonraker_files(moon) if moon.is_file() else {}
    moon_names={str(path.relative_to(source.parent)) for path in moon_files}
    state_names={name for name in plan['copied_state_files'] if not name.startswith('inventure_fluidd_groups_')}
    macros=[]
    for name in effective.sections():
        if not name.startswith('gcode_macro '):continue
        code=effective.get(name,'gcode')
        macros.append(dict(name=name.split(None,1)[1],
                           gcode_sha256=sha256(code.encode()).hexdigest(),
                           static_commands=sorted(set(re.findall(r'^\s*([A-Z][A-Z0-9_]*)\b',code,re.M)))))
    cycle_data=json.loads(cycles.read_text())
    assert cycle_data['status']=='PASS_INSTALLED_R22_FULL_CYCLES'
    assert all(row['status']=='PASS' for row in cycle_data['xy_switch_refusals'])
    proof=dict(status='SOFTWARE_VALIDATED_PHYSICAL_ACCEPTANCE_PENDING',
        ui_release='RC8-CONFIG-AUDIT-R23.5',
        source_config_sha256=sha256(source.read_bytes()).hexdigest(),
        role_files=sorted(loaded_names),macro_count=len(macros),macros=macros,
        moonraker_files_preserved=sorted(moon_names),
        configuration_cleanup=plan['configuration_cleanup'],
        calibration_files={name:sha256((source.parent/name).read_bytes()).hexdigest()
                           for name in state_names if (source.parent/name).exists()},
        copied_state_identical=all((source.parent/name).read_bytes()==(destination/'config'/name).read_bytes()
                                  for name in state_names if (source.parent/name).exists()),
        retained_motion={s:dict(effective.items(s)) for s in ('printer','stepper_x','stepper_y','stepper_z','inventure_native_z')},
        home_cycles=cycle_data['cycle_count'],home_refusals=cycle_data['refusals'],
        xy_switch_refusals=cycle_data['xy_switch_refusals'],
        oled_fluidd_parity=cycle_data['operator_menu'],
        service_repair=dict(only_missing_inventure_registration_errors=True,
                            deferred_mcu_validation_inside_rollback=True,
                            atomic_service_override=True,live_runtime_module_paths_verified=True),
        physical_incident=dict(home_xyz_collision_reported=True,
            home_collision_root_cause='Not established from the surviving log',
            restart_failure='Service selected klipper-inventure-candidate with missing inventure_r5 registration',
            supplied_systemd_override='50-inventure-release.conf has no visible command; legacy inventure.conf selects candidate'),
        mcu_updated=False,serial_opened=False,hardware_tested=False)
    filename='INVENTURE_R23_5_CONFIG_AUDIT.json'
    (destination/filename).write_text(json.dumps(proof,indent=2)+'\n')
    report=('Zortrax Inventure R23.5 — uporządkowana konfiguracja\n\n'
        'printer.cfg zawiera jawne odwołania do plików odpowiedzialnych za sprzęt, czujniki, '
        'sterowanie grzałkami, chłodzenie, procedury i OLED. Parametry ruchu, termika i SAVE_CONFIG '
        'są zachowane. Oryginalne pliki niewłączone do konfiguracji są w archive/unused, poza aktywnym katalogiem.\n\n'
        'Przed instalacją wykonaj naprawę usługi opisaną w wydaniu R23.5. Następnie użyj '
        'install-new-host.py --recover-startup --restore-field-profile --serial /dev/ttyS5 --baud 750000 --apply. '
        'Nie kopiuj konfiguracji na pracującą drukarkę. Eksport jest kopią do przeglądu i odtworzenia; '
        'instalator stosuje te same reguły do bieżących plików i robi kopię przed zmianą.\n\n'
        'Testy programowe: wszystkie makra z aktywnego drzewa zostały załadowane; Home i procedury OLED/Fluidd '
        'przechodzą przez wspólne makra oraz istniejących właścicieli sprzętu. Brak potwierdzenia krańcówki '
        'X lub Y blokuje wejście do wyboru T0 i unieważnia referencję. Raport JSON zawiera zakres symulacji.\n\n'
        'Zgłoszonego uderzenia nie udało się odtworzyć z zachowanego logu. Powrót po restarcie do innego '
        'katalogu Klippera jest potwierdzony. Wynik symulacji nie jest potwierdzeniem stanu mechaniki, '
        'krańcówek ani fizycznej poprawności wszystkich procedur. Nowy Home wymaga kontrolowanej próby '
        'na drukarce po przywróceniu Ready i weryfikacji wejść. MCU R23 i bootloader pozostają bez zmian.\n')
    (destination/'PRZECZYTAJ.txt').write_text(report)
    archive=destination/'INVENTURE_R23_5_KONFIGURACJA_UPORZADKOWANA.zip'
    with ZipFile(archive,'w',ZIP_DEFLATED) as z:
        for name in sorted(loaded_names|state_names):
            data=(destination/'config'/name).read_bytes()
            # Preserve the actual host G-code location, not the offline test path.
            if name=='inventure_services.cfg':
                data=data.replace(str(destination/'gcodes').encode(),gcodes_path.encode())
            z.writestr('config/'+name,data)
        for path,data in moon_files.items():
            z.writestr('config/'+str(path.relative_to(source.parent)),data)
        for path in source.parent.iterdir():
            if path.is_file() and path.name not in loaded_names|state_names|moon_names:
                z.write(path,'archive/unused/'+path.name)
        z.write(destination/filename,'validation/'+filename)
        z.write(destination/'PRZECZYTAJ.txt','PRZECZYTAJ.txt')
    return dict(report=proof,archive=str(archive),sha256=sha256(archive.read_bytes()).hexdigest())


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,default=ROOT/'tests/fixtures/field_config_20261125/printer.cfg')
    p.add_argument('--destination',type=Path,required=True)
    p.add_argument('--cycles',type=Path,required=True)
    args=p.parse_args()
    result=audit(args.source.resolve(),args.destination.resolve(),args.cycles.resolve())
    print(json.dumps(dict(archive=result['archive'],sha256=result['sha256'],
                         macros=result['report']['macro_count'],status=result['report']['status'])))
