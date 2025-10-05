
# Orquestador Delivery (FastAPI)

Orquesta microservicios de **Usuarios (MS1)**, **Productos (MS2)** y **Pedidos (MS3)** para:

* **Cotización de carrito** (valida usuario/dirección y recalcula precios contra MS2).
* **Detalle enriquecido de pedido** (trae pedido de MS3 y lo enriquece con info actual de MS2 y resumen de MS1).

Incluye:

* **CORS configurable** vía variable de entorno.
* **Health check** simple y profundo.

---

## Arquitectura y dependencias

* **Python**: 3.11+
* **Framework**: FastAPI + Uvicorn
* **HTTP client**: httpx

Microservicios consumidos:

* **MS1 (Usuarios)**: `GET /usuarios/{id}`, `GET /direcciones/{id_usuario}`
* **MS2 (Productos)**: `GET /productos/{id}`, `GET /categorias`, `GET /productos`
* **MS3 (Pedidos)**: `GET /pedidos/{order_id}`, `GET /pedidos`

---

## Endpoints

### 1) Cotización de carrito

`POST /orq/cart/price-quote`

Orquesta **MS1 + MS2**:

* Valida que el usuario exista (MS1).
* (Opcional) Valida que la dirección pertenezca al usuario (MS1).
* Consulta cada producto al MS2 para obtener **precio vigente** (no confía en el cliente).
* Calcula `subtotal`, `taxes` y `total` (usa `TAX_RATE`).

**Body (ejemplo)**

```json
{
  "id_usuario": 1,
  "id_direccion": 1,
  "items": [
    {"id_producto": 1, "cantidad": 2}
  ]
}
```

**Respuesta (ejemplo)**

```json
{
  "generatedAt": "2025-09-30T23:03:07Z",
  "items": [
    {
      "id_producto": 1,
      "nombre": "producto_1",
      "precio_unitario": 473.58,
      "cantidad": 2,
      "line_total": 947.16,
      "categoria_id": null,
      "categoria_nombre": null,
      "price_changed": false
    }
  ],
  "issues": [],
  "totals": {
    "subtotal": 947.16,
    "taxes": 170.49,
    "total": 1117.65
  }
}
```

---

### 2) Detalle enriquecido de pedido

`GET /orq/orders/{order_id}/details?id_usuario=...`

Orquesta **MS3 + MS2 + MS1**:

* Trae el pedido (MS3) y verifica que **pertenezca** a `id_usuario`.
* En cada línea, trae el producto actual (MS2) y marca si **cambió el precio** desde que se creó el pedido.
* Mapea categoría (MS2) y agrega un **resumen del usuario** (MS1) incluyendo cantidad de direcciones.

**Ejemplo**

```
GET /orq/orders/68dc67973081efedbf717c7d/details?id_usuario=1
```

**Respuesta (recortada)**

```json
{
  "orderId": "68dc67973081efedbf717c7d",
  "estado": "pendiente",
  "fecha_pedido": "2025-09-30T23:28:23.441Z",
  "user": {
    "id_usuario": 1,
    "nombre": "Juan",
    "correo": "juan@acme.com",
    "telefono": "999999999",
    "direcciones_count": 1
  },
  "lines": [
    {
      "id_producto": 1,
      "nombre": "producto_1",
      "cantidad": 1,
      "precio_unitario_ms3": 100,
      "line_total_ms3": 100,
      "current_price_ms2": 120,
      "price_changed_since_order": true,
      "categoria_id": 5,
      "categoria_nombre": "Electrónica"
    }
  ],
  "issues": [
    {"id_producto": 1, "reason": "PRICE_CHANGED_SINCE_ORDER"}
  ],
  "totals": {
    "total_ms3": 100,
    "recomputed_subtotal_ms3": 100,
    "taxes_estimated": 18,
    "total_estimated": 118
  }
}
```

## ¿Qué hace exactamente, paso a paso?

1. **Lee el pedido en MS3**

   * Llama a `MS3 /pedidos/{order_id}`.
   * Si no existe → **404**.
   * Extrae: `estado`, `fecha_pedido`, `total` y las líneas (`productos` con `id_producto`, `cantidad`, `precio_unitario` guardado en el pedido).

2. **Verifica que el pedido sea del usuario**

   * Compara el `id_usuario` del pedido (MS3) con el `id_usuario` enviado en el querystring.
   * Si no coincide → **403** (“No autorizado”).

   > Esto evita que un usuario vea pedidos ajenos.

3. **Enriquece cada línea del pedido con datos actuales de MS2**

   * Por cada `id_producto` del pedido, consulta `MS2 /productos/{id}` en paralelo.
   * Añade a cada línea:

     * `nombre` del producto (actual)
     * `current_price_ms2` (precio vigente en el catálogo)
     * `categoria_id` y `categoria_nombre` (usando `MS2 /categorias`, best-effort)
   * Si el precio de catálogo **cambió** respecto al que se guardó en el pedido, marca `price_changed_since_order: true` y agrega un issue `PRICE_CHANGED_SINCE_ORDER`.

4. **Adjunta un resumen del usuario desde MS1**

   * `MS1 /usuarios/{id_usuario}` → añade `nombre`, `correo`, `telefono`.
   * `MS1 /direcciones/{id_usuario}` → cuenta cuántas direcciones tiene (`direcciones_count`).

   > No expone datos sensibles; solo un resumen útil para interfaz/boleta.

5. **Calcula totales estimados** (con tu `TAX_RATE`)

   * Recalcula `recomputed_subtotal_ms3` (usando las líneas que venían del pedido).
   * Calcula `taxes_estimated` y `total_estimated`.
   * Si difiere del `total` que guardó MS3, agrega un issue `TOTAL_MISMATCH` (sirve para auditoría/UI).

## ¿Qué devuelve?

Un JSON con:

* `orderId`, `estado`, `fecha_pedido`
* `user` (resumen de MS1)
* `lines` (cada línea con datos de MS3 **y** enriquecimiento de MS2)
* `issues` (alertas como `PRODUCT_NOT_FOUND`, `PRICE_CHANGED_SINCE_ORDER`, `TOTAL_MISMATCH`)
* `totals` (total original de MS3 vs estimado con impuestos actuales)

---

### 3) Health check

`GET /health`
`GET /health?deep=1` → consulta MS1/MS2/MS3 (best-effort)

**Ejemplo**

```json
{
  "service": "orquestador",
  "time": "2025-09-30T23:59:59Z",
  "cors": "*",
  "status": "ready",
  "dependencies": {
    "ms1_usuarios": {"url": "http://ms1-usuarios:8000/usuarios/1", "status": 200},
    "ms2_productos": {"url": "http://ms2-productos:8080/productos", "status": 200},
    "ms3_pedidos": {"url": "http://ms3-pedidos:3003/pedidos", "status": 200}
  }
}
```

---

### 4) (Opcional) Debug direcciones

`GET /orq/_debug/addresses/{id_usuario}`
Devuelve crudo lo que responde MS1 y cómo se normaliza.

---

## **Qué debes eliminar** para quedarte solo con los 2 endpoints

En tu `main.py`, **borra** (si aún existen):

* `@app.post("/orq/orders")` (crear pedido)
* `@app.put("/orq/orders/{order_id}/cancel")` (cancelar pedido)
* Helpers que **solo** usaban esos endpoints:

  * `extract_order_id`, `extract_order_id_from_location`
  * `write_history`
  * Cache de idempotencia (`_IDEMP_CACHE`, `idem_get`, `idem_set`)
* Imports asociados a lo anterior si ya no se usan:

  * `from fastapi import Header` (si no queda ningún uso)
  * `from app.schemas import CreateOrderReq` (si tienes la clase `CreateOrderReq` definida local y no usas un archivo externo)

**Mantén**:

* `ensure_user_and_address`, `pick`, `to_float`, `normalize_list`, `extract_category_id`, `extract_category_name`
* Config/CORS/health
* `POST /orq/cart/price-quote`
* `GET /orq/orders/{order_id}/details`

> Si quieres un orquestador **solo-lectura**, puedes incluso borrar la clase `CreateOrderReq`.
> Si más adelante reactivas creación/cancelación, la vuelves a usar.

---

## Variables de entorno

| Variable               | Descripción                                      | Default                 |
| ---------------------- | ------------------------------------------------ | ----------------------- |
| `MS1_URL`              | Base URL de usuarios                             | `http://localhost:8001` |
| `MS2_URL`              | Base URL de productos                            | `http://localhost:8002` |
| `MS3_URL`              | Base URL de pedidos                              | `http://localhost:8003` |
| `REQUEST_TIMEOUT`      | Timeout (s) para httpx                           | `5.0`                   |
| `TAX_RATE`             | Impuesto aplicado en cotización                  | `0.18`                  |
| `CORS_ALLOWED_ORIGINS` | `*`, lista separada por comas o `regex:^patrón$` | `*`                     |

**Ejemplos de `CORS_ALLOWED_ORIGINS`**

* `*`
* `http://localhost:3000,http://localhost:5173`
* `regex:^https://.*\.tu-dominio\.com$`

---

## Correr local

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8004
```

Swagger: `http://localhost:8004/docs`
Redoc: `http://localhost:8004/redoc`

---

## Docker

**Dockerfile** (ya lo tienes):

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 8004
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8004"]
```

**Build & run** (si MS1/2/3 corren en tu host):

```bash
docker build -t jdrosales6/ms4-orquestador:1.0.2 -t jdrosales6/ms4-orquestador:latest .
docker run --rm -p 8004:8004 \
  --add-host=host.docker.internal:host-gateway \
  -e MS1_URL=http://host.docker.internal:8001 \
  -e MS2_URL=http://host.docker.internal:8002 \
  -e MS3_URL=http://host.docker.internal:8003 \
  -e CORS_ALLOWED_ORIGINS="*" \
  jdrosales6/ms4-orquestador:1.0.2
```

**docker-compose** junto a tus MS (misma red):

```yaml
services:
  ms4-orquestador:
    image: jdrosales6/ms4-orquestador:1.0.2
    container_name: ms4-orquestador
    restart: unless-stopped
    ports:
      - "8004:8004"
    environment:
      MS1_URL: "http://ms1-usuarios:8000"
      MS2_URL: "http://ms2-productos:8080"
      MS3_URL: "http://ms3-pedidos:3003"
      REQUEST_TIMEOUT: "5.0"
      TAX_RATE: "0.18"
      CORS_ALLOWED_ORIGINS: ${GLOBAL_CORS}
    networks:
      - backend
    depends_on:
      - ms1-usuarios
      - ms2-productos
      - ms3-pedidos
```

`.env`:

```
GLOBAL_CORS=*
```

---

