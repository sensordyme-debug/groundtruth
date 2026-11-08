# Groundtruth: convenience targets (Linux / macOS). Windows: see AGENTS.md "Run the API locally".
PY ?= python3
VENV ?= .venv
BUNDLE_TAG ?= contract-v0.2.1
BUNDLE ?= groundtruth-slices-chips224s192-g0.6-v3c8p192.tar.gz
BUNDLE_SHA256 ?= 01c3841d0bcc16ea4b14a6d988cde34ecc655f485f0f31dc600b0fcd89093005
REPO ?= sensordyme-debug/groundtruth
PORT ?= 8000

.PHONY: api-slice venv slice-data api test

## One command: env + slice data + API on http://127.0.0.1:$(PORT)
api-slice: venv slice-data api

venv:
	test -d $(VENV) || $(PY) -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip
	$(VENV)/bin/pip install -q torch torchvision --index-url https://download.pytorch.org/whl/cpu
	$(VENV)/bin/pip install -q -e ".[api,ml,dev]"

slice-data:
	test -f data/.slice-bundle-$(BUNDLE_SHA256) || ( \
	  mkdir -p data/incoming && \
	  curl -fL -o data/incoming/$(BUNDLE) https://github.com/$(REPO)/releases/download/$(BUNDLE_TAG)/$(BUNDLE) && \
	  echo "$(BUNDLE_SHA256)  data/incoming/$(BUNDLE)" | shasum -a 256 -c - && \
	  tar -xzf data/incoming/$(BUNDLE) && touch data/.slice-bundle-$(BUNDLE_SHA256) )

api:
	PYTHONPATH=engine:. HF_HUB_DISABLE_TELEMETRY=1 $(VENV)/bin/uvicorn api.app:app --host 127.0.0.1 --port $(PORT)

test:
	$(VENV)/bin/pytest -q
