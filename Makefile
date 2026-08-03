# NIGHTGLASS
#
# `make up` is the entry point and must stay a single command that works on a
# fresh clone — EXECUTION_SPEC §M6 is literally "someone else could clone,
# make up, and reproduce the demo".

SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose
PROVISION := $(COMPOSE) --profile provision

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
	@echo "Enclave up. Models: make pull-models (once). Proof: make air-gap-proof"

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

##@ Using it

.PHONY: ask
ask:  ## ask the agent a question (M5). make ask Q="Houve alguma embarcação...?"
	$(COMPOSE) --profile cli run --rm agent $(Q)

.PHONY: shell
shell:  ## shell inside the api container
	$(COMPOSE) exec api bash
