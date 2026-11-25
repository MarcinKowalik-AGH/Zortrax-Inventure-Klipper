"""Release the native motor owner before an operator SERVICE procedure."""
from hashlib import sha1, sha256

RELEASE = 'RC8-OPERATOR-SERVICE-ENTRY-R23.1'
TARGET = 'klippy/extras/inventure_mcu.py'
PRIOR = 'f269cd01832ba20586ee23ea243fe48119e4a58e'
CURRENT = '8d64f94d3ae4257fbc20e936b9145ba0419f34ed'

BEFORE = b'''            if head is not None:
                head.wait_moves()
            if self._operator_service_preflight(manual_fan=manual_fan, homing=homing)['mode'] != 'NATIVE':
                raise ValueError('Native control changed while waiting for motion to finish')
'''
AFTER = b'''            enables = self.printer.lookup_object('stepper_enable', None)
            if head is None or not callable(getattr(enables, 'motor_off', None)):
                raise ValueError('Native SERVICE handoff requires the Klipper toolhead and stepper enable owner')
            head.wait_moves()
            if self._operator_service_preflight(manual_fan=manual_fan, homing=homing)['mode'] != 'NATIVE':
                raise ValueError('Native control changed while waiting for motion to finish')
            # An idle native axis still holds the shared physical PE10 enable.
            # Release it through Klipper, including its cached enable state,
            # and wait for that queued write before asking the MCU for SERVICE.
            enables.motor_off()
            head.wait_moves()
            if self._operator_service_preflight(manual_fan=manual_fan, homing=homing)['mode'] != 'NATIVE':
                raise ValueError('Native control changed while releasing the motor owner')
'''


def git_blob(data):
    return sha1(b'blob ' + str(len(data)).encode('ascii') + b'\0' + data).hexdigest()


def restored_operator_service(repo, files):
    before = files[TARGET][0]
    if git_blob(before) != PRIOR or before.count(BEFORE) != 1:
        raise ValueError('Unexpected R22 module before operator SERVICE restoration')
    after = before.replace(BEFORE, AFTER, 1)
    if git_blob(after) != CURRENT:
        raise ValueError('Operator SERVICE restoration integrity mismatch')
    compile(after, TARGET, 'exec')
    return {TARGET: (after, 0o644)}, dict(
        release=RELEASE, target=TARGET, prior_blob=PRIOR, installed_blob=CURRENT,
        sha256=sha256(after).hexdigest(), physical_enable='Klipper motor_off + wait_moves',
        mcu_update_required=False, hardware_qualified=False)
