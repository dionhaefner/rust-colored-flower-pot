"""Explicit, bounded, atomic public asset acquisition; cache verification is offline."""
import argparse
import datetime
import shutil
import urllib.request
from common import ROOT, CACHE, read_json, write_json, sha256


def acquire(args):
    manifest = read_json(ROOT / 'process/validation/reference/assets.json')
    CACHE.mkdir(parents=True, exist_ok=True)
    pending = [a for a in manifest['assets'] if not (CACHE / (a['id'] + '.tif')).exists()]
    expected = sum(a['bytes'] for a in pending)
    print(f'Expected transfer: {expected:,} bytes; cache: {CACHE}', flush=True)
    if expected > args.max_bytes:
        raise ValueError(f'transfer exceeds --max-bytes={args.max_bytes}')
    if shutil.disk_usage(CACHE).free < expected + 256 * 1024**2:
        raise ValueError('insufficient free space including 256 MiB reserve')
    records = []
    for asset in manifest['assets']:
        target = CACHE / (asset['id'] + '.tif')
        if not target.exists():
            if args.offline:
                raise FileNotFoundError(f'{target}; run acquire explicitly without --offline')
            request = urllib.request.Request(asset['url'], headers={'If-Match': asset['etag']})
            temporary = target.with_suffix('.tif.part')
            # Failed transfers stay .part; a retry starts again and never publishes partial data.
            with urllib.request.urlopen(request, timeout=120) as response:
                if response.headers.get('ETag') != asset['etag']:
                    raise ValueError(f'changed ETag: {asset["id"]}')
                if int(response.headers['Content-Length']) != asset['bytes']:
                    raise ValueError(f'changed length: {asset["id"]}')
                count = 0
                with temporary.open('wb') as out:
                    while chunk := response.read(1024 * 1024):
                        count += len(chunk)
                        if count > asset['bytes']:
                            raise ValueError('response exceeds pinned length')
                        out.write(chunk)
            if temporary.stat().st_size != asset['bytes']:
                raise ValueError(f'truncated transfer: {asset["id"]}')
            if asset.get('sha256') and sha256(temporary) != asset['sha256']:
                raise ValueError(f'checksum mismatch: {asset["id"]}')
            temporary.replace(target)
        digest = sha256(target)
        if target.stat().st_size != asset['bytes'] or (asset.get('sha256') and digest != asset['sha256']):
            raise ValueError(f'invalid cached asset: {target}')
        records.append(dict(id=asset['id'], bytes=target.stat().st_size, sha256=digest))
        print(f'Verified {asset["id"]}: {digest}', flush=True)
    write_json(args.report, {'verified_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'assets': records})


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--offline', action='store_true')
    p.add_argument('--max-bytes', type=int, default=600 * 1024**2)
    p.add_argument('--report', default=str(ROOT / 'process/validation/artifacts/acquisition.json'))
    acquire(p.parse_args())
