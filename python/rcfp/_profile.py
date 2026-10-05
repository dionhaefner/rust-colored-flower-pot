"""Process compatibility selection, frozen before public APIs are initialized."""

import os

PROFILES = ("native", "terracotta-0.10.1")
PROFILE = os.environ.get("RCFP_COMPAT_PROFILE", "native")
if PROFILE not in PROFILES:
    raise ValueError(
        f"Unknown compatibility profile {PROFILE!r}; expected {' or '.join(PROFILES)}"
    )


def settings(values=None):
    values = dict(values or {})
    requested = values.get("COMPAT_PROFILE", PROFILE)
    if requested != PROFILE:
        raise ValueError(
            "Compatibility profile is frozen on import; start a new process to change it"
        )
    values["COMPAT_PROFILE"] = PROFILE
    return values


def require_compatibility():
    if PROFILE != "terracotta-0.10.1":
        raise RuntimeError(
            "This API requires RCFP_COMPAT_PROFILE=terracotta-0.10.1 before importing rcfp"
        )
