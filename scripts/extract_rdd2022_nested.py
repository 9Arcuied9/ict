"""Expand official country archives, retaining the verified outer archive."""
import json
import shutil
import zipfile
from pathlib import Path

root = Path(__file__).resolve().parents[1]
base = (root / 'data/raw/rdd2022/RDD2022').resolve()
reports = []
for source in sorted(base.glob('*.zip')):
    with zipfile.ZipFile(source) as archive:
        required = sum(x.file_size for x in archive.infolist())
        if shutil.disk_usage(base).free < required + 512 * 1024**2:
            raise RuntimeError(f'Insufficient disk space for {source.name}')
        for entry in archive.infolist():
            target = (base / entry.filename).resolve()
            if not target.is_relative_to(base):
                raise RuntimeError('Unsafe archive path')
            archive.extract(entry, base)  # verifies member CRC
    # Only remove this generated intermediate copy; the full outer ZIP is retained.
    assert source.resolve().parent == base and source.suffix == '.zip'
    source.unlink()
    print('Extracted and CRC verified:', source.name, flush=True)

for country in sorted(x for x in base.iterdir() if x.is_dir()):
    row = {'subset': country.name}
    for split in ['train', 'test']:
        row[split + '_images'] = sum(1 for _ in (country / split / 'images').glob('*.jpg'))
        row[split + '_xml'] = sum(1 for _ in (country / split / 'annotations').rglob('*.xml'))
    reports.append(row)
report = {'root': str(base), 'subsets': reports,
          'total_images': sum(x['train_images'] + x['test_images'] for x in reports),
          'total_xml': sum(x['train_xml'] + x['test_xml'] for x in reports),
          'integrity': 'Outer archive official MD5 and all extracted ZIP member CRC verified'}
assert report['total_images'] == 47420, report
assert report['total_xml'] == 38385, report
(root / 'data/downloads/extracted-manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
print(json.dumps(report, indent=2), flush=True)
