from peewee import CharField

from cv.data import BaseModel


class SearchState(BaseModel):
    query = CharField(unique=True)
    result = CharField()

class FetchState(BaseModel):
    url = CharField(unique=True)
    result = CharField()

