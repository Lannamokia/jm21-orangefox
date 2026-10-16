#!/usr/bin/env python3
"""Apply consolidated patches against exactly pinned upstream commits."""
import json
import hashlib
import pathlib
import shutil
import subprocess
import sys

bundle = pathlib.Path(__file__).resolve().parents[1]
source = pathlib.Path(sys.argv[1]).resolve()
kernel = json.loads((bundle/'kernel-input.json').read_text())
data = (bundle/kernel['path']).read_bytes()
if len(data) != kernel['bytes'] or hashlib.sha256(data).hexdigest() != kernel['sha256']:
    raise SystemExit('Kernel input hash mismatch')
# OrangeFox embeds the branch name in recovery. repo sync normally detaches
# HEAD; give this pinned checkout its original branch name before building.
subprocess.run(['git','-C',str(source/'bootable/recovery'),
                'checkout','-B','fox_12.1','HEAD'], check=True)
for record in json.loads((bundle/'patches.json').read_text()):
    project = source/record['path']
    revision = subprocess.check_output(['git','-C',str(project),'rev-parse','HEAD'], text=True).strip()
    if revision != record['revision']:
        raise SystemExit('Unexpected revision: '+record['path'])
    patch = str(bundle/record['patch'])
    cmd = ['git','-C',str(project),'apply']
    if subprocess.run(cmd+['--reverse','--check',patch], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
        print('Already applied:', record['path'])
        continue
    subprocess.run(cmd+['--check',patch], check=True)
    subprocess.run(cmd+[patch], check=True)
    print('Applied:', record['path'])
shutil.copytree(bundle/'device/fiio/jm21', source/'device/fiio/jm21', dirs_exist_ok=True)
