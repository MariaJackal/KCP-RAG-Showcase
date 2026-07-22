"""Persona routes: list available personas."""

from fastapi import APIRouter, Depends

from api.auth import get_current_user
from api.schemas import PersonaOut
from personas import PERSONAS

router = APIRouter(prefix="/personas", tags=["personas"])


@router.get("", response_model=list[PersonaOut])
async def list_personas(_user: dict = Depends(get_current_user)):
    return [
        PersonaOut(id=p.id, display_name=p.display_name, icon=p.icon)
        for p in PERSONAS.values()
    ]
