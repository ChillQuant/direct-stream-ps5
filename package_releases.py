#!/usr/bin/env python3
"""Build and package distribution bundles for macOS, Windows, and Pure Python."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
dist = root / 'dist'

# Ensure macOS bundle is up to date first
subprocess.run([sys.executable, str(root / "build_bundle.py")], check=True)

if dist.exists():
    shutil.rmtree(dist)
dist.mkdir(parents=True, exist_ok=True)

# 1. macOS Bundle
mac_dir = dist / 'DIRECT-STREAM-FOR-PLAYSTATION-5-macOS'
mac_dir.mkdir(parents=True, exist_ok=True)
shutil.copytree(root / 'PS5 Direct Streamer.app', mac_dir / 'PS5 Direct Streamer.app', symlinks=True)
for item in [
    'Launch Direct Stream for PlayStation 5.command',
    'Launch PS5 Streamer.command',
    'launch.sh',
    'install_to_applications.sh',
    'ps5_streamer.py',
    'transfer_core.py',
    'resolver.py',
    'zip_streamer.py',
    'AppIcon.icns',
    'README.md',
    'LICENSE',
    'requirements.txt',
    'requirements-dev.txt',
    '.gitignore',
    'VALIDATION.md'
]:
    p = root / item
    if p.exists():
        shutil.copy2(p, mac_dir / item)
shutil.copytree(root / 'web', mac_dir / 'web')
shutil.copytree(root / 'tests', mac_dir / 'tests')

# 2. Windows Bundle
win_dir = dist / 'DIRECT-STREAM-FOR-PLAYSTATION-5-Windows'
win_dir.mkdir(parents=True, exist_ok=True)
for item in [
    'Launch Direct Stream for PlayStation 5.bat',
    'run.bat',
    'ps5_streamer.py',
    'transfer_core.py',
    'resolver.py',
    'zip_streamer.py',
    'README.md',
    'LICENSE',
    'requirements.txt',
    'requirements-dev.txt',
    '.gitignore',
    'VALIDATION.md'
]:
    p = root / item
    if p.exists():
        shutil.copy2(p, win_dir / item)
shutil.copytree(root / 'web', win_dir / 'web')
shutil.copytree(root / 'tests', win_dir / 'tests')

# 3. Pure Python (Cross-platform, clean for GitHub)
py_dir = dist / 'DIRECT-STREAM-FOR-PLAYSTATION-5-PurePython'
py_dir.mkdir(parents=True, exist_ok=True)
for item in [
    'ps5_streamer.py',
    'transfer_core.py',
    'resolver.py',
    'zip_streamer.py',
    'run_android.sh',
    'install_android.sh',
    'README.md',
    'LICENSE',
    'requirements.txt',
    'requirements-dev.txt',
    '.gitignore',
    'VALIDATION.md'
]:

    p = root / item
    if p.exists():
        shutil.copy2(p, py_dir / item)
shutil.copytree(root / 'web', py_dir / 'web')
shutil.copytree(root / 'tests', py_dir / 'tests')

# Create ZIP archives
for folder_name in ['DIRECT-STREAM-FOR-PLAYSTATION-5-macOS', 'DIRECT-STREAM-FOR-PLAYSTATION-5-Windows', 'DIRECT-STREAM-FOR-PLAYSTATION-5-PurePython']:
    zip_path = dist / f'{folder_name}.zip'
    shutil.make_archive(str(dist / folder_name), 'zip', root_dir=dist, base_dir=folder_name)
    print(f'Created: dist/{folder_name}.zip ({zip_path.stat().st_size / 1024:.1f} KB)')

print('\nDistribution packaging complete. All bundles ready in dist/')
