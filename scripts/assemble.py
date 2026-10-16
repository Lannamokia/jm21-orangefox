#!/usr/bin/env python3
"""Assemble using rebuilt recovery/libbase and the hashed fixed asset bundle."""
import hashlib
import json
import pathlib
import subprocess
import sys

bundle = pathlib.Path(__file__).resolve().parents[1]
root = pathlib.Path(sys.argv[1]).resolve()
output = pathlib.Path(sys.argv[2]).resolve()
for entry in json.loads((bundle/'assembly-inputs.json').read_text()):
    data = (bundle/entry['path']).read_bytes()
    if len(data) != entry['bytes'] or hashlib.sha256(data).hexdigest() != entry['sha256']:
        raise SystemExit('Input hash mismatch: '+entry['path'])
def run(script, *args):
    subprocess.run([sys.executable,str(bundle/'work/_cmp'/script),*map(str,args)], cwd=bundle, check=True)
run('cpio_edit.py','--no-image')
run('cpio_swap.py','system/bin/recovery',root/'system/bin/recovery')
run('cpio_swap.py','system/lib64/libbase.so',root/'system/lib64/libbase.so')
run('cpio_add_hal.py')
output.parent.mkdir(parents=True,exist_ok=True)
output.write_bytes((bundle/'work/v32-crypto-km-lz4.img').read_bytes())
print(hashlib.sha256(output.read_bytes()).hexdigest(), output.name)
