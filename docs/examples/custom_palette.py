"""Create a grayscale palette for TC_EXTRA_CMAP_FOLDER."""

import argparse
from pathlib import Path

import numpy as np


def create(folder):
    folder.mkdir(parents=True, exist_ok=True)
    ramp = np.arange(255, dtype=np.uint8)
    colors = np.column_stack([ramp, ramp, ramp, np.full(255, 255, dtype=np.uint8)])
    np.save(folder / "graycustom_rgba.npy", colors)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    create(parser.parse_args().folder)
