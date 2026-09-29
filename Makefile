PYTHON ?= python3.11
VENV := .venv
OPA_IMAGE := openpolicyagent/opa:1.21.0

.PHONY: venv up down test-unit test-component test-opa test-e2e

venv:
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install -q -r requirements-dev.txt

up:
	docker compose up -d --build --wait

down:
	docker compose down

test-unit:
	$(VENV)/bin/pytest -m "not component and not e2e"

test-component:
	$(VENV)/bin/pytest -m component

test-opa:
	docker run --rm -v "$(CURDIR)/opa:/policies:ro" $(OPA_IMAGE) test /policies -v

test-e2e:
	$(VENV)/bin/pytest -m e2e
