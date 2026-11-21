"""Download, verify and extract the official complete RDD2022 archive."""
import hashlib
import json
import shutil
import time
import zipfile
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DOWNLOADS = ROOT / 'data/downloads'
META = json.loads((DOWNLOADS / 'figshare-metadata.json').read_text(encoding='utf-8-sig'))
FILE = next(f for f in META['files'] if f['id'] == 38030910)
ARCHIVE = DOWNLOADS / FILE['name']
PART = ARCHIVE.with_suffix('.zip.part')


def digest(path):
    result = hashlib.md5()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024**2), b''):
            result.update(block)
    return result.hexdigest()


def main():
    if not ARCHIVE.exists():
        for attempt in range(10):
            offset = PART.stat().st_size if PART.exists() else 0
            if offset == FILE['size']:
                break
            if shutil.disk_usage(ROOT).free < FILE['size'] - offset + 1024**3:
                raise RuntimeError('Insufficient space to finish download')
            try:
                # A unique query prevents reuse of an earlier cached range response.
                url = FILE['download_url'] + f'?download=1&resume={offset}&t={time.time_ns()}'
                headers = {'Range': f'bytes={offset}-'} if offset else {}
                with requests.get(url, headers=headers, stream=True, timeout=(30, 120)) as response:
                    response.raise_for_status()
                    if offset and (response.status_code != 206 or not response.headers.get('Content-Range', '').startswith(f'bytes {offset}-')):
                        raise RuntimeError('Server did not honor resume range; existing partial preserved')
                    if not offset and response.status_code != 200:
                        raise RuntimeError('Expected full archive response')
                    last = time.monotonic()
                    with PART.open('ab') as stream:
                        for block in response.iter_content(4 * 1024**2):
                            stream.write(block)
                            offset += len(block)
                            if offset > FILE['size']:
                                raise RuntimeError('Archive exceeds expected size')
                            if time.monotonic() - last > 20:
                                print(f'Download: {offset/1024**3:.2f}/{FILE["size"]/1024**3:.2f} GiB ({100*offset/FILE["size"]:.1f}%)', flush=True)
                                last = time.monotonic()
                if offset != FILE['size']:
                    raise RuntimeError('Incomplete response')
                break
            except (requests.RequestException, OSError) as error:
                print(f'Retry {attempt+1}: {error}', flush=True)
                if attempt == 9:
                    raise
                time.sleep(3)
        if PART.stat().st_size != FILE['size']:
            raise RuntimeError('Incomplete archive')
        print('Checking official MD5...', flush=True)
        if digest(PART) != FILE['computed_md5']:
            raise RuntimeError('MD5 mismatch; partial archive retained for inspection')
        PART.rename(ARCHIVE)
    else:
        if ARCHIVE.stat().st_size != FILE['size'] or digest(ARCHIVE) != FILE['computed_md5']:
            raise RuntimeError('Existing archive failed verification')
    print('MD5 verified', flush=True)
    destination = ROOT / 'data/raw/rdd2022'
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(ARCHIVE) as archive:
        entries = archive.infolist()
        required = sum(f.file_size for f in entries)
        print(f'Extraction size: {required/1024**3:.2f} GiB', flush=True)
        if shutil.disk_usage(ROOT).free < required + 1024**3:
            raise RuntimeError('Archive verified but insufficient free space for extraction')
        for i, entry in enumerate(entries):
            target = (destination / entry.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise RuntimeError('Unsafe archive path')
            archive.extract(entry, destination)
            if i % 5000 == 0:
                print(f'Extracted {i}/{len(entries)} entries', flush=True)
        from collections import Counter
        summary = Counter()
        for entry in entries:
            if not entry.is_dir():
                summary[Path(entry.filename).suffix.lower()] += 1
        report = {'archive': str(ARCHIVE), 'destination': str(destination), 'bytes': FILE['size'], 'md5': FILE['computed_md5'], 'source': FILE['download_url'], 'doi': META['doi'], 'license': META['license'], 'uncompressed_bytes': required, 'files_by_extension': dict(summary), 'integrity': 'Official archive MD5 and extracted member CRC verified'}
        (DOWNLOADS / 'full-manifest.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2), flush=True)


if __name__ == '__main__':
    main()
