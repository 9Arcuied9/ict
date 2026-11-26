"""设置 INSPECTION_TEST_POSTGRES=1 可在独立临时数据库上运行集成测试。"""
from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor
import csv
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from scripts.inspection_archive import FIELDS, iter_archives


# A one-pixel JPEG for exercising the image round trip in a temporary database.
JPEG = b64decode(
    '/9j/4AAQSkZJRgABAQEAYABgAAD/2wBDAAMCAgMCAgMDAwMEAwMEBQgFBQQEBQoHBwYIDAoMDAsKCwsNDhIQDQ'
    '4RDgsLEBYQERMUFRUVDA8XGBYUGBIUFRT/2wBDAQMEBAUEBQkFBQkUDQsNFBQUFBQUFBQUFBQUFBQUFBQUFBQU'
    'FBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBQUFBT/wAARCAABAAEDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAA'
    'AAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUFBAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0Kx'
    'wRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVWV1hZWmNkZWZnaGlqc3R1dnd4eXqDhI'
    'WGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi4+Tl5ufo6erx8vP0'
    '9fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAECAx'
    'EEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RV'
    'VldYWVpjZGVmZ2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyM'
    'nK0tPU1dbX2Nna4uPk5ebn6Onq8vP09fb3+Pn6/9oADAMBAAIRAxEAPwD8qqKKKAP/2Q=='
)


def archive_rows():
    first = '2026-11-20T08:10:00+08:00'
    last = '2026-11-22T08:12:00+08:00'
    located = {'class': 'D40', 'lat': 30.67, 'lon': 104.06}
    visits = {
        'trip-1': dict(located, event_id='1', captured_at=first, gps_quality='aligned'),
        'trip-2': dict(located, event_id='1', captured_at=last, gps_quality='aligned'),
    }
    no_gps = {'class': 'D00', 'lat': None, 'lon': None}
    return [
        dict(located, defect_id=1, first_seen=first, last_seen=last, observation_count=2,
             crop='crops/1.jpg', visits=json.dumps(visits)),
        dict(no_gps, defect_id=2, first_seen=last, last_seen=last, observation_count=1,
             crop='', visits=json.dumps({'trip-2': dict(no_gps, event_id='2', captured_at=last, gps_quality='gap')})),
    ]


def write_archive(path, rows, fields=FIELDS):
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


class ArchiveTest(unittest.TestCase):
    def test_final_archive_and_raw_tracker_format(self):
        with TemporaryDirectory() as work:
            path = Path(work) / 'events.csv'
            write_archive(path, archive_rows())
            archives = list(iter_archives(path))
            self.assertEqual([len(a['visits']) for a in archives], [2, 1])
            self.assertIsNone(archives[1]['lat'])
            path.write_text('event_id,peak_s,video\n1,2,trip.mp4\n')
            with self.assertRaisesRegex(ValueError, 'defect_id'):
                list(iter_archives(path))


@unittest.skipUnless(os.environ.get('INSPECTION_TEST_POSTGRES') == '1', '需要临时 PostgreSQL 测试库')
class DatabaseTest(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        self.admin = psycopg.connect(autocommit=True, connect_timeout=8)
        self.addCleanup(self.admin.close)
        name = 'inspection_test_' + uuid4().hex
        db_name = sql.Identifier(name)
        self.admin.execute(sql.SQL('CREATE DATABASE {}').format(db_name))
        self.addCleanup(self.admin.execute, sql.SQL('DROP DATABASE {}').format(db_name))
        config = patch.dict(os.environ, PGDATABASE=name)
        config.start()
        self.addCleanup(config.stop)
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / 'events.csv'
        self.input_path = Path(folder.name) / 'input' / 'events.csv'
        self.rows = archive_rows()
        crops = self.input_path.parent / 'crops'
        crops.mkdir(parents=True)
        (crops / '1.jpg').write_bytes(JPEG)
        write_archive(self.input_path, self.rows)

    def test_concurrent_replay_timeline_and_images(self):
        from fastapi import HTTPException
        from backend.api import defect_detail, defect_image, list_defects
        from backend.import_events import import_events
        from web.coords import wgs84_to_gcj02
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(import_events, [self.input_path] * 2)), [(2, 3, 0)] * 2)
        one = defect_detail(1)
        self.assertEqual(one['observation_count'], 2)
        self.assertEqual([v['trip_id'] for v in one['observations']], ['trip-1', 'trip-2'])
        self.assertEqual(defect_image(1).body, JPEG)
        two = defect_detail(2)
        self.assertIsNone(two['lat'])
        self.assertIsNone(two['image_url'])
        self.assertEqual(two['observations'][0]['gps_quality'], 'gap')
        located, unlocated = list_defects()
        self.assertEqual((located['id'], located['obs_count'], located['image_url']),
                         (str(one['defect_id']), one['observation_count'], one['image_url']))
        self.assertEqual((located['lng_gcj'], located['lat_gcj']), wgs84_to_gcj02(one['lon'], one['lat']))
        self.assertEqual(unlocated['id'], str(two['defect_id']))
        self.assertIsNone(unlocated['lng_gcj'])
        self.assertIsNone(unlocated['lat_gcj'])
        for endpoint, ident in [(defect_detail, 999), (defect_image, 2)]:
            with self.assertRaises(HTTPException) as failure:
                endpoint(ident)
            self.assertEqual(failure.exception.status_code, 404)

    def test_legacy_archive_image_backfill_and_increment(self):
        from backend.api import defect_detail
        from backend.import_events import import_events
        row = self.rows[0]
        write_archive(self.path, [row], FIELDS[:8])
        self.assertEqual(import_events(self.path), (1, 0, 1))
        self.assertEqual(defect_detail(1)['observation_count'], 2)
        self.assertEqual(defect_detail(1)['observations'], [])
        self.assertIsNone(defect_detail(1)['image_url'])
        import_events(self.input_path)
        visits = json.loads(row['visits'])
        visit = next(iter(visits.values()))
        visits['trip-3'] = dict(visit, trip_id='trip-3', captured_at='2026-11-25T00:00:00Z')
        row.update(visits=json.dumps(visits), observation_count=3, last_seen='2026-11-25T00:00:00Z')
        write_archive(self.path, [row])
        import_events(self.path)
        self.assertEqual(len(defect_detail(1)['observations']), 3)
        self.assertEqual(defect_detail(1)['image_url'], '/api/defects/1/image')
        with self.assertRaisesRegex(ValueError, '最新档案'):
            import_events(self.input_path)
        self.assertEqual(defect_detail(1)['observation_count'], 3)

    def test_invalid_late_row_rolls_back_whole_file(self):
        from backend.api import defect_detail
        from backend.import_events import import_events
        import_events(self.input_path)
        self.rows[0]['class'] = 'D20'
        self.rows[1]['lon'] = '181'
        write_archive(self.path, self.rows)
        with self.assertRaisesRegex(ValueError, '病害 2'):
            import_events(self.path)
        self.assertEqual(defect_detail(1)['type'], 'D40')
