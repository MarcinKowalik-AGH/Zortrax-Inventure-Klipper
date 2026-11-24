"""R20 exclusive SERVICE scope: test actual MCU host action policy."""
import importlib.util
from pathlib import Path
import unittest

def load_policy():
    path = Path(__file__).resolve().parents[1]/'restored/exclusive_service_r20/inventure_mcu.py'
    spec = importlib.util.spec_from_file_location('orange_pi.inventure_mcu_r20_policy_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_native_excursion_action

class ExclusiveServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.validate = staticmethod(load_policy())

    def setUp(self):
        self.frame = dict(klipper_xy_prepared=True, release_mm={'X':0.,'Y':0.},
                          pad_xy=[82.8,68.], frame_verified=False,
                          handoff_attempted=False, handoff_completed=False)
        self.factory = dict(operation='CLEAN',
                            parameters=dict(tool=0,speed_profile='OEM',
                                            center_x=82.8,center_y=152.5),
                            native_homing=self.frame, homed=['X','Y'],
                            selection_commanded=False,cleaning_done=False,
                            position=[82.8,152.5,0.],module_stack=[],
                            move=None,motion_legs={})

    def check(self, kind, **kwargs):
        return self.validate(self.factory, dict(kind=kind,**kwargs))

    def certify(self):
        self.factory['selection_commanded']=True
        self.factory['cleaning_done']=True
        self.check('native_home_frame')
        self.frame['frame_verified']=True
        self.factory['position'][:2]=[82.8,68.]
        self.factory['motion_legs']['z_contact_pad']=dict(
            target=[82.8,68.],reason='completed')

    def done(self):
        self.check('done',success=True,terminal='HOME_PREPARE',
                   native_xy_offsets_mm=[0.,0.],service_xy_mm=[82.8,68.])

    def test_service_t1_is_distinct_and_never_claims_native_handoff(self):
        self.factory.pop('native_homing')
        self.factory['parameters']['tool']=1
        self.check('home',axis='X')
        self.check('move',e=0.5,speed=25.)
        self.factory['native_homing']=dict(self.frame)
        with self.assertRaisesRegex(ValueError,'verified T0'):
            self.check('move',x=80,y=68,speed=20.)

    def test_reject_other_homing_z_e_feeder_unknown_modules(self):
        forbidden = (
            dict(kind='home',axis='Y'),dict(kind='home_release',axis='X'),
            dict(kind='z_contact_move',steps=100,speed=10.),
            dict(kind='nozzle_z_refine',steps=100),
            dict(kind='move',x=80.,y=80.,e=1.,speed=20.),
            dict(kind='move',x=80.,y=80.,z=1.,speed=20.),
            dict(kind='move',x=80.,y=80.,leg='unbounded',speed=20.),
            dict(kind='mode',mode=2),dict(kind='mode',mode=0,monitor=1),
            dict(kind='module',state='start',name='home_xy'),
            dict(kind='phase',name='HOME'),
        )
        for action in forbidden:
            with self.subTest(action=action),self.assertRaises(ValueError):
                self.validate(self.factory,action)

    def test_allowed_t0_selector_wipe_and_bin(self):
        for action in (
            dict(kind='module',state='start',name='position_bin'),
            dict(kind='move',x=82.8,y=152.5,e=0.,speed=60.),
            dict(kind='module',state='done',name='position_bin'),
            dict(kind='phase',name='HEAT'),dict(kind='heat'),
            dict(kind='module',state='start',name='select_tool'),
            dict(kind='mode',mode=0,monitor=0),
            dict(kind='phase',name='SWITCH'),
            dict(kind='move',x=154.5,y=154.5,e=0.,leg='selector_press',speed=15.),
            dict(kind='module',state='done',name='select_tool'),
            dict(kind='module',state='start',name='clean'),
            dict(kind='move',x=82.8,y=158.0,e=0.,speed=40.),
            dict(kind='module',state='done',name='clean'),
        ):
            with self.subTest(action=action):
                self.validate(self.factory,action)

    def test_certification_requires_selected_t0_wipe_and_bin(self):
        with self.assertRaisesRegex(ValueError,'did not finish'):
            self.check('native_home_frame')
        self.factory['selection_commanded']=True
        self.factory['cleaning_done']=True
        self.factory['position'][1]=68.
        with self.assertRaisesRegex(ValueError,'did not finish'):
            self.check('native_home_frame')
        self.factory['position'][1]=152.5
        self.check('native_home_frame')
        self.frame['frame_verified']=True
        with self.assertRaisesRegex(ValueError,'once only'):
            self.check('native_home_frame')

    def test_after_certified_frame_only_final_pad_move_is_allowed(self):
        self.certify()
        for action in (
            dict(kind='move',x=100.,y=152.,speed=40.),
            dict(kind='module',name='clean',state='start'),
            dict(kind='move',x=154.5,y=154.5,leg='selector_press',speed=10.)):
            with self.subTest(action=action),self.assertRaises(ValueError):
                self.validate(self.factory,action)
        self.check('module',state='start',name='z_position_pad')
        self.check('move',x=82.8,y=68.,leg='z_contact_pad',e=0.,speed=25.)
        self.check('module',state='done',name='z_position_pad')

    def test_handoff_requires_completed_pad_and_empty_motion_queue(self):
        self.frame['frame_verified']=True
        with self.assertRaisesRegex(ValueError,'Native handoff refused'):
            self.done()
        self.factory['selection_commanded']=True
        self.factory['cleaning_done']=True
        self.factory['position'][:2]=[82.8,68.]
        self.factory['motion_legs']['z_contact_pad']=dict(target=[82.8,68.],reason='running')
        with self.assertRaisesRegex(ValueError,'Native handoff refused'):
            self.done()
        self.factory['motion_legs']['z_contact_pad']['reason']='completed'
        self.factory['move']=dict(active=True)
        with self.assertRaisesRegex(ValueError,'Native handoff refused'):
            self.done()
        self.factory['move']=None
        self.done()

    def test_wrong_offsets_and_any_action_after_handoff_fail(self):
        self.certify()
        with self.assertRaisesRegex(ValueError,'Native handoff refused'):
            self.check('done',success=True,terminal='HOME_PREPARE',
                       service_xy_mm=[82.8,68.],native_xy_offsets_mm=[.1,0.])
        self.done()
        self.frame['handoff_attempted']=True
        with self.assertRaisesRegex(ValueError,'after native handoff'):
            self.done()
        with self.assertRaisesRegex(ValueError,'after native handoff'):
            self.check('move',x=82.8,y=68.,leg='z_contact_pad',speed=20.)

    def test_factory_calibration_stays_strict_not_native(self):
        self.frame['klipper_xy_prepared']=False
        self.check('home',axis='Y')
        self.frame['klipper_xy_prepared']=True
        self.factory['parameters']['speed_profile']='SERVICE'
        with self.assertRaisesRegex(ValueError,'verified T0'):
            self.check('heat')

if __name__=='__main__':
    unittest.main()
