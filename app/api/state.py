"""In-memory conversational state.

Kept in-process (not in the DB or Redis): the deployment runs a single replica,
and Telegram delivers a user's updates in order, so a process-local dict is
sufficient. If we ever scale to multiple replicas this must move to a shared
store — documented here as a known limitation.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict


class FlowType(str, Enum):
    NONE = "none"
    ADD = "add"          # guided /add flow
    NOTION = "notion"    # Notion setup flow
    CONFIRM = "confirm"  # pending natural-language reminder awaiting confirm


# Guided-add step markers.
ADD_NAME = "name"
ADD_TYPE = "type"        # waiting on a button
ADD_UNIT = "unit"        # waiting on a button
ADD_AMOUNT = "amount"
ADD_CONTENT = "content"

# Notion step markers.
N_TOKEN = "token"
N_DB_ID = "db_id"
N_NAME_PROP = "name_prop"
N_TIME_PROP = "time_prop"
N_STATUS_PROP = "status_prop"
N_IMPORT = "import"      # waiting on yes/no button


class UserState:
    def __init__(self) -> None:
        self.flow: FlowType = FlowType.NONE
        self.step: str = ""
        self.data: Dict[str, Any] = {}

    def set(self, flow: FlowType, step: str = "") -> None:
        self.flow = flow
        self.step = step
        self.data = {}

    def reset(self) -> None:
        self.flow = FlowType.NONE
        self.step = ""
        self.data = {}


_states: Dict[str, UserState] = {}


def get_state(chat_id) -> UserState:
    key = str(chat_id)
    if key not in _states:
        _states[key] = UserState()
    return _states[key]


def clear_state(chat_id) -> None:
    _states.pop(str(chat_id), None)
