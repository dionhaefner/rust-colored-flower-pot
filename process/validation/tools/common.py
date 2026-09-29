"""Shared deterministic manifest helpers. No implicit network access."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
COMMIT = '6db38d5873cde5ebabe47338a1f61ddc788eada6'
CACHE = ROOT / '.cache/corpus'


def sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, allow_nan=False) + '\n')


def read_json(path):
    return json.loads(Path(path).read_text())
