"""Verify the offline OCR data before packaging the desktop backend."""
import hashlib
import json
from pathlib import Path


def main():
    resources = Path(__file__).resolve().parents[1] / 'backend/resources/ocr'
    manifest = json.loads((resources / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['license'] != 'Apache-2.0' or not (resources / 'LICENSE').is_file():
        raise RuntimeError('OCR data license is missing.')
    if set(manifest['files']) != {'eng.traineddata', 'kor.traineddata'}:
        raise RuntimeError('English and Korean OCR data must both be bundled.')
    for filename, expected in manifest['files'].items():
        path = resources / 'tessdata' / filename
        if not path.is_file() or path.stat().st_size != expected['size']:
            raise RuntimeError(f'OCR data missing or incomplete: {filename}')
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected['sha256']:
            raise RuntimeError(f'OCR data checksum mismatch: {filename}')
    print('Verified bundled English and Korean OCR data.')


if __name__ == '__main__':
    main()
