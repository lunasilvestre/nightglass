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

##@ Using it

.PHONY: ask
ask:  ## ask the agent a question (M5). make ask Q="Houve alguma embarcação...?"
	$(COMPOSE) --profile cli run --rm agent "$(Q)"

.PHONY: shell
shell:  ## shell inside the api container
	$(COMPOSE) exec api bash
