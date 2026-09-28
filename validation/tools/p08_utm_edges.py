"""Check exact GDAL fallback at UTM overview, magnification, and nodata edges."""
import argparse
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image
import p08_runtime
from p08_runtime import Service, fetch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--rust-binary', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    p08_runtime.BINARY = args.rust_binary.resolve()
    args.out.mkdir(parents=True, exist_ok=False)
    red = '?stretch_range=[0,3000]'
    paths = {
        'overview_zoom12': '/singleband/scene/red/12/2190/1458.png' + red,
        'magnification_zoom14': '/singleband/scene/red/14/8762/5834.png' + red,
        'east_edge_3pct': '/singleband/scene/red/13/4391/2930.png' + red,
        'south_edge_29pct': '/singleband/scene/red/13/4375/2946.png' + red,
        'transparent_edge': '/singleband/scene/red/13/4359/2930.png' + red,
        'rgb_east_edge': '/rgb/scene/13/4391/2930.png?r=red&g=green&b=blue&r_range=%5B0%2C3000%5D&g_range=%5B0%2C3000%5D&b_range=%5B0%2C3000%5D',
    }
    rgba = {}
    for engine in ('python', 'rust'):
        extra = {'TC_RUST_UTM_FAST': '1', 'TC_SOURCE_BLOCK_CACHE_BYTES': str(112 * 1024**2)} if engine == 'rust' else {}
        with Service(args.database, args.out / engine, engine, extra=extra) as service:
            for label, path in paths.items():
                status, body = fetch(service.base, path)
                assert status == 200, (engine, label, status)
                with Image.open(io.BytesIO(body)) as image:
                    rgba[engine, label] = np.asarray(image.convert('RGBA')).copy()
    rows = []
    for label in paths:
        a, b = rgba['python', label], rgba['rust', label]
        exact = bool(np.array_equal(a, b))
        coverage = float(np.mean(a[:, :, 3] > 0))
        assert exact, (label, coverage)
        rows.append(dict(label=label, path=paths[label], exact_rgba=exact, opaque_fraction=coverage))
    assert 0 < rows[2]['opaque_fraction'] < .1
    assert .2 < rows[3]['opaque_fraction'] < .4
    assert rows[4]['opaque_fraction'] == 0
    assert 0 < rows[5]['opaque_fraction'] < .1
    report = dict(passed=True, binary=str(args.rust_binary.resolve()), cases=rows)
    (args.out / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    print('Exact GDAL fallback: 6/6 overview, magnification and source-edge tiles')


if __name__ == '__main__':
    main()
