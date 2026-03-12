
# Command examples:

## Processa una cartella
python run.py process ./foto_sessione_roma/

## Riprendi una sessione interrotta
python run.py resume ./workspace/session_20250306/

## Riprocessa solo uno step su un gruppo
python run.py rerun --session 20250306 --group group_001 --step color

## Mostra stato sessione
python run.py status ./workspace/session_20250306/


# How to setup dev environment

## Create a python environment:
- python -m venv .venv


## Activate it:
- source .venv/scripts/activate


## Install uv, create toml and sync:
- pip install uv
- create file pyproject.toml
- run "uv sync --active"