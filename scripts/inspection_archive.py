"""巡检算法和数据库导入器共用的最终档案格式。"""
import csv
import json

from .trip_observations import utc_time


FIELDS = ('defect_id', 'class', 'lat', 'lon', 'first_seen', 'last_seen', 'observation_count',
          'crop', 'heading', 'road_side', 'visits')


def iter_archives(path):
    # 长期跟踪同一病害时，visits 可能超过 csv 默认的单元格长度。
    csv.field_size_limit(max(csv.field_size_limit(), 2**31 - 1))
    with path.open(encoding='utf-8-sig', newline='') as stream:
        rows = csv.DictReader(stream, strict=True)
        missing = set(FIELDS[:8]).difference(rows.fieldnames or ())
        if missing:
            raise ValueError(f'最终病害档案缺少列 {", ".join(sorted(missing))}：{path}')
        seen = set()
        for row in rows:
            try:
                if None in row or any(row[field] is None for field in FIELDS[:8]):
                    raise ValueError('字段数量与表头不符')
                archive = {field: row.get(field) or None for field in FIELDS}
                defect_id = int(row['defect_id'])
                if defect_id in seen:
                    raise ValueError(f'重复病害编号 {defect_id}')
                seen.add(defect_id)
                archive.update(defect_id=defect_id, observation_count=int(row['observation_count']),
                               visits=json.loads(row.get('visits') or '{}'))
                for field in ('lat', 'lon', 'heading'):
                    if archive[field] is not None:
                        archive[field] = float(archive[field])
                for field in ('first_seen', 'last_seen'):
                    if archive[field] is not None:
                        archive[field] = utc_time(archive[field]).isoformat()
            except (ValueError, TypeError) as exc:
                raise ValueError(f'{path.name} 第 {rows.line_num} 行：{exc}') from exc
            yield archive
