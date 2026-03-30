from __future__ import annotations

import json
from datetime import timezone
from typing import Any

from sqlalchemy.orm import Session

from app.models import TopologyEditorState
from app.schemas import TopologyEditorStatePayload, TopologyEditorStateUpdate


TOPOLOGY_EDITOR_STATE_SCOPE = "default"


def _decode_json_object(raw_value: str | None, default: dict[str, Any] | None) -> dict[str, Any] | None:
    if not raw_value:
        return default
    try:
        parsed = json.loads(raw_value)
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, dict) else default


def get_topology_editor_state_payload(
    db: Session,
    scope: str = TOPOLOGY_EDITOR_STATE_SCOPE,
) -> dict[str, Any]:
    state = db.get(TopologyEditorState, scope)
    if not state:
        return TopologyEditorStatePayload(
            scope=scope,
            exists=False,
            layout_overrides={},
            state_log_layout=None,
            link_anchor_assignments={},
            demo_mode="off",
            updated_at=None,
        ).model_dump()

    updated_at = state.updated_at.astimezone(timezone.utc).isoformat() if state.updated_at else None
    return TopologyEditorStatePayload(
        scope=scope,
        exists=True,
        layout_overrides=_decode_json_object(state.layout_overrides_json, {}) or {},
        state_log_layout=_decode_json_object(state.state_log_layout_json, None),
        link_anchor_assignments=_decode_json_object(state.link_anchor_assignments_json, {}) or {},
        demo_mode=str(state.demo_mode_json or "off"),
        updated_at=updated_at,
    ).model_dump()


def upsert_topology_editor_state(
    payload: TopologyEditorStateUpdate,
    db: Session,
    scope: str = TOPOLOGY_EDITOR_STATE_SCOPE,
) -> dict[str, Any]:
    state = db.get(TopologyEditorState, scope)
    if not state:
        state = TopologyEditorState(scope=scope)
        db.add(state)

    state.layout_overrides_json = json.dumps(payload.layout_overrides or {})
    state.state_log_layout_json = json.dumps(payload.state_log_layout) if payload.state_log_layout is not None else None
    state.link_anchor_assignments_json = json.dumps(payload.link_anchor_assignments or {})
    state.demo_mode_json = payload.demo_mode or "off"

    db.commit()
    db.refresh(state)
    return get_topology_editor_state_payload(db, scope)
