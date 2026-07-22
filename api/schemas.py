"""Pydantic request / response schemas for the API."""

from pydantic import BaseModel, Field


class LoginResponse(BaseModel):
    token: str
    role: str  # "user" | "admin"


class AskRequest(BaseModel):
    question: str = Field(..., max_length=4000)


class PresetRequest(BaseModel):
    preset_id: str


class CreateConversationRequest(BaseModel):
    persona_id: str = "traffic"


class PatchPersonaRequest(BaseModel):
    persona_id: str


class ConversationSummary(BaseModel):
    id: str
    title: str
    persona_id: str
    created_at: str


class MessageOut(BaseModel):
    role: str
    content: str
    timestamp: str
    citations: list = []
    rating: str = ""


class PersonaOut(BaseModel):
    id: str
    display_name: str
    icon: str
