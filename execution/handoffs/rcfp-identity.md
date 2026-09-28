> Historical handoff. The current RCFP package layout and corrected source boundary are in [this handoff](rcfp-source-boundary.md).

# RCFP Python package identity handoff

- **User direction:** The pip distribution and primary import must be `rcfp`, independent of the old Terracotta Python package.
- **Implemented:** Renamed Python source packages to `rcfp` and `rcfp_native`, distribution to `rcfp`, console entry to `rcfp`, public driver/settings/error types to RCFP names, and package-facing HTML/API titles. `rcfp.__version__` is 0.1.0; `rcfp.__protocol_version__` is 0.10.1 for existing database and HTTP compatibility. The wheel excludes the old Python package directories and separate native executable. Setuptools clears the project package directories in generated output before source builds.
- **Validation:** [Evidence](../../validation/evidence/p08/rcfp-identity/README.md) covers the final Linux/CPython 3.12 wheel, direct source install, staged wheel, sdist, fresh environment, CLI, real raster tile and Flask/WSGI.
- **Limits:** Existing Python scripts must change imports/command names; this is intentional. Other platforms, online isolated dependency resolution and the prior full legacy Python suite under the new namespace remain untested. Previous sealed P08 artifacts have the earlier package identity. Separate native Rust bundle names and retained browser client content remain historical.
