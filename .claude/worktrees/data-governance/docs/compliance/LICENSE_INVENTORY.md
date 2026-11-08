# Licence inventory (software dependencies)

Generated 2026-11-08 from what is **actually installed**. These are research notes, not legal advice. The
"commercial use" column is a recommendation pending human sign-off (see `COMMERCIAL_USE_APPROVAL.md`).

**Method.**
- **Python:** `importlib.metadata` was read in every Groundtruth venv in WSL (`~/.venvs/`):
  - `groundtruth`: 122 packages (CPU API env).
  - `groundtruth-gpu`: 137 packages.
  - `gt-routing`: 9 packages (Valhalla build).
  - `titiler`: 113 packages.
  - `gt-browser`: 4 packages (Playwright, dev only).
  - `oryn-gpu` is ORYN's and was not inventoried.
- **Licence field:** taken from `License-Expression`, falling back to `License ::` classifiers and then `License`.
- **Native libraries bundled inside wheels:** read from the `*.libs/` folders and the pyvalhalla auditwheel SBOM.
  Package metadata does **not** cover these, and they are where the copyleft sits.
- **npm:** `web/package-lock.json` (156 entries) was read with each installed `package.json`. `npm ls --all` reports
  only unmet optional peer deps of Vite (sass, stylus, sugarss, terser: not installed).
- **Laptop heavy env** (`scripts/heavy/requirements-heavy.txt`): its pins are the same packages and versions as the PC
  CPU env above, plus CPU torch 2.15.0 / torchvision 0.29.2. It was not inspected on the laptop itself.

## Summary of flags

| # | item | issue | recommended class |
|---|---|---|---|
| **F1** | **pyvalhalla 3.9.1** wheel (used in-process by `isochrone`; also `gt-routing` CLI tools) | The package is MIT, but `pyvalhalla.libs/` bundles copyleft and old libraries (listed below). | HUMAN/LEGAL REVIEW REQUIRED before any **distribution** of a build (container, installer, appliance). Hosted (server-side) use alone is not distribution under GPL-2.0, and none of these are AGPL. |
| **F2** | GEOS (LGPL-2.1) in shapely, rasterio, pyvalhalla wheels and the DuckDB spatial extension | Unmodified, dynamically loaded shared library. This is LICENCE_GATE D4. | APPROVED WITH RESTRICTIONS: do not modify or statically link; ship the LGPL text and allow relinking if distributed. Still needs owner sign-off on D4. |
| F3 | NVIDIA CUDA wheels (`nvidia-*-cu12`, `cuda-toolkit`, `cuda-bindings`) in `groundtruth-gpu` only | Proprietary NVIDIA EULA | APPROVED WITH RESTRICTIONS: use is fine; redistribution only as the NVIDIA EULA allows (relevant for Docker images). The CPU env has none. |
| F4 | GCC runtime (libgfortran, libquadmath, libgomp) in numpy, scipy, faiss-cpu, scikit-learn wheels | GPL-3.0 **with the GCC Runtime Library Exception** | APPROVED: the exception covers this use |
| F5 | MPL-2.0: certifi, tqdm (MPL-2.0 AND MIT) | File-level weak copyleft | APPROVED: unmodified use |
| F6 | Fonts `@fontsource/inter`, `@fontsource/ibm-plex-mono` | SIL OFL 1.1 | APPROVED: fonts may be bundled; do not sell the fonts on their own |
| F7 | `caniuse-lite` (CC-BY-4.0) | Data licence; build-time only, not shipped | APPROVED |
| F8 | Unknown licence metadata | Only `cuda-toolkit` (empty, NVIDIA EULA by origin). No other unknowns. | see F3 |
| F9 | Ultralytics / AGPL | **Not installed** in any env (checked) | – (stays forbidden) |
| F10 | Security: pyvalhalla bundles **OpenSSL 1.1.1k** (end of life) and **libcurl 7.61.1** in the API process | Not a licence issue; see `docs/SECURITY_AUDIT.md` | – |

### F1: what is inside `pyvalhalla.libs/` (manylinux_2_28 build)

**GPL-2.0-or-later.** These are the items that matter on distribution.
- `librttopo.so.1.1.0` (RT Topology Library). A dependency of spatialite.
- `libjbig.so.2.1` (jbigkit 2.1). Pulled in by libtiff.

**Tri-licensed MPL-1.1 / GPL-2.0+ / LGPL-2.1+.** Choose MPL or LGPL.
- `libspatialite.so.8.1.0`
- `libfreexl.so.1.2.0`

**LGPL.**
- `libgeos` 3.13.1: LGPL-2.1.
- `libssh` 0.9.6: LGPL-2.1.
- `libpgm` (openpgm 5.2.122): LGPL-2.1.
- `libzmq` 4.3.4: LGPL-3.0+ with a static-linking exception (relicensed to MPL-2.0 from 4.3.5).
- `libidn2` 2.2.0: LGPL-3.0+ or GPL-2.0+.
- `libunistring` 0.9.9: LGPL-3.0+ or GPL-2.0+.

**MPL-2.0.**
- `libczmq` 4.2.1.

**Permissive.** These have no copyleft obligations.
- prime_server: BSD-2.
- luajit, libxml2, expat, libgeotiff, PROJ 6.3.2, krb5, libpsl: MIT.
- OpenSSL 1.1.1k: OpenSSL/SSLeay.
- libcurl: curl.
- sqlite: public domain.
- lz4, zlib, xz, bzip2, brotli, libsodium (ISC), libbsd, libmd, libuuid, cyrus-sasl, openldap (OLDAP-2.8),
  libselinux (public domain), libunwind (MIT), pcre2 (BSD).

The licence labels come from upstream projects and AlmaLinux 8 package headers. They were not re-verified file by file.
The SBOM ships at `pyvalhalla-3.9.1.dist-info/sboms/auditwheel.cdx.json`.

**Options for F1.**
- (a) Keep it, and accept GPL obligations if a build is ever distributed.
- (b) Build Valhalla without spatialite. Spatialite is only needed for the admin/timezone databases, which this graph
  does not use (`valhalla.json` points to absent `/data/valhalla/admin.sqlite`). Such a build drops librttopo, freexl
  and spatialite, though jbig would still need a libtiff without JBIG.
- (c) Switch to OSRM (BSD-2), which is already PASS in LICENCE_GATE.

## Other software in use outside pip/npm

| component | version | licence | commercial | where |
|---|---|---|---|---|
| DuckDB extensions `spatial`, `httpfs` | matching duckdb 2.0.0 (`~/.duckdb/extensions`) | MIT. Spatial bundles GEOS (LGPL-2.1), GDAL (MIT), PROJ (MIT), sqlite (PD) | Yes (F2) | `engine/groundtruth/vector.py:35` |
| tippecanoe (Felt fork) | not version-checked | BSD-2-Clause | Yes | `scripts/build_pmtiles.py` (build time) |
| llama.cpp `llama-server` (laptop) | not version-checked | MIT | Yes | `scripts/heavy/llm_server.sh` |
| Node.js (fnm) | v26.13.0 | MIT (+ bundled deps) | Yes | build/dev only |
| Valhalla CLI tools (`gt-routing` venv) | 3.9.1 (pyvalhalla wheel) | MIT + F1 | F1 | `scripts/build_routing.py` |

## Python packages (all envs, de-duplicated by name + version)

| package | version | licence (from installed metadata) | envs | commercial use | notes | url |
|---|---|---|---|---|---|---|
| affine | 3.0.1 | BSD-3-Clause | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/rasterio/affine |
| annotated-doc | 0.0.5 | MIT | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/fastapi/annotated-doc |
| annotated-types | 0.8.0 | MIT | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/annotated-types/annotated-types |
| anyio | 4.16.0 | MIT | groundtruth, groundtruth-gpu, titiler | Yes |  | https://anyio.readthedocs.io/en/latest/ |
| asgiref | 3.13.0 | BSD-3-Clause | titiler | Yes |  | https://github.com/django/asgiref/ |
| attrs | 26.1.0 | MIT | groundtruth, groundtruth-gpu, titiler | Yes |  | https://www.attrs.org/ |
| cachetools | 7.3.0 | MIT | titiler | Yes |  | https://github.com/tkem/cachetools/ |
| cachetools | 8.0.0 | MIT | groundtruth, groundtruth-gpu | Yes |  | https://github.com/tkem/cachetools/ |
| certifi | 2026.10.23 | Mozilla Public License 2.0 (MPL 2.0) | groundtruth, groundtruth-gpu, gt-routing, titiler | Yes (MPL-2.0 weak copyleft: file-level; unmodified use OK) | Mozilla CA bundle | https://github.com/certifi/python-certifi |
| charset-normalizer | 3.5.2 | MIT | groundtruth, groundtruth-gpu, gt-routing, titiler | Yes |  | https://github.com/jawah/charset_normalizer/blob/master/CHANGELOG.md |
| click | 8.5.1 | BSD-3-Clause | groundtruth, groundtruth-gpu, titiler | Yes |  | https://click.palletsprojects.com/page/changes/ |
| click-plugins | 1.1.1.2 | BSD License | titiler | Yes |  | https://github.com/click-contrib/click-plugins |
| cligj | 0.7.2 | BSD | titiler | Yes |  | https://github.com/mapbox/cligj |
| cloudpickle | 3.1.2 | BSD License | groundtruth, groundtruth-gpu | Yes |  | https://github.com/cloudpipe/cloudpickle |
| cogeo-mosaic | 9.2.0 | MIT License | titiler | Yes |  | https://github.com/developmentseed/cogeo-mosaic |
| color-operations | 0.4.0 | MIT | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/vincentsarago/color-operations |
| cramjam | 2.11.0 | MIT | titiler | Yes |  | https://github.com/milesgranger/pyrus-cramjam |
| cuda-bindings | 12.9.7 | LicenseRef-NVIDIA-SOFTWARE-LICENSE | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://github.com/NVIDIA/cuda-python |
| cuda-pathfinder | 1.6.0 | Apache-2.0 | groundtruth-gpu | Yes |  | https://github.com/NVIDIA/cuda-python |
| cuda-toolkit | 12.6.3 |  | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) | GPU env only; NVIDIA CUDA EULA | https://developer.nvidia.com/cuda-toolkit |
| donfig | 0.8.1.post1 | MIT License | titiler | Yes |  | https://github.com/pytroll/donfig |
| duckdb | 2.0.0 | MIT License | groundtruth, groundtruth-gpu | Yes; FLAG: bundled LGPL-2.1 (GEOS), see F2 | core MIT; **spatial extension** (installed in ~/.duckdb) bundles **GEOS (LGPL-2.1)**, GDAL, PROJ | https://duckdb.org/docs/stable/clients/python/overview |
| faiss-cpu | 1.15.1 | MIT | groundtruth, groundtruth-gpu | Yes (GCC Runtime Library Exception for bundled libgfortran/libgomp) | bundles OpenBLAS (BSD-3), **libgfortran/libquadmath (GPL-3 with GCC Runtime Library Exception)**, libgomp (same exception) | https://github.com/facebookresearch/faiss |
| fastapi | 0.162.0 | MIT | titiler | Yes |  | https://github.com/fastapi/fastapi |
| fastapi | 0.162.2 | MIT | groundtruth, groundtruth-gpu | Yes |  | https://github.com/fastapi/fastapi |
| filelock | 4.2.4 | MIT | groundtruth, groundtruth-gpu | Yes |  | https://py-filelock.readthedocs.io |
| fsspec | 2026.9.0 | BSD-3-Clause | groundtruth, groundtruth-gpu | Yes |  | https://filesystem-spec.readthedocs.io/en/latest/changelog.html |
| ftfy | 6.3.1 | Apache-2.0 | groundtruth, groundtruth-gpu | Yes |  | https://ftfy.readthedocs.io/en/latest/ |
| geojson | 3.3.0 | BSD License | groundtruth | Yes |  | https://github.com/jazzband/geojson |
| geojson-pydantic | 2.2.0 | MIT License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/developmentseed/geojson-pydantic |
| google-crc32c | 1.9.0 | Apache-2.0 | titiler | Yes |  | https://github.com/googleapis/python-crc32c |
| googleapis-common-protos | 1.76.0 | Apache-2.0 | titiler | Yes |  | https://github.com/googleapis/google-cloud-python/tree/main/packages/googleapis-common-protos |
| greenlet | 3.5.6 | MIT AND PSF-2.0 | gt-browser | Yes |  | https://greenlet.readthedocs.io |
| grpcio | 1.85.1 | Apache-2.0 | titiler | Yes |  | https://grpc.io |
| h11 | 0.16.0 | MIT License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/python-hyper/h11 |
| hf-xet | 1.8.0 | Apache-2.0 | groundtruth, groundtruth-gpu | Yes |  | https://huggingface.co/docs/hub/xet/index |
| httpcore | 1.0.9 | BSD-3-Clause | groundtruth, groundtruth-gpu | Yes |  | https://www.encode.io/httpcore |
| httpcore2 | 2.16.0 | BSD-3-Clause | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/pydantic/httpx2/blob/main/src/httpcore2/CHANGELOG.md |
| httptools | 0.9.0 | MIT | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/MagicStack/httptools |
| httpx | 0.28.1 | BSD License | groundtruth, groundtruth-gpu | Yes |  | https://github.com/encode/httpx/blob/master/CHANGELOG.md |
| httpx2 | 2.16.0 | BSD-3-Clause | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/pydantic/httpx2/blob/main/src/httpx2/CHANGELOG.md |
| huggingface_hub | 2.4.0 | Apache Software License | groundtruth, groundtruth-gpu | Yes |  | https://github.com/huggingface/huggingface_hub |
| idna | 3.20 | BSD-3-Clause | groundtruth, groundtruth-gpu, gt-routing, titiler | Yes |  | https://github.com/kjd/idna/blob/master/HISTORY.md |
| iniconfig | 2.3.1 | MIT | groundtruth, groundtruth-gpu | Yes |  | https://github.com/pytest-dev/iniconfig |
| Jinja2 | 3.1.6 | BSD License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://jinja.palletsprojects.com/changes/ |
| joblib | 1.6.0 | BSD-3-Clause | groundtruth, groundtruth-gpu | Yes |  | https://joblib.readthedocs.io |
| laspy | 2.7.0 | BSD-2-Clause | groundtruth | Yes |  | https://github.com/laspy/laspy |
| lazrs | 0.8.2 | MIT | groundtruth | Yes |  | https://pypi.org/project/lazrs/ |
| markdown-it-py | 4.2.0 | MIT License | groundtruth, groundtruth-gpu | Yes |  | https://markdown-it-py.readthedocs.io |
| MarkupSafe | 3.0.3 | BSD-3-Clause | groundtruth, groundtruth-gpu | Yes |  | https://palletsprojects.com/donate |
| MarkupSafe | 3.0.4 | BSD-3-Clause | titiler | Yes |  | https://palletsprojects.com/donate |
| mdurl | 0.1.2 | MIT License | groundtruth, groundtruth-gpu | Yes |  | https://github.com/executablebooks/mdurl |
| morecantile | 6.2.0 | BSD License | groundtruth, groundtruth-gpu | Yes |  | https://developmentseed.org/morecantile/ |
| morecantile | 7.1.0 | MIT License | titiler | Yes |  | https://github.com/developmentseed/morecantile |
| mpmath | 1.3.0 | BSD License | groundtruth, groundtruth-gpu | Yes |  | http://mpmath.org/ |
| msgspec | 0.22.0 | BSD-3-Clause | titiler | Yes |  | https://msgspec.dev |
| narwhals | 2.28.0 | MIT | groundtruth, groundtruth-gpu | Yes |  | https://github.com/narwhals-dev/narwhals |
| networkx | 3.7 | BSD-3-Clause | groundtruth, groundtruth-gpu | Yes |  | https://networkx.org/ |
| numcodecs | 0.17.1 | MIT | titiler | Yes |  | https://github.com/zarr-developers/numcodecs/issues |
| numexpr | 2.14.2 | MIT | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/pydata/numexpr |
| numpy | 2.5.4 | BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0 | groundtruth, groundtruth-gpu, gt-routing, titiler | Yes (GCC Runtime Library Exception for bundled libgfortran/libgomp) | bundles scipy-openblas, libgfortran/libquadmath (GCC RLE) | https://numpy.org |
| nvidia-cublas-cu12 | 12.6.4.1 | Other/Proprietary License | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://developer.nvidia.com/cuda-zone |
| nvidia-cuda-cupti-cu12 | 12.6.80 | Other/Proprietary License | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://developer.nvidia.com/cuda-zone |
| nvidia-cuda-nvrtc-cu12 | 12.6.85 | Other/Proprietary License | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://developer.nvidia.com/cuda-zone |
| nvidia-cuda-runtime-cu12 | 12.6.77 | Other/Proprietary License | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://developer.nvidia.com/cuda-zone |
| nvidia-cudnn-cu12 | 9.10.2.21 | Other/Proprietary License | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://developer.nvidia.com/cuda-zone |
| nvidia-cufft-cu12 | 11.3.0.4 | Other/Proprietary License | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://developer.nvidia.com/cuda-zone |
| nvidia-cufile-cu12 | 1.11.1.6 | Other/Proprietary License | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://developer.nvidia.com/cuda-zone |
| nvidia-curand-cu12 | 10.3.7.77 | Other/Proprietary License | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://developer.nvidia.com/cuda-zone |
| nvidia-cusolver-cu12 | 11.7.1.2 | Other/Proprietary License | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://developer.nvidia.com/cuda-zone |
| nvidia-cusparse-cu12 | 12.5.4.2 | Other/Proprietary License | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://developer.nvidia.com/cuda-zone |
| nvidia-cusparselt-cu12 | 0.7.1 | NVIDIA Proprietary Software | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://developer.nvidia.com/cusparselt |
| nvidia-nccl-cu12 | 2.29.3 | LicenseRef-NVIDIA-Proprietary | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://developer.nvidia.com/cuda-zone |
| nvidia-nvjitlink-cu12 | 12.9.86 | Other/Proprietary License | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://developer.nvidia.com/cuda-zone |
| nvidia-nvshmem-cu12 | 3.4.5 | LicenseRef-NVIDIA-Proprietary | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://developer.nvidia.com/cuda-zone |
| nvidia-nvtx-cu12 | 12.6.77 | Other/Proprietary License | groundtruth-gpu | FLAG: proprietary NVIDIA EULA (use OK; redistribution only per EULA) |  | https://developer.nvidia.com/cuda-zone |
| obstore | 0.12.1 | MIT License | titiler | Yes |  | https://developmentseed.org/obstore |
| open_clip_torch | 4.2.1 | MIT License | groundtruth, groundtruth-gpu | Yes | MIT | https://github.com/mlfoundations/open_clip |
| opentelemetry-api | 1.45.2 | Apache-2.0 | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-api |
| opentelemetry-exporter-http-transport | 0.66b2 | Apache-2.0 | titiler | Yes |  | https://github.com/open-telemetry/opentelemetry-python/tree/main/exporter/opentelemetry-exporter-http-transport |
| opentelemetry-exporter-otlp | 1.45.2 | Apache-2.0 | titiler | Yes |  | https://github.com/open-telemetry/opentelemetry-python/tree/main/exporter/opentelemetry-exporter-otlp |
| opentelemetry-exporter-otlp-common | 0.66b2 | Apache-2.0 | titiler | Yes |  | https://github.com/open-telemetry/opentelemetry-python/tree/main/exporter/opentelemetry-exporter-otlp-common |
| opentelemetry-exporter-otlp-proto-common | 1.45.2 | Apache-2.0 | titiler | Yes |  | https://github.com/open-telemetry/opentelemetry-python/tree/main/exporter/opentelemetry-exporter-otlp-proto-common |
| opentelemetry-exporter-otlp-proto-grpc | 1.45.2 | Apache-2.0 | titiler | Yes |  | https://github.com/open-telemetry/opentelemetry-python/tree/main/exporter/opentelemetry-exporter-otlp-proto-grpc |
| opentelemetry-exporter-otlp-proto-http | 1.45.2 | Apache-2.0 | titiler | Yes |  | https://github.com/open-telemetry/opentelemetry-python/tree/main/exporter/opentelemetry-exporter-otlp-proto-http |
| opentelemetry-instrumentation | 0.66b2 | Apache-2.0 | titiler | Yes |  | https://github.com/open-telemetry/opentelemetry-python-contrib/tree/main/opentelemetry-instrumentation |
| opentelemetry-instrumentation-asgi | 0.66b2 | Apache-2.0 | titiler | Yes |  | https://github.com/open-telemetry/opentelemetry-python-contrib/tree/main/instrumentation/opentelemetry-instrumentation-asgi |
| opentelemetry-instrumentation-fastapi | 0.66b2 | Apache-2.0 | titiler | Yes |  | https://github.com/open-telemetry/opentelemetry-python-contrib/tree/main/instrumentation/opentelemetry-instrumentation-fastapi |
| opentelemetry-instrumentation-logging | 0.66b2 | Apache-2.0 | titiler | Yes |  | https://github.com/open-telemetry/opentelemetry-python-contrib/tree/main/instrumentation/opentelemetry-instrumentation-logging |
| opentelemetry-proto | 1.45.2 | Apache-2.0 | titiler | Yes |  | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-proto |
| opentelemetry-sdk | 1.45.2 | Apache-2.0 | titiler | Yes |  | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-sdk |
| opentelemetry-semantic-conventions | 0.66b2 | Apache-2.0 | titiler | Yes |  | https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-semantic-conventions |
| opentelemetry-util-http | 0.66b2 | Apache-2.0 | titiler | Yes |  | https://github.com/open-telemetry/opentelemetry-python-contrib/tree/main/util/opentelemetry-util-http |
| osmium | 4.3.2 | BSD License | groundtruth, gt-routing | Yes | bundles libosmium (BSL-1.0), bz2, lz4 | https://osmcode.org/pyosmium |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | groundtruth, groundtruth-gpu, titiler | Yes |  | https://packaging.pypa.io/ |
| pandas | 3.1.1 | BSD-3-Clause | titiler | Yes |  | https://pandas.pydata.org |
| pillow | 13.0.0 | MIT-CMU | groundtruth, groundtruth-gpu | Yes | bundles libjpeg, libpng, libtiff, libwebp, freetype (FTL), harfbuzz, lcms2, openjpeg, libavif (all permissive) | https://pillow.readthedocs.io/en/stable/releasenotes/index.html |
| pip | 26.3 | MIT | titiler | Yes |  | https://pip.pypa.io/en/stable/news/ |
| planetary-computer | 1.0.0 | MIT License | groundtruth, groundtruth-gpu | Yes |  | https://pypi.org/project/planetary-computer/ |
| playwright | 1.64.1 | Apache-2.0 | gt-browser | Yes | dev tool only (gt-browser env); downloads browser binaries with their own licences | https://github.com/Microsoft/playwright-python |
| pluggy | 1.7.0 | MIT License | groundtruth, groundtruth-gpu | Yes |  | https://github.com/pytest-dev/pluggy |
| pmtiles | 3.8.1 | BSD License | groundtruth, groundtruth-gpu | Yes |  | https://github.com/protomaps/pmtiles |
| protobuf | 7.36.2 | 3-Clause BSD License | titiler | Yes |  | https://developers.google.com/protocol-buffers/ |
| pyarrow | 26.0.1 | Apache-2.0 | groundtruth, groundtruth-gpu | Yes |  | https://arrow.apache.org/ |
| pydantic | 2.14.3 | MIT | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/pydantic/pydantic |
| pydantic_core | 2.50.4 | MIT | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/pydantic/pydantic |
| pydantic-settings | 2.16.2 | MIT | titiler | Yes |  | https://github.com/pydantic/pydantic-settings |
| pyee | 14.0.0 | MIT License | gt-browser | Yes |  | https://github.com/jfhbrook/pyee |
| Pygments | 2.21.0 | BSD-2-Clause | groundtruth, groundtruth-gpu | Yes |  | https://pygments.org |
| pyparsing | 3.3.3 | MIT | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pyparsing-docs.readthedocs.io/en/latest/ |
| pyproj | 3.8.0 | MIT | groundtruth, groundtruth-gpu, titiler | Yes | bundles PROJ (MIT), libcurl, sqlite, libtiff | https://github.com/pyproj4/pyproj |
| pystac | 1.16.0 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-client | 0.9.1 | Apache Software License | groundtruth, groundtruth-gpu | Yes |  | https://github.com/stac-utils/pystac-client |
| pystac-core | 1.16.0 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-classification | 2.0.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-datacube | 2.2.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-eo | 1.1.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-file | 2.1.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-grid | 1.1.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-item-assets | 1.0.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-label | 1.0.3 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-mgrs | 1.0.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-mlm | 1.4.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-pointcloud | 1.0.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-projection | 2.0.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-raster | 1.1.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-render | 2.0.1 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-sar | 1.0.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-sat | 1.0.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-scientific | 1.0.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-storage | 2.0.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-table | 1.2.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-timestamps | 1.1.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-version | 1.2.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-view | 1.0.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pystac-ext-xarray-assets | 1.0.2 | Apache Software License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pystac.readthedocs.io |
| pytest | 9.2.0 | MIT | groundtruth, groundtruth-gpu | Yes |  | https://docs.pytest.org/en/stable/changelog.html |
| python-dateutil | 4.0.0 | BSD-3-Clause | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/dateutil/dateutil |
| python-dotenv | 1.2.4 | BSD-3-Clause | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/theskumar/python-dotenv |
| pytz | 2026.6 | MIT License | groundtruth, groundtruth-gpu | Yes |  | http://pythonhosted.org/pytz |
| pyvalhalla | 3.9.1 | MIT | groundtruth, groundtruth-gpu, gt-routing | FLAG: REVIEW (F1): bundled GPL-2.0+ libs | **see flag F1**: wheel bundles GPL/LGPL libs (librttopo GPL-2.0+, jbigkit GPL-2.0+, spatialite/freexl MPL/GPL/LGPL, GEOS, libssh, openpgm LGPL-2.1, libidn2/libunistring LGPL-3+/GPL-2+, zeromq 4.3.4 LGPL-3+ w/ exception), OpenSSL 1.1.1k (EOL), libcurl 7.61.1 | https://github.com/valhalla/valhalla |
| PyYAML | 6.0.3 | MIT License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://pyyaml.org/ |
| rasterio | 1.5.2 | BSD-3-Clause | groundtruth, groundtruth-gpu, titiler | Yes; FLAG: bundled LGPL-2.1 (GEOS), see F2 | wheel bundles GDAL (MIT), **GEOS (LGPL-2.1)**, PROJ, HDF5, netCDF, libcurl, OpenSSL 3 | https://rasterio.readthedocs.io/ |
| regex | 2026.11.7 | Apache-2.0 AND CNRI-Python | groundtruth, groundtruth-gpu | Yes |  | https://github.com/mrabarnett/mrab-regex |
| reportlab | 4.4.4 | BSD License | groundtruth, groundtruth-gpu | Yes | BSD | https://www.reportlab.com/ |
| requests | 2.34.2 | Apache Software License | groundtruth, groundtruth-gpu, gt-routing, titiler | Yes |  | https://requests.readthedocs.io |
| rich | 15.0.0 | MIT License | groundtruth, groundtruth-gpu | Yes |  | https://rich.readthedocs.io/en/latest/ |
| rio-cogeo | 7.0.4 | BSD License | titiler | Yes |  | https://github.com/cogeotiff/rio-cogeo |
| rio-stac | 0.12.0 | BSD License | titiler | Yes |  | https://developmentseed.org/rio-stac/ |
| rio-tiler | 7.9.6 | BSD License | groundtruth, groundtruth-gpu | Yes |  | https://cogeotiff.github.io/rio-tiler/ |
| rio-tiler | 9.4.13 | BSD License | titiler | Yes |  | https://cogeotiff.github.io/rio-tiler/ |
| rioxarray | 0.23.0 | Apache-2.0 | titiler | Yes |  | https://corteva.github.io/rioxarray/ |
| safetensors | 0.8.0 | Apache Software License | groundtruth, groundtruth-gpu | Yes |  | https://github.com/huggingface/safetensors |
| scikit-learn | 1.9.1 | BSD-3-Clause | groundtruth, groundtruth-gpu | Yes (GCC Runtime Library Exception for bundled libgfortran/libgomp) | bundles libgomp (GCC RLE) | https://scikit-learn.org |
| scipy | 1.18.1 | BSD License | groundtruth, groundtruth-gpu | Yes (GCC Runtime Library Exception for bundled libgfortran/libgomp) | bundles OpenBLAS, libgfortran (GCC RLE) | https://scipy.org/ |
| setuptools | 78.1.0 | MIT License | groundtruth, groundtruth-gpu | Yes |  | https://github.com/pypa/setuptools |
| shapely | 2.2.0 | BSD-3-Clause | groundtruth, groundtruth-gpu, gt-routing, titiler | Yes; FLAG: bundled LGPL-2.1 (GEOS), see F2 | wheel bundles **GEOS (LGPL-2.1)** | https://shapely.readthedocs.io/ |
| shellingham | 1.5.4 | ISC License (ISCL) | groundtruth, groundtruth-gpu | Yes |  | https://github.com/sarugaku/shellingham |
| simplejson | 4.2.0 | MIT OR AFL-2.1 | groundtruth, groundtruth-gpu, titiler | Yes | dual MIT OR AFL-2.1 (take MIT) | https://github.com/simplejson/simplejson |
| starlette | 1.10.0 | BSD-3-Clause | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/Kludex/starlette |
| starlette-cramjam | 0.7.0 | MIT License | titiler | Yes |  | https://github.com/developmentseed/starlette-cramjam |
| supermorecado | 0.2.0 | BSD License | titiler | Yes |  | https://github.com/developmentseed/supermorecado |
| sympy | 1.14.0 | BSD License | groundtruth, groundtruth-gpu | Yes |  | https://sympy.org |
| threadpoolctl | 3.8.0 | BSD-3-Clause | groundtruth, groundtruth-gpu | Yes |  | https://github.com/joblib/threadpoolctl |
| tiktoken | 0.14.0 | MIT License | groundtruth, groundtruth-gpu | Yes |  | https://github.com/openai/tiktoken |
| timm | 1.0.31 | Apache Software License | groundtruth, groundtruth-gpu | Yes | Apache-2.0 | https://github.com/huggingface/pytorch-image-models |
| titiler-application | 2.4.0 | MIT License | titiler | Yes |  | https://developmentseed.org/titiler/ |
| titiler-core | 0.24.2 | MIT License | groundtruth, groundtruth-gpu | Yes |  | https://developmentseed.org/titiler/ |
| titiler-core | 2.4.0 | MIT License | titiler | Yes |  | https://developmentseed.org/titiler/ |
| titiler-extensions | 2.4.0 | MIT License | titiler | Yes |  | https://developmentseed.org/titiler/ |
| titiler-mosaic | 2.4.0 | MIT License | titiler | Yes |  | https://developmentseed.org/titiler/ |
| titiler-xarray | 2.4.0 | MIT License | titiler | Yes |  | https://developmentseed.org/titiler/ |
| tokenizers | 0.23.3 | Apache Software License | groundtruth, groundtruth-gpu | Yes |  | https://github.com/huggingface/tokenizers |
| torch | 2.14.1+cu126 | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND MIT | groundtruth-gpu | Yes | BSD-style; CUDA build pulls NVIDIA libs | https://pytorch.org |
| torch | 2.15.0+cpu | Apache-2.0 AND Apache-2.0 WITH LLVM-exception AND BSD-2-Clause AND BSD-3-Clause AND BSL-1.0 AND ISC AND MIT AND Zlib | groundtruth | Yes | BSD-style; CUDA build pulls NVIDIA libs | https://pytorch.org |
| torchvision | 0.29.1+cu126 | BSD | groundtruth-gpu | Yes |  | https://github.com/pytorch/vision |
| torchvision | 0.29.2+cpu | BSD | groundtruth | Yes |  | https://github.com/pytorch/vision |
| tqdm | 4.70.1 | MPL-2.0 AND MIT | groundtruth, groundtruth-gpu | Yes (MPL-2.0 weak copyleft: file-level; unmodified use OK) | MPL-2.0 AND MIT | https://tqdm.github.io |
| transformers | 5.22.2 | Apache 2.0 License | groundtruth, groundtruth-gpu | Yes | Apache-2.0 | https://github.com/huggingface/transformers |
| triton | 3.8.0 | MIT | groundtruth-gpu | Yes | GPU env only | https://github.com/triton-lang/triton/ |
| truststore | 0.10.5 | MIT | groundtruth, groundtruth-gpu, titiler | Yes |  | https://truststore.readthedocs.io |
| typer | 0.28.0 | MIT | groundtruth, groundtruth-gpu | Yes |  | https://github.com/fastapi/typer |
| typing_extensions | 4.16.0 | PSF-2.0 | groundtruth, groundtruth-gpu, gt-browser, titiler | Yes |  | https://github.com/python/typing_extensions/issues |
| typing-inspection | 0.4.5 | MIT | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/pydantic/typing-inspection |
| tzdata | 2026.6 | Apache-2.0 | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/python/tzdata |
| urllib3 | 2.8.0 | MIT | groundtruth, groundtruth-gpu, gt-routing, titiler | Yes |  | https://github.com/urllib3/urllib3/blob/main/CHANGES.rst |
| uvicorn | 0.54.0 | BSD-3-Clause | groundtruth, groundtruth-gpu, titiler | Yes |  | https://uvicorn.dev/release-notes |
| uvloop | 0.23.0 | Apache Software License / MIT License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/MagicStack/uvloop |
| watchfiles | 1.3.0 | MIT License | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/samuelcolvin/watchfiles |
| wcwidth | 0.9.4 | MIT License | groundtruth, groundtruth-gpu | Yes |  | https://github.com/jquast/wcwidth |
| websockets | 17.4 | BSD-3-Clause | groundtruth, groundtruth-gpu, titiler | Yes |  | https://github.com/python-websockets/websockets |
| wrapt | 2.5.2 | BSD-2-Clause | titiler | Yes |  | https://github.com/GrahamDumpleton/wrapt |
| xarray | 2026.9.0 | Apache-2.0 | titiler | Yes |  | https://docs.xarray.dev |
| zarr | 3.4.2 | MIT | titiler | Yes |  | https://github.com/zarr-developers/zarr-python/issues |

## npm packages (web/, from package-lock.json; one lockfile serves web/ and web/app-v2)

All 156 are permissive: MIT 122, ISC 20, BSD-3-Clause 6, OFL-1.1 2, BSD-2-Clause 2, Apache-2.0 2, CC-BY-4.0 1 (dev data), BlueOak-1.0.0 1. No GPL/LGPL/AGPL, no unknowns.

| package | version | licence | scope | commercial use | notes |
|---|---|---|---|---|---|
| @babel/code-frame | 7.29.7 | MIT | dev | Yes |  |
| @babel/compat-data | 7.29.7 | MIT | dev | Yes |  |
| @babel/core | 7.29.13 | MIT | dev | Yes |  |
| @babel/generator | 7.29.13 | MIT | dev | Yes |  |
| @babel/helper-compilation-targets | 7.29.13 | MIT | dev | Yes |  |
| @babel/helper-globals | 7.29.7 | MIT | dev | Yes |  |
| @babel/helper-module-imports | 7.29.7 | MIT | dev | Yes |  |
| @babel/helper-module-transforms | 7.29.13 | MIT | dev | Yes |  |
| @babel/helper-plugin-utils | 7.29.7 | MIT | dev | Yes |  |
| @babel/helper-string-parser | 7.29.13 | MIT | dev | Yes |  |
| @babel/helper-validator-identifier | 7.29.7 | MIT | dev | Yes |  |
| @babel/helper-validator-option | 7.29.7 | MIT | dev | Yes |  |
| @babel/helpers | 7.29.13 | MIT | dev | Yes |  |
| @babel/parser | 7.29.13 | MIT | dev | Yes |  |
| @babel/plugin-transform-react-jsx-self | 7.29.7 | MIT | dev | Yes |  |
| @babel/plugin-transform-react-jsx-source | 7.29.7 | MIT | dev | Yes |  |
| @babel/template | 7.29.7 | MIT | dev | Yes |  |
| @babel/traverse | 7.29.13 | MIT | dev | Yes |  |
| @babel/types | 7.29.11 | MIT | dev | Yes |  |
| @esbuild/aix-ppc64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/android-arm | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/android-arm64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/android-x64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/darwin-arm64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/darwin-x64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/freebsd-arm64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/freebsd-x64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/linux-arm | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/linux-arm64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/linux-ia32 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/linux-loong64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/linux-mips64el | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/linux-ppc64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/linux-riscv64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/linux-s390x | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/linux-x64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/netbsd-x64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/openbsd-x64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/sunos-x64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/win32-arm64 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/win32-ia32 | 0.21.5 | MIT | dev optional | Yes |  |
| @esbuild/win32-x64 | 0.21.5 | MIT | dev optional | Yes |  |
| @fontsource/ibm-plex-mono | 5.1.0 | OFL-1.1 | runtime (bundled) | Yes | font files SIL OFL 1.1 |
| @fontsource/inter | 5.1.0 | OFL-1.1 | runtime (bundled) | Yes | font files SIL OFL 1.1 |
| @jridgewell/gen-mapping | 0.3.13 | MIT | dev | Yes |  |
| @jridgewell/remapping | 2.3.5 | MIT | dev | Yes |  |
| @jridgewell/resolve-uri | 3.1.2 | MIT | dev | Yes |  |
| @jridgewell/sourcemap-codec | 1.6.0 | MIT | dev | Yes |  |
| @jridgewell/trace-mapping | 0.3.31 | MIT | dev | Yes |  |
| @mapbox/geojson-rewind | 0.5.2 | ISC | runtime (bundled) | Yes |  |
| @mapbox/jsonlint-lines-primitives | 2.0.3 | MIT | runtime (bundled) | Yes |  |
| @mapbox/point-geometry | 0.1.0 | ISC | runtime (bundled) | Yes |  |
| @mapbox/tiny-sdf | 2.3.0 | BSD-2-Clause | runtime (bundled) | Yes |  |
| @mapbox/unitbezier | 0.0.1 | BSD-2-Clause | runtime (bundled) | Yes |  |
| @mapbox/vector-tile | 1.3.1 | BSD-3-Clause | runtime (bundled) | Yes |  |
| @mapbox/whoots-js | 3.1.0 | ISC | runtime (bundled) | Yes |  |
| @maplibre/maplibre-gl-style-spec | 20.4.0 | ISC | runtime (bundled) | Yes |  |
| @rollup/rollup-android-arm-eabi | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-android-arm64 | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-android-x64 | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-darwin-arm64 | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-darwin-x64 | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-freebsd-arm64 | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-freebsd-x64 | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-linux-arm-gnueabihf | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-linux-arm-musleabihf | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-linux-arm64-gnu | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-linux-arm64-musl | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-linux-loong64-gnu | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-linux-loong64-musl | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-linux-ppc64-gnu | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-linux-ppc64-musl | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-linux-riscv64-gnu | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-linux-riscv64-musl | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-linux-s390x-gnu | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-linux-x64-gnu | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-linux-x64-musl | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-openbsd-x64 | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-openharmony-arm64 | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-win32-arm64-msvc | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-win32-ia32-msvc | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-win32-x64-gnu | 4.68.0 | MIT | dev optional | Yes |  |
| @rollup/rollup-win32-x64-msvc | 4.68.0 | MIT | dev optional | Yes |  |
| @types/babel__core | 7.20.5 | MIT | dev | Yes |  |
| @types/babel__generator | 7.27.0 | MIT | dev | Yes |  |
| @types/babel__template | 7.4.4 | MIT | dev | Yes |  |
| @types/babel__traverse | 7.28.0 | MIT | dev | Yes |  |
| @types/estree | 1.0.10 | MIT | dev | Yes |  |
| @types/geojson | 7946.0.16 | MIT | runtime (bundled) | Yes |  |
| @types/geojson-vt | 3.2.5 | MIT | runtime (bundled) | Yes |  |
| @types/mapbox__point-geometry | 0.1.4 | MIT | runtime (bundled) | Yes |  |
| @types/mapbox__vector-tile | 1.3.4 | MIT | runtime (bundled) | Yes |  |
| @types/pbf | 3.0.5 | MIT | runtime (bundled) | Yes |  |
| @types/prop-types | 15.7.15 | MIT | dev | Yes |  |
| @types/react | 18.3.12 | MIT | dev | Yes |  |
| @types/react-dom | 18.3.1 | MIT | dev | Yes |  |
| @types/supercluster | 7.1.3 | MIT | runtime (bundled) | Yes |  |
| @vitejs/plugin-react | 4.3.4 | MIT | dev | Yes |  |
| baseline-browser-mapping | 2.11.35 | Apache-2.0 | dev | Yes |  |
| browserslist | 4.29.5 | MIT | dev | Yes |  |
| caniuse-lite | 1.0.30001823 | CC-BY-4.0 | dev | Yes | data, CC-BY-4.0, build-time only (not shipped) |
| convert-source-map | 2.0.0 | MIT | dev | Yes |  |
| csstype | 3.2.3 | MIT | dev | Yes |  |
| debug | 4.4.3 | MIT | dev | Yes |  |
| earcut | 3.2.4 | ISC | runtime (bundled) | Yes |  |
| electron-to-chromium | 1.5.478 | ISC | dev | Yes |  |
| esbuild | 0.21.5 | MIT | dev | Yes |  |
| escalade | 3.2.0 | MIT | dev | Yes |  |
| fsevents | 2.3.3 | MIT | dev optional | Yes |  |
| gensync | 1.0.0-beta.2 | MIT | dev | Yes |  |
| geojson-vt | 4.0.3 | ISC | runtime (bundled) | Yes |  |
| get-stream | 6.0.1 | MIT | runtime (bundled) | Yes |  |
| gl-matrix | 3.4.4 | MIT | runtime (bundled) | Yes |  |
| global-prefix | 4.0.0 | MIT | runtime (bundled) | Yes |  |
| ieee754 | 1.2.1 | BSD-3-Clause | runtime (bundled) | Yes |  |
| ini | 4.1.3 | ISC | runtime (bundled) | Yes |  |
| isexe | 3.1.5 | BlueOak-1.0.0 | runtime (bundled) | Yes | Blue Oak Model License 1.0 (permissive) |
| js-tokens | 4.0.0 | MIT | runtime (bundled) | Yes |  |
| jsesc | 3.1.0 | MIT | dev | Yes |  |
| json-stringify-pretty-compact | 4.0.0 | MIT | runtime (bundled) | Yes |  |
| json5 | 2.2.3 | MIT | dev | Yes |  |
| kdbush | 4.1.0 | ISC | runtime (bundled) | Yes |  |
| kind-of | 6.0.3 | MIT | runtime (bundled) | Yes |  |
| loose-envify | 1.4.0 | MIT | runtime (bundled) | Yes |  |
| lru-cache | 5.1.1 | ISC | dev | Yes |  |
| lucide-react | 0.460.0 | ISC | runtime (bundled) | Yes |  |
| maplibre-gl | 4.7.1 | BSD-3-Clause | runtime (bundled) | Yes | BSD-3-Clause |
| minimist | 1.2.8 | MIT | runtime (bundled) | Yes |  |
| ms | 2.1.3 | MIT | dev | Yes |  |
| murmurhash-js | 1.0.0 | MIT | runtime (bundled) | Yes |  |
| nanoid | 3.3.20 | MIT | dev | Yes |  |
| node-releases | 2.0.63 | MIT | dev | Yes |  |
| pbf | 3.3.0 | BSD-3-Clause | runtime (bundled) | Yes |  |
| picocolors | 1.1.1 | ISC | dev | Yes |  |
| postcss | 8.5.32 | MIT | dev | Yes |  |
| potpack | 2.1.0 | ISC | runtime (bundled) | Yes |  |
| protocol-buffers-schema | 3.6.1 | MIT | runtime (bundled) | Yes |  |
| quickselect | 2.0.0 | ISC | runtime (bundled) | Yes |  |
| quickselect | 3.0.0 | ISC | runtime (bundled) | Yes |  |
| react | 18.3.1 | MIT | runtime (bundled) | Yes |  |
| react-dom | 18.3.1 | MIT | runtime (bundled) | Yes |  |
| react-refresh | 0.14.2 | MIT | dev | Yes |  |
| resolve-protobuf-schema | 2.1.0 | MIT | runtime (bundled) | Yes |  |
| rollup | 4.68.0 | MIT | dev | Yes |  |
| rw | 1.3.3 | BSD-3-Clause | runtime (bundled) | Yes |  |
| scheduler | 0.23.2 | MIT | runtime (bundled) | Yes |  |
| semver | 6.3.2 | ISC | dev | Yes |  |
| source-map-js | 1.2.2 | BSD-3-Clause | dev | Yes |  |
| supercluster | 8.0.1 | ISC | runtime (bundled) | Yes |  |
| tinyqueue | 3.0.0 | ISC | runtime (bundled) | Yes |  |
| typescript | 5.6.3 | Apache-2.0 | dev | Yes |  |
| update-browserslist-db | 1.3.4 | MIT | dev | Yes |  |
| vite | 5.4.11 | MIT | dev | Yes |  |
| vt-pbf | 3.1.3 | MIT | runtime (bundled) | Yes |  |
| which | 4.0.0 | ISC | runtime (bundled) | Yes |  |
| yallist | 3.1.1 | ISC | dev | Yes |  |
