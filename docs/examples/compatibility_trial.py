"""Inspect a staging catalog through the Terracotta compatibility API."""

import argparse
import json

import rcfp as tc


def trial(database):
    if tc.compatibility_profile != "terracotta-0.10.1":
        raise RuntimeError(
            "Set RCFP_COMPAT_PROFILE=terracotta-0.10.1 before starting Python"
        )
    tc.update_settings(DRIVER_PATH=database, USE_MULTIPROCESSING=False)
    driver = tc.get_driver(database)
    with driver.connect():
        print(
            json.dumps(
                {
                    "keys": list(driver.get_keys()),
                    "datasets": [list(keys) for keys in driver.get_datasets()],
                }
            )
        )
    status, _, body, _ = tc.request(database, "GET", "/keys")
    if status != 200:
        raise RuntimeError(body.decode())
    return json.loads(body)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("database")
    trial(parser.parse_args().database)
