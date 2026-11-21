"""Build the small code/weight upload folder; dataset is uploaded separately."""
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

root = Path(__file__).resolve().parents[1]
target = root / 'output/modelarts/code'
files = ['train.py', 'requirements-modelarts.txt', 'MODELARTS.md',
         'training/__init__.py', 'training/prepare.py', 'configs/train.yaml', 'weights/yolo11n.pt']
manifest = {}
for name in files:
    source = root / name
    destination = target / name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    manifest[name] = hashlib.sha256(source.read_bytes()).hexdigest()
(target / 'code-manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
bundle = target.parent / 'modelarts-code.zip'
with zipfile.ZipFile(bundle, 'w', zipfile.ZIP_DEFLATED) as archive:
    for name in files + ['code-manifest.json']:
        archive.write(target / name, name)
print('OBS code upload directory:', target)
print('Portable code ZIP (extract before OBS upload):', bundle)
