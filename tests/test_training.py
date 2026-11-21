import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from collections import Counter

from PIL import Image

from training.prepare import convert, prepare
from train import obs_copy


def fixture(root):
    for index in range(8):
        name = f'Country_{index*100:06d}'
        images = root / 'Country/train/images'
        labels = root / 'Country/train/annotations/xmls'
        images.mkdir(parents=True, exist_ok=True)
        labels.mkdir(parents=True, exist_ok=True)
        Image.new('RGB', (64, 64), (index*20, 60, 100)).save(images / f'{name}.jpg')
        objects = ''.join(f'<object><name>{cls}</name><bndbox><xmin>2</xmin><ymin>2</ymin><xmax>40</xmax><ymax>40</ymax></bndbox></object>' for cls in ['D00', 'D10', 'D20', 'D40'])
        (labels / f'{name}.xml').write_text(f'<annotation><size><width>64</width><height>64</height></size>{objects}</annotation>')
    # Force identical pixels/encoding across two different sequence blocks.
    (images / 'Country_000100.jpg').write_bytes((images / 'Country_000000.jpg').read_bytes())
    test = root / 'Country/test/images'
    test.mkdir(parents=True)
    Image.new('RGB', (64, 64)).save(test / 'held_out.jpg')


class DatasetTests(unittest.TestCase):
    def test_obs_transfers_use_platform_sdk(self):
        calls = []
        sdk = SimpleNamespace(file=SimpleNamespace(
            copy_parallel=lambda a, b: calls.append(('directory', a, b)),
            copy=lambda a, b: calls.append(('file', a, b))))
        with patch.dict('sys.modules', {'moxing': sdk}):
            obs_copy('obs://bucket/input/', '/cache/input')
            obs_copy('/cache/out', 'obs://bucket/out/')
            obs_copy('obs://bucket/model.pt', '/cache/model.pt', directory=False)
        self.assertEqual([x[0] for x in calls], ['directory', 'directory', 'file'])

    def test_deterministic_grouping_duplicates_and_official_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root / 'raw')
            first = prepare(root / 'raw', root / 'one')
            prepare(root / 'raw', root / 'two')
            self.assertEqual((root / 'one/manifest.jsonl').read_bytes(), (root / 'two/manifest.jsonl').read_bytes())
            rows = [json.loads(x) for x in (root / 'one/manifest.jsonl').read_text().splitlines()]
            hashes = {split: {x['sha256'] for x in rows if x['split'] == split} for split in ['train', 'val']}
            self.assertFalse(hashes['train'] & hashes['val'])
            self.assertEqual(first['stats']['duplicate_images'], 1)
            self.assertEqual(first['official_test_images'], 1)
            self.assertEqual(len(rows), 8)
            self.assertFalse(any('held_out' in x['source'] for x in rows))
            with self.assertRaises(FileExistsError):
                prepare(root / 'raw', root / 'one')

    def test_clipping_unknown_and_degenerate_boxes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'x.xml'
            box = lambda name, x1, x2: f'<object><name>{name}</name><bndbox><xmin>{x1}</xmin><ymin>1</ymin><xmax>{x2}</xmax><ymax>64</ymax></bndbox></object>'
            path.write_text('<annotation><size><width>64</width><height>64</height></size>' + box('D00', -5, 100) + box('D10', 40, 10) + box('Repair', 1, 64) + '</annotation>')
            stats = Counter()
            labels = convert(path, 64, 64, stats)
            self.assertEqual(labels, ['0 0.50000000 0.50000000 1.00000000 1.00000000'])
            self.assertEqual(stats['ignored_class:Repair'], 1)
            self.assertEqual(stats['invalid_boxes_dropped'], 1)

    def test_missing_xml_is_not_silently_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture(root / 'raw')
            next((root / 'raw/Country/train/annotations/xmls').glob('*.xml')).unlink()
            with self.assertRaises(FileNotFoundError):
                prepare(root / 'raw', root / 'output')


if __name__ == '__main__':
    unittest.main()
