from os.path import expanduser

from peewee import CharField, DatabaseProxy, Model, SqliteDatabase

from cv.config import Config
from cv.util import silentremove

DEFAULT_DB = "~/.local/state/web.sqlite"

db = DatabaseProxy()

class BaseModel(Model):
    class Meta:
        database = db


class SearchState(BaseModel):
    query = CharField(unique=True)
    result = CharField()

class FetchState(BaseModel):
    url = CharField(unique=True)
    result = CharField()

def init_db(cfg: Config):
    db_path = expanduser(cfg.data.db) or DEFAULT_DB
    silentremove(db_path)
    database = SqliteDatabase(db_path)
    db.initialize(database)
    database.connect()
    database.create_tables([SearchState, FetchState])
