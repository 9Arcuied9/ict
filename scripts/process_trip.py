"""处理一趟巡检，按最终病害 ID 更新共用的 CSV 和代表图。"""
from collections import Counter
import csv
from functools import lru_cache
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from xml.etree.ElementTree import ParseError

from filelock import FileLock

from . import trip_observations
from .damage_appearance import describe_crop
from .damage_matching import distance_m, match_appearance


FIELDS = ('defect_id', 'class', 'lat', 'lon', 'first_seen', 'last_seen', 'observation_count',
          'crop', 'heading', 'road_side', 'visits')
OBSERVATION_FIELDS = ('event_id', 'class', 'lat', 'lon', 'captured_at', 'peak_s', 'gps_quality')


def _read_archive(path):
    if not path.is_file():
        return {}
    # visits 保存各趟观测摘要，长期跟踪的单元格可能超过 csv 默认上限。
    csv.field_size_limit(max(csv.field_size_limit(), 2**31 - 1))
    archives = {}
    with path.open(encoding='utf-8-sig', newline='') as stream:
        rows = csv.DictReader(stream)
        if not set(FIELDS[:8]).issubset(rows.fieldnames or ()):
            raise ValueError(f'历史档案表缺少必要列：{path}')
        for row in rows:
            defect_id = int(row['defect_id'])
            if defect_id in archives:
                raise ValueError(f'历史档案表包含重复编号：{defect_id}')
            archive = {name: row.get(name) or None for name in FIELDS}
            archive.update(defect_id=defect_id, observation_count=int(row['observation_count']),
                           visits=json.loads(row.get('visits') or '{}'))
            for field in ('lat', 'lon', 'heading'):
                if archive[field] is not None:
                    archive[field] = float(archive[field])
            for field in ('first_seen', 'last_seen'):
                if archive[field] is not None:
                    archive[field] = trip_observations.utc_time(archive[field]).isoformat()
            archives[defect_id] = archive
    return archives


def _result(archive, visit, output_root):
    return dict(visit, crop=str(output_root / archive['crop']) if archive['crop'] else None,
                evidence=None, defect_id=archive['defect_id'], observation_count=archive['observation_count'],
                last_seen=archive['last_seen'])


def _match_trip(observations, archives, output_root):
    """只使用本趟开始前的档案，先判定所有候选，避免先处理的轨迹抢占档案。"""
    features = lru_cache(maxsize=128)(describe_crop)
    same_trip_ids = {obs['event_id'] for obs in observations}
    references = [dict(row, crop=str(output_root / row['crop']) if row['crop'] else None)
                  for row in archives.values() if row['lat'] is not None and row['lon'] is not None]
    plans = []
    strong_claims = Counter()
    for obs in observations:
        if obs['lat'] is None or obs['lon'] is None:
            plans.append((obs, None, 'new_without_location'))
            continue
        nearby = [ref for ref in references if distance_m(obs['lat'], obs['lon'], ref['lat'], ref['lon']) <= 15]
        strong = [ref['defect_id'] for ref in nearby
                  if match_appearance(obs, ref, 0.8, features)[1] == 'strong']
        strong_claims.update(strong)
        if same_trip_ids.intersection(obs['possible_duplicate_of'].split('|')):
            plans.append((obs, None, 'new_within_trip'))
        elif len(nearby) == len(strong) == 1:
            plans.append((obs, strong[0], 'auto_match'))
        else:
            plans.append((obs, None, 'new_unmatched' if nearby else 'new'))
    return [(obs, None, 'new_within_trip') if defect_id and strong_claims[defect_id] > 1
            else (obs, defect_id, decision) for obs, defect_id, decision in plans]


def _apply_trip(plans, archives, trip_id, output_root, work):
    next_id = max(archives, default=0) + 1
    results = []
    created = []
    try:
        for obs, defect_id, decision in plans:
            captured = obs['captured_at']
            if defect_id is None:
                relative_crop = None
                if obs['crop']:
                    relative_crop = f'crops/{next_id}.jpg'
                    destination = output_root / relative_crop
                    destination.parent.mkdir(exist_ok=True)
                    Path(obs['crop']).replace(destination)
                    created.append(destination)
                archive = {field: obs[field] for field in ('class', 'lat', 'lon', 'heading', 'road_side')}
                archive.update(defect_id=next_id, first_seen=captured, last_seen=captured,
                               observation_count=0, crop=relative_crop, visits={})
                archives[next_id] = archive
                next_id += 1
            else:
                archive = archives[defect_id]
                archive['first_seen'] = min(archive['first_seen'] or captured, captured)
                archive['last_seen'] = max(archive['last_seen'] or captured, captured)
            archive['observation_count'] += 1
            visit = {field: obs[field] for field in OBSERVATION_FIELDS}
            visit.update(trip_id=trip_id, decision=decision)
            archive['visits'][trip_id] = visit
            results.append(_result(archive, visit, output_root))

        archive_csv = work / 'archive.csv'
        with archive_csv.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            for row in archives.values():
                writer.writerow(dict(row, visits=json.dumps(row['visits'], ensure_ascii=False, separators=(',', ':'))))
        archive_csv.replace(output_root / 'events.csv')
    except Exception:
        for crop in created:
            crop.unlink(missing_ok=True)
        raise
    return results


def process_trip(video_path, gps_path, start=None, *, weights, output_root, device='cpu'):
    """返回本趟观测。每个病害在 events.csv 中占一行，在 crops/ 中保存一张代表图。"""
    video, gps = Path(video_path).resolve(), Path(gps_path).resolve()
    output_root = Path(output_root).resolve()
    try:
        gps_fixes, started, start_source = trip_observations.read_video_gps(gps, video, start)
    except ParseError as exc:
        raise ValueError('GPS 文件不是有效的 GPX 文档。') from exc
    trip_id = 'upload-' + uuid4().hex
    output_root.mkdir(parents=True, exist_ok=True)

    # 同一输出目录的读取、匹配和写入共用一把锁，防止并发覆盖 CSV。
    with FileLock(str(output_root) + '.lock'):
        weights = Path(weights).resolve()
        if not weights.is_file():
            raise FileNotFoundError(f'找不到模型权重：{weights}')
        from .track_video import run

        with TemporaryDirectory(prefix='.tracking-', dir=output_root) as temporary:
            work = Path(temporary)
            run(video, weights, work, device)
            observations = trip_observations.load_observations(
                work / 'events.csv', trip_id, started, gps_fixes=gps_fixes, max_gap_s=5.0, max_accuracy_m=25.0)
            archives = _read_archive(output_root / 'events.csv')
            results = _apply_trip(_match_trip(observations, archives, output_root),
                                  archives, trip_id, output_root, work)
        return {'trip_id': trip_id, 'observations': results,
                'video_start': started.isoformat(), 'video_start_source': start_source}

