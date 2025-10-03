import os

# Service URLs (default to localhost; override with env vars if you dockerize)
MS1_URL = os.getenv("MS1_URL", "http://localhost:8001")  # usuarios
MS2_URL = os.getenv("MS2_URL", "http://localhost:8002")  # productos
MS3_URL = os.getenv("MS3_URL", "http://localhost:8003")  # pedidos

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "5.0"))
TAX_RATE = float(os.getenv("TAX_RATE", "0.18"))  # IGV example
