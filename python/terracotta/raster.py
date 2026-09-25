"""Public NumPy boundary. Raster I/O and statistics run in the Rust/GDAL core."""
import threading
import numpy as np
from terracotta._bridge import Session, metadata, operation, settings
from terracotta.profile import trace

_LOCAL = threading.local()
_DTYPES = {1: 'uint8', 2: 'uint16', 3: 'int16', 4: 'uint32', 5: 'int32', 6: 'float32', 7: 'float64', 12: 'uint64', 13: 'int64', 14: 'int8'}

@trace("get_raster_tile")
def get_raster_tile(path, *, tile_bounds=None, tile_size=(256, 256), preserve_values=False,
                    reprojection_method='nearest', resampling_method='nearest', target_crs='epsg:3857',
                    rio_env_options=None, aws_s3_config=None):
    config = settings()
    config["TARGET_CRS"] = target_crs
    config["RASTER_WORKERS"] = 1
    config["GDAL_OPTIONS"] = rio_env_options or {}
    if aws_s3_config:
        config.update(RASTER_AWS_ACCESS_KEY=aws_s3_config.get('aws_access_key_id'),
                      RASTER_AWS_SECRET_KEY=aws_s3_config.get('aws_secret_access_key'),
                      RASTER_AWS_S3_ENDPOINT=aws_s3_config.get('endpoint_url'))
    config.update(REPROJECTION_METHOD=reprojection_method, RESAMPLING_METHOD=resampling_method)
    key = repr(config)
    if getattr(_LOCAL, 'key', None) != key:
        _LOCAL.session = Session('raster', config=config)
        _LOCAL.key = key
    result = _LOCAL.session.call(path=str(path), tile_bounds=tile_bounds, tile_size=list(tile_size), preserve_values=preserve_values)
    values = np.asarray(result['values'], dtype='float64').astype(_DTYPES[result['dtype']]).reshape(result['shape'])
    mask = np.asarray(result['mask'], dtype=bool).reshape(result['shape'])
    return np.ma.masked_array(values, mask=mask)

@trace("compute_metadata")
def compute_metadata(path, *, extra_metadata=None, use_chunks=None, max_shape=None,
                     large_raster_threshold=None, rio_env_options=None):
    import warnings
    import rasterio
    from terracotta import exceptions
    from terracotta.cog import validate
    if max_shape is not None and len(max_shape) != 2:
        raise ValueError('max_shape argument must contain 2 values')
    with rasterio.Env(**(rio_env_options or {})):
        if not validate(str(path)):
            warnings.warn(f'Raster file {path} is not a valid cloud-optimized GeoTIFF. Consider optimizing it through `terracotta optimize-rasters` before ingestion.', exceptions.PerformanceWarning, stacklevel=2)
        with rasterio.open(str(path)) as src:
            if src.nodata is None and not has_alpha_band(src):
                warnings.warn(f'Raster file {path} does not have a valid nodata value, and does not contain an alpha band. No data will be masked.', stacklevel=2)
    if use_chunks and not has_crick:
        warnings.warn('Processing a large raster file, but crick failed to import. Reading whole file into memory instead.', exceptions.PerformanceWarning)
        use_chunks = False
    result = metadata(operation(operation='compute_metadata', path=str(path), options=dict(
        extra_metadata=extra_metadata, use_chunks=use_chunks, max_shape=max_shape,
        large_raster_threshold=large_raster_threshold if large_raster_threshold is not None else 2**63-1)))

    result["percentiles"] = np.asarray(result["percentiles"], dtype="float64")
    return result


# The native digest is always available; this public compatibility flag can still
# request the historical full-array fallback.
has_crick = True


def compute_image_stats(dataset, max_shape=None):
    return _image_stats(dataset, False, max_shape)


def compute_image_stats_chunked(dataset):
    return _image_stats(dataset, True, None)


def _image_stats(dataset, chunked, max_shape):
    try:
        result = metadata(operation(operation='compute_metadata', path=dataset.name,
                          options=dict(use_chunks=chunked, max_shape=max_shape)))
    except ValueError as error:
        if 'does not contain any valid data' in str(error):
            return None
        raise
    for key in ('bounds', 'metadata'):
        result.pop(key, None)
    result['percentiles'] = np.asarray(result['percentiles'])
    return result

from typing import Any
import functools

@functools.cache
def get_aws_session(
    aws_access_key_id: str, aws_secret_access_key: str, endpoint_url: str
) -> Any:
    import boto3
    from rasterio.session import AWSSession

    aws_session = AWSSession(
        boto3.Session(
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        ),
        endpoint_url=endpoint_url,
    )
    return aws_session

def convex_hull_candidate_mask(mask: np.ndarray) -> np.ndarray:
    """Returns a reduced boolean mask to speed up convex hull computations.

    Exploits the fact that only the first and last elements of each row and column
    can contribute to the convex hull of a dataset.
    """
    assert mask.ndim == 2
    assert mask.dtype == np.bool_

    nx, ny = mask.shape
    out = np.zeros_like(mask)

    # these operations do not short-circuit, but seems to be the best we can do
    # NOTE: argmax returns 0 if a slice is all True or all False
    first_row = np.argmax(mask, axis=0)
    last_row = nx - 1 - np.argmax(mask[::-1, :], axis=0)
    first_col = np.argmax(mask, axis=1)
    last_col = ny - 1 - np.argmax(mask[:, ::-1], axis=1)

    all_rows = np.arange(nx)
    all_cols = np.arange(ny)

    out[first_row, all_cols] = out[last_row, all_cols] = True
    out[all_rows, first_col] = out[all_rows, last_col] = True

    # filter all-False slices
    out &= mask

    return out

def get_resampling_enum(method: str) -> Any:
    from rasterio.enums import Resampling

    if method == "nearest":
        return Resampling.nearest

    if method == "linear":
        return Resampling.bilinear

    if method == "cubic":
        return Resampling.cubic

    if method == "average":
        return Resampling.average

    raise ValueError(f"unknown resampling method {method}")

def has_alpha_band(src: "DatasetReader") -> bool:
    from rasterio.enums import MaskFlags, ColorInterp

    return (
        any([MaskFlags.alpha in flags for flags in src.mask_flag_enums])
        or ColorInterp.alpha in src.colorinterp
    )
