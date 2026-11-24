"""Restore the published V2 OLED renderer above the pinned R38.6 runtime.

The original is retained byte for byte.  The only adaptation maps the current
T1 heater name to its existing animation, with the original name as fallback.
No source in the immutable Home/MCU release is changed or executed here.
"""
from hashlib import sha256
import json
from pathlib import Path
import stat

ROOT = Path(__file__).resolve().parent
SOURCE = "restored/oled_dual_thermal_v2/inventure_oled_render.py"
PROVENANCE = "restored/oled_dual_thermal_v2/provenance.json"
TARGET = "klippy/extras/inventure_oled_render.py"
SOURCE_SHA256 = "c37e1c9c9e085193818b9b9d472c0dc3f1ac1399465bb5db6d3f4b0c099ad437"
PROVENANCE_SHA256 = "a75c5928076a2590b77a65286498390d595deda7cf550a2f42afdf489d51bc4e"
RESTORED_SHA256 = "8156c957c6d5f646c5ff34b547a1b02630ad75abbb794a62867492065a96cb0f"
LEGACY_SHA256 = "ccd4f6f881f854242917aa662b887d70143ffbd29a25717290260fb71e72330f"

_BEFORE = b"            heater = heater_map.get(name)\n"
_AFTER = (
    b"            heater = (heater_map.get('inventure_t1')\n"
    b"                      if name == 'extruder1' else None)\n"
    b"            if heater is None:\n"
    b"                heater = heater_map.get(name)\n"
)


def _read_input(repo, name, digest):
    path = repo
    for component in Path(name).parts:
        path = path/component
        if path.is_symlink():
            raise ValueError("Symlink in restored OLED source: " + str(path))
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Expected a regular restored OLED source: " + str(path))
    data = path.read_bytes()
    if sha256(data).hexdigest() != digest:
        raise ValueError("Restored OLED source checksum mismatch: " + name)
    return data


def restored_renderer(repo=ROOT):
    """Return authenticated adapted bytes and provenance without any writes."""
    repo = Path(repo).expanduser().resolve(strict=True)
    original = _read_input(repo, SOURCE, SOURCE_SHA256)
    provenance = json.loads(_read_input(repo, PROVENANCE, PROVENANCE_SHA256))
    if original.count(_BEFORE) != 1:
        raise ValueError("Unexpected published OLED heater lookup")
    adapted = original.replace(_BEFORE, _AFTER)
    if sha256(adapted).hexdigest() != RESTORED_SHA256:
        raise ValueError("Adapted OLED renderer checksum mismatch")
    return adapted, provenance
