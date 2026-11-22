"""Build placeholder map data from a GPX track so the map page can be developed before
detection and dedup are ready. The defects are FAKE: they are placed on real track points
with made-up type and severity, and every record carries "demo": true.

Usage:
    python make_demo_data.py path/to/track.gpx [--count 8]
Writes defects.json and track.json next to this script.
"""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from coords import wgs84_to_gcj02

TYPES = ['D00', 'D10', 'D20', 'D40']
SEVERITIES = ['轻', '中', '重']


def read_track(path):
    points = []
    for el in ET.parse(path).iter():
        if el.tag.rsplit('}', 1)[-1] != 'trkpt':
            continue
        time = next((c.text for c in el if c.tag.rsplit('}', 1)[-1] == 'time'), None)
        points.append({'lng': float(el.get('lon')), 'lat': float(el.get('lat')), 'time': time})
    if not points:
        raise SystemExit(f'No trkpt found in {path}')
    return points


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('gpx')
    parser.add_argument('--count', type=int, default=8)
    args = parser.parse_args()
    out = Path(__file__).parent
    track = read_track(args.gpx)

    line = [[round(v, 7) for v in wgs84_to_gcj02(p['lng'], p['lat'])] for p in track]
    (out / 'track.json').write_text(json.dumps(line), encoding='utf-8')

    count = min(args.count, len(track))
    defects = []
    for i in range(count):
        p = track[(i * (len(track) - 1)) // max(count - 1, 1)]
        lng, lat = wgs84_to_gcj02(p['lng'], p['lat'])
        defects.append({
            'id': f'DEMO-{i + 1:04d}',
            'demo': True,
            'type': TYPES[i % len(TYPES)],
            'severity': SEVERITIES[(i * 2) % len(SEVERITIES)],
            'lng_wgs': p['lng'], 'lat_wgs': p['lat'],
            'lng_gcj': round(lng, 7), 'lat_gcj': round(lat, 7),
            'first_seen': p['time'], 'last_seen': p['time'],
            'obs_count': 1,
            'status': '待复核',
            'image_url': None,
        })
    (out / 'defects.json').write_text(json.dumps(defects, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Wrote {len(defects)} demo defects and {len(line)} track points to {out}')


if __name__ == '__main__':
    main()
