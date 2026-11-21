# SPDX-License-Identifier: GPL-3.0-or-later
"""Install the versioned Inventure Fluidd build into the active nginx site."""
import ctypes
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
from zipfile import ZipFile

_release = json.loads((Path(__file__).resolve().parent/'orange_pi/RELEASE.json').read_text())
TAG = _release['fluidd_dashboard']['web_build_tag']
ASSET = _release['fluidd_dashboard']['web_package']
RELEASE = ('https://github.com/MarcinKowalik-AGH/Zortrax-Inventure-Klipper/releases/download/'
           + _release['nozzle_pad_calibration']['release_tag'] + '/')
MARKER = '.inventure-fluidd-build'


def is_fluidd(path):
    try:
        page = (path/'index.html').read_bytes()[:32768]
        return bool(re.search(br'<title>\s*fluidd\s*</title>', page, re.I)
                    and b'assets/' in page and path.parent != path)
    except OSError:
        return False


def web_root(override=None):
    if override is not None:
        root = Path(override).expanduser().resolve(strict=True)
        if not is_fluidd(root):
            raise ValueError('No Fluidd application at '+str(root))
        return root
    # nginx -T expands sites-enabled includes; do not select a second site by port alone.
    command = ['nginx', '-T'] if os.geteuid() == 0 else ['sudo', 'nginx', '-T']
    config = subprocess.run(command, check=True, capture_output=True, text=True).stdout
    tokens = shlex.shlex(config, posix=True, punctuation_chars='{};')
    tokens.whitespace_split = True
    words, paths = [], set()
    for token in tokens:
        # shlex returns an empty string for a quoted empty nginx argument.
        if token.startswith(('{', '}', ';')):
            if len(words) == 2 and words[0] == 'root' and '$' not in words[1]:
                path = Path(words[1])
                if path.is_absolute() and is_fluidd(path):
                    paths.add(path.resolve())
            words = []
        else:
            words.append(token)
    if len(paths) != 1:
        raise ValueError('Found %d Fluidd nginx roots; specify the Inventure site with --fluidd-root PATH' % len(paths))
    return paths.pop()


def release_bundle(directory, local=None):
    def read(name):
        if local is not None:
            return (local.parent/name).read_bytes()
        with urlopen(Request(RELEASE+name, headers={'User-Agent':'Inventure-Orange-Pi'}), timeout=90) as response:
            return response.read()
    listing = read('SHA256SUMS').decode('ascii')
    records = [line.split() for line in listing.splitlines()]
    expected = [sha for sha, name in records if name == ASSET]
    data = local.read_bytes() if local is not None else read(ASSET)
    if expected != [hashlib.sha256(data).hexdigest()]:
        raise ValueError('Fluidd release ZIP checksum does not match SHA256SUMS')
    archive = directory/ASSET
    archive.write_bytes(data)
    with ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(set(names)) != len(names) or bundle.read(MARKER).decode().strip() != TAG:
            raise ValueError('Unexpected Inventure Fluidd package')
        if not {'index.html', 'sw.js', 'manifest.webmanifest'}.issubset(names):
            raise ValueError('Incomplete Inventure Fluidd package')
        for name in names:
            p = Path(name)
            if p.is_absolute() or '..' in p.parts or not p.parts:
                raise ValueError('Invalid Fluidd asset path')
        bundle.extractall(directory/'dist')
    return directory/'dist'


def same_build(source, root):
    # A Moonraker update or interrupted copy may leave a marker behind.
    return all((root/file.relative_to(source)).is_file()
               and (root/file.relative_to(source)).read_bytes() == file.read_bytes()
               for file in source.rglob('*') if file.is_file() and file.name != 'config.json')


def exchange(first, second):
    fn = ctypes.CDLL(None, use_errno=True).renameat2
    if fn(-100, os.fsencode(first), -100, os.fsencode(second), 2):
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def deploy(source, root):
    """Run against a dedicated Fluidd root, retaining custom files and a complete backup."""
    if not is_fluidd(root) or not is_fluidd(source) or (source/MARKER).read_text().strip() != TAG:
        raise ValueError('Fluidd source or destination identity changed')
    descriptor = os.open(root.parent, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        if same_build(source, root):
            return None
        stage = Path(tempfile.mkdtemp(prefix='.inventure-fluidd-previous-', dir=root.parent))
        try:
            # Copy original site, including owner configuration and older hashed assets used by open tabs.
            subprocess.run(['cp', '-a', '--reflink=auto', '--', str(root)+'/.', str(stage)], check=True)
            for file in sorted(source.rglob('*')):
                if not file.is_file():
                    continue
                dest = stage/file.relative_to(source)
                if dest.name == 'config.json' and dest.exists():
                    continue
                if any(x.is_symlink() for x in (dest, *dest.parents) if x != stage and stage in x.parents):
                    raise ValueError('Web asset would overwrite a symlink: '+str(dest.relative_to(stage)))
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, dest)
                os.chmod(dest, 0o644)
            if not same_build(source, stage):
                raise ValueError('Staged Fluidd assets differ from the release')
            exchange(root, stage)
        except BaseException:
            shutil.rmtree(stage)
            raise
        return stage
    finally:
        os.close(descriptor)


def activate(source, root):
    cmd = [sys.executable, str(Path(__file__).resolve()), 'deploy', str(source), str(root)]
    if os.geteuid() != 0:
        cmd = ['sudo', '--'] + cmd
    return json.loads(subprocess.check_output(cmd, text=True))['previous']


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != 'deploy':
        raise SystemExit('Use install-orange-pi.py --configure-fluidd')
    previous = deploy(Path(sys.argv[2]).resolve(), Path(sys.argv[3]).resolve())
    print(json.dumps({'previous': str(previous) if previous else None}))
