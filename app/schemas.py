from pydantic import BaseModel, Field
from typing import List, Optional

class CartItem(BaseModel):
    id_producto: int
    cantidad: int = Field(gt=0)
    expected_price: Optional[float] = None  # optional price guard from UI

class PriceQuoteReq(BaseModel):
    id_usuario: int
    id_direccion: Optional[int] = None
    items: List[CartItem]

class CreateOrderReq(BaseModel):
    id_usuario: int
    id_direccion: int
    items: List[CartItem]  # only id & cantidad matter; prices are re-fetched

class AddressReq(BaseModel):
    direccion: str
    ciudad: str
    codigo_postal: str
