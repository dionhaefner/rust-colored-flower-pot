"""Qualify the pinned original-UTM interior map navigation experiment.

This is a corpus- and operation-specific visual rule, not a global pixel tolerance.
The ordinary-scale image review is recorded separately in the evidence README.
"""
import argparse
import json
from pathlib import Path
from statistics import median


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    pixels = report['pixel_comparison']
    expected = {(users, mode, engine, trial) for users in (1, 4, 8)
                for mode in ('singleband', 'rgb') for engine in ('python', 'rust')
                for trial in range(3)}
    actual = {(row['users'], row['mode'], row['engine'], row['trial'])
              for row in report['runs']}
    assert actual == expected and len(report['runs']) == len(expected)
    assert len(report['decoded_image_hashes']) == 412
    assert len(pixels) == 412
    assert all(row['failures'] == 0 for row in report['runs'])
    assert all(value >= .95 for value in report['coverage'].values())
    assert all(item['alpha_changed_pixels'] == 0 for item in pixels.values())
    # Established from the pinned zoom-13 UTM Sentinel-2 sample, including its
    # sharpest RGB outlier. Applicable only to this four-sample UTM interior path.
    assert all(item['rgb_mean_absolute'] <= .61 and
               item['rgb_p99_absolute'] <= 4 and
               item['rgb_max_absolute'] <= 68 for item in pixels.values())
    groups = []
    for users in (1, 4, 8):
        for mode in ('singleband', 'rgb'):
            pairs = []
            for trial in range(3):
                rows = {row['engine']: row for row in report['runs']
                        if row['users'] == users and row['mode'] == mode and row['trial'] == trial}
                p, r = rows['python'], rows['rust']
                pairs.append(dict(trial=trial,
                                  python_pan_p50_ms=p['pan']['p50_ms'],
                                  rust_pan_p50_ms=r['pan']['p50_ms'],
                                  speedup=p['pan']['p50_ms']/r['pan']['p50_ms']))
            python = [pair['python_pan_p50_ms'] for pair in pairs]
            rust = [pair['rust_pan_p50_ms'] for pair in pairs]
            assert all(pair['speedup'] >= 10 for pair in pairs)
            groups.append(dict(users=users, mode=mode,
                               python_pan_p50_median_ms=median(python),
                               rust_pan_p50_median_ms=median(rust),
                               median_speedup=median(python)/median(rust),
                               minimum_trial_speedup=min(pair['speedup'] for pair in pairs),
                               pairs=pairs,
                               python_first_view_p50_median_ms=median(
                                   row['first_view']['p50_ms'] for row in report['runs']
                                   if row['users'] == users and row['mode'] == mode and row['engine'] == 'python'),
                               rust_first_view_p50_median_ms=median(
                                   row['first_view']['p50_ms'] for row in report['runs']
                                   if row['users'] == users and row['mode'] == mode and row['engine'] == 'rust'),
                               python_pan_p95_median_ms=median(
                                   row['pan']['p95_ms'] for row in report['runs']
                                   if row['users'] == users and row['mode'] == mode and row['engine'] == 'python'),
                               rust_pan_p95_median_ms=median(
                                   row['pan']['p95_ms'] for row in report['runs']
                                   if row['users'] == users and row['mode'] == mode and row['engine'] == 'rust')))
    summary = dict(qualified=True, qualification='pinned original UTM COG zoom-13 near-native opaque tiles',
                   strict_byte_exact=report['passed'],
                   native_sha256=report['native_sha256'],
                   source_sha256={source['path']: source['sha256'] for source in report['sources']},
                   distinct_urls=len(pixels), image_mismatches=len(report['mismatch_paths']),
                   alpha_changed_pixels=max(item['alpha_changed_pixels'] for item in pixels.values()),
                   maximum_image_rgb_mean_absolute=max(item['rgb_mean_absolute'] for item in pixels.values()),
                   maximum_image_rgb_p99_absolute=max(item['rgb_p99_absolute'] for item in pixels.values()),
                   maximum_channel_difference=max(item['rgb_max_absolute'] for item in pixels.values()),
                   groups=groups, conditions=report['conditions'])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=2) + '\n')
    print('Qualified original UTM pan: all 18 paired cells >=10x, 412 masks exact, bounded visual differences')


if __name__ == '__main__':
    main()
