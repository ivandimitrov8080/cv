from os.path import expanduser

from peewee import DatabaseProxy, Model, SqliteDatabase

DEFAULT_DB = "~/.local/state/web.sqlite"

db = DatabaseProxy()

class BaseModel(Model):
    class Meta:
        database = db


from cv.config import Config
from cv.data.lead import Comment, Lead
from cv.data.web import FetchState, SearchState


def init_db(cfg: Config):
    db_path = expanduser(cfg.data.db) or DEFAULT_DB
    database = SqliteDatabase(db_path)
    db.initialize(database)
    database.connect()
    database.drop_tables([SearchState, FetchState])
    database.create_tables([SearchState, FetchState, Lead, Comment])
