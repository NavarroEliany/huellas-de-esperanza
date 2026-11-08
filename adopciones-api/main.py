import os
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import Client, create_client

load_dotenv()

supabase: Client = create_client(
    os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"]
)
MASCOTAS_URL = os.environ["MASCOTAS_API_URL"].rstrip("/")
TABLA = "solicitudes"

app = FastAPI(
    title="API Adopciones - Huellas de Esperanza",
    version="1.0.0",
    description=(
        "Servicio que registra y gestiona las solicitudes de adopción. "
        "Se comunica por HTTP con la API de Mascotas para verificar la "
        "disponibilidad y para marcar la mascota como adoptada."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.getenv("CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------- Esquemas ----------
class EstadoSolicitud(str, Enum):
    pendiente = "pendiente"
    aprobada = "aprobada"
    rechazada = "rechazada"


class SolicitudCreate(BaseModel):
    mascota_id: int = Field(gt=0, examples=[1])
    nombre_solicitante: str = Field(min_length=2, max_length=120, examples=["Laura Sanjuán"])
    correo: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", examples=["laura@correo.com"])
    telefono: Optional[str] = Field(default=None, max_length=30)
    motivo: Optional[str] = Field(default=None, max_length=1000)


class Solicitud(SolicitudCreate):
    id: int
    estado: EstadoSolicitud
    created_at: datetime
    fecha_decision: Optional[datetime] = None


# ---------- Comunicación con la API de Mascotas ----------
def consultar_mascota(mascota_id: int) -> dict:
    """GET /mascotas/{id} en el otro servicio."""
    try:
        r = httpx.get(f"{MASCOTAS_URL}/mascotas/{mascota_id}", timeout=10)
    except httpx.RequestError:
        raise HTTPException(
            status_code=503, detail="No se pudo contactar la API de Mascotas"
        )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="La mascota no existe")
    if r.status_code != 200:
        raise HTTPException(
            status_code=502, detail=f"La API de Mascotas respondió {r.status_code}"
        )
    return r.json()


def marcar_mascota_adoptada(mascota_id: int) -> None:
    """PATCH /mascotas/{id}/estado en el otro servicio."""
    try:
        r = httpx.patch(
            f"{MASCOTAS_URL}/mascotas/{mascota_id}/estado",
            json={"estado": "adoptada"},
            timeout=10,
        )
    except httpx.RequestError:
        raise HTTPException(
            status_code=503, detail="No se pudo contactar la API de Mascotas"
        )
    if r.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"No se pudo actualizar la mascota (API Mascotas respondió {r.status_code})",
        )


# ---------- Utilidades ----------
def ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def obtener_solicitud_o_404(solicitud_id: int) -> dict:
    res = supabase.table(TABLA).select("*").eq("id", solicitud_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Solicitud no encontrada")
    return res.data[0]


# ---------- Endpoints ----------
@app.get("/", tags=["Salud"])
def raiz():
    return {"servicio": "adopciones", "estado": "ok", "mascotas_api": MASCOTAS_URL}


@app.post("/solicitudes", response_model=Solicitud, status_code=201, tags=["Solicitudes"])
def crear_solicitud(datos: SolicitudCreate):
    """Registra una solicitud. Antes consulta a la API de Mascotas y solo
    la guarda si la mascota existe y está disponible."""
    mascota = consultar_mascota(datos.mascota_id)
    if mascota["estado"] != "disponible":
        raise HTTPException(
            status_code=409,
            detail=f"La mascota no está disponible (estado actual: {mascota['estado']})",
        )
    res = supabase.table(TABLA).insert(datos.model_dump(mode="json")).execute()
    return res.data[0]


@app.get("/solicitudes", response_model=list[Solicitud], tags=["Solicitudes"])
def listar_solicitudes(
    estado: Optional[EstadoSolicitud] = Query(default=None),
    mascota_id: Optional[int] = None,
):
    consulta = supabase.table(TABLA).select("*").order("id")
    if estado:
        consulta = consulta.eq("estado", estado.value)
    if mascota_id:
        consulta = consulta.eq("mascota_id", mascota_id)
    return consulta.execute().data


@app.get("/solicitudes/{solicitud_id}", response_model=Solicitud, tags=["Solicitudes"])
def consultar_solicitud(solicitud_id: int):
    return obtener_solicitud_o_404(solicitud_id)


@app.patch(
    "/solicitudes/{solicitud_id}/aprobar", response_model=Solicitud, tags=["Decisión"]
)
def aprobar_solicitud(solicitud_id: int):
    """Aprueba una solicitud pendiente.

    Orden de los pasos:
    1. Verifica con la API de Mascotas que sigue disponible.
    2. Marca la solicitud como aprobada. El índice único de Supabase rechaza
       una segunda aprobación para la misma mascota (error 23505 -> 409).
    3. Le pide a la API de Mascotas que la marque como adoptada. Si eso falla,
       se devuelve la solicitud a pendiente para no dejar datos a medias.
    4. Rechaza las demás solicitudes pendientes de esa mascota.
    """
    sol = obtener_solicitud_o_404(solicitud_id)
    if sol["estado"] != "pendiente":
        raise HTTPException(
            status_code=409, detail=f"La solicitud ya está {sol['estado']}"
        )

    mascota = consultar_mascota(sol["mascota_id"])
    if mascota["estado"] != "disponible":
        raise HTTPException(
            status_code=409,
            detail=f"La mascota ya no está disponible (estado actual: {mascota['estado']})",
        )

    try:
        res = (
            supabase.table(TABLA)
            .update({"estado": "aprobada", "fecha_decision": ahora()})
            .eq("id", solicitud_id)
            .execute()
        )
    except Exception as e:
        if getattr(e, "code", None) == "23505":
            raise HTTPException(
                status_code=409,
                detail="Esa mascota ya tiene una adopción aprobada",
            )
        raise

    try:
        marcar_mascota_adoptada(sol["mascota_id"])
    except HTTPException:
        supabase.table(TABLA).update(
            {"estado": "pendiente", "fecha_decision": None}
        ).eq("id", solicitud_id).execute()
        raise

    supabase.table(TABLA).update(
        {"estado": "rechazada", "fecha_decision": ahora()}
    ).eq("mascota_id", sol["mascota_id"]).eq("estado", "pendiente").execute()

    return res.data[0]


@app.patch(
    "/solicitudes/{solicitud_id}/rechazar", response_model=Solicitud, tags=["Decisión"]
)
def rechazar_solicitud(solicitud_id: int):
    sol = obtener_solicitud_o_404(solicitud_id)
    if sol["estado"] != "pendiente":
        raise HTTPException(
            status_code=409, detail=f"La solicitud ya está {sol['estado']}"
        )
    res = (
        supabase.table(TABLA)
        .update({"estado": "rechazada", "fecha_decision": ahora()})
        .eq("id", solicitud_id)
        .execute()
    )
    return res.data[0]
