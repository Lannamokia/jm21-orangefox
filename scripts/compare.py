#!/usr/bin/env python3
"""Compare a rebuilt image with a versioned device-verified reference."""
import argparse
import hashlib
from pathlib import Path
references = {
    'v64': '4ca6a5c88c087f73b89ab6cad7d6f6928a14ac553d3ea1c6b6049e9451847464',
    'v65': '56cfe1c935d70899b2aef4ed658de9b3b338c8f154d38057d2df9631c0f6d36f',
}
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('image', type=Path)
parser.add_argument('--reference', choices=references, default='v65')
args = parser.parse_args()
expected = references[args.reference]
image = args.image
actual = hashlib.sha256(image.read_bytes()).hexdigest()
print(actual, image.name)
if actual != expected:
    raise SystemExit('Image differs from '+args.reference)
if image.stat().st_size != 27275264:
    raise SystemExit('Unexpected image size')
print('PASS: byte-identical to device-verified '+args.reference)
