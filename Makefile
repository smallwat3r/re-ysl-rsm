# Usage (the device address comes from RSM_ADDRESS, find it with `make scan`):
#   make venv                 create .venv and install bleak
#   make capture              pull the btsnoop log off the phone and print the ATT frames
#   make frames               re-print frames from an existing btsnoop_hci.log
#   make dispense A=249,180,183   dispense 3 cartridge amounts (over a bin first)
#   make test                 run the offline test suite (pytest)
#   make lint                 ruff + black --check
#   make format               ruff --fix + black
#   make scan                 scan for the device (8 s)
#   make pair                 bond with the device once per machine (it drops unbonded centrals)
#   make web                  start the control page (http://127.0.0.1:8765)
#   make mock                 the control page against a simulated device, no hardware needed
#   make deploy               push the checkout to a Pi (RSM_PI or PI=pi@host) and restart the app, SETUP=1 reruns pi/setup.sh

# local runtime config (RSM_ADDRESS, RSM_LAN), gitignored
-include .env
# pass them to app.py / control.py
export RSM_ADDRESS RSM_LAN
ADDR ?= $(RSM_ADDRESS)
PI   ?= $(RSM_PI)
PY   := .venv/bin/python
# uv when installed, else python3 -m venv / pip
UV   := $(shell command -v uv 2>/dev/null)
PIP  := $(if $(UV),uv pip install --python $(PY),$(PY) -m pip install)

.PHONY: help scan pair venv capture frames dispense test lint format web mock deploy

help:
	@sed -n 's/^#   //p' $(MAKEFILE_LIST)

venv: .venv/bin/python
.venv/bin/python:
	$(if $(UV),uv venv .venv,python3 -m venv .venv)
	$(PIP) -r requirements.txt

capture:
	./re_tools/phone.sh capture
	python3 re_tools/frames.py

frames:
	python3 re_tools/frames.py

dispense: venv
	@test -n "$(A)" || { echo "usage: make dispense A=249,180,183   (3 cartridge amounts)"; exit 2; }
	@test -n "$(ADDR)" || { echo "set the device address: RSM_ADDRESS=... make dispense A=$(A)   (find it with make scan)"; exit 2; }
	PYTHONPATH=. $(PY) re_tools/control.py $(ADDR) --dispense $(A)

test: .venv/bin/pytest
	.venv/bin/python -m pytest -q

lint: .venv/bin/pytest
	.venv/bin/ruff check .
	.venv/bin/black --check .

format: .venv/bin/pytest
	.venv/bin/ruff check --fix .
	.venv/bin/black .

.venv/bin/pytest: .venv/bin/python
	$(PIP) -r requirements-dev.txt
scan: venv
	$(PY) re_tools/enumerate.py

pair:
	@test -n "$(ADDR)" || { echo "set the device address: RSM_ADDRESS=... make pair   (find it with make scan)"; exit 2; }
	( echo "scan on"; sleep 10; echo "scan off"; echo "pair $(ADDR)"; sleep 20; echo "quit" ) | bluetoothctl --agent NoInputNoOutput

web: venv
	RSM_ADDRESS=$(ADDR) $(PY) app.py

mock: venv
	RSM_MOCK=1 $(PY) app.py

deploy:
	SETUP=$(SETUP) ./pi/deploy.sh $(PI)
