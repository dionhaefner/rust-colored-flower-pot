"""STAC Item consumption and bounded STAC API Item Search in the native core."""

from __future__ import annotations

import copy
import json

from . import RcfpError, _native
from ._profile import settings


def _credentials(callback):
    if callback is None:
        return {}
    try:
        return dict(callback())
    except Exception:  # noqa: BLE001 - callback exceptions can include credentials
        raise RcfpError("STAC credential callback failed") from None


class Selection:
    """An immutable, checksum-pinned selection with private native asset locations.

    Use ``from_items`` or ``SearchClient.search`` to construct a selection.
    ``signer(asset_descriptor)`` supplies a fresh private location when rendering.
    """

    def __init__(self, request, *, signer=None):
        try:
            self._handle = _native.session_open(
                json.dumps(dict(request, kind="stac", settings=settings()))
            )
        except RuntimeError as error:
            raise RcfpError(str(error)) from None
        self._signer = signer
        self._description = self._call(operation="describe")

    @classmethod
    def from_items(
        cls,
        items,
        *,
        assets,
        revisions=None,
        base_url=None,
        max_items=1000,
        max_staged_bytes=512 * 1024 * 1024,
        signer=None,
    ):
        """Consume STAC 1.0/1.1 Items without inserting a SQL catalog.

        ``revisions`` maps ``collection/item/asset`` to hexadecimal SHA-256 when
        the asset omits a SHA-256 multihash ``file:checksum``. ``base_url`` resolves
        relative links. Only explicitly requested asset keys are consumed.
        """
        return cls(
            {
                "items": list(items),
                "options": {
                    "assets": list(assets),
                    "revisions": revisions or {},
                    "base_url": base_url,
                    "max_items": max_items,
                    "max_staged_bytes": max_staged_bytes,
                },
            },
            signer=signer,
        )

    def _call(self, **request):
        if self._handle is None:
            raise RcfpError("STAC selection is closed")
        result = json.loads(
            _native.session_operation(self._handle, json.dumps(request))
        )
        if "error" in result:
            raise RcfpError(result["error"])
        return result["ok"]

    @property
    def metadata(self):
        """A copy of public revision/band/footprint/time metadata, without URLs."""
        return copy.deepcopy(self._description)

    @property
    def id(self):
        return self._description["id"]

    def render(self, asset_index, *, bounds_mercator, range, band=1, size=(256, 256)):
        """Render one selected asset/band as viridis PNG on an explicit map grid.

        ``range`` is in physical units after STAC scale/offset. ``size`` is
        (height, width). No search runs during rendering. Verified local assets
        are reused within this selection; a new revision owns separate workers
        and staging files. Closing releases those resources.
        """
        if not isinstance(asset_index, int) or not 0 <= asset_index < len(
            self._description["assets"]
        ):
            raise RcfpError("STAC asset_index out of range")
        request = {
            "operation": "render",
            "asset_index": asset_index,
            "band": band,
            "bounds_mercator": list(bounds_mercator),
            "range": list(range),
            "size": list(size),
        }
        for attempt in (0, 1):
            if self._signer is not None:
                try:
                    href = self._signer(
                        copy.deepcopy(self._description["assets"][asset_index])
                    )
                    if not isinstance(href, str):
                        raise TypeError
                except Exception:  # noqa: BLE001 - callback exceptions can include signed URLs
                    raise RcfpError("STAC signer failed") from None
                request["signed_href"] = href
            try:
                return bytes(self._call(**request))
            except RcfpError as error:
                if (
                    attempt
                    or self._signer is None
                    or str(error)
                    not in ("STAC upstream HTTP 401", "STAC upstream HTTP 403")
                ):
                    raise

    def close(self):
        """Drain workers and remove this selection's private staged files."""
        self._handle = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class SearchClient:
    """Adapter to a POST STAC API Item Search endpoint, including PgSTAC APIs.

    ``credentials()`` supplies Authorization and/or X-API-Key headers for each
    search. A 401/403 restarts the bounded search once with refreshed credentials.
    Redirects and pagination outside the provider origin are rejected.
    """

    def __init__(self, endpoint, *, credentials=None, signer=None):
        self._endpoint = endpoint
        self._credentials = credentials
        self._signer = signer

    def search(
        self,
        *,
        assets,
        bbox=None,
        datetime=None,
        collections=None,
        ids=None,
        page_size=100,
        max_items=1000,
        max_pages=100,
        max_staged_bytes=512 * 1024 * 1024,
        revisions=None,
    ):
        """Resolve all pages; limit/error failures never return a partial selection.

        Ordering is ascending UTC start time, collection, Item ID, asset key.
        Empty search results are valid. Spatial/time matching belongs to the
        provider's index; no catalog crawl or client-side spatial scan is used.
        """
        query = {"limit": page_size}
        for key, value in (
            ("bbox", bbox),
            ("datetime", datetime),
            ("collections", collections),
            ("ids", ids),
        ):
            if value is not None:
                query[key] = value
        options = {
            "assets": list(assets),
            "revisions": revisions or {},
            "max_items": max_items,
            "max_pages": max_pages,
            "max_staged_bytes": max_staged_bytes,
        }
        for attempt in (0, 1):
            try:
                return Selection(
                    {
                        "endpoint": self._endpoint,
                        "query": query,
                        "headers": _credentials(self._credentials),
                        "options": options,
                    },
                    signer=self._signer,
                )
            except RcfpError as error:
                if (
                    attempt
                    or self._credentials is None
                    or str(error)
                    not in ("STAC upstream HTTP 401", "STAC upstream HTTP 403")
                ):
                    raise
