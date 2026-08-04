# NIGHTGLASS application image — serves api, mcp and agent from one build.
#
# Building needs the network. That is fine and is the same category of act as
# `make pull-models`: provisioning is online, operation is not. Nothing in the
# running image ever reaches out — see docker-compose.yml.

FROM python:3.13-slim-trixie AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# curl is NOT incidental. EXECUTION_SPEC §M1's proof is, verbatim:
#     docker compose exec api curl -m 5 https://example.com
# so curl has to exist inside the image for the milestone to be demonstrable at
# all. The container healthchecks use it too.
#
# libexpat1 is here for rasterio. Its manylinux wheel vendors GDAL, PROJ and the
# rest, but still dynamically links the system libexpat -- and python:3.13-slim
# does not ship it. `pip install rasterio` succeeds; `import rasterio` then dies
# with "libexpat.so.1: cannot open shared object file". A green build is not
# proof the enclave can read a pixel, and there is no apt inside the enclave to
# fix it afterwards.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates libexpat1 \
 && rm -rf /var/lib/apt/lists/*


FROM base AS runtime

WORKDIR /app

# Dependency layer first, so edits to src/ don't reinstall the world.
#
# `spatial` (rasterio, shapely, pyproj, geopandas, pandas) is installed HERE, at
# build time, and that is the whole point. Inside the enclave there is no package
# index to install from -- the first `make test` proved it by failing with
# "Temporary failure in name resolution". Anything the running system needs has
# to be in the image before the network goes away. The wheels are self-contained:
# rasterio and pyproj vendor their own GDAL and PROJ, so no apt-level geo stack
# is needed and the enclave's GDAL version is pinned by the wheel rather than by
# whatever the base image happens to ship.
COPY pyproject.toml README.md ./
COPY src/nightglass/__init__.py src/nightglass/__init__.py
RUN pip install --no-cache-dir -e ".[services,spatial,viz,agent]"

COPY src/ src/
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Drop privileges. Nothing here needs root, and an enclave service running as
# root is the kind of detail that gets asked about.
RUN useradd --create-home --uid 10001 nightglass \
 && chown -R nightglass:nightglass /app
USER nightglass

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "nightglass.api.main:app", "--host", "0.0.0.0", "--port", "8000"]


# The corpus fetcher. ONLINE, and the only image here that is: it runs on the
# `provision` network to download the public half of the document corpus, in the
# same way `model-puller` runs there to download weights.
#
# poppler-utils is installed HERE and not in `runtime`, deliberately. PDF text
# extraction is a provisioning-time concern -- the enclave only ever reads
# normalised markdown, so giving it a PDF parser would be giving it a capability
# it has no use for. It is also a measured choice over a pure-Python parser:
# pypdf inserts spurious spaces inside words on the EUR-Lex Official Journal
# PDFs ("REGUL A TIONS", "concer ning restr ictiv e"), 676 occurrences in one
# regulation against 0 for pdftotext, and mangled words would poison the
# embeddings and appear inside quoted evidence.
FROM runtime AS fetcher
USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils \
 && rm -rf /var/lib/apt/lists/*
USER nightglass
ENTRYPOINT ["nightglass-corpus"]
CMD ["fetch"]


# Test deps are baked into a separate stage rather than pip-installed at test
# time, because inside the enclave there is no pypi to install from -- the first
# attempt at `make test` failed with "Temporary failure in name resolution",
# which is the air gap working exactly as designed. Anything that assumes a
# package index at runtime has to be moved to build time.
FROM runtime AS dev
USER root
RUN pip install --no-cache-dir pytest ruff
USER nightglass
