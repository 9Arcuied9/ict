"""单趟入口：读入视频和模型权重，默认写出确认事件表和代表裁剪图。

process_trip() 读取本次输出，对齐 GPS 并更新跨行程档案。轨迹 ID 仅在本视频内有效。
"""
import argparse
import csv
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import mkdtemp
from types import SimpleNamespace

import cv2
import numpy as np
import torch


@dataclass
class Event:
    key: str
    track_id: int | None
    first: int
    start_box: np.ndarray
    last: int = 0
    last_box: np.ndarray | None = None
    velocity: np.ndarray = field(default_factory=lambda: np.zeros(4))
    votes: Counter = field(default_factory=Counter)
    hits: int = 0
    peak: float = 0
    peak_frame: int = 0
    peak_box: np.ndarray | None = None
    peak_label: str = ''
    confirmed_at_peak: bool = False
    confirmed: bool = False

    def observe(self, frame, box, label, score, fps):
        """更新轨迹和峰值帧信息。先记帧号和框，结束跟踪后才为确认轨迹取图。"""
        if self.hits:
            self.velocity = (box - self.last_box) / (frame - self.last)
        self.last, self.last_box = frame, box.copy()
        self.hits += 1
        # 同一轨迹可能出现多个类别，最终取各帧置信度累加后得分最高的类别。
        self.votes[label] += score
        improved = score > self.peak
        if improved:
            self.peak, self.peak_frame = score, frame
            self.peak_box = box.copy()
            self.peak_label = label
        # 项目计数规则：同一 ID 至少匹配 3 帧，首尾相隔至少 0.1 秒，且至少一帧达到 0.50。
        # 减小帧数或时间跨度会放行更短的轨迹，也更容易计入闪现的误检。三个条件必须同时满足。
        if self.hits >= 3 and (frame - self.first) / fps >= 0.1 and self.peak >= 0.50:
            self.confirmed = True
        if improved:
            self.confirmed_at_peak = self.confirmed

    @property
    def label(self):
        return self.votes.most_common(1)[0][0]


def overlap(a, b):
    intersection = np.maximum(0, np.minimum(a[2:], b[2:]) - np.maximum(a[:2], b[:2])).prod()
    return intersection / max(1., np.maximum(0, a[2:] - a[:2]).prod() + (b[2:] - b[:2]).prod() - intersection)


def possible_predecessors(current, previous, fps):
    """找出可能在本视频中断开过的前序轨迹 ID，写入 possible_duplicate_of。"""
    candidates = []
    for old in previous:
        gap = current.first - old.last
        # 两段相隔最多约 0.5 秒、框的 IoU 至少 0.35。下游发现前序也是确认事件时，会给当前事件独立建档。
        if 0 < gap <= round(0.5 * fps) and current.label == old.label:
            # 将新轨迹的首框分别与旧轨迹的末框、最多向前预测 3 帧的位置计算交并比。
            predicted = old.last_box + old.velocity * min(gap, 3)
            if max(overlap(predicted, current.start_box), overlap(old.last_box, current.start_box)) >= 0.35:
                candidates.append(old.key)
    return candidates


def draw(image, box, text, color):
    x1, y1, x2, y2 = box.astype(int)
    cv2.rectangle(image, (x1, y1), (x2, y2), color, 2)
    cv2.putText(image, text, (max(0, x1), max(22, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 4)
    cv2.putText(image, text, (max(0, x1), max(22, y1 - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)


def peak_bounds(box, size):
    # CSV 记录原视频像素坐标，右、下边界不包含在框内。
    x1, y1 = np.maximum(0, np.floor(box[:2])).astype(int)
    x2, y2 = np.minimum(size, np.ceil(box[2:])).astype(int)
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f'Empty peak detection box: {box}')
    return int(x1), int(y1), int(x2), int(y2)


def save_peak_crop(frame, box, path):
    # 四周各扩 10% 保留路面纹理。先在原始帧上裁剪，避免框线进入外观比对。
    height, width = frame.shape[:2]
    x1, y1, x2, y2 = peak_bounds(box, (width, height))
    pad_x = round((x2 - x1) * 0.10)
    pad_y = round((y2 - y1) * 0.10)
    crop = frame[max(0, y1 - pad_y):min(height, y2 + pad_y),
                 max(0, x1 - pad_x):min(width, x2 + pad_x)]
    if not cv2.imwrite(str(path), crop):
        raise OSError(f'Could not save peak crop: {path}')


def save_confirmed_images(source, events, out, diagnostics):
    """按帧号从原视频读回确认轨迹的峰值。同一帧只取一次。"""
    by_frame = defaultdict(list)
    for event in events:
        if event.confirmed:
            by_frame[event.peak_frame].append(event)
    if not by_frame:
        return

    (out / 'crops').mkdir(exist_ok=True)
    if diagnostics:
        (out / 'evidence').mkdir(exist_ok=True)
    cap = cv2.VideoCapture(str(source))
    try:
        for index in range(max(by_frame) + 1):
            if not cap.grab():
                raise OSError(f'Cannot read frame {index} for confirmed tracks: {source}')
            if index not in by_frame:
                continue
            ok, frame = cap.retrieve()
            if not ok:
                raise OSError(f'Cannot decode peak frame {index}: {source}')
            for event in by_frame[index]:
                filename = f'{event.key}.jpg'
                save_peak_crop(frame, event.peak_box, out / 'crops' / filename)
                if not diagnostics:
                    continue
                snapshot = frame.copy()
                caption = f'{event.key} {event.peak_label} {event.peak:.2f}'
                color = (80, 230, 90) if event.confirmed_at_peak else (20, 200, 255)
                draw(snapshot, event.peak_box, caption, color)
                if not cv2.imwrite(str(out / 'evidence' / filename), snapshot):
                    raise OSError(f'Could not save evidence: {filename}')
    finally:
        cap.release()


def write_event_table(path, source, events, fps, size, *, diagnostics, confirmed):
    header = ['video', 'event_id', 'track_id', 'class', 'status', 'start_s', 'end_s', 'matched_frames',
              'peak_conf', 'peak_s', 'evidence', 'possible_duplicate_of',
              'crop', 'peak_x1', 'peak_y1', 'peak_x2', 'peak_y2']
    pairs = 0
    with path.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.writer(stream)
        writer.writerow(header)
        for i, event in enumerate(events):
            if event.confirmed != confirmed:
                continue
            candidates = possible_predecessors(event, events[:i], fps)
            pairs += len(candidates)
            writer.writerow([source.name, event.key, event.track_id, event.label,
                             'confirmed' if confirmed else 'review', round(event.first/fps, 3),
                             round(event.last/fps, 3), event.hits, round(event.peak, 4), round(event.peak_frame/fps, 3),
                             f'evidence/{event.key}.jpg' if confirmed and diagnostics else '', '|'.join(candidates),
                             f'crops/{event.key}.jpg' if confirmed else '', *peak_bounds(event.peak_box, size)])
    return pairs


def run(source, weights, out, device, *, diagnostics=False):
    """用 YOLO.predict 检测每帧，交给 BYTETracker.update 分配 ID，再按轨迹汇总到 out。

    events.csv 每条确认轨迹一行，crops/ 存放峰值局部图。
    事件秒数由从零开始的帧号除以视频 FPS 得到，变帧率视频与 GPS 对时可能出现偏差。
    排查检测效果时，diagnostics=True 可额外保存诊断表、带框全图、标注视频和汇总。
    """
    settings_dir = Path(__file__).resolve().parents[1] / '.tools' / 'ultralytics'
    os.environ.setdefault('YOLO_CONFIG_DIR', str(settings_dir))
    os.environ.setdefault('YOLO_OFFLINE', 'true')
    from ultralytics import YOLO
    from ultralytics.trackers.byte_tracker import BYTETracker

    torch.set_num_threads(min(4, os.cpu_count() or 1))
    model = YOLO(str(weights))
    cap = cv2.VideoCapture(str(source))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not cap.isOpened() or not np.isfinite(fps) or fps <= 0:
        raise ValueError(f'Cannot read video or frame rate: {source}')
    count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    size = tuple(int(cap.get(p)) for p in (cv2.CAP_PROP_FRAME_WIDTH, cv2.CAP_PROP_FRAME_HEIGHT))
    # Ultralytics 的 ByteTrack 负责跨帧分配 ID。高分框可新建轨迹，低分框可帮助延续已有轨迹。
    # track_buffer 以帧为单位，按视频帧率给暂时丢失的轨迹保留约半秒。
    # 调高 high/new 阈值会使高分匹配/建轨更严格；match_thresh 是匹配代价上限，调高会放宽第一阶段关联。
    config = dict(track_high_thresh=0.50, new_track_thresh=0.50, track_low_thresh=0.10,
                  match_thresh=0.8, fuse_score=True, track_buffer=max(1, round(fps * 0.5)))
    tracker = BYTETracker(SimpleNamespace(**config))
    writer = None
    events = {}
    high_boxes = confirmed = index = 0
    try:
        if diagnostics:
            writer = cv2.VideoWriter(str(out / 'tracked.mp4'), cv2.VideoWriter_fourcc(*'mp4v'), fps, size)
            if not writer.isOpened():
                raise RuntimeError('Cannot write annotated video')
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            # 单帧检测调参：imgsz 是推理尺寸（改时同步更新 summary 的 imgsz）；conf 是送入跟踪器的最低分，
            # 应兼顾 track_low_thresh，以保留续轨用的低分框。iou 是单帧 NMS 阈值，调低会更积极地抑制重叠框。
            boxes = model.predict(frame, imgsz=640, conf=0.10, iou=0.7, device=device, verbose=False)[0].boxes.cpu().numpy()
            tracker.update(boxes, frame)
            # t.idx 是当前帧的检测框序号。这里也取刚建立的轨迹，以便保留只出现一帧的高分检测。
            ids = {int(t.idx): int(t.track_id) for t in tracker.tracked_stracks if t.frame_id == tracker.frame_id}
            annotated = frame.copy() if writer is not None else None
            for idx, (box, score, cls) in enumerate(zip(boxes.xyxy, boxes.conf, boxes.cls)):
                score = float(score)
                tid = ids.get(idx)
                # 无跟踪 ID 的检测只用于诊断。
                if tid is None and (not diagnostics or score < 0.50):
                    continue
                high_boxes += int(score >= 0.50)
                # T 是跟踪器给的 ID。高分框若没有 ID，用帧号和框序号生成单帧诊断记录 U。
                key = f'T{tid}' if tid is not None else f'U{index}-{idx}'
                if key not in events:
                    events[key] = Event(key, tid, index, box.copy())
                event = events[key]
                was_confirmed = event.confirmed
                label = model.names[int(cls)]
                event.observe(index, box, label, score, fps)
                # 只在轨迹首次达到确认条件时加一。匹配上的低分框也更新轨迹，高分框才画进视频。
                confirmed += int(event.confirmed and not was_confirmed)
                if annotated is None or score < 0.50:
                    continue
                caption = f'{key} {label} {score:.2f}'
                color = (80, 230, 90) if event.confirmed else (20, 200, 255)
                draw(annotated, box, caption, color)
            if writer is not None:
                cv2.putText(annotated, f'{index/fps:.1f}s | confirmed IDs: {confirmed}', (12, 32), 0, 0.85, (0, 0, 0), 5)
                cv2.putText(annotated, f'{index/fps:.1f}s | confirmed IDs: {confirmed}', (12, 32), 0, 0.85, (255, 255, 255), 2)
                writer.write(annotated)
            index += 1
            if index % 300 == 0:
                print(f'{index}/{count} frames, {confirmed} confirmed IDs', flush=True)
    finally:
        cap.release()
        if writer is not None:
            writer.release()
    if not index or (count > 0 and index != count):
        raise RuntimeError(f'Incomplete input decode: {index} of {count}')
    ordered = sorted(events.values(), key=lambda event: event.first)
    save_confirmed_images(source, ordered, out, diagnostics)
    table_events = ordered if diagnostics else [event for event in ordered if event.confirmed]
    pairs = write_event_table(out / 'events.csv', source, table_events, fps, size,
                              diagnostics=diagnostics, confirmed=True)
    if diagnostics:
        pairs += write_event_table(out / 'review.csv', source, ordered, fps, size,
                                   diagnostics=True, confirmed=False)
        summary = dict(video=str(source), model=str(weights), fps=fps, frames=index, imgsz=640, tracker=config,
                       high_conf_boxes=high_boxes, confirmed_ids=confirmed, review_ids=len(events)-confirmed,
                       possible_duplicate_pairs=pairs)
        (out / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(json.dumps(summary), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--model', required=True, type=Path)
    parser.add_argument('--output', type=Path, default=Path('output/bytetrack'))
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--diagnostics', action='store_true',
                        help='also save the annotated video, evidence images, and diagnostic reports')
    args = parser.parse_args()
    for path in (args.model, args.source):
        if not path.is_file():
            parser.error(f'File not found: {path}')
    args.output.mkdir(exist_ok=True, parents=True)
    directory = Path(mkdtemp(prefix=args.source.stem + '-', dir=args.output)).resolve()
    print(directory, flush=True)
    run(args.source.resolve(), args.model.resolve(), directory, args.device, diagnostics=args.diagnostics)
