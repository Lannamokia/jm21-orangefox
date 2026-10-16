#!/usr/bin/env python3
"""Fail unless a rebuilt image exactly matches the device-verified v64 image."""
import hashlib
from pathlib import Path
import sys

expected = '4ca6a5c88c087f73b89ab6cad7d6f6928a14ac553d3ea1c6b6049e9451847464'
image = Path(sys.argv[1])
actual = hashlib.sha256(image.read_bytes()).hexdigest()
print(actual, image.name)
if actual != expected:
    raise SystemExit('Image differs from v64; do not publish as a reproduced v64')
if image.stat().st_size != 27275264:
    raise SystemExit('Unexpected image size')
print('PASS: byte-identical to device-verified v64')
