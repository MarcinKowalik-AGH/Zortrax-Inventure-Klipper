"""Pinned read-only R21 scheduler diagnostics overlay above R20.

The R20 motion-ownership and physical service interlocks remain byte-verifiable
and restorable. R21 changes only host diagnostics and sparse audit logging.
"""
from hashlib import sha1,sha256
from pathlib import Path
import stat

RELEASE="RC8-STM32-SCHEDULER-DIAGNOSTICS-R21"
SOURCES={
    "klippy/extras/inventure_mcu.py":(
        "restored/scheduler_r21/inventure_mcu.py",
        "a9a02476a34d194685ef74d091fdd3636e637cb7",
        "22f3b958cce86b03ffe24e92424c3348e406396c"),
    "klippy/extras/inventure_native_z.py":(
        "restored/scheduler_r21/inventure_native_z.py",
        "226a4041a3a118e98dc589cd5a01906586c56964",
        "3fc954974a4a375e003b50c8624304301457f1de"),
}

def git_blob(data):
    return sha1(b"blob "+str(len(data)).encode("ascii")+b"\0"+data).hexdigest()

def restored_scheduler(repo,files):
    updates={}
    for target,(name,prior,new) in SOURCES.items():
        path=Path(repo)/name
        if path.is_symlink() or not stat.S_ISREG(path.lstat().st_mode):
            raise ValueError('Expected a regular R21 scheduler module: '+name)
        original=files[target][0]
        data=path.read_bytes()
        if git_blob(original)!=prior or git_blob(data)!=new:
            raise ValueError('R21 scheduler module integrity mismatch: '+name)
        compile(data,str(path),'exec')
        updates[target]=(data,0o644)
    return updates,dict(release=RELEASE,
        files={target:dict(source=name,prior_blob=prior,installed_blob=new,
                           sha256=sha256(updates[target][0]).hexdigest())
               for target,(name,prior,new) in SOURCES.items()},
        runtime_command='INVENTURE_TIMING_STATUS',
        owner_audit='INVENTURE_OWNER_AUDIT',
        mcu_scheduler_abi=1,
        hardware_qualified=False)
