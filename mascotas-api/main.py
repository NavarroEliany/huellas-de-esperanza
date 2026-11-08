import os
from enum import Enum
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import Client, create_client

load_dotenv()

supabase: Client = create_client(
    os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"]
)
TABLA = "mascotas"

app = FastAPI(
    title="API Mascotas - Huellas de Esperanza",
    version="1.0.0",
    description=(
        "Servicio que administra el registro, la consulta y la disponibilidad "
        "de las mascotas. La API de Adopciones lo consulta por HTTP para "
        "verificar disponibilidad y actualizar el estado."
    ),
)

# En producción pon aquí la URL real del frontend, separada por comas.
origenes = os.getenv("CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origenes,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Esquemas ----------
class Estado(str, Enum):
    disponible = "disponible"
    en_proceso = "en_proceso"
    adoptada = "adoptada"


class Especie(str, Enum):
    perro = "perro"
    gato = "gato"
    otro = "otro"


class MascotaCreate(BaseModel):
    nombre: str = Field(min_length=1, max_length=80, examples=["Canela"])
    especie: Especie
    raza: Optional[str] = Field(default=None, max_length=80)
    edad_meses: int = Field(ge=0, le=360, examples=[18])
    descripcion: Optional[str] = None
    foto_url: Optional[str] = None


class MascotaUpdate(BaseModel):
    nombre: Optional[str] = Field(default=None, min_length=1, max_length=80)
    especie: Optional[Especie] = None
    raza: Optional[str] = None
    edad_meses: Optional[int] = Field(default=None, ge=0, le=360)
    descripcion: Optional[str] = None
    foto_url: Optional[str] = None


class Mascota(MascotaCreate):
    id: int
    estado: Estado


class EstadoUpdate(BaseModel):
    estado: Estado


# ---------- Utilidades ----------
def obtener_o_404(mascota_id: int) -> dict:
    res = supabase.table(TABLA).select("*").eq("id", mascota_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Mascota no encontrada")
    return res.data[0]


# ---------- Endpoints ----------
@app.get("/", tags=["Salud"])
def raiz():
    return {"servicio": "mascotas", "estado": "ok"}


@app.post("/mascotas", response_model=Mascota, status_code=201, tags=["Mascotas"])
def crear_mascota(datos: MascotaCreate):
    """Registra una mascota nueva. Siempre nace con estado `disponible`."""
    res = supabase.table(TABLA).insert(datos.model_dump(mode="json")).execute()
    return res.data[0]


@app.get("/mascotas", response_model=list[Mascota], tags=["Mascotas"])
def listar_mascotas(
    estado: Optional[Estado] = Query(default=None, description="Filtra por estado"),
    especie: Optional[Especie] = None,
):
    consulta = supabase.table(TABLA).select("*").order("id")
    if estado:
        consulta = consulta.eq("estado", estado.value)
    if especie:
        consulta = consulta.eq("especie", especie.value)
    return consulta.execute().data


@app.get("/mascotas/{mascota_id}", response_model=Mascota, tags=["Mascotas"])
def consultar_mascota(mascota_id: int):
    """La API de Adopciones usa este endpoint para verificar disponibilidad."""
    return obtener_o_404(mascota_id)


@app.put("/mascotas/{mascota_id}", response_model=Mascota, tags=["Mascotas"])
def actualizar_mascota(mascota_id: int, datos: MascotaUpdate):
    obtener_o_404(mascota_id)
    cambios = datos.model_dump(mode="json", exclude_unset=True)
    if not cambios:
        raise HTTPException(status_code=400, detail="No enviaste ningún campo")
    res = supabase.table(TABLA).update(cambios).eq("id", mascota_id).execute()
    return res.data[0]


@app.patch(
    "/mascotas/{mascota_id}/estado", response_model=Mascota, tags=["Disponibilidad"]
)
def cambiar_estado(mascota_id: int, datos: EstadoUpdate):
    """Cambia el estado de una mascota. Una mascota adoptada no vuelve a
    `disponible` ni a `en_proceso` por esta vía."""
    actual = obtener_o_404(mascota_id)
    if actual["estado"] == "adoptada" and datos.estado != Estado.adoptada:
        raise HTTPException(
            status_code=409, detail="La mascota ya fue adoptada y no puede cambiar de estado"
        )
    res = (
        supabase.table(TABLA)
        .update({"estado": datos.estado.value})
        .eq("id", mascota_id)
        .execute()
    )
    return res.data[0]


@app.delete("/mascotas/{mascota_id}", status_code=204, tags=["Mascotas"])
def eliminar_mascota(mascota_id: int):
    actual = obtener_o_404(mascota_id)
    if actual["estado"] == "adoptada":
        raise HTTPException(status_code=409, detail="No se elimina una mascota adoptada")
    supabase.table(TABLA).delete().eq("id", mascota_id).execute()
