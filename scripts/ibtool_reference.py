#!/usr/bin/env python3
"""Offline package builder and bounded serial file server for InventureBoot LAB.

This does not install the bootloader, bypass stock validation, read arbitrary
printer memory, or change protection. Serial requires this loader ALREADY running.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import sys
import time
import zlib

BASE = 0x08010000
LIMIT = 0x0803FF00 - BASE
TARGET = 0x32463956
MAGIC = b"INVB001\0"
HEADER = struct.Struct("<8sHHIIIII32s")


def validate_vectors(data: bytes) -> tuple[int, int]:
    if not 8 <= len(data) <= LIMIT:
        raise ValueError(f"Application size must be 8..{LIMIT} bytes")
    sp, pc = struct.unpack_from("<II", data)
    if not 0x20000000 < sp <= 0x20020000 or sp & 7:
        raise ValueError("Initial MSP is outside 128 KiB SRAM or is not 8-byte aligned")
    if not pc & 1 or not BASE + 8 <= pc & ~1 <= BASE + len(data) - 2:
        raise ValueError("Reset vector is not a Thumb entry within the application")
    return sp, pc


def pack(data: bytes) -> bytes:
    validate_vectors(data)
    return HEADER.pack(MAGIC, 64, 1, TARGET, BASE, len(data), 0, 0,
                       hashlib.sha256(data).digest()) + data


def inspect(data: bytes) -> dict:
    if len(data) < 72:
        raise ValueError("Truncated package")
    magic, size, version, target, base, length, flags, reserved, digest = HEADER.unpack_from(data)
    if (magic, size, version, target, base, flags, reserved) != (MAGIC, 64, 1, TARGET, BASE, 0, 0):
        raise ValueError("Unknown package, target, address, flags, or version")
    if length != len(data) - 64 or not 8 <= length <= LIMIT:
        raise ValueError("Payload length mismatch or exceeds application slot")
    payload = data[64:]
    if hashlib.sha256(payload).digest() != digest:
        raise ValueError("SHA-256 mismatch")
    sp, pc = validate_vectors(payload)
    return {"format": "INVB001", "application_base": hex(base), "payload_bytes": length,
            "sha256": digest.hex(), "initial_msp": hex(sp), "reset_vector": hex(pc),
            "signature": "NONE: integrity check, not authentication",
            "compatibility": "InventureBoot LAB only; NOT the stock InventureUpdate format"}


def read_bounded(path: Path, maximum: int) -> bytes:
    if path.stat().st_size > maximum:
        raise ValueError(f"File exceeds {maximum} bytes")
    data = path.read_bytes()
    if len(data) > maximum:
        raise ValueError("File grew while reading")
    return data


def serve(port: str, baud: int, data: bytes, update: bool) -> None:
    inspect(data)
    try:
        import serial
    except ImportError as exc:
        raise RuntimeError("Serial mode requires pyserial. Offline pack/inspect have no dependencies.") from exc
    # No auto-detection, auto-reset, arbitrary-memory or erase commands.
    ser = serial.Serial()
    ser.port, ser.baudrate, ser.timeout, ser.write_timeout = port, baud, 1, 5
    ser.dtr = False
    ser.rts = False
    ser.open()
    print("Requires InventureBoot already in maintenance mode; stock silent P4 is not supported.")
    print("Physical OK+UP confirmation will be required." if update else "Checking package only; no installation requested.")
    try:
        ser.reset_input_buffer()
        ser.write(f"{'UARTUPDATE' if update else 'UARTCHECK'} {len(data)}\n".encode("ascii"))
        deadline = time.monotonic() + 120
        requests = 0
        while time.monotonic() < deadline:
            line = ser.read_until(b"\n", 160)
            if not line:
                continue
            if not line.endswith(b"\n"):
                raise RuntimeError("Malformed or overlong response line")
            text = line.decode("ascii", errors="strict").strip()
            if text.startswith("READ "):
                parts = text.split()
                if len(parts) != 3:
                    raise RuntimeError("Malformed READ request")
                off, count = int(parts[1]), int(parts[2])
                if not 1 <= count <= 512 or not 0 <= off <= len(data) or count > len(data)-off:
                    raise RuntimeError("Out-of-range READ request")
                requests += 1
                if requests > 2000:
                    raise RuntimeError("Excessive READ requests")
                body = struct.pack("<4sIH", b"IBD1", off, count) + data[off:off+count]
                ser.write(body + struct.pack("<I", zlib.crc32(body) & 0xFFFFFFFF))
                deadline = time.monotonic() + 120
            else:
                print(text, flush=True)
                if text.startswith("ERROR "):
                    raise RuntimeError(text)
                if text == "DONE OK":
                    return
        raise RuntimeError("No completed operation before timeout")
    finally:
        ser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("pack", help="Package a raw application linked at 0x08010000, not a bootloader")
    p.add_argument("application", type=Path)
    p.add_argument("output", type=Path)
    p = sub.add_parser("inspect", help="Validate an .IVB file locally, without connecting to the printer")
    p.add_argument("package", type=Path)
    p = sub.add_parser("serial", help="Serve a package to an already-installed InventureBoot")
    p.add_argument("package", type=Path)
    p.add_argument("--port", required=True)
    p.add_argument("--baud", type=int, default=250000)
    p.add_argument("--update", action="store_true", help="Explicitly request application replacement (physical confirmation still required)")
    args = parser.parse_args()
    try:
        if args.command == "pack":
            data = pack(read_bounded(args.application, LIMIT))
            # Exclusive creation prevents accidentally replacing an original binary.
            with args.output.open("xb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            print(json.dumps(inspect(data), indent=2, ensure_ascii=False))
        else:
            data = read_bounded(args.package, LIMIT+64)
            if args.command == "inspect":
                print(json.dumps(inspect(data), indent=2, ensure_ascii=False))
            else:
                serve(args.port, args.baud, data, args.update)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
