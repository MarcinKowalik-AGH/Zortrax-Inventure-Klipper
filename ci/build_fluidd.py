#!/usr/bin/env python3
"""Build the Inventure panel against the pinned upstream Fluidd source."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = 'b2edab5bc690f5265a5aa1e56ae3f67669053fd4'
WEB = ROOT/'fluidd/inventure-ui'
TAG = json.loads((ROOT/'orange_pi/RELEASE.json').read_text())['fluidd_dashboard']['web_build_tag']


def package(checkout, archive):
    dist = checkout/'dist'
    assert (dist/'sw.js').is_file() and b'assets/' in (dist/'index.html').read_bytes()
    content = b''.join(p.read_bytes() for p in (dist/'assets').glob('*.js'))
    assert all(key in content for key in (b'OLED INVENTURE', b'Extruder 0', b'Extruder 1', b'inventure_chamber chamber'))
    files = {str(p.relative_to(dist)):p.read_bytes() for p in dist.rglob('*') if p.is_file()}
    files['.inventure-fluidd-build'] = (TAG+'\n').encode()
    files['LICENSE-Fluidd.txt'] = (checkout/'LICENSE').read_bytes()
    archive.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive,'w') as bundle:
        for name in sorted(files):
            item = ZipInfo(name,(2026,11,21,0,0,0))
            item.external_attr = 0o100644 << 16
            bundle.writestr(item, files[name], compress_type=ZIP_DEFLATED, compresslevel=9)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('checkout', type=Path)
    p.add_argument('archive', type=Path)
    p.add_argument('--built', action='store_true')
    args = p.parse_args()
    checkout = args.checkout.resolve()
    if not args.built:
        subprocess.run(['git','clone','--quiet','--depth','1','--branch','v1.37.7',
                        'https://github.com/fluidd-core/fluidd.git',str(checkout)],check=True)
    actual = subprocess.check_output(['git','rev-parse','HEAD'],cwd=checkout,text=True).strip()
    if actual != UPSTREAM:
        raise SystemExit('Unexpected Fluidd source commit: '+actual)
    if not args.built:
        subprocess.run(['git','apply',str(WEB/'fluidd-1.37.7.patch')],cwd=checkout,check=True)
        shutil.copy2(WEB/'InventureOledPreview.vue', checkout/'src/components/widgets/thermals')
        for name in ('InventureSegmentedSlider.vue', 'InventurePlatformStatus.vue'):
            shutil.copy2(WEB/name, checkout/'src/components/common')
        for source in sorted((WEB/'util').glob('*.ts')):
            shutil.copy2(source, checkout/'src/util'/source.name)
        specs = ['src/util/'+source.name for source in sorted((WEB/'util').glob('*.spec.ts'))]
        for task in (['install','--frozen-lockfile'], ['exec','vitest','run',*specs], ['run','type-check'], ['run','build']):
            subprocess.run(['corepack','pnpm@12.11.2',*task],cwd=checkout,check=True)
    package(checkout,args.archive)
