# ms4-orquestadorDelivery (FastAPI, no DB)

### Prerrequisitos
- ms1-usuarios en http://localhost:8001
- ms2-productos en http://localhost:8002
- ms3-pedidos   en http://localhost:8003

> Si usas contenedores en la misma red, puedes definir:
> MS1_URL=http://ms1-usuarios:8001  MS2_URL=http://ms2-productos:8002  MS3_URL=http://ms3-pedidos:8003

## Ejecutar local (sin Docker)
```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8004
