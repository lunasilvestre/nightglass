#!/usr/bin/env bash
# Thin entrypoint. Exists for one reason: to fail loudly and early on a
# misconfigured AOI rather than 40 minutes later inside a spatial join.
#
# EXECUTION_SPEC §3.1 makes AOI parameterisation a hard requirement -- "bbox,
# time window, AIS source adapter, detector config all come from config, never
# hardcoded". A config error should therefore surface at container start.
set -euo pipefail

python -c 'from nightglass.config import settings; settings.describe()'

exec "$@"
