from dataclasses import dataclass
from datetime import datetime


@dataclass
class RawCandidate:
    external_id: str
    url: str
    title: str
    snippet: str | None = None
    published_at: datetime | None = None
    language: str = "en"
