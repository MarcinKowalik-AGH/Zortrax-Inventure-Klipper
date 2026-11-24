"""R22 read-only effective safety policy diagnostic overlay on R21 host.

No movement, heater setpoint, display or mesh source is replaced.
"""
from hashlib import sha1,sha256
from pathlib import Path
import stat

RELEASE="RC8-SERVICE-NATIVE-POLICY-R22"
SOURCE="restored/safety_policy_r22/inventure_mcu.py"
TARGET="klippy/extras/inventure_mcu.py"
PRIOR="22f3b958cce86b03ffe24e92424c3348e406396c"
CURRENT="f269cd01832ba20586ee23ea243fe48119e4a58e"

def git_blob(data):
    return sha1(b"blob "+str(len(data)).encode("ascii")+b"\0"+data).hexdigest()

def restored_safety_policy(repo, files):
    path=Path(repo)/SOURCE
    if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Expected verified R22 safety policy module")
    before=files[TARGET][0]
    after=path.read_bytes()
    if git_blob(before)!=PRIOR or git_blob(after)!=CURRENT:
        raise ValueError("R22 safety policy module integrity mismatch")
    compile(after,str(path),'exec')
    return {TARGET:(after,0o644)},dict(
        release=RELEASE,source=SOURCE,target=TARGET,
        prior_blob=PRIOR,installed_blob=CURRENT,
        sha256=sha256(after).hexdigest(),
        native_mode="absolute limit + hot/active heater sensing",
        service_mode="user strict mask and service calibration limits",
        mcu_service_native_split_abi=1,read_only_status="INVENTURE_SAFETY_POLICY_STATUS",
        hardware_qualified=False)
