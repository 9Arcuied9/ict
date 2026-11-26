"""将一个巡检输出目录的累计 events.csv 同步到 PostgreSQL。"""
import argparse
import csv
from pathlib import Path, PurePosixPath
import sys

import psycopg

from scripts.inspection_archive import iter_archives
from scripts.trip_observations import utc_time
from .db import connect


UPSERT_DEFECT = """
    INSERT INTO inspection.defects AS d
        (defect_id, damage_class, lat, lon, first_seen, last_seen, observation_count, heading, road_side, crop)
    VALUES (%(defect_id)s, %(class)s, %(lat)s, %(lon)s, %(first_seen)s, %(last_seen)s,
            %(observation_count)s, %(heading)s, %(road_side)s, %(crop)s)
    ON CONFLICT (defect_id) DO UPDATE SET
        damage_class = excluded.damage_class, lat = excluded.lat, lon = excluded.lon,
        first_seen = excluded.first_seen, last_seen = excluded.last_seen,
        observation_count = excluded.observation_count, heading = excluded.heading,
        road_side = excluded.road_side, crop = excluded.crop
    WHERE d.observation_count <= excluded.observation_count
      AND NOT EXISTS (
          SELECT 1 FROM inspection.observations o WHERE o.defect_id = d.defect_id
            AND o.trip_id <> ALL(%(trip_ids)s::text[])
      )
"""

UPSERT_VISIT = """
    INSERT INTO inspection.observations
        (defect_id, trip_id, event_id, damage_class, captured_at, lat, lon, peak_s, gps_quality, decision)
    VALUES (%(defect_id)s, %(trip_id)s, %(event_id)s, %(class)s, %(captured_at)s,
            %(lat)s, %(lon)s, %(peak_s)s, %(gps_quality)s, %(decision)s)
    ON CONFLICT (defect_id, trip_id) DO UPDATE SET
        event_id = excluded.event_id, damage_class = excluded.damage_class, captured_at = excluded.captured_at,
        lat = excluded.lat, lon = excluded.lon, peak_s = excluded.peak_s,
        gps_quality = excluded.gps_quality, decision = excluded.decision
"""


def sync_image(cur, root, archive):
    """缺少照片时仍可导入档案。下次提供照片并重导即可补齐。"""
    crop = archive['crop']
    if not crop:
        return False
    relative = PurePosixPath(crop.replace('\\', '/'))
    if len(relative.parts) < 2 or relative.parts[0] != 'crops' or '..' in relative.parts:
        raise ValueError('代表图必须是 crops/ 下的相对路径')
    path = (root / relative).resolve()
    if not path.is_relative_to(root / 'crops') or path.suffix.lower() not in {'.jpg', '.jpeg'}:
        raise ValueError('代表图必须是 crops/ 下的 JPEG 文件')
    if not path.is_file():
        return True
    data = path.read_bytes()
    if not data.startswith(b'\xff\xd8\xff') or not data.rstrip().endswith(b'\xff\xd9'):
        raise ValueError(f'无效的 JPEG 文件：{relative}')
    cur.execute("""
        INSERT INTO inspection.images AS i (defect_id, crop, jpeg) VALUES (%s, %s, %s)
        ON CONFLICT (defect_id) DO UPDATE SET crop = excluded.crop, jpeg = excluded.jpeg
        WHERE (i.crop, i.jpeg) IS DISTINCT FROM (excluded.crop, excluded.jpeg)
    """, (archive['defect_id'], crop, data))
    return False


def sync_defect(cur, root, archive):
    history = archive['visits']
    if not isinstance(history, dict) or len(history) > archive['observation_count']:
        raise ValueError('visits 应为按巡检编号索引的对象，且条数不能超过累计观测数')
    cur.execute(UPSERT_DEFECT, dict(archive, trip_ids=list(history)))
    if cur.rowcount != 1:
        raise ValueError('该快照的累计次数更少或缺少已入库的巡检，请使用同一输出目录中的最新档案')
    visits = []
    for trip_id, visit in history.items():
        if not isinstance(visit, dict) or visit.get('trip_id', trip_id) != trip_id:
            raise ValueError(f'巡检 {trip_id} 的记录格式或编号不一致')
        captured = visit.get('captured_at')
        visits.append({
            'defect_id': archive['defect_id'], 'trip_id': trip_id,
            'event_id': visit['event_id'], 'class': visit['class'],
            'captured_at': utc_time(captured) if captured else None,
            'lat': visit.get('lat'), 'lon': visit.get('lon'), 'peak_s': visit.get('peak_s'),
            'gps_quality': visit.get('gps_quality'), 'decision': visit.get('decision'),
        })
    if visits:
        cur.executemany(UPSERT_VISIT, visits)
    return sync_image(cur, root, archive)


def import_events(path):
    path = Path(path).resolve(strict=True)
    defects = observations = missing_images = 0
    with connect() as conn, conn.cursor() as cur:
        # 同一个数据库只接收一个巡检档案序列，串行化并发导入，包括首次建表。
        cur.execute("SELECT pg_advisory_xact_lock(782431, 1)")
        cur.execute(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
        for archive in iter_archives(path):
            try:
                missing_images += sync_defect(cur, path.parent, archive)
            except (KeyError, TypeError, ValueError, AttributeError, psycopg.DataError, psycopg.IntegrityError) as exc:
                raise ValueError(f"病害 {archive['defect_id']} 导入失败：{exc}") from exc
            defects += 1
            observations += len(archive['visits'])
    return defects, observations, missing_images


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv', type=Path, help='最终病害档案，例如 output/inspections/events.csv')
    try:
        defects, observations, missing_images = import_events(parser.parse_args().csv)
    except (ValueError, OSError, csv.Error, psycopg.Error) as exc:
        print(f'导入未完成：{exc}', file=sys.stderr)
        return 1
    print(f'导入成功。本次 CSV 含 {defects} 处病害、{observations} 条观测明细，'
          f'引用但在本地缺失的代表图 {missing_images} 张。')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
