from os.path import expanduser

from peewee import CharField, Model, SqliteDatabase

from cv import cfg

db = SqliteDatabase(expanduser(cfg.data.db))

class BaseModel(Model):
    class Meta:
        database = db


class SearchState(BaseModel):
    query = CharField(unique=True)

class FetchState(BaseModel):
    url = CharField(unique=True)

def init():
    db.connect()
    db.create_tables([SearchState, FetchState])
