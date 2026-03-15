
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


# External Tools

## Photomatix

### Command example for Realistic:
PhotomatixCL -a2 -ca -no2 -md -n 0 -d "c:\temp\\" -5 -5a 0.0 -5c 0.0 -5h 2.0 "C:\temp\pipeline_tests\0H8A4390.JPG" "C:\temp\pipeline_tests\0H8A4391.JPG" "C:\temp\pipeline_tests\0H8A4392.JPG"

### Command example for Photographic:
PhotomatixCL -a2 -ca -no2 -md -n 0 -d "c:\temp\\" -3 -t2 -x2 "C:\temp\pipeline_tests\photographic.xmp" "C:\temp\pipeline_tests\0H8A4390.JPG" "C:\temp\pipeline_tests\0H8A4391.JPG" "C:\temp\pipeline_tests\0H8A4392.JPG"

### Command example for Adjusted:
PhotomatixCL -a2 -ca -no2 -md -n 0 -d "c:\temp\\" -2 -2a 5.0 -2b -6.0 -2c 2.0 -2h 5.0 -2k 6.0 -2m 2.0 -2s 6.0 -2w 6.0 "C:\temp\pipeline_tests\0H8A4390_s.JPG" "C:\temp\pipeline_tests\0H8A4391_s.JPG" "C:\temp\pipeline_tests\0H8A4392_s.JPG"

## Rawtherapee

### Location
C:\Program Files\RawTherapee\5.12\rawtherapee-cli.exe

### Command example to convert raw files to jpg with different exposures (-2, 0, +2)
rawtherapee-cli -p "C:\temp\pipeline_tests\rawtherapee_profile_exposure_0.pp3" -o "C:\temp\pipeline_tests\raw\0H8A4482_0.jpg" -c "C:\temp\pipeline_tests\raw\0H8A4482.CR3"
rawtherapee-cli -p "C:\temp\pipeline_tests\rawtherapee_profile_exposure_-2.pp3" -o "C:\temp\pipeline_tests\raw\0H8A4482_-2.jpg" -c "C:\temp\pipeline_tests\raw\0H8A4482.CR3"
rawtherapee-cli -p "C:\temp\pipeline_tests\rawtherapee_profile_exposure_+2.pp3" -o "C:\temp\pipeline_tests\raw\0H8A4482_+2.jpg" -c "C:\temp\pipeline_tests\raw\0H8A4482.CR3"