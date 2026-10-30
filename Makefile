PY := .venv/bin

.PHONY: setup lint typecheck test tf-fmt tf-validate secrets check

setup:
	python3.12 -m venv .venv
	$(PY)/pip install -r requirements-dev.txt
	git config core.hooksPath .githooks

lint:
	$(PY)/ruff check .
	$(PY)/ruff format --check .

typecheck:
	$(PY)/mypy

test:
	$(PY)/pytest

tf-fmt:
	terraform -chdir=infra fmt -check -recursive

tf-validate:
	terraform -chdir=infra init -backend=false -input=false
	terraform -chdir=infra validate

secrets:
	gitleaks detect --source . --config .gitleaks.toml

check: lint typecheck test tf-fmt tf-validate secrets
