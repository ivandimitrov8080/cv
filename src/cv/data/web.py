from peewee import TextField

from cv.data import BaseModel


class SearchState(BaseModel):
    query = TextField(unique=True)
    result = TextField()

class FetchState(BaseModel):
    url = TextField(unique=True)
    result = TextField()

