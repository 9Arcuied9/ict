"""将确认轨迹的代表帧与 GPS 对齐，返回本趟观测。"""

from bisect import bisect_left
import csv
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET


BEIJING = timezone(timedelta(hours=8))


def utc_time(value):
    if not isinstance(value, datetime):
        value = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if value.utcoffset() is None:
        raise ValueError("Timestamps must include a timezone, e.g. 2026-11-23T08:00:00+08:00")
    return value.astimezone(timezone.utc)


def number(value, field, minimum=-math.inf, maximum=math.inf):
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"Invalid {field}: {value!r}")
    return result


@dataclass(frozen=True)
class Fix:
    time: datetime
    lat: float
    lon: float
    segment: int
    usable: bool
    accuracy: float | None
    heading: float | None


def parse_fix(row, segment):
    accuracy = row.get("accuracy_m")
    heading = row.get("heading")
    return Fix(
        utc_time(row["timestamp"]),
        number(row["lat"], "lat", -90, 90),
        number(row["lon"], "lon", -180, 180),
        segment,
        (row.get("fix") or "").strip().lower() not in {"none", "0", "invalid"},
        number(accuracy, "accuracy_m", 0) if accuracy else None,
        number(heading, "heading", 0, 360) % 360 if heading else None,
    )


def _gpx_fixes(root):
    fixes = []
    for segment, trkseg in enumerate(root.findall(".//{*}trkseg")):
        for point in trkseg.findall("{*}trkpt"):
            stamp = point.findtext("{*}time")
            if not stamp:
                raise ValueError("GPX track point is missing a timestamp")
            row = dict(point.attrib, timestamp=stamp, fix=point.findtext("{*}fix"),
                       heading=point.findtext("{*}course"))
            fixes.append(parse_fix(row, segment))
    return fixes


def _sorted_fixes(fixes):
    fixes.sort(key=lambda point: point.time)
    cleaned = []
    for point in fixes:
        if cleaned and point.time == cleaned[-1].time:
            if point != cleaned[-1]:
                raise ValueError(f"Conflicting GPS fixes at {point.time.isoformat()}")
        else:
            cleaned.append(point)
    return cleaned


def read_fixes(path):
    """读取 GPX 或 GPS CSV，返回按 UTC 时间排序的采样点，保留 GPX 分段以识别轨迹中断。"""
    if path is None:
        return []
    path = Path(path)
    fixes = []
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not {"timestamp", "lat", "lon"} <= set(reader.fieldnames or ()):
                raise ValueError("GPS CSV requires timestamp,lat,lon")
            for row in reader:
                fixes.append(parse_fix(row, 0))
    elif path.suffix.lower() == ".gpx":
        fixes = _gpx_fixes(ET.parse(path).getroot())
    else:
        raise ValueError(f"Expected a GPX or CSV track: {path}")
    return _sorted_fixes(fixes)


def _gpx_video_start(root, fixes, video_path):
    local_times = sorted(point.time.astimezone(BEIJING) for point in fixes)
    notes = root.findall("./{*}trk/{*}desc") + root.findall("./{*}metadata/{*}desc")
    stated = {clock for note in notes for clock in
              re.findall(r"北京时间\s*(\d{1,2}:\d{2}:\d{2})\s*开拍", note.text or "")}
    if len(stated) > 1:
        raise ValueError("GPX 写有多个开录时间，请手动填写 VIDEO_START。")
    if stated:
        clock = time.fromisoformat(next(iter(stated)).zfill(8))
        source = "gpx_description"
    else:
        match = re.fullmatch(r"(\d{2})\.(\d{2})\.(\d{2})", Path(video_path).stem)
        if match is None:
            raise ValueError("GPX 未注明开录时间，视频文件名也没有 HH.MM.SS，请手动填写 VIDEO_START。")
        clock = time(*map(int, match.groups()))
        source = "video_filename"
    days = {stamp.date() for stamp in local_times}
    candidates = [datetime.combine(day, clock, BEIJING) for day in days]
    if len(candidates) > 1:
        candidates = [stamp for stamp in candidates if local_times[0] <= stamp <= local_times[-1]]
    if len(candidates) != 1:
        raise ValueError("无法从 GPX 确定开录日期，请手动填写 VIDEO_START。")
    return utc_time(candidates[0]), source


def read_video_gps(path, video_path, start=None):
    """同时获取采样点和开录时间，GPX 只解析一次。"""
    if Path(path).suffix.lower() != ".gpx":
        if start is None:
            raise ValueError("GPS CSV 请配合手动填写的 VIDEO_START 使用。")
        return read_fixes(path), utc_time(start), "manual"

    root = ET.parse(path).getroot()
    fixes = _gpx_fixes(root)
    started, source = (utc_time(start), "manual") if start is not None else _gpx_video_start(root, fixes, video_path)
    # 开录前同一时刻的冲突坐标无法确认，跳过该时刻，开录后的冲突仍报错。
    earlier = {}
    conflicts = set()
    for point in fixes:
        if point.time < started:
            first = earlier.setdefault(point.time, point)
            if first != point:
                conflicts.add(point.time)
    return _sorted_fixes([point for point in fixes if point.time not in conflicts]), started, source


def location_at(time, times, fixes, max_gap_s, max_accuracy_m):
    """取指定时刻的 GPS 点或在相邻采样间插值。返回相机经纬度、精度、航向和质量标记。"""
    location = dict(lat=None, lon=None, accuracy_m=None, heading=None, gps_quality="missing")
    if not fixes:
        return location
    i = bisect_left(times, time)
    if i < len(times) and times[i] == time:
        left = right = fixes[i]
        fraction = 0.0
    else:
        if i == 0 or i == len(times):
            return dict(location, gps_quality="outside_track")
        left, right = fixes[i-1], fixes[i]
        if left.segment != right.segment:
            return dict(location, gps_quality="segment_break")
        duration = (right.time - left.time).total_seconds()
        if duration > max_gap_s:
            return dict(location, gps_quality="gap")
        fraction = (time - left.time).total_seconds() / duration
    accuracies = [p.accuracy for p in (left, right) if p.accuracy is not None]
    if not left.usable or not right.usable or any(a > max_accuracy_m for a in accuracies):
        return dict(location, gps_quality="poor_fix", accuracy_m=max(accuracies, default=None))
    # 经度和方向在边界处按最短角度插值，例如 359° 到 1° 的中点是正北。
    lon_delta = (right.lon - left.lon + 180) % 360 - 180
    heading = None
    if left.heading is not None and right.heading is not None:
        turn = (right.heading - left.heading + 180) % 360 - 180
        heading = (left.heading + turn * fraction) % 360
    accuracy = max(accuracies) if len(accuracies) == 2 else None
    return dict(lat=left.lat + (right.lat-left.lat) * fraction,
                lon=(left.lon + lon_delta * fraction + 180) % 360 - 180,
                accuracy_m=accuracy, heading=heading, gps_quality="aligned")


def evidence_path(folder, name):
    if not name:
        return None
    path = (folder / name).resolve()
    if not path.is_relative_to(folder):
        raise ValueError(f"Evidence path outside the event directory: {name!r}")
    return str(path) if path.is_file() else None


def load_observations(events_csv, trip_id, video_start=None, gps_path=None, *,
                      gps_offset_s=0.0, max_gap_s=10.0, max_accuracy_m=25.0, include_review=False,
                      gps_fixes=None):
    """读取事件表，默认每条 confirmed 轨迹返回一条观测。

    captured_at 是录像开始时间加峰值帧秒数，另加 gps_offset_s 才是查询 GPS 的时刻。
    调用方可以直接传入 read_fixes() 返回的采样点。用 location_at() 逐条对齐。缺定位仍返回观测，
    经纬度留空。crop 和 evidence 只解析为本机文件路径，图像特征由外观模块提取。
    """
    # process_trip() 显式传入 5 秒作为最大 GPS 采样间隔。
    events_csv = Path(events_csv).resolve()
    if not str(trip_id).strip():
        raise ValueError("trip_id must not be empty")
    if video_start is None and (gps_path is not None or gps_fixes is not None):
        raise ValueError("video_start with timezone is required to align a GPS track")
    start = utc_time(video_start) if video_start is not None else None
    offset = number(gps_offset_s, "gps_offset_s")
    gap = number(max_gap_s, "max_gap_s", 0)
    accuracy_limit = number(max_accuracy_m, "max_accuracy_m", 0)
    fixes = read_fixes(gps_path) if gps_fixes is None else gps_fixes
    times = [p.time for p in fixes]
    observations = []
    seen = set()
    videos = set()
    with events_csv.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"video", "event_id", "class", "status", "peak_s", "peak_conf", "evidence"}
        absent = sorted(required.difference(reader.fieldnames or ()))
        if absent:
            raise ValueError("Missing events.csv columns: " + ", ".join(absent))
        for row in reader:
            eid = row["event_id"].strip()
            status = row["status"].strip()
            if not eid or eid in seen:
                raise ValueError(f"Empty or duplicate event ID: {eid!r}")
            seen.add(eid)
            videos.add(row["video"])
            if len(videos) > 1:
                raise ValueError("A trip import expects events from one video")
            if status not in {"confirmed", "review"}:
                raise ValueError(f"Unknown event status: {status!r}")
            if status == "review" and not include_review:
                continue
            seconds = number(row["peak_s"], "peak_s", 0)
            captured = start + timedelta(seconds=seconds) if start is not None else None
            gps_at = captured + timedelta(seconds=offset) if captured is not None else None
            observations.append({
                "trip_id": trip_id,
                "video": row["video"],
                "event_id": eid,
                "class": row["class"],
                "road_side": (row.get("road_side") or "").strip() or None,
                "status": status,
                "captured_at": captured.isoformat() if captured else None,
                "gps_at": gps_at.isoformat() if gps_at else None,
                "gps_offset_s": offset,
                "peak_s": seconds,
                "peak_conf": number(row["peak_conf"], "peak_conf", 0, 1),
                "evidence": evidence_path(events_csv.parent, row["evidence"]),
                "crop": evidence_path(events_csv.parent, row.get("crop")),
                "possible_duplicate_of": row.get("possible_duplicate_of") or "",
                **location_at(gps_at, times, fixes, gap, accuracy_limit),
            })
    return observations
