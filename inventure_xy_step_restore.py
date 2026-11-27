"""Restore the CoreXY pulse-rate bound for the paired R28 host."""
from hashlib import sha256

TARGET = 'klippy/kinematics/corexy.py'
RELEASE = 'RC8-XYSTEP-R28'
BASE_SHA256 = '0c2db1d8c0ab6aa00a0efd7142584f7879e53e5f6b1f45a1b1d386bef3ccbcd6'


def restore(repo, original):
    if sha256(original).hexdigest() != BASE_SHA256:
        raise ValueError('Unexpected pinned CoreXY kinematics for Inventure XY timers')
    text = original.decode()
    velocity = '        max_velocity, max_accel = toolhead.get_max_velocity()\n'
    text = text.replace(velocity, velocity + '        self._inventure_xy_step = InventureXYStep(\n'
                        '            config, self.rails[:2], max_velocity)\n', 1)
    check = '        if not move.axes_d[2]:\n'
    text = text.replace(check, '        self._inventure_xy_step.check_move(move)\n' + check, 1)
    body = (repo/'restored/xy_step_r28/inventure_xy_step.py').read_text()
    data = (text + '\n\n' + body).encode()
    compile(data, TARGET, 'exec')
    return data, dict(release=RELEASE, sha256=sha256(data).hexdigest(),
                      source_sha256=BASE_SHA256, hardware_tested=False)
