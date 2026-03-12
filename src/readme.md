
# Command examples:

## Processa una cartella
python run.py process ./foto_sessione_roma/

## Riprendi una sessione interrotta
python run.py resume ./workspace/session_20250306/

## Riprocessa solo uno step su un gruppo
python run.py rerun --session 20250306 --group group_001 --step color

## Mostra stato sessione
python run.py status ./workspace/session_20250306/