from datetime import datetime
from enum import Enum

from peewee import CharField, DateTimeField, ForeignKeyField, ModelSelect, TextField

from cv.data import BaseModel


class LeadState(str, Enum):
    NEW = "new"
    PROCESSING = "processing"
    APPLIED = "applied"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

class Tags(str, Enum):
    REMOTE = "remote"
    BACKEND = "backend"
    FRONTEND = "frontend"
    SYSADMIN = "sysadmin"
    ON_SITE = "onsite"
    JAVA = "java"
    JAVASCRIPT = "javascript"
    NIX = "nix"
    LINUX = "linux"


class Lead(BaseModel):
    url = TextField(unique=True)
    title = TextField()
    description = TextField()
    state = CharField(default=LeadState.NEW, choices=[(c.value, c.name) for c in LeadState])
    created = DateTimeField(default=datetime.now)

    @property
    def comments(self) -> ModelSelect[Comment]:
        return Comment.select().where(Comment.lead == self)

    @property
    def tags(self) -> ModelSelect[Tag]:
        return Tag.select().where(Tag.lead == self)


class Comment(BaseModel):
    lead = ForeignKeyField(Lead, backref="comments", on_delete="CASCADE")
    text = TextField()
    created = DateTimeField(default=datetime.now)

class Tag(BaseModel):
    lead = ForeignKeyField(Lead, backref="tags", on_delete="CASCADE")
    tag = CharField(choices=[(c.value, c.name) for c in Tags])
    created = DateTimeField(default=datetime.now)
