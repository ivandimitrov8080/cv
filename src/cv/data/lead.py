from datetime import datetime
from enum import Enum

from peewee import CharField, DateTimeField, ForeignKeyField, TextField

from cv.data import BaseModel


class LeadState(str, Enum):
    NEW = "new"
    PROCESSING = "processing"
    APPLIED = "applied"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

class Lead(BaseModel):
    url = TextField(unique=True)
    title = TextField()
    description = TextField()
    state = CharField(default=LeadState.NEW, choices=[(c.value, c.name) for c in LeadState])
    created = DateTimeField(default=datetime.now)


class Comment(BaseModel):
    lead = ForeignKeyField(Lead, backref="comments", on_delete="CASCADE")
    text = TextField()
    created = DateTimeField(default=datetime.now)
