from datetime import datetime
from enum import Enum

import pydantic
from peewee import CharField, DateTimeField, ForeignKeyField, TextField

from cv.data import BaseModel, db


class LeadState(str, Enum):
    NEW = "new"
    PROCESSING = "processing"
    APPLIED = "applied"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

class LeadTag(str, Enum):
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
    url = TextField(unique=True, primary_key=True)
    title = TextField()
    description = TextField()
    state = CharField(
        default=LeadState.NEW,
        choices=[(c.value, c.name) for c in LeadState]
    )
    created = DateTimeField(default=datetime.now)
    comments: list[Comment]
    tags: list[Tag]

class Comment(BaseModel):
    lead = ForeignKeyField(Lead, backref="comments", on_delete="CASCADE")
    value = TextField()
    created = DateTimeField(default=datetime.now)

class Tag(BaseModel):
    lead = ForeignKeyField(Lead, backref="tags", on_delete="CASCADE")
    value = CharField(choices=[(c.value, c.name) for c in LeadTag])
    created = DateTimeField(default=datetime.now)

class LeadSchema(pydantic.BaseModel):
    url: str
    title: str
    description: str
    state: LeadState = LeadState.NEW
    comments: list[str] = pydantic.Field(default_factory=list)
    tags: list[LeadTag] = pydantic.Field(default_factory=list)


def save_leads(leads: list[LeadSchema]) -> list[Lead]:
    by_url = {lead.url: lead for lead in leads}
    if not by_url:
        return []

    existing = {
        row.url for row in
        Lead.select(Lead.url).where(Lead.url.in_(list(by_url)))
    }
    fresh = [s for url, s in by_url.items() if url not in existing]
    if not fresh:
        return []

    rows = [
        Lead(url=s.url,
             title=s.title,
             description=s.description,
             state=LeadState(s.state).value)
        for s in fresh
    ]

    with db.atomic():
        Lead.bulk_create(rows, batch_size=100)
        Tag.bulk_create(
            [Tag(lead=r.url, value=LeadTag(t).value) for r, s in zip(rows, fresh) for t in s.tags],
            batch_size=100,
        ) if any(s.tags for s in fresh) else None
        Comment.bulk_create(
            [Comment(lead=r.url, value=c) for r, s in zip(rows, fresh) for c in s.comments],
            batch_size=100,
        ) if any(s.comments for s in fresh) else None

    return rows


def update_lead(lead: LeadSchema):
    with db.atomic():
        Lead.update(
            title=lead.title,
            description=lead.description,
            state=LeadState(lead.state),
        ).where(Lead.url == lead.url).execute()
        Tag.delete().where(Tag.lead == lead.url).execute()
        Comment.delete().where(Comment.lead == lead.url).execute()
        for t in lead.tags:
            Tag.create(lead=lead.url, value=LeadTag(t).value)
        for c in lead.comments:
            Comment.create(lead=lead.url, value=c)
        return Lead.get(Lead.url == lead.url)

