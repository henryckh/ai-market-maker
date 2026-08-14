"""API for the outside-graph Config Designer agent.

Chat to design / review deploy JSON (agents, weights, decision_threshold, LLM flags)
without running the trading graph.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from agents.config_designer import STYLE_SEEDS, ConfigDesignerAgent, ConfigDesignerTurn

router = APIRouter(tags=["config-designer"])
_agent = ConfigDesignerAgent()


class ChatMessage(BaseModel):
    role: str = Field(description="user | assistant")
    content: str


class ConfigDesignerChatRequest(BaseModel):
    message: str
    history: list[ChatMessage] = Field(default_factory=list)
    current_config: dict[str, Any] | None = None


class ConfigDesignerChatResponse(BaseModel):
    reply: str
    draft_config: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    style: str | None = None


class StyleRequest(BaseModel):
    style: str = Field(description="macro_tilt | ohlcv_measurement | conservative | ...")


@router.get("/config-designer/styles")
def list_styles() -> dict[str, Any]:
    return {
        "styles": sorted(STYLE_SEEDS.keys()),
        "hint": 'POST /config-designer/style with {"style": "macro_tilt"} or chat with \'style: macro_tilt\'',
    }


@router.post("/config-designer/style", response_model=ConfigDesignerChatResponse)
def seed_style(req: StyleRequest) -> ConfigDesignerChatResponse:
    result = _agent.seed_style(req.style)
    return ConfigDesignerChatResponse(
        reply=result.reply,
        draft_config=result.draft_config,
        warnings=result.warnings,
        style=result.style,
    )


@router.post("/config-designer/review", response_model=ConfigDesignerChatResponse)
def review_config(body: dict[str, Any]) -> ConfigDesignerChatResponse:
    """Review a full deploy JSON body."""
    result = _agent.review(body)
    return ConfigDesignerChatResponse(
        reply=result.reply,
        draft_config=result.draft_config,
        warnings=result.warnings,
    )


@router.post("/config-designer/chat", response_model=ConfigDesignerChatResponse)
def chat_design(req: ConfigDesignerChatRequest) -> ConfigDesignerChatResponse:
    history = [ConfigDesignerTurn(role=m.role, content=m.content) for m in req.history]
    result = _agent.chat(
        req.message,
        history=history,
        current_config=req.current_config,
    )
    return ConfigDesignerChatResponse(
        reply=result.reply,
        draft_config=result.draft_config,
        warnings=result.warnings,
        style=result.style,
    )
