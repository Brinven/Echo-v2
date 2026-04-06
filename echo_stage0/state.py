"""
Conversation state machine for Echo.

States:
  LISTENING  -> VAD speech onset or SPACE press  -> RECORDING
  RECORDING  -> VAD silence (>700ms) or SPACE up -> PROCESSING
  PROCESSING -> pipeline complete                -> SPEAKING
  PROCESSING -> sign-off detected                -> SIGN_OFF
  SPEAKING   -> audio queue empty                -> LISTENING
  SPEAKING   -> M key                            -> MUTED
  SIGN_OFF   -> goodbye + summary complete       -> SHUTDOWN
  MUTED      -> M key                            -> LISTENING
  ANY        -> Q key                            -> SHUTDOWN
"""

from enum import Enum, auto


class State(Enum):
    LISTENING = auto()
    RECORDING = auto()
    PROCESSING = auto()
    SPEAKING = auto()
    SIGN_OFF = auto()
    MUTED = auto()
    SHUTDOWN = auto()


class StateMachine:
    """Manages conversation state transitions."""

    def __init__(self):
        self._state = State.LISTENING
        self._listeners: list = []

    @property
    def state(self) -> State:
        return self._state

    def transition(self, new_state: State):
        """Transition to a new state."""
        old = self._state
        self._state = new_state
        for callback in self._listeners:
            callback(old, new_state)

    def on_transition(self, callback):
        """Register a callback for state transitions: callback(old, new)."""
        self._listeners.append(callback)

    @property
    def can_record(self) -> bool:
        """Whether mic input should be active."""
        return self._state in (State.LISTENING, State.RECORDING)

    @property
    def is_shutdown(self) -> bool:
        return self._state == State.SHUTDOWN

    @property
    def label(self) -> str:
        return self._state.name
