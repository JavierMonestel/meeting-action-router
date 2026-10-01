from .attio import AttioConnector
from .base import Connector, DispatchResult, PlannedRequest
from .calendar import CalendarConnector, build_ics
from .linear import LinearConnector
from .notion import NotionConnector

CONNECTORS: dict[str, Connector] = {c.name: c for c in (LinearConnector(), AttioConnector(), NotionConnector(), CalendarConnector())}

__all__ = ["CONNECTORS", "Connector", "DispatchResult", "PlannedRequest", "build_ics"]
