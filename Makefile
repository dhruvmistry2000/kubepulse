# kubepulse developer tasks.
# Override the interpreter with: make test PYTHON=.venv/bin/python

PYTHON ?= python3
HELM   ?= helm
CHART  := helm

DASHBOARD_IMAGE ?= kubepulse-dashboard:latest
AGENT_IMAGE     ?= kubepulse-agent:latest

.DEFAULT_GOAL := help

.PHONY: help compile test helm image-dashboard image-agent images check

help:
	@echo "Targets:"
	@echo "  make compile          Byte-compile dashboard, agent, and common"
	@echo "  make test             Run unit tests"
	@echo "  make helm             Lint the chart and render default plus toggles"
	@echo "  make image-dashboard  Build $(DASHBOARD_IMAGE)"
	@echo "  make image-agent      Build $(AGENT_IMAGE)"
	@echo "  make images           Build both images"
	@echo "  make check            compile, test, and helm"

compile:
	$(PYTHON) -m compileall dashboard agent common

test:
	$(PYTHON) -m unittest discover -s tests -v

helm:
	$(HELM) lint $(CHART)
	$(HELM) template test $(CHART) > /dev/null
	$(HELM) template test $(CHART) --set podDisruptionBudget.enabled=true > /dev/null
	$(HELM) template test $(CHART) --set persistence.mode=file > /dev/null
	$(HELM) template test $(CHART) --set persistence.mode=memory > /dev/null

image-dashboard:
	docker build -t $(DASHBOARD_IMAGE) dashboard

image-agent:
	docker build -t $(AGENT_IMAGE) agent

images: image-dashboard image-agent

check: compile test helm
