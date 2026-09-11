PYTHON := .venv/bin/python
export UV_CACHE_DIR := /tmp/timesfm-uv
export HF_HOME := $(CURDIR)/.cache/huggingface

.PHONY: install install-model check collect collect-5m demo serve audit audit-5m forecast-5m experiments experiments-5m
install:
	uv venv --python 3.12 .venv
	uv pip install --python $(PYTHON) -e '.[dev]'
install-model:
	uv pip install --python $(PYTHON) torch --index-url https://download.pytorch.org/whl/cpu
	uv pip install --python $(PYTHON) -e '.[model]'
check:
	$(PYTHON) -m ruff check src tests
	$(PYTHON) -m pytest -q
collect:
	$(PYTHON) -m timesfm_lab collect --start 2023-01-01
collect-5m:
	$(PYTHON) -m timesfm_lab --interval 5m collect
audit-5m:
	$(PYTHON) -m timesfm_lab --interval 5m audit
forecast-5m:
	$(PYTHON) -m timesfm_lab --interval 5m forecast --context 64 --horizon 5 --allow-retrospective --latest-complete
experiments-5m:
	$(PYTHON) -m timesfm_lab --interval 5m compare --context 64 --horizon 5 --folds 12 --allow-retrospective
audit:
	$(PYTHON) -m timesfm_lab audit
demo:
	$(PYTHON) -m timesfm_lab demo
serve:
	$(PYTHON) -m timesfm_lab serve --port 8050
experiments:
	$(PYTHON) -m timesfm_lab compare --allow-retrospective --context 128 --folds 12
