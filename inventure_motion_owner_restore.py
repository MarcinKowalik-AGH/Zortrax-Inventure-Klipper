"""Authenticated Klipper-only normal-motion ownership overlay for Inventure R19.

The frozen R38.6 and verified R18 layers are retained for regression and
rollback. Only these three planned runtime modules may be replaced.
"""
from hashlib import sha1,sha256
from pathlib import Path
import stat

RELEASE = "RC8-KLIPPER-SINGLE-MOTION-OWNER-R19"
SOURCES = {
    "klippy/extras/inventure_native_z.py": (
        "restored/single_owner_r19/inventure_native_z.py",
        "d20e943a4e1b8556096ecfbe6c3c6c5d6c77bf13",
        "bded3a5db90fccdee9610edbe2e49df664eb2964"),
    "klippy/extras/inventure_z_home.py": (
        "restored/single_owner_r19/inventure_z_home.py",
        "6473c5473dec6b0889772ff10536390c6ad377c6",
        "951363e7ee52ab0a592337f6a71b66895ddf169e"),
    "klippy/extras/inventure_mcu.py": (
        "restored/single_owner_r19/inventure_mcu.py",
        "50fa4d260d2dee7038962c4f2376733ae34dc62d",
        "c1d5c251762ed532c71bf7051c987a8e8a07b4cc"),
}

def git_blob(data):
    return sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()

def restored_motion_owner(repo, files):
    repo=Path(repo)
    updates={}
    for target,(name,base_sha,new_sha) in SOURCES.items():
        path=repo/name
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("Expected a regular R19 single-owner source: "+name)
        original=files[target][0]
        data=path.read_bytes()
        if git_blob(original)!=base_sha or git_blob(data)!=new_sha:
            raise ValueError("R19 motion-owner source integrity mismatch: "+name)
        compile(data,str(path),"exec")
        updates[target]=(data,0o644)
    # These are source-to-destination file hashes, not claims of mechanical
    # qualification or a substitute for the physical acceptance tests.
    provenance=dict(
        release=RELEASE,
        files={target:dict(source=name,prior_blob=base_sha,
                           installed_blob=new_sha,sha256=sha256(updates[target][0]).hexdigest())
               for target,(name,base_sha,new_sha) in SOURCES.items()},
        native_xy_planner="Klipper G28 Y/X only",
        service_scope="tool selection, wipe and bounded non-native operations",
        hardware_qualified=False,
    )
    return updates,provenance
