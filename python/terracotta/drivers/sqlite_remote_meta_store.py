from .relational_meta_store import RelationalMetaStore
from urllib.parse import urlparse
from terracotta._bridge import Session
from sqlalchemy.engine import URL
import sqlalchemy as sqla
class RemoteSQLiteMetaStore(RelationalMetaStore):
    SQL_DIALECT = 'sqlite-remote'
    _WRITABLE = False
    def __init__(self, path):
        parsed = urlparse(str(path))
        if parsed.scheme != 's3' or not parsed.netloc or not parsed.path.strip('/'):
            raise ValueError('Expected s3:// URL')
        super().__init__(path)
        # The legacy constructor downloads eagerly; errors must surface here.
        self._session = Session('store', self._native_path, self.SQL_DIALECT)

    @property
    def _local_path(self):
        return self._session.call(operation='snapshot_path', refresh=False)

    @property
    def sqla_engine(self):
        path = self._local_path
        if getattr(self, '_engine_path', None) != path:
            if getattr(self, '_engine', None) is not None:
                self._engine.dispose()
            self._engine = sqla.create_engine(URL.create('sqlite+pysqlite', database='file:'+path,
                query={'mode':'ro','uri':'true'}), poolclass=sqla.pool.NullPool)
            self._engine_path = path
        return self._engine

    def __del__(self):
        if getattr(self, '_engine', None) is not None:
            self._engine.dispose()
        self._session = None
    @classmethod
    def _normalize_path(cls, path):
        parsed = urlparse(path if '://' in path else 'https://' + path)
        return f'{parsed.scheme}://{parsed.hostname}:{parsed.port or (443 if parsed.scheme == "https" else 80)}{parsed.path}'.rstrip('/')
    @classmethod
    def _parse_path(cls, path):
        return urlparse(path)
