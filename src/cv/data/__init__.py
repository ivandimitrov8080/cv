from os.path import expanduser

from peewee import DatabaseProxy, Model, SqliteDatabase

DEFAULT_DB = ":memory:"

db = DatabaseProxy()

class BaseModel(Model):
    class Meta:
        database = db

from cv.config import Config
from cv.data.lead import Comment, Lead


def init_db(cfg: Config):
    db_path = expanduser(cfg.data.db) or DEFAULT_DB
    database = SqliteDatabase(db_path)
    db.initialize(database)
    database.connect()
    database.create_tables([Lead, Comment])
