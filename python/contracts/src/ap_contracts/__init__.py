"""Pydantic models generated from the JSON Schema in /contracts. Do not edit the generated modules."""

from ap_contracts.agent_definition import AgentDefinition, ToolRef
from ap_contracts.gateway import (
    CallContext,
    Message,
    ModelDone,
    ModelRequest,
    ProviderBlock,
    TextBlock,
    TextDelta,
    ToolCall,
    ToolCallBlock,
    ToolCallRequest,
    ToolCallResult,
    ToolResultBlock,
    ToolSpec,
    Usage,
)
from ap_contracts.outbound_event import OutboundEvent
from ap_contracts.turn_envelope import CallIdentity, TurnEnvelope

# One event in a model stream. Every stream ends with exactly one ModelDone.
ModelEvent = TextDelta | ToolCall | ProviderBlock | ModelDone
ContentBlock = TextBlock | ToolCallBlock | ToolResultBlock | ProviderBlock

__all__ = [
    "AgentDefinition",
    "CallContext",
    "CallIdentity",
    "ContentBlock",
    "Message",
    "ModelDone",
    "ModelEvent",
    "ModelRequest",
    "OutboundEvent",
    "ProviderBlock",
    "TextBlock",
    "TextDelta",
    "ToolCall",
    "ToolCallBlock",
    "ToolCallRequest",
    "ToolCallResult",
    "ToolRef",
    "ToolResultBlock",
    "ToolSpec",
    "TurnEnvelope",
    "Usage",
]
