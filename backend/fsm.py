from enum import Enum


class AppointmentState(str, Enum):
    INQUIRY = "INQUIRY"
    TENTATIVE = "TENTATIVE"
    CONFIRMED = "CONFIRMED"
    RESCHEDULED = "RESCHEDULED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"
    COMPLETED = "COMPLETED"


class AppointmentStateMachine:
    def __init__(self, appointment_id: str, current_state: str = "INQUIRY"):
        self.appointment_id = appointment_id
        self.state = AppointmentState(current_state)

    def get_state(self):
        return self.state.value

    def transition(self, new_state: str, metadata: dict = None):

        # Allowed transitions map
        valid_transitions = {
            "INQUIRY": ["TENTATIVE", "CONFIRMED"],
            "TENTATIVE": ["CONFIRMED", "CANCELLED"],
            "CONFIRMED": ["RESCHEDULED", "CANCELLED", "NO_SHOW", "COMPLETED"],
            "RESCHEDULED": ["CONFIRMED", "CANCELLED", "NO_SHOW", "COMPLETED"],
            "CANCELLED": [],
            "NO_SHOW": [],
            "COMPLETED": ["TENTATIVE"]  # TC083: allow rebooking after completion
        }

        if new_state in valid_transitions[self.state.value]:
            self.state = AppointmentState(new_state)
            print(
                f"Transitioned to {self.state.value} | Metadata: {metadata}"
            )
        else:
            print(
                f"Invalid transition from {self.state.value} to {new_state}"
            )