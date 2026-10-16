#!/usr/bin/env python3
"""Create independent clean checkouts using a local source Git object cache.

Only committed objects are reused. No working files, out or compiler caches
are copied. Each clone repacks and removes alternates before checkout.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

bundle = Path(__file__).resolve().parents[1]
cache, target = map(lambda p: Path(p).resolve(), sys.argv[1:3])
if target.exists() and any(target.iterdir()):
    raise SystemExit('Destination must be empty')
target.mkdir(parents=True, exist_ok=True)
for project in ET.parse(bundle/'manifest.xml').getroot().findall('project'):
    rel = project.get('path', project.get('name'))
    destination = target/rel
    destination.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git','clone','--local','--no-checkout',str(cache/rel),str(destination)], check=True)
    subprocess.run(['git','-C',str(destination),'repack','-a','-d'], check=True)
    alternate = destination/'.git/objects/info/alternates'
    if alternate.exists():
        alternate.unlink()
    subprocess.run(['git','-C',str(destination),'checkout','--detach',project.get('revision')], check=True)
    for directive in project:
        if directive.tag not in ('copyfile','linkfile'):
            continue
        src = destination/directive.get('src')
        dst = target/directive.get('dest')
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() or dst.is_symlink():
            dst.unlink()
        if directive.tag == 'copyfile':
            shutil.copyfile(src, dst)
        else:
            dst.symlink_to(os.path.relpath(src, dst.parent))
