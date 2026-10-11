# `make check` is the contract: it runs precisely what CI runs.
.DEFAULT_GOAL := help
UV ?= uv

.PHONY: help install check lint fix types test test-cov evals evals-network run tools clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install: ## Install the project with all extras and dev dependencies
	$(UV) sync --all-extras

check: lint types test ## Lint, typecheck and test -- run before every commit

lint: ## Check formatting and lint rules
	$(UV) run ruff check src tests evals
	$(UV) run ruff format --check src tests evals

fix: ## Auto-fix lint and formatting
	$(UV) run ruff check --fix src tests evals
	$(UV) run ruff format src tests evals

types: ## Type-check with mypy
	$(UV) run mypy

test: ## Run the offline test suite
	$(UV) run pytest -q

test-cov: ## Test with a coverage report
	$(UV) run pytest --cov=geospatial_mcp --cov-report=term-missing

evals: ## Validate the tool-selection eval library (offline cases)
	$(UV) run python evals/run.py --mode validate

evals-network: ## Validate every eval case, including live-API ones
	$(UV) run python evals/run.py --mode validate --network

run: ## Start the server on stdio
	$(UV) run mcp-geospatial

tools: ## Print the registered tool catalogue as JSON
	$(UV) run mcp-geospatial --list-tools

clean: ## Remove caches and build artefacts
	rm -rf .pytest_cache .ruff_cache .mypy_cache dist build *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
