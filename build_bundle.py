#!/usr/bin/env python3
"""Keep the native Mac bundle in sync with the editable source tree."""
from pathlib import Path
import shutil
root = Path(__file__).resolve().parent
resources = root / 'PS5 Direct Streamer.app' / 'Contents' / 'Resources'
resources.mkdir(parents=True, exist_ok=True)
for name in ('ps5_streamer.py', 'transfer_core.py', 'resolver.py', 'zip_streamer.py', 'launch.sh', 'AppIcon.icns'):
    if (root / name).exists():
        shutil.copy2(root / name, resources / name)
shutil.copytree(root / 'web', resources / 'web', dirs_exist_ok=True)
print('Mac bundle source and web assets updated.')
