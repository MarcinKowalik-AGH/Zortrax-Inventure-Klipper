"""Independently authenticated R18 NATIVE Z profile layered on pinned R38.6.

Never edit the frozen orange_pi/ release payload. The source remains equal to
commit ee9cd2f; this overlay is recorded as a separate runtime restoration.
"""
from hashlib import sha1, sha256
from pathlib import Path
import stat

SOURCE = "restored/native_z_r18/inventure_native_z.py"
TARGET = "klippy/extras/inventure_native_z.py"
BASE_BLOB = "9355e8f2de8880f80c5302f4270f34fbc7d5cf93"
RESTORED_BLOB = "d20e943a4e1b8556096ecfbe6c3c6c5d6c77bf13"

def git_blob(data):
    return sha1(b"blob " + str(len(data)).encode("ascii") + b"\\0" + data).hexdigest()

def restored_native_z(repo, base_source):
    path=Path(repo)/SOURCE
    if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Expected a regular authenticated NATIVE Z restoration file")
    data=path.read_bytes()
    if git_blob(base_source) != BASE_BLOB:
        raise ValueError("Pinned R38.6 NATIVE Z source unexpectedly changed")
    if git_blob(data) != RESTORED_BLOB:
        raise ValueError("R18 NATIVE Z source integrity mismatch")
    compile(data, str(path), "exec")
    for symbol in (b"NATIVE_Z_TEMP_BELOW_TARGET_C = 20.",
                   b"NATIVE_Z_TEMP_ABOVE_TARGET_C = 15."):
        if symbol not in data:
            raise ValueError("Missing R18 NATIVE Z temperature profile")
    provenance=dict(release="RC8-NATIVE-Z-POLICY-R18",
                    source=SOURCE, target=TARGET, original_blob=BASE_BLOB,
                    restored_blob=RESTORED_BLOB,
                    restored_sha256=sha256(data).hexdigest())
    return data, provenance
