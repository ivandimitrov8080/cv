from datetime import datetime

from peewee import DateTimeField, ForeignKeyField, TextField

from cv.data import BaseModel


class Lead(BaseModel):
    url = TextField(unique=True)
    title = TextField()
    description = TextField()
    comments = TextField()
    created = DateTimeField(default=datetime.now)


class Comment(BaseModel):
    lead = ForeignKeyField(Lead, backref="comments", on_delete="CASCADE")
    text = TextField()
    created = DateTimeField(default=datetime.now)
