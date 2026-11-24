"""Authenticated R20 SERVICE excursion layer above the verified R19 host.

Applies only the audited T0 SERVICE action/return safeguards; it does not
replace any STM32 firmware, OLED source, geometry, mesh or R18 safety policy.
"""
from hashlib import sha1, sha256
from pathlib import Path
import stat

RELEASE = "RC8-EXCLUSIVE-SERVICE-HANDOFF-R20"
SOURCES = {
    "klippy/extras/inventure_mcu.py": (
        "restored/exclusive_service_r20/inventure_mcu.py",
        "c1d5c251762ed532c71bf7051c987a8e8a07b4cc",
        "a9e26a6549913d8b2998655b3de1a7cb9be2302c"),
    "klippy/extras/inventure_native_z.py": (
        "restored/exclusive_service_r20/inventure_native_z.py",
        "bded3a5db90fccdee9610edbe2e49df664eb2964",
        "4d3fb413e0ceb3da70a300dfc2549798e9a7277d"),
}

def git_blob(data):
    return sha1(b"blob " + str(len(data)).encode("ascii") + b"\0" + data).hexdigest()

def restored_service_excursion(repo, files):
    repo = Path(repo)
    updates = {}
    for target,(source,prior_blob,new_blob) in SOURCES.items():
        path = repo/source
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError("Expected a regular R20 exclusive SERVICE source: "+source)
        old_data = files[target][0]
        data = path.read_bytes()
        if git_blob(old_data) != prior_blob or git_blob(data) != new_blob:
            raise ValueError("R20 SERVICE excursion source integrity mismatch: "+source)
        compile(data, str(path), "exec")
        updates[target] = data, 0o644
    return updates, dict(
        release=RELEASE,
        files={target:dict(source=source, prior_blob=old_blob,
                           installed_blob=new_blob, sha256=sha256(updates[target][0]).hexdigest())
               for target,(source,old_blob,new_blob) in SOURCES.items()},
        ownership="NATIVE Klipper -> exclusive SERVICE T0 selector/wipe -> NATIVE Klipper",
        native_handoff="verified MCU owner/run/token and independent mode readback",
        t1_policy="SERVICE-only until separately qualified native tool offsets",
        hardware_qualified=False,
    )
