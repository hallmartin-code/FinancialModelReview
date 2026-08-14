# Cross-platform venv bin directory: POSIX uses .venv/bin, Windows uses .venv/Scripts.
ifeq ($(OS),Windows_NT)
VENV_BIN := .venv/Scripts
else
VENV_BIN := .venv/bin
endif

PORT ?= 8000

.PHONY: install test lint typecheck check serve template clean

install:
	python -m venv .venv && $(VENV_BIN)/pip install -e ".[dev]"

test:
	$(VENV_BIN)/python -m pytest --cov=deckscan --cov-report=term-missing

lint:
	$(VENV_BIN)/python -m ruff check src tests
	$(VENV_BIN)/python -m ruff format --check src tests

typecheck:
	$(VENV_BIN)/python -m mypy

check: lint typecheck test

serve:
	$(VENV_BIN)/uvicorn deckscan.web:app --reload --port $(PORT)

run:
	$(VENV_BIN)/deckscan analyze $(DECK)

template:
	$(VENV_BIN)/deckscan template --out out/onepager-template.pdf

clean:
	rm -rf .pytest_cache .mypy_cache .ruff_cache .coverage htmlcov out
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
