"""Install R24 operator routing around authenticated R38.6 procedure bodies."""
import ast
from hashlib import sha256
from pathlib import Path
from inventure_oem_home_restore import BASELINE_SHA256

PROFILE='operator-home-r24'
RELEASE='RC8-HOME-XY-RELEASE-ZERO-R24.3-XY5MM'
RC1_RELEASE='RC8-HOME-XY-RELEASE-Z-REUSE-R24'
RC2_RELEASE='RC8-HOME-XY-RELEASE-Z-REUSE-R24.1-XY5MM'
RC4_RELEASE='RC8-HOME-XY-RELEASE-ZERO-R24.2-XY5MM'
BASELINE_BLOB='9355e8f2de8880f80c5302f4270f34fbc7d5cf93'
OPERATOR_SHA256='79dc8b712cf54f0e86b67936d1569690071345e8662a29d4ae026236acc53352'
RC1_OPERATOR_SHA256='d5c97c401e538f4b5012b967e51805c43bff1b153ed60bcfc12c82b08900c9e3'
RC2_OPERATOR_SHA256='ab4a5a694825b7603d61c29e291728f590ef67fc0c08119abaa6c860d38e6054'
RC4_OPERATOR_SHA256='a8db616130e7bf4f90e791af275e073b5369714295136d6485e9044720f47132'

def require_reference_backup(repo,home,host):
    from inventure_oem_home_restore import require_reference_backup as verify
    return verify(repo,home,host)

def replace(data,before,after):
    if data.count(before)!=1:raise ValueError('Unexpected frozen OEM Home anchor: '+before[:70])
    return data.replace(before,after,1)

def restored_operator_home(repo,files,*,revision='current'):
    repo=Path(repo)
    if revision not in ('current','rc1','rc2','rc4'):
        raise ValueError('Unknown operator Home revision: '+revision)
    native='klippy/extras/inventure_native_z.py'
    driver='klippy/extras/inventure_mcu.py'
    program='klippy/extras/inventure_z_home.py'
    extension='klippy/extras/inventure_operator_home.py'
    updates={}
    data=files[native][0]+b'''\n# R24 changes operator routing; the accepted native Z measurement stays above.\nfrom .inventure_operator_home import OperatorHome\nclass InventureNativeZ(OperatorHome, InventureNativeZ):\n    pass\n'''
    updates[native]=(data,0o644)
    data=files[program][0].decode()
    data=replace(data,'    def prepare_native_home(self, pad_xy, pad_speed):',
                 '    def prepare_native_home(self, pad_xy, pad_speed, prepared_xy=False):')
    prefix=('    def prepare_native_home(self, pad_xy, pad_speed, prepared_xy=False):\n'
            '        """Compose the tested modules once, then hand the measured XY frame to Klipper."""\n')
    data=replace(data,prefix+"        yield from self._module('z_initial_clearance', self._nozzle_pre_xy_clearance())\n"
                      "        yield from self._module('home_xy', self._home_steps(y_first=True, include_z=False))\n",
                      prefix+"        if not prepared_xy:\n"
                      "            yield from self._module('z_initial_clearance', self._nozzle_pre_xy_clearance())\n"
                      "            yield from self._module('home_xy', self._home_steps(y_first=True, include_z=False))\n")
    updates[program]=(data.encode(),0o644)
    data=files[driver][0].decode()
    data=replace(data,"        if was_armed and not p['armed'] and not stopping:\n",
        "        held_home=(getattr(self,'r24_held_home_owner',0)\n"
        "                   and p['mode']==2 and not p['fault'] and not p['latched'])\n"
        "        if was_armed and not p['armed'] and not stopping and not held_home:\n")
    data=replace(data,"                        native_homing['pad_xy'], native_homing['pad_speed'])",
                      "                        native_homing['pad_xy'], native_homing['pad_speed'],\n"
                      "                        prepared_xy=bool(native_homing.get('held_xy')))")
    anchor="        if interactive:\n            saved = getattr(getattr(self, 'material_service', None), 'profiles', {}).get('T%d' % p['tool'], {})"
    insertion='''        if native_homing is not None and native_homing.get('held_xy') is not None:
            frame=native_homing['held_xy']
            if (operation!='CLEAN' or p['tool'] or p['speed_profile']!='OEM'
                    or set(frame)!=set(('owner','service_xy','release_mm'))
                    or type(frame['owner']) is not int or not 0<frame['owner']<=0xffffffff
                    or len(frame['service_xy'])!=2 or set(frame['release_mm'])!={'X','Y'}
                    or not all(math.isfinite(v) for v in frame['service_xy'])
                    or not all(0<v<=5000./640. for v in frame['release_mm'].values())):
                self._control_abort()
                raise ValueError('Invalid retained XY frame for held T0 preparation')
            context['factory'].update(token=frame['owner'],homed=['X','Y'])
            context['factory']['position'][:2]=frame['service_xy']
            context['factory']['native_homing']['release_mm']=dict(frame['release_mm'])
'''
    data=replace(data,anchor,insertion+anchor)
    updates[driver]=(data.encode(),0o644)
    helper=repo/'restored/operator_home_r24'
    if revision!='current':helper=helper/revision
    code=(helper/'inventure_operator_home.py').read_bytes()
    expected={'rc1':RC1_OPERATOR_SHA256,'rc2':RC2_OPERATOR_SHA256,'rc4':RC4_OPERATOR_SHA256,'current':OPERATOR_SHA256}[revision]
    if sha256(code).hexdigest()!=expected:
        raise ValueError('The reviewed R24 operator Home source changed')
    updates[extension]=(code,0o644)
    # Apart from the preparation dispatcher, every accepted engine method
    # remains the same AST, including all PC14, PE12, T0 motion and heat code.
    for name,allowed in ((driver,{'_factory_start','_control_query'}),(program,{'prepare_native_home'})):
        old=ast.parse(files[name][0]);new=ast.parse(updates[name][0])
        def methods(tree):
            return {n.name:ast.dump(n,include_attributes=False) for c in tree.body
                    if isinstance(c,ast.ClassDef) for n in c.body if isinstance(n,ast.FunctionDef)
                    and n.name not in allowed}
        if methods(old)!=methods(new):raise ValueError('R24 changed an accepted engine outside operator preparation')
    for name,(data,_) in updates.items():compile(data,name,'exec')
    return updates,dict(release={'rc1':RC1_RELEASE,'rc2':RC2_RELEASE,'rc4':RC4_RELEASE,'current':RELEASE}[revision],profile=PROFILE,baseline_blob=BASELINE_BLOB,
        required_mcu='RC8-CONTROL-R7-HOME-R24-OLED',required_held_service_abi=1,
        accepted_z_and_factory_engine_ast_unchanged=True,
        files={name:sha256(data).hexdigest() for name,(data,_) in updates.items()},hardware_tested=False)
