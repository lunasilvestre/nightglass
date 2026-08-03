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
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*


FROM base AS runtime

WORKDIR /app

# Dependency layer first, so edits to src/ don't reinstall the world.
COPY pyproject.toml README.md ./
COPY src/nightglass/__init__.py src/nightglass/__init__.py
RUN pip install --no-cache-dir -e ".[services]"

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


# Test deps are baked into a separate stage rather than pip-installed at test
# time, because inside the enclave there is no pypi to install from -- the first
# attempt at `make test` failed with "Temporary failure in name resolution",
# which is the air gap working exactly as designed. Anything that assumes a
# package index at runtime has to be moved to build time.
FROM runtime AS dev
USER root
RUN pip install --no-cache-dir pytest ruff
USER nightglass
