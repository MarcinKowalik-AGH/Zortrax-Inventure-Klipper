#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Stage the Zero 3 lab bootloader in a regular, uncompressed MBR SD image."""
import argparse
from hashlib import sha256
import os
from pathlib import Path
import stat
import struct

SECTOR_BYTES = 512
SPL_OFFSET = 8 * 1024
SPL_MAGIC = b'eGON.BT0'


def check_spl(boot):
    if len(boot) < 32 or boot[4:12] != SPL_MAGIC:
        raise ValueError('Expected an Allwinner eGON SPL image')
    checksum, length = struct.unpack_from('<II', boot, 12)
    if length < 32 or length > len(boot) or length % 4:
        raise ValueError('Invalid eGON SPL length')
    words = struct.iter_unpack('<I', boot[:length])
    actual = (sum(word for word, in words) - checksum + 0x5f0a6c39) & 0xffffffff
    if checksum != actual:
        raise ValueError('eGON SPL checksum mismatch')


def inspect_gap(image, size):
    mbr = image.read(SECTOR_BYTES)
    if mbr[510:512] != b'\x55\xaa':
        raise ValueError('Expected an MBR SD image')
    starts = []
    for offset in range(446, 510, 16):
        kind = mbr[offset + 4]
        if kind == 0xee:
            raise ValueError('GPT detected; the 8 KiB boot slot overlaps GPT metadata')
        if kind:
            start, sectors = struct.unpack_from('<II', mbr, offset + 8)
            if not start or not sectors or (start + sectors) * SECTOR_BYTES > size:
                raise ValueError('Invalid MBR partition extent')
            starts.append(start * SECTOR_BYTES)
    if not starts or min(starts) <= SPL_OFFSET:
        raise ValueError('Image has no usable unpartitioned boot area')
    return min(starts)


def stage(image_path, boot_path, apply):
    boot = boot_path.read_bytes()
    check_spl(boot)
    if image_path.is_symlink() or not image_path.is_file():
        raise ValueError('Pass a regular SD image file, not a device or symlink')
    with image_path.open('r+b' if apply else 'rb') as image:
        info = os.fstat(image.fileno())
        if not stat.S_ISREG(info.st_mode):
            raise ValueError('SD image must be a regular file')
        first_partition = inspect_gap(image, info.st_size)
        if SPL_OFFSET + len(boot) > first_partition:
            raise ValueError('New bootloader would overlap the first partition')
        image.seek(SPL_OFFSET)
        original = image.read(len(boot))
        if original == boot:
            print('ALREADY_STAGED')
            return
        if original[4:12] != SPL_MAGIC:
            raise ValueError('Expected an existing sunxi bootloader at 8 KiB')
        print(f'BOOT_OFFSET={SPL_OFFSET} BOOT_BYTES={len(boot)} FIRST_PARTITION={first_partition}')
        print(f'BOOT_SHA256={sha256(boot).hexdigest()}')
        if not apply:
            print('DRY_RUN_OK')
            return
        backup = image_path.with_suffix(image_path.suffix + '.boot-before-p4.bin')
        with backup.open('xb') as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            image.seek(SPL_OFFSET)
            image.write(boot)
            image.flush()
            os.fsync(image.fileno())
            image.seek(SPL_OFFSET)
            if image.read(len(boot)) != boot:
                raise ValueError('Bootloader readback mismatch')
        except BaseException:
            image.seek(SPL_OFFSET)
            image.write(original)
            image.flush()
            os.fsync(image.fileno())
            raise
        print(f'STAGED_OK BACKUP={backup}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sd_image', type=Path)
    parser.add_argument('bootloader', type=Path)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    try:
        stage(args.sd_image, args.bootloader, args.apply)
    except (OSError, ValueError) as error:
        raise SystemExit(f'REFUSED: {error}')
