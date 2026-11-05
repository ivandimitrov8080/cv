import uuid
from datetime import datetime, timedelta, timezone

from peewee import IntegrityError, SqliteDatabase, fn
from playhouse.reflection import generate_models

RESERVED = frozenset({"id", "state", "submitting", "claim_token", "claimed_until"})


def now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Store:
    def __init__(self, config):
        self.config = config
        self.db = SqliteDatabase(
            None, pragmas={"journal_mode": "wal", "busy_timeout": 10000})
        self.row = None
        self.memo = None

    def _open(self):
        if not self.db.is_closed():
            self.db.close()
        self.config.db.parent.mkdir(parents=True, exist_ok=True)
        self.db.init(str(self.config.db))
        self.db.connect()
        return self

    def connect(self):
        self._open()
        models = generate_models(self.db)
        for name in (self.config.table, self.config.memo):
            if name not in models:
                raise LookupError(f"{self.config.db} has no table {name!r}; run init")
        self.row, self.memo = models[self.config.table], models[self.config.memo]
        return self

    def create(self):
        self._open()
        self.db.connection().executescript(self.config.schema.read_text(encoding="utf-8"))
        return self.connect()

    def close(self):
        if not self.db.is_closed():
            self.db.close()

    @property
    def new(self):
        return self.config.new

    @property
    def working(self):
        return self.config.working

    @property
    def review(self):
        return self.config.review

    @property
    def columns(self):
        return tuple(sorted(set(self.row._meta.fields) - RESERVED))

    def counts(self):
        m = self.row
        rows = (m.select(m.state, fn.COUNT(m.id).alias("n")).group_by(m.state).tuples())
        return {state: count for state, count in rows}


class Claim:
    def __init__(self, store, lease=1800):
        self.store = store
        self.lease = timedelta(seconds=lease)
        self.row = None
        self.token = None

    @property
    def model(self):
        return self.store.row

    def free(self):
        m = self.model
        return m.select().where((m.state == self.store.new) |
                                ((m.state == self.store.working) &
                                 (m.claimed_until < now())))

    def count(self):
        return self.free().count()

    def recover(self):
        m = self.model
        if not self.store.review:
            return 0
        return (m.update(state=self.store.review, submitting=False, claim_token=None,
                         claimed_until=None)
                .where(m.state == self.store.working, m.submitting == True,
                       m.claimed_until < now()).execute())

    def take(self):
        self.recover()
        m = self.model
        if self.row is not None:
            live = self.mine()
            if live is not None:
                return live
        token = uuid.uuid4().hex
        for _ in range(24):
            row = self.free().order_by(m.id).first()
            if row is None:
                return None
            guard = (m.claim_token.is_null() if row.claim_token is None
                     else m.claim_token == row.claim_token)
            won = (m.update(state=self.store.working, claim_token=token,
                            claimed_until=now() + self.lease)
                   .where(m.id == row.id, m.state == row.state, guard).execute())
            if won:
                self.row, self.token = m.get(m.claim_token == token), token
                return self.row
        return None

    def renew(self):
        if self.row is None:
            return False
        m = self.model
        return (m.update(claimed_until=now() + self.lease)
                .where(m.id == self.row.id, m.state == self.store.working,
                       m.claim_token == self.token).execute()) == 1

    def mine(self):
        if self.row is None or not self.renew():
            self.row = None
            return None
        m = self.model
        return m.get_or_none(m.id == self.row.id, m.state == self.store.working,
                             m.claim_token == self.token)

    def guard(self):
        if self.row is None:
            return False
        m = self.model
        return (m.update(submitting=True, claimed_until=now() + self.lease)
                .where(m.id == self.row.id, m.state == self.store.working,
                       m.claim_token == self.token).execute()) == 1

    def end(self, **fields):
        if self.row is None:
            return False
        m = self.model
        done = (m.update(claim_token=None, claimed_until=None, **fields)
                .where(m.id == self.row.id, m.state == self.store.working,
                       m.claim_token == self.token).execute())
        self.row, self.token = None, None
        return done == 1


class Memo:
    def __init__(self, store, ttl=()):
        self.store = store
        self.ttl = {kind: timedelta(days=float(days)) for kind, days in dict(ttl).items()}

    def get(self, key):
        return self.store.memo.get_or_none(self.store.memo.key == key)

    def stale(self, row):
        if row.verdict in ("recorded", "duplicate"):
            return False
        age = self.ttl.get(row.kind)
        return age is not None and row.at < now() - age

    def claim(self, key, kind):
        m = self.store.memo
        row = self.get(key)
        if row is None:
            try:
                m.create(key=key, kind=kind, verdict="claimed", at=now())
                return True, None
            except IntegrityError:
                row = self.get(key)
                if row is None:
                    return False, None
        if not self.stale(row):
            return False, row
        if (m.update(verdict="claimed", note=None, at=now())
                .where(m.key == key, m.verdict == row.verdict).execute()):
            return True, None
        return False, self.get(key)

    def settle(self, key, verdict, note=None):
        m = self.store.memo
        fields = dict(verdict=verdict, note=(note or None), at=now())
        if m.update(**fields).where(m.key == key).execute():
            return True
        try:
            m.create(key=key, kind="url", **fields)
        except IntegrityError:
            pass
        return False

    def release(self, keys):
        keys = list(keys)
        if keys:
            m = self.store.memo
            return m.delete().where(m.key << keys, m.verdict == "claimed").execute()
        return 0

    def forget(self, days=365):
        m = self.store.memo
        return m.delete().where(m.at < now() - timedelta(days=days)).execute()
