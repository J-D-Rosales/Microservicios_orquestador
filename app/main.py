from fastapi import FastAPI, HTTPException
import httpx, os, asyncio, time
from pydantic import BaseModel, Field
from typing import List, Optional, Any
from fastapi.middleware.cors import CORSMiddleware
import os
from app.schemas import CreateOrderReq


# ---------- Config ----------
MS1 = os.getenv("MS1_URL", "http://localhost:8001")  # usuarios
MS2 = os.getenv("MS2_URL", "http://localhost:8002")  # productos
MS3 = os.getenv("MS3_URL", "http://localhost:8003")  # pedidos

REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "5.0"))
TAX_RATE = float(os.getenv("TAX_RATE", "0.18"))
_CORS_ENV = os.getenv("CORS_ALLOWED_ORIGINS", "*").strip()

def _parse_cors(env_val: str):
    if not env_val or env_val == "*":
        return {"allow_origins": ["*"]}
    if env_val.lower().startswith("regex:"):
        # quitar el prefijo y usar allow_origin_regex
        return {"allow_origin_regex": env_val[len("regex:"):]}
    # lista separada por comas
    origins = [o.strip() for o in env_val.split(",") if o.strip()]
    return {"allow_origins": origins or ["*"]}

app = FastAPI(
    title="Orquestador Delivery",
    version="1.0.0",
    description="API Orquestador: cotización, creación y cancelación de pedidos (MS1/MS2/MS3).",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json"
)

_cors_kwargs = _parse_cors(_CORS_ENV)
app.add_middleware(
    CORSMiddleware,
    **_cors_kwargs,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Location"]
)

client: Optional[httpx.AsyncClient] = None

@app.on_event("startup")
async def _startup():
    global client
    client = httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT))

@app.on_event("shutdown")
async def _shutdown():
    global client
    if client:
        await client.aclose()

def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

# ---------- Utilidades robustas ----------
def pick(d: dict, *keys: str, default=None):
    """Devuelve el primer valor presente entre varias posibles claves."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default

def to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return default

def normalize_list(maybe_list_or_wrapper):
    """
    Devuelve SIEMPRE una lista de direcciones.
    - Si ya es lista -> la retorna
    - Si es dict y parece un address único (tiene id_direccion/direccion/id) -> [dict]
    - Si es dict contenedor -> busca la primera lista dentro
    - Caso contrario -> []
    """
    if isinstance(maybe_list_or_wrapper, list):
        return maybe_list_or_wrapper
    if isinstance(maybe_list_or_wrapper, dict):
        # ¿Parece una sola dirección?
        if any(k in maybe_list_or_wrapper for k in ("id_direccion", "direccion_id", "id")) and \
           any(k in maybe_list_or_wrapper for k in ("direccion", "ciudad", "codigo_postal")):
            return [maybe_list_or_wrapper]
        # Buscar una lista dentro de un envoltorio {"data":[...]} | {"direcciones":[...]} | etc.
        for _, val in maybe_list_or_wrapper.items():
            if isinstance(val, list):
                return val
    return []

def address_list_contains(addresses, id_direccion: int) -> bool:
    """
    Acepta varios esquemas:
    - id_direccion
    - direccion_id
    - id
    - o strings numéricos
    """
    for a in addresses:
        addr_id = pick(a, "id_direccion", "direccion_id", "id")
        try:
            if int(addr_id) == int(id_direccion):
                return True
        except Exception:
            continue
    return False

def extract_category_id(product: dict) -> Optional[int]:
    """
    Admite varios esquemas posibles:
    - categoria_id
    - id_categoria
    - category_id / categoriaId
    - categoria: { id: ... }
    """
    cid = pick(product, "categoria_id", "id_categoria", "category_id", "categoriaId")
    if cid is not None:
        try:
            return int(cid)
        except Exception:
            pass
    # anidado
    cat_obj = pick(product, "categoria", "category")
    if isinstance(cat_obj, dict):
        nested_id = pick(cat_obj, "id", "id_categoria", "categoria_id")
        try:
            return int(nested_id)
        except Exception:
            return None
    return None

def extract_category_name(category: dict) -> Optional[str]:
    return pick(category, "nombre_categoria", "categoria_nombre", "nombre", "name")

# ---------- Utilidades de idempotencia y validación ----------
_IDEMP_CACHE: dict[str, dict] = {}

def idem_get(key: Optional[str]):
    return _IDEMP_CACHE.get(key) if key else None

def idem_set(key: Optional[str], value: dict):
    if key:
        _IDEMP_CACHE[key] = value

async def ensure_user_and_address(id_usuario: int, id_direccion: Optional[int]):
    # usuario
    u = await client.get(f"{MS1}/usuarios/{id_usuario}")
    if u.status_code != 200:
        raise HTTPException(404, "Usuario no existe")
    # dirección
    if id_direccion is not None:
        d = await client.get(f"{MS1}/direcciones/{id_usuario}")
        if d.status_code != 200:
            raise HTTPException(400, "No se pudo obtener direcciones del usuario")
        dir_list = normalize_list(d.json())
        if not address_list_contains(dir_list, id_direccion):
            disponibles = []
            for a in dir_list:
                for k in ("id_direccion", "direccion_id", "id"):
                    if k in a:
                        try:
                            disponibles.append(int(a[k]))
                        except Exception:
                            pass
                        break
            raise HTTPException(400, f"Dirección inválida para el usuario. Disponibles: {disponibles}")


# ---------- Modelos mínimos para el body ----------
class CartItem(BaseModel):
    id_producto: int
    cantidad: int = Field(gt=0)
    expected_price: Optional[float] = None

class PriceQuoteReq(BaseModel):
    id_usuario: int
    id_direccion: Optional[int] = None
    items: List[CartItem]

class CreateOrderReq(BaseModel):
    id_usuario: int
    id_direccion: int
    items: List[CartItem]

# endopint del healt
from fastapi import Query

@app.get("/health")
async def healthz(deep: int = Query(default=0, ge=0, le=1)):
    """
    Liveness / Readiness:
    - GET /healthz         -> rápido (no consulta dependencias)
    - GET /healthz?deep=1  -> verifica MS1/MS2/MS3 (best-effort)
    """
    status = {
        "service": "orquestador",
        "time": now_iso(),
        "cors": _CORS_ENV,
        "status": "ok"
    }

    if not deep:
        return status

    async def check(url: str):
        try:
            r = await client.get(url)
            return {"url": url, "status": r.status_code}
        except Exception as e:
            return {"url": url, "error": str(e)}

    # Endpoints ligeros y reales de tus MS
    checks = await asyncio.gather(
        check(f"{MS1}/usuarios/1"),   # cambia el ID si lo necesitas
        check(f"{MS2}/productos"),
        check(f"{MS3}/pedidos")
    )

    status["dependencies"] = {
        "ms1_usuarios": checks[0],
        "ms2_productos": checks[1],
        "ms3_pedidos":   checks[2]
    }
    all_ok = all(c.get("status") == 200 for c in checks)
    status["status"] = "ready" if all_ok else "degraded"
    return status
# ---------- Endpoint ÚNICO: /orq/cart/price-quote ----------
@app.post("/orq/cart/price-quote")
async def price_quote(payload: PriceQuoteReq):
    # 1) Validar usuario
    u = await client.get(f"{MS1}/usuarios/{payload.id_usuario}")
    if u.status_code != 200:
        raise HTTPException(404, "Usuario no existe")

    # 2) Validar direccion si viene
    if payload.id_direccion is not None:
        d = await client.get(f"{MS1}/direcciones/{payload.id_usuario}")
        if d.status_code != 200:
            raise HTTPException(400, "No se pudo obtener direcciones del usuario")
        dir_list = normalize_list(d.json())
        if not address_list_contains(dir_list, payload.id_direccion):
            # En vez de romper, devolvemos error claro (lo que ya viste)
            raise HTTPException(400, "Dirección inválida para el usuario")

    # 3) Traer productos en paralelo
    async def fetch(prod_id: int):
        return await client.get(f"{MS2}/productos/{prod_id}")

    tasks = [fetch(i.id_producto) for i in payload.items]
    responses = await asyncio.gather(*tasks)

    quote_items = []
    issues = []
    subtotal = 0.0

    for req_item, resp in zip(payload.items, responses):
        if resp.status_code != 200:
            issues.append({"id_producto": req_item.id_producto, "reason": "NOT_FOUND"})
            continue
        prod = resp.json()
        nombre = pick(prod, "nombre", "name", default=f"producto:{req_item.id_producto}")
        precio_unit = to_float(pick(prod, "precio", "price", "valor", default=0.0))
        line_total = round(precio_unit * req_item.cantidad, 2)
        subtotal += line_total

        # detectar drift si el cliente mandó expected_price
        price_changed = (
            req_item.expected_price is not None and
            abs(precio_unit - float(req_item.expected_price)) > 1e-6
        )
        if price_changed:
            issues.append({"id_producto": req_item.id_producto, "reason": "PRICE_CHANGED"})

        quote_items.append({
            "id_producto": req_item.id_producto,
            "nombre": nombre,
            "precio_unitario": precio_unit,
            "cantidad": req_item.cantidad,
            "line_total": line_total,
            # los completamos luego:
            "categoria_id": None,
            "categoria_nombre": None,
            "price_changed": price_changed,
        })

    # 4) Intentar enriquecer con categoría (best-effort, no rompe si falta)
    #    a) primero intentamos sacar categoria_id de cada producto (si no lo tiene, quedará None)
    #    b) luego pedimos /categorias y hacemos map id->nombre admitiendo distintos campos
    try:
        # Re-fetch productos (ligero) solo para sacar categoria_id de forma robusta
        # (si ya lo tienes con seguridad, puedes evitar este paso)
        for qi in quote_items:
            r = await client.get(f"{MS2}/productos/{qi['id_producto']}")
            if r.status_code == 200:
                prod = r.json()
                cid = extract_category_id(prod)
                if cid is not None:
                    qi["categoria_id"] = cid

        cats = await client.get(f"{MS2}/categorias")
        cat_map = {}
        if cats.status_code == 200 and isinstance(cats.json(), list):
            for c in cats.json():
                # claves posibles de id de categoría
                cat_id = pick(c, "id_categoria", "categoria_id", "id", "category_id")
                try:
                    cat_id = int(cat_id)
                except Exception:
                    continue
                cat_map[cat_id] = extract_category_name(c)
        # asignar nombre si tenemos id y nombre
        for qi in quote_items:
            if qi["categoria_id"] in cat_map:
                qi["categoria_nombre"] = cat_map[qi["categoria_id"]]
    except Exception:
        # no frenamos la cotización si /categorias u otros esquemas difieren
        pass

    taxes = round(subtotal * TAX_RATE, 2)
    total = round(subtotal + taxes, 2)

    return {
        "generatedAt": now_iso(),
        "items": quote_items,
        "issues": issues,  # NOT_FOUND o PRICE_CHANGED si aplica
        "totals": {"subtotal": round(subtotal, 2), "taxes": taxes, "total": total}
    }
from fastapi import Header

from typing import Tuple

def _maybe_oid(v):
    # acepta {"$oid":"..."} de Mongo o directos
    if isinstance(v, dict):
        if "$oid" in v and isinstance(v["$oid"], str):
            return v["$oid"]
    if isinstance(v, (str, int)):
        return str(v)
    return None

def extract_order_id(obj) -> str | None:
    """
    Busca el ID en varios formatos y también en anidaciones típicas: pedido, data, result, etc.
    """
    if obj is None:
        return None
    # 1) planos frecuentes
    for k in ["_id", "id", "order_id", "id_pedido", "pedido_id", "pedidoId",
              "inserted_id", "insertedId", "created_id", "createdId"]:
        if isinstance(obj, dict) and k in obj:
            oid = _maybe_oid(obj[k])
            if oid:
                return oid
    # 2) anidados comunes
    if isinstance(obj, dict):
        for nest in ["pedido", "data", "result", "payload"]:
            if nest in obj and isinstance(obj[nest], dict):
                nid = extract_order_id(obj[nest])
                if nid:
                    return nid
    # 3) arrays (a veces devuelven lista con el creado)
    if isinstance(obj, list) and obj:
        return extract_order_id(obj[0])
    return None

def extract_order_id_from_location(headers: dict) -> str | None:
    # ej: Location: /pedidos/6520abc123...
    loc = headers.get("Location") or headers.get("location")
    if not loc:
        return None
    parts = [p for p in str(loc).split("/") if p]
    return parts[-1] if parts else None

# ---------- Helper: escribir historial con rutas alternativas (u omitir si no existe) ----------
async def write_history(order_id: str, estado: str, comentarios: str) -> tuple[bool, str]:
    """
    Intenta varias rutas/payloads típicas para MS3. Si todas devuelven 404, lo trata como 'no soportado' y no falla.
    Devuelve (ok, msg). ok=True si lo logró o si no está soportado; ok=False solo si MS3 sí tiene historial pero falló.
    """
    # posibles rutas
    paths = [
        f"{MS3}/historial",
        f"{MS3}/pedidos/{order_id}/historial",
        f"{MS3}/historial/{order_id}",
    ]
    # posibles payloads
    payloads = [
        {"id_pedido": order_id, "estado": estado, "comentarios": comentarios},
        {"idPedido": order_id, "estado": estado, "comentarios": comentarios},
        {"pedido_id": order_id, "estado": estado, "comentarios": comentarios},
        {"estado": estado, "comentarios": comentarios},  # por si la ruta ya infiere el id
    ]

    last_status = None
    last_body = None
    saw_404 = True  # asumimos 'no soportado' hasta ver algo distinto a 404

    for path in paths:
        for body in payloads:
            try:
                r = await client.post(path, json=body)
            except Exception as e:
                last_status = "EXC"
                last_body = repr(e)
                continue

            last_status = r.status_code
            try:
                last_body = r.json()
            except Exception:
                try:
                    last_body = (await r.aread()).decode()
                except Exception:
                    last_body = str(r)

            if r.status_code in (200, 201):
                return True, f"historial ok via {path}"
            if r.status_code != 404:
                saw_404 = False  # MS3 sí tiene algo en esa ruta, pero falló (4xx/5xx distinto a 404)

    # Si solo vimos 404: historial no existe en MS3 => lo tratamos como opcional
    if saw_404:
        return True, "historial no soportado en MS3 (404 en todas las rutas)"

    # Si hubo rutas que respondieron distinto a 404 y ninguna funcionó => devolver False (fallo real)
    return False, f"fallo historial MS3 (último status {last_status}, body={last_body})"

@app.post("/orq/orders", status_code=201)
async def create_order(payload: CreateOrderReq, Idempotency_Key: Optional[str] = Header(default=None)):
    # Idempotencia
    cached = idem_get(Idempotency_Key)
    if cached:
        return cached

    # Validaciones base (usuario + dirección perteneciente)
    await ensure_user_and_address(payload.id_usuario, payload.id_direccion)

    # 1) Revalidar precios en MS2 (autoritativo)
    async def fetch(prod_id: int):
        return await client.get(f"{MS2}/productos/{prod_id}")

    tasks = [fetch(i.id_producto) for i in payload.items]
    responses = await asyncio.gather(*tasks)

    lineas, subtotal = [], 0.0
    for req_item, resp in zip(payload.items, responses):
        if resp.status_code != 200:
            raise HTTPException(400, f"Producto {req_item.id_producto} inválido")
        prod = resp.json()
        precio = to_float(pick(prod, "precio", "price", "valor", default=0.0))
        lineas.append({
            "id_producto": req_item.id_producto,
            "cantidad": req_item.cantidad,
            "precio_unitario": precio
        })
        subtotal += precio * req_item.cantidad

    taxes = round(subtotal * TAX_RATE, 2)
    total = round(subtotal + taxes, 2)

    pedido_payload = {
        "id_usuario": payload.id_usuario,
        "fecha_pedido": now_iso(),
        "estado": "pendiente",
        "total": total,
        "productos": lineas
    }

    # 2) Crear pedido en MS3 (acepta 200 o 201)
    create = await client.post(f"{MS3}/pedidos", json=pedido_payload)
    if create.status_code not in (200, 201):
        raise HTTPException(502, f"No se pudo crear el pedido (MS3 status {create.status_code})")

    # Intentar leer el body; si no, queda en None
    try:
        pedido_obj = create.json()
    except Exception:
        pedido_obj = None

    # 2.1) Extraer order_id del body o del header Location
    order_id = extract_order_id(pedido_obj)
    if not order_id:
        order_id = extract_order_id_from_location(create.headers)

    if not order_id:
        raise HTTPException(502, "MS3 no devolvió el id del pedido")

    # 3) === HISTORIAL (AQUÍ VA EL BLOQUE QUE TE CONFUNDÍA) ===
    ok_hist, hist_msg = await write_history(order_id, "pendiente", "Pedido creado vía orquestador")
    if not ok_hist:
        # NO hacemos rollback: dejamos creado el pedido y avisamos
        return {
            "orderId": order_id,
            "estado": "pendiente",
            "totals": {"subtotal": round(subtotal, 2), "taxes": taxes, "total": total},
            "lineas": lineas,
            "direccion_entrega_id": payload.id_direccion,
            "createdAt": now_iso(),
            "warnings": [hist_msg]
        }

    # 4) Respuesta final del orquestador
    response = {
        "orderId": order_id,
        "estado": "pendiente",
        "totals": {"subtotal": round(subtotal, 2), "taxes": taxes, "total": total},
        "lineas": lineas,
        "direccion_entrega_id": payload.id_direccion,
        "createdAt": now_iso()
    }
    idem_set(Idempotency_Key, response)
    return response

@app.put("/orq/orders/{order_id}/cancel")
async def cancel_order(order_id: str, id_usuario: int):
    # Obtener pedido
    r = await client.get(f"{MS3}/pedidos/{order_id}")
    if r.status_code != 200:
        raise HTTPException(404, "Pedido no existe")
    pedido = r.json()

    # Verificar dueño (esquemas flexibles)
    pedido_user = pick(pedido, "id_usuario", "usuario_id", "user_id")
    if pedido_user is None or int(pedido_user) != int(id_usuario):
        raise HTTPException(403, "No autorizado")

    estado_anterior = pick(pedido, "estado", "status", default="pendiente")

    # Cambiar estado
    upd = await client.put(f"{MS3}/pedidos/{order_id}", json={"estado": "cancelado"})
    if upd.status_code != 200:
        raise HTTPException(502, "No se pudo cancelar el pedido")

    # Historial; revertir si falla
    ok_hist, hist_msg = await write_history(order_id, "cancelado", "Cancelación solicitada por el usuario")
    if ok_hist:
        return {"orderId": order_id, "estado": "cancelado"}
    # Si el historial “existía” pero falló, devolvemos 200 igualmente y avisamos con warning
    return {"orderId": order_id, "estado": "cancelado", "warnings": [hist_msg]}


@app.get("/orq/_debug/addresses/{id_usuario}")
async def debug_addresses(id_usuario: int):
    r = await client.get(f"{MS1}/direcciones/{id_usuario}")
    try:
        raw = r.json()
    except Exception:
        raw = r.text
    return {"status": r.status_code, "raw": raw, "normalized": normalize_list(raw)}
