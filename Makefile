# NIGHTGLASS
#
# `make up` is the entry point and must stay a single command that works on a
# fresh clone — the reproducibility bar is literally "someone else could clone,
# make up, and reproduce the demo".

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose
PROVISION := $(COMPOSE) --profile provision

# The corpus fetcher writes onto a host bind mount, so it runs as the invoking
# user rather than as the image's uid. Without this the corpus lands root-owned
# and `make ingest` fails with a permission error two steps downstream.
export HOST_UID := $(shell id -u)
export HOST_GID := $(shell id -g)

.PHONY: help
help:  ## show this help
	@awk 'BEGIN {FS = ":.*##"; print "NIGHTGLASS\n"} \
	     /^[a-zA-Z_-]+:.*?##/ { printf "  \033[1m%-18s\033[0m %s\n", $$1, $$2 } \
	     /^##@/ { printf "\n\033[2m%s\033[0m\n", substr($$0, 5) }' $(MAKEFILE_LIST)

##@ Running

.PHONY: preflight
preflight:  ## check the host can actually run the enclave
	@scripts/preflight.sh

.PHONY: up
up: preflight  ## build and start the enclave, wait for healthy
	$(COMPOSE) up -d --build --wait
	@echo
	@$(COMPOSE) ps
	@echo
	@echo "Enclave up."
	@echo "  once:   make pull-models (10 GB)  fetch-corpus (35 MB)  fetch-granules (2.8 GB)"
	@echo "          make fetch-ais (890 MB)   fetch-coastline       fetch-gfw     then ingest"
	@echo "  proof:  make air-gap-proof (M1)  rag-proof (M2)  dark-proof (M3)  tool-proof (M4)"
	@echo "  demo:   make demo         §6 end to end, both AOIs, ~60 s"

.PHONY: down
down:  ## stop the enclave, keep volumes
	$(COMPOSE) down

.PHONY: clean
clean:  ## stop and DESTROY volumes — including the ~10 GB model blobs
	@echo "This deletes the postgis, qdrant AND ollama model volumes."
	@read -p "Type 'yes' to continue: " ans && [ "$$ans" = "yes" ]
	$(COMPOSE) down -v

.PHONY: ps logs
ps:  ## container status
	$(COMPOSE) ps
logs:  ## follow logs (S=api to scope to one service)
	$(COMPOSE) logs -f $(S)

##@ Provisioning  (the only targets that touch the network)

.PHONY: pull-models
pull-models:  ## fetch chat + embedding models into the enclave volume
	@echo ">> Runs on the provision network, not the enclave. ~10 GB on a cold volume."
	$(PROVISION) run --rm model-puller

.PHONY: seed-models
seed-models:  ## LOCAL SHORTCUT: copy models from the host ollama instead of downloading
	@echo ">> Not the documented path — 'make pull-models' is. This exists because"
	@echo ">> this machine already has the blobs and re-downloading 10 GB is waste."
	@scripts/seed-models-from-host.sh

.PHONY: fetch-corpus
fetch-corpus:  ## fetch the public half of the document corpus (M2). ~35 MB.
	@echo ">> Runs on the provision network, not the enclave. Re-runs are cached;"
	@echo ">> add FORCE=1 to re-download."
	@mkdir -p data/corpus
	$(PROVISION) run --rm corpus-fetcher fetch \
	  --sources /app/corpus/sources.yaml --out /app/data/corpus $(if $(FORCE),--force,)

.PHONY: fetch-granules
fetch-granules:  ## fetch the Sentinel-1 granules every proof runs over (M6). 2.8 GB. Needs Earthdata.
	@echo ">> Runs on the provision network, not the enclave."
	@echo ">> Sentinel-1 is free and open, but ASF wants an Earthdata login AND the"
	@echo ">> EULA accepted at urs.earthdata.nasa.gov/profile. load-env.sh reads"
	@echo ">> ~/.netrc; compose forwards the two variables by name, never a value."
	@echo ">> ALL=1 adds the optional and superseded entries (4.7 GB); VERIFY=1"
	@echo ">> sha256s what is already on disk instead of checking its size."
	@mkdir -p data/raw/sar
	@source scripts/load-env.sh >/dev/null && \
	  $(PROVISION) run --rm granule-fetcher fetch-granules --out /app/data/raw/sar \
	    $(if $(ALL),--all,) $(if $(VERIFY),--verify,) $(if $(FORCE),--force,) \
	    $(if $(NAME),--name $(NAME),)

.PHONY: fetch-ais
fetch-ais:  ## fetch the DMA day and cut the acquisition window out of it (M6). 890 MB. No credentials.
	@echo ">> Runs on the provision network, not the enclave. Public S3, no token."
	@echo ">> Needs the granules first — the slice window comes from the scene's own"
	@echo ">> acquisition time, not from a hardcoded pair of clock times."
	@mkdir -p data/raw/ais data/interim
	$(PROVISION) run --rm -e NIGHTGLASS_AOI=$(or $(AIS_AOI),kattegat) ais-fetcher \
	  fetch-ais --out /app/data/raw/ais --slice-out /app/data/interim \
	    $(if $(ALL),--all,) $(if $(VERIFY),--verify,) $(if $(FORCE),--force,)

.PHONY: fetch-coastline
fetch-coastline:  ## fetch GSHHG and clip it to the configured AOIs (M3). 149 MB in, ~200 KB out.
	@echo ">> Runs on the provision network, not the enclave."
	@echo ">> The detector's data-derived land mask cannot tell a 100 m skerry from a"
	@echo ">> 100 m hull — see src/nightglass/spatial/coastline.py. This is the fix."
	@mkdir -p data/coastline
	$(PROVISION) run --rm coastline-fetcher fetch-coastline \
	  --out /app/data/coastline $(if $(FORCE),--force,)

.PHONY: fetch-gfw
fetch-gfw:  ## fetch GFW's published detections as a cross-check layer (M6). Needs GFW_TOKEN.
	@echo ">> Runs on the provision network, not the enclave."
	@echo ">> A REFERENCE layer, never an input to ais_match — see spatial/gfw.py."
	@mkdir -p data/gfw
	@# The token is a provider identity and lives in ~/.config/eo-credentials.env,
	@# not in this repo. load-env.sh knows the precedence (a blank in .env means
	@# 'defer to central', not 'override with empty'); compose forwards it.
	@# GFW_AOI, not AOI: the spatial targets above default AOI to kattegat, and
	@# GFW is a Portuguese cross-check. Sharing the variable silently fetched the
	@# wrong AOI on the first run.
	@source scripts/load-env.sh >/dev/null && \
	  $(PROVISION) run --rm gfw-fetcher gfw-reference --out /app/data/gfw \
	    --aoi $(or $(GFW_AOI),lisbon) \
	    --start $(or $(START),2026-06-13) --end $(or $(END),2026-06-14)

##@ Verification

.PHONY: air-gap-proof
air-gap-proof:  ## §M1: prove no egress while inference still works
	@scripts/air-gap-proof.sh

.PHONY: test
test:  ## run the unit tests
	@# --network none, not the enclave network: the tests need no network at all,
	@# and saying so explicitly is cheaper than discovering otherwise later.
	@# tests/ is mounted rather than baked in — not part of the runtime artifact.
	docker build -q -t nightglass/app:test --target dev -f docker/Dockerfile . >/dev/null
	docker run --rm --network none -v "$(CURDIR)/tests:/app/tests:ro" \
	  --entrypoint python nightglass/app:test -m pytest -q

.PHONY: lint
lint:  ## ruff
	docker build -q -t nightglass/app:test --target dev -f docker/Dockerfile . >/dev/null
	docker run --rm --network none -v "$(CURDIR)/tests:/app/tests:ro" \
	  --entrypoint ruff nightglass/app:test check src tests

.PHONY: config
config:  ## show the resolved AOI as the containers see it
	$(COMPOSE) exec api curl -fsS localhost:8000/config
	@echo

.PHONY: ready
ready:  ## dependency readiness from inside the enclave
	$(COMPOSE) exec api curl -fsS localhost:8000/ready
	@echo

##@ Documents  (M2)

.PHONY: ingest
ingest:  ## chunk + embed the corpus into Qdrant (idempotent; add RECREATE=1 to rebuild)
	$(COMPOSE) exec api nightglass-corpus ingest $(if $(RECREATE),--recreate,) -v

.PHONY: docs-stats
docs-stats:  ## what is actually in the document index
	$(COMPOSE) exec api nightglass-corpus stats

.PHONY: docs-search
docs-search:  ## doc_search from the CLI. make docs-search Q="dark vessel"
	$(COMPOSE) exec api nightglass-corpus search "$(Q)" -k $(or $(K),8)

.PHONY: ask-docs
ask-docs:  ## grounded, cited answer. make ask-docs Q="what is a dark vessel?"
	$(COMPOSE) exec api nightglass-corpus ask "$(Q)" --sources

.PHONY: rag-proof
rag-proof:  ## §M2: same question ungrounded vs grounded, plus the refusal path
	@scripts/rag-proof.sh

##@ Spatial  (M3)

# The Danish scene and its AIS day. Denmark is the validation AOI (§3.1): it is
# the only one with free point-level historical AIS, so it is the only place a
# claim about the matcher can be checked rather than asserted.
DK_SCENE ?= /app/data/raw/sar/S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13.zip
# Written by `make fetch-ais`, and named after the window it holds rather than
# chosen: ±30 min around this scene's acquisition. The matcher takes ±11 of it.
DK_AIS   ?= /app/data/interim/ais_kattegat_20260717_0453-0553.csv
AOI      ?= kattegat
SPATIAL   = $(COMPOSE) exec -T -e NIGHTGLASS_AOI=$(AOI) api nightglass-spatial

.PHONY: migrate
migrate:  ## create/refresh the M3 PostGIS schema (add DROP=1 to start clean)
	$(SPATIAL) migrate $(if $(DROP),--drop,)

.PHONY: scenes
scenes:  ## catalogue every granule on disk as a STAC item
	$(SPATIAL) scenes

.PHONY: detect
detect:  ## run the vessel detector over one granule and load the detections
	$(SPATIAL) detect $(or $(SCENE),$(DK_SCENE))

.PHONY: load-ais
load-ais:  ## load AIS for the acquisition window into PostGIS
	$(SPATIAL) load-ais $(or $(AIS),$(DK_AIS)) --granule $(or $(SCENE),$(DK_SCENE))

.PHONY: dark
dark:  ## §M3's query — detections with no AIS correspondence
	$(SPATIAL) dark $(or $(SCENE_ID),$(notdir $(basename $(or $(SCENE),$(DK_SCENE)))))

.PHONY: validate-shift
validate-shift:  ## measure the azimuth-displacement correction against DMA truth
	$(SPATIAL) validate-shift $(or $(SCENE),$(DK_SCENE)) $(or $(AIS),$(DK_AIS))

.PHONY: render
render:  ## chips, scene overview and map view into data/out/
	$(SPATIAL) render $(or $(SCENE),$(DK_SCENE))

.PHONY: dark-proof
dark-proof:  ## §M3 end to end: schema, scene, detections, AIS, the query, the evidence
	@scripts/dark-proof.sh

.PHONY: gfw-compare
gfw-compare:  ## our detector vs GFW's published layer, over the identical granule
	@# GFW_AOI, not AOI — see fetch-gfw. AOI defaults to kattegat for the M3
	@# targets, and GFW is the Portuguese cross-check.
	$(COMPOSE) exec -T -e NIGHTGLASS_AOI=$(or $(GFW_AOI),lisbon) api nightglass-spatial \
	  gfw-compare $(if $(SCENE_ID),--scene-id $(SCENE_ID),)

##@ Using it  (M5)

# The agent is a one-shot, not a service (see docker-compose.yml). Each of
# these starts a fresh container, which is exactly the point of M5: `ask` drafts
# and exits, and `approve` is a DIFFERENT container picking the halted run up
# out of Postgres. AGENT_AOI rather than AOI, because AOI defaults to kattegat
# for the M3 spatial targets.
AGENT = $(COMPOSE) --profile cli run --rm -e NIGHTGLASS_AOI=$(or $(AGENT_AOI),kattegat) agent

.PHONY: ask
ask:  ## M5: run the agent to the human gate and stop. make ask Q="Were there any...?"
	$(AGENT) ask "$(Q)"

.PHONY: pending
pending:  ## M5: runs halted at the human gate, awaiting review
	$(AGENT) pending

.PHONY: show
show:  ## M5: the draft awaiting review. make show T=ng-...
	$(AGENT) show $(T)

.PHONY: approve
approve:  ## M5: resume the halted graph and release. make approve T=ng-...
	$(AGENT) approve $(T) $(if $(NOTE),--note "$(NOTE)",)

.PHONY: reject
reject:  ## M5: resume and withhold. make reject T=ng-... NOTE="why"
	$(AGENT) reject $(T) $(if $(NOTE),--note "$(NOTE)",)

.PHONY: agent-proof
agent-proof:  ## §M5 end to end: a question, a halt at the gate, a resume on approval
	@scripts/agent-proof.sh

.PHONY: shell
shell:  ## shell inside the api container
	$(COMPOSE) exec api bash

##@ Tools + MCP  (M4)

.PHONY: tools
tools:  ## the six §5 tools and the active AOI
	$(COMPOSE) exec api nightglass-tools list

.PHONY: tool-call
tool-call:  ## one tool, raw JSON. make tool-call T=correlate J='{"bbox":[...],"start":"...","end":"..."}'
	$(COMPOSE) exec -T -e NIGHTGLASS_AOI=$(AOI) api nightglass-tools call $(T) --json '$(J)'

.PHONY: chain
chain:  ## let the local model pick and chain tools. make chain Q="Were there any...?"
	$(COMPOSE) exec -T -e NIGHTGLASS_AOI=$(AOI) api nightglass-tools chain "$(Q)"

.PHONY: mcp-tools
mcp-tools:  ## list the MCP tools over the stdio transport Claude Desktop uses
	@scripts/mcp-stdio.sh tools/list

.PHONY: tool-proof
tool-proof:  ## §M4 end to end: MCP over stdio, the local model chaining, the INTREP guard
	@scripts/tool-proof.sh

##@ The demo  (M6)

.PHONY: demo
demo:  ## §6 end to end in ~60 s: both AOIs, the gate, and the refusals
	@scripts/demo.sh

.PHONY: record-demo
record-demo:  ## re-record docs/demo.cast, then render the GIF and the MP4 from it
	@scripts/demo.sh --record docs/demo.cast
	@scripts/render-demo.sh

.PHONY: render-demo
render-demo:  ## re-pace and re-render docs/demo.gif + docs/demo.mp4 from the cast
	@scripts/render-demo.sh

.PHONY: check-demo
check-demo:  ## can a human follow the video? per-row dwell + a 1 fps slice of it
	@# Non-zero if any row is on screen for under 3 s, so it can gate a release.
	@tmp=$$(mktemp -d) && trap 'rm -rf "$$tmp"' EXIT && \
	  scripts/pace-demo.py docs/demo.cast $$tmp/p.cast >/dev/null && \
	  scripts/check-demo.py $$tmp/p.cast docs/demo.mp4

##@ The offline bundle  (M7)

# The bundler is Go, and the host does not need Go: it is built in a container
# and delivered as a static binary, the same way `make test` runs pytest in a
# container rather than asking the host for a Python. `scripts/preflight.sh`
# gains no new line because the contract is still docker and make.
BUNDLE ?= nightglass-bundle-0.1.0.tar
NGBUNDLE = bundler/bin/nightglass-bundle

.PHONY: bundler
bundler:  ## build the static bundler binary into bundler/bin/ (no Go on the host)
	docker build -q --target bin --output bundler/bin -f docker/Dockerfile.bundler . >/dev/null
	@file $(NGBUNDLE) | sed 's/^/   /'

.PHONY: bundler-test
bundler-test:  ## go vet + go test for the bundler, in the build container
	docker build -q --target test -f docker/Dockerfile.bundler . >/dev/null && echo "   ok"

.PHONY: bundle
bundle: bundler  ## build the ~18 GB offline transfer bundle. Needs images, models and data.
	@echo ">> The wheelhouse step needs a package index; everything else is local."
	@echo ">> Needs the images built (make up), the model volume filled (make"
	@echo ">> pull-models) and the data fetched (make fetch-granules fetch-ais)."
	@echo ">> ALL=1 adds sources.yaml's optional and superseded granules."
	$(NGBUNDLE) create -o $(BUNDLE) --repo . $(if $(ALL),--all,) \
	  $(if $(SKIP_WHEELS),--skip-wheels,) $(if $(CREATED),--created $(CREATED),)

.PHONY: verify-bundle
verify-bundle: bundler  ## stream a bundle and check every byte against its manifest
	$(NGBUNDLE) verify $(BUNDLE) $(if $(V),-v,)

.PHONY: inspect-bundle
inspect-bundle: bundler  ## read a bundle's manifest without reading the bundle
	$(NGBUNDLE) inspect $(BUNDLE)

.PHONY: restore-bundle
restore-bundle: bundler  ## verify a bundle, then load it into docker and this clone
	@echo ">> Unpacks to INTO (default ./bundle-restore), then docker load, fills the"
	@echo ">> model volume and places data/raw. Nothing is committed until the whole"
	@echo ">> archive has verified."
	$(NGBUNDLE) restore $(BUNDLE) --into $(or $(INTO),./bundle-restore) \
	  --install --repo .

.PHONY: bundle-proof
bundle-proof:  ## §M7 end to end: create, verify, four refusals, restore into a clean daemon
	@scripts/bundle-proof.sh
