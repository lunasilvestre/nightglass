# NIGHTGLASS
#
# `make up` is the entry point and must stay a single command that works on a
# fresh clone — EXECUTION_SPEC §M6 is literally "someone else could clone,
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
	@echo "  once:   make pull-models      (~10 GB)   make fetch-corpus (~35 MB)  make ingest"
	@echo "  proof:  make air-gap-proof    (M1)       make rag-proof              (M2)"

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
docs-search:  ## doc_search from the CLI. make docs-search Q="embarcação escura"
	$(COMPOSE) exec api nightglass-corpus search "$(Q)" -k $(or $(K),8)

.PHONY: ask-docs
ask-docs:  ## grounded, cited answer. make ask-docs Q="o que é uma embarcação escura?"
	$(COMPOSE) exec api nightglass-corpus ask "$(Q)" --sources

.PHONY: rag-proof
rag-proof:  ## §M2: same question ungrounded vs grounded, plus the refusal path
	@scripts/rag-proof.sh

##@ Spatial  (M3)

# The Danish scene and its AIS day. Denmark is the validation AOI (§3.1): it is
# the only one with free point-level historical AIS, so it is the only place a
# claim about the matcher can be checked rather than asserted.
DK_SCENE ?= /app/data/raw/sar/S1D_IW_GRDH_1SDV_20260717T052324_20260717T052349_003709_006A36_BC13.zip
DK_AIS   ?= /app/data/interim/ais_kattegat_20260717_0513-0534.csv
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

##@ Using it

.PHONY: ask
ask:  ## ask the agent a question (M5). make ask Q="Houve alguma embarcação...?"
	$(COMPOSE) --profile cli run --rm agent "$(Q)"

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
chain:  ## let the local model pick and chain tools. make chain Q="Houve alguma...?"
	$(COMPOSE) exec -T -e NIGHTGLASS_AOI=$(AOI) api nightglass-tools chain "$(Q)"

.PHONY: mcp-tools
mcp-tools:  ## list the MCP tools over the stdio transport Claude Desktop uses
	@scripts/mcp-stdio.sh tools/list

.PHONY: tool-proof
tool-proof:  ## §M4 end to end: MCP over stdio, the local model chaining, the INTREP guard
	@scripts/tool-proof.sh
