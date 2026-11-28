"""Attach the R31 cassette commands to the verified operator runtime."""
from hashlib import sha256

TARGET = 'klippy/extras/inventure_mcu.py'
BASE_SHA256 = '2f9216e819c5d9ca34330db87bbe4032eacbde2dc73aa05ec51316532a0dfe6b'


def restore(repo, original):
    if sha256(original).hexdigest() != BASE_SHA256:
        raise ValueError('R31 cassette controller requires the verified R30 host source')
    text = original.decode()
    safety = '        temperature=self._load_test_safety(context,control_state=self.control_last if waiting else None)\n'
    if text.count(safety) != 1:
        raise ValueError('Missing factory safety poll in the verified host source')
    text = text.replace(safety, '        feed_only = f.get("cassette_window") is not None and f["move"] is None\n'
                        '        cached_control = waiting or feed_only\n'
                        '        temperature=self._load_test_safety(context,control_state=self.control_last if cached_control else None)\n')
    profile = repo/'restored/cassette_windows_r31/inventure_mcu_profile.py'
    data = (text + '\n\n' + profile.read_text()).encode()
    compile(data, TARGET, 'exec')
    return data, dict(release='RC8-CASSETTE-WINDOWS-R31', source_sha256=BASE_SHA256,
                      sha256=sha256(data).hexdigest(), hardware_tested=False)
