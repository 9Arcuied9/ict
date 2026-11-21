"""Convert original RDD2022 VOC annotations without copying image contents."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

from PIL import Image
import yaml

CLASSES = ['D00', 'D10', 'D20', 'D40']


def convert(xml, width, height, stats):
    annotation = ET.parse(xml).getroot()
    if (int(annotation.findtext('size/width')), int(annotation.findtext('size/height'))) != (width, height):
        stats['xml_size_mismatch'] += 1
    labels = []
    for obj in annotation.findall('object'):
        name = obj.findtext('name', '').strip()
        if name not in CLASSES:
            stats['ignored_class:' + name] += 1
            continue
        box = obj.find('bndbox')
        values = [float(box.findtext(k)) for k in ['xmin', 'ymin', 'xmax', 'ymax']]
        if not all(math.isfinite(x) for x in values):
            raise ValueError(f'Non-finite box in {xml}')
        # VOC uses 1-based inclusive coordinates; convert to zero-based half-open.
        x1, y1, x2, y2 = values
        x1, y1 = x1 - 1, y1 - 1
        clipped = [max(0, min(width, x1)), max(0, min(height, y1)),
                   max(0, min(width, x2)), max(0, min(height, y2))]
        if clipped != [x1, y1, x2, y2]:
            stats['clipped_boxes'] += 1
        x1, y1, x2, y2 = clipped
        if x2 <= x1 or y2 <= y1:
            stats['invalid_boxes_dropped'] += 1
            continue
        cls = CLASSES.index(name)
        labels.append(f'{cls} {(x1+x2)/2/width:.8f} {(y1+y2)/2/height:.8f} {(x2-x1)/width:.8f} {(y2-y1)/height:.8f}')
        stats['boxes:' + name] += 1
    return labels


def prepare(source, output, val_fraction=0.2, seed=42, group_size=100, subsets=None, mode='hardlink'):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not 0 < val_fraction < 1 or group_size < 1:
        raise ValueError('Require 0 < val_fraction < 1 and group_size >= 1')
    if output.exists():
        raise FileExistsError(f'Output already exists; choose a new version: {output}')
    countries = sorted(p for p in source.iterdir() if p.is_dir() and (not subsets or p.name in subsets))
    if subsets and set(subsets) != {p.name for p in countries}:
        raise ValueError('Requested subset missing')
    stats = Counter()
    records, official = [], []
    # Union duplicate-content groups so exact duplicates cannot cross train/val.
    parents, hashes = {}, {}
    def find(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key
    for country in countries:
        images = sorted((country / 'train/images').glob('*.jpg'))
        if not images:
            raise ValueError(f'No training images: {country}')
        for image in images:
            xml = country / 'train/annotations/xmls' / (image.stem + '.xml')
            if not xml.is_file():
                raise FileNotFoundError(f'Missing annotation: {xml}')
            with Image.open(image) as im:
                width, height = im.size
                im.verify()
            labels = convert(xml, width, height, stats)
            match = re.search(r'(\d+)$', image.stem)
            if not match:
                raise ValueError(f'No sequence ID: {image}')
            group = f'{country.name}:{int(match[1]) // group_size}'
            find(group)
            sha = hashlib.sha256(image.read_bytes()).hexdigest()
            if sha in hashes:
                parents[find(group)] = find(hashes[sha])
                stats['duplicate_images'] += 1
            hashes[sha] = group
            records.append({'source': image.relative_to(source).as_posix(), 'subset': country.name,
                            'group': group, 'sha256': sha, 'labels': labels})
        official.extend(p.relative_to(source).as_posix() for p in sorted((country / 'test/images').glob('*.jpg')))
        print(f'Checked {country.name}: {len(images)} training images', flush=True)
    for record in records:
        record['group'] = find(record['group'])
    # Select complete sequence groups deterministically, per country.
    val_groups = set()
    for country in countries:
        keys = sorted({r['group'] for r in records if r['subset'] == country.name},
                      key=lambda k: hashlib.sha256(f'{seed}:{k}'.encode()).hexdigest())
        if len(keys) < 2:
            raise ValueError(f'{country.name}: need at least two groups; reduce --group-size')
        val_groups.update(keys[:max(1, min(len(keys)-1, round(len(keys)*val_fraction)))])
    output.mkdir(parents=True)
    distribution = defaultdict(Counter)
    manifest = []
    for record in records:
        split = 'val' if record['group'] in val_groups else 'train'
        image = source / record['source']
        target = output / 'images' / split / image.name
        label = output / 'labels' / split / (image.stem + '.txt')
        target.parent.mkdir(parents=True, exist_ok=True)
        label.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ValueError(f'Duplicate image filename: {target}')
        if mode == 'hardlink':
            os.link(image, target)  # fail rather than silently copy many GB
        else:
            shutil.copy2(image, target)
        label.write_text('\n'.join(record['labels']) + ('\n' if record['labels'] else ''), encoding='utf-8')
        distribution[split]['images'] += 1
        if not record['labels']:
            distribution[split]['negative_images'] += 1
        for line in record['labels']:
            distribution[split][CLASSES[int(line.split()[0])]] += 1
        manifest.append({k: v for k, v in record.items() if k != 'labels'} | {'split': split})
    missing = [f'{split}:{name}' for split in ['train', 'val'] for name in CLASSES if not distribution[split][name]]
    report = {'classes': CLASSES, 'seed': seed, 'group_size': group_size, 'val_fraction': val_fraction,
              'split_method': 'country/sequence blocks with exact SHA256 duplicate groups joined',
              'limitation': 'Sequence IDs are a proxy, not verified route IDs; near duplicates across blocks may remain.',
              'coordinate_convention': 'VOC 1-based inclusive to zero-based half-open, clipped to actual image size',
              'stats': dict(stats), 'distribution': {k: dict(v) for k, v in distribution.items()},
              'missing_classes': missing, 'official_test_images': len(official), 'image_mode': mode}
    (output / 'dataset.yaml').write_text(yaml.safe_dump({'path': '.', 'train': 'images/train', 'val': 'images/val', 'names': dict(enumerate(CLASSES))}, sort_keys=False), encoding='utf-8')
    (output / 'manifest.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in manifest), encoding='utf-8')
    (output / 'official_test.json').write_text(json.dumps(official, indent=2), encoding='utf-8')
    (output / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if missing:
        raise ValueError(f'Classes absent in split: {missing}; inspect {output}/report.json')
    (output / 'READY').write_text('Conversion completed\n', encoding='utf-8')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='data/raw/rdd2022/RDD2022')
    parser.add_argument('--output', default='data/processed/rdd2022-modelarts-v1')
    parser.add_argument('--val-fraction', type=float, default=0.2)
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--group-size', type=int, default=100)
    parser.add_argument('--subsets', nargs='+')
    parser.add_argument('--mode', choices=['hardlink', 'copy'], default='hardlink')
    args = parser.parse_args()
    print(json.dumps(prepare(**vars(args)), indent=2))


if __name__ == '__main__':
    main()
