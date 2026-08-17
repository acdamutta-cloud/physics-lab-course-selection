from datetime import datetime

from pydantic import BaseModel, Field


class SelectionWindowConfigRequest(BaseModel):
    start_at: datetime
    end_at: datetime
    withdraw_end_at: datetime | None = None


class SelectionWindowOut(BaseModel):
    id: str
    term_id: str
    start_at: datetime
    end_at: datetime
    withdraw_end_at: datetime | None
    status: str
