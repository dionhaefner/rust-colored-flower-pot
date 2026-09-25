"""Legacy connection and object boundary over native metadata stores."""
import contextlib
import os
import re
import urllib.parse
from collections import OrderedDict
from decimal import Decimal
from sqlalchemy.engine import URL
import sqlalchemy as sqla
from functools import cached_property
import terracotta
from terracotta import exceptions
from terracotta._bridge import Session, operation, metadata as decode_metadata
from .base_classes import MetaStore, requires_writable

_ERROR_ON_CONNECT = ('Could not connect to database. Make sure that the given path points '
                     'to a valid Terracotta database, and that you ran driver.create().')

class RelationalMetaStore(MetaStore):
    SQL_DIALECT = 'sqlite'
    SQL_DRIVER = 'pysqlite'
    DEFAULT_PORT = None

    def __init__(self, path):
        self._native_path = str(path)
        self.path = self._normalize_path(str(path))
        self.url = self._parse_path(str(path))
        if self.SQL_DIALECT in ('mysql', 'postgresql'):
            if not self.url.database:
                raise ValueError('database must be specified in database path')
            if '/' in self.url.database.strip('/'):
                raise ValueError('invalid database path')
        self.connected = False
        self.db_version_verified = False
        self._connection = None
        self._session = None
        self._created = False
        self._db_keys = None

    @classmethod
    def _parse_path(cls, path):
        if cls.SQL_DIALECT == 'sqlite':
            return URL.create('sqlite+pysqlite', database=cls._normalize_path(path))
        if '//' not in path:
            path = '//' + path
        parsed = urllib.parse.urlparse(path)
        if parsed.scheme and parsed.scheme != cls.SQL_DIALECT:
            raise ValueError(f'unsupported URL scheme "{parsed.scheme}"')
        settings = terracotta.get_settings()
        return URL.create(f'{cls.SQL_DIALECT}+{cls.SQL_DRIVER}',
                          username=parsed.username or settings.SQL_USER,
                          password=parsed.password or settings.SQL_PASSWORD,
                          host=parsed.hostname, port=parsed.port,
                          database=parsed.path.lstrip('/'), query=dict(urllib.parse.parse_qsl(parsed.query)))

    @classmethod
    def _normalize_path(cls, path):
        if cls.SQL_DIALECT == 'sqlite':
            return os.path.realpath(path.removeprefix('sqlite:///'))
        url = cls._parse_path(path)
        return f'{url.drivername}://{url.host}:{url.port or cls.DEFAULT_PORT}/{url.database}'.rstrip('/')

    @cached_property
    def sqla_engine(self):
        # Raw SQL connections remain a Python integration boundary (e.g. historical
        # migration tooling). Ordinary driver methods use the native store.
        return sqla.create_engine(self.url, future=True, pool_pre_ping=True)

    @contextlib.contextmanager
    def connect(self, verify=True):
        if not verify:
            with self.sqla_engine.connect().execution_options(isolation_level="READ UNCOMMITTED") as connection:
                previous = getattr(self, '_raw_connection', None)
                self._raw_connection = connection
                try:
                    yield connection
                except BaseException:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
                finally:
                    self._raw_connection = previous
            return
        with self._native_connect():
            if self._connection is not None:
                yield self._connection
                return
            with self.sqla_engine.connect().execution_options(isolation_level="READ UNCOMMITTED") as connection:
                self._connection = connection
                try:
                    yield connection
                except BaseException:
                    connection.rollback()
                    raise
                else:
                    connection.commit()
                finally:
                    self._connection = None

    @contextlib.contextmanager
    def _native_connect(self):
        outer = not self.connected
        try:
            if outer:
                try:
                    if self._session is None:
                        self._session = Session('store', self._native_path, self.SQL_DIALECT)
                    self.connected = True
                    version = self._session.call(operation='version')
                    if not self.db_version_verified:
                        if version.split('.')[:2] != terracotta.__version__.split('.')[:2]:
                            raise exceptions.InvalidDatabaseError(f'Version conflict: database was created in v{version}, but this is v{terracotta.__version__}')
                        self.db_version_verified = True
                except exceptions.InvalidDatabaseError as error:
                    self._session = None
                    if 'Version conflict' in str(error):
                        raise
                    raise exceptions.InvalidDatabaseError(_ERROR_ON_CONNECT) from error
            yield self._session
        finally:
            if outer:
                self.connected = False
                self._connection = None

    def _call(self, **request):
        if self._connection is not None:
            # A public caller may close/invalidate its borrowed SQL connection.
            # Preserve failures and context cleanup rather than hiding them behind
            # the independently owned native connection.
            self._connection.execute(sqla.text('SELECT 1'))
        with self._native_connect() as connection:
            return connection.call(refresh=False, **request)

    @property
    def db_version(self):
        if getattr(self, '_raw_connection', None) is not None:
            return str(self._raw_connection.execute(sqla.text('SELECT version FROM terracotta LIMIT 1')).scalar())
        return self._call(operation='version')

    @property
    def key_names(self):
        if self._db_keys is None:
            self._db_keys = self.get_keys()
        return tuple(self._db_keys)

    @requires_writable
    def create(self, keys, *, key_descriptions=None):
        descriptions = key_descriptions or {}
        if set(descriptions) - set(keys):
            raise exceptions.InvalidKeyError('key description dict contains unknown keys')
        if not all(re.match(r'^\w+$', key) for key in keys):
            raise exceptions.InvalidKeyError('key names must be alphanumeric')
        if any(k in self._RESERVED_KEYS for k in keys):
            raise exceptions.InvalidKeyError(f'key names cannot be one of {self._RESERVED_KEYS}')
        operation(operation='create', path=self._native_path, provider=self.SQL_DIALECT,
                  keys=[dict(key=k, description=descriptions.get(k, '')) for k in keys])
        self._created = True
        self._session = None
        self._db_keys = None

    def get_keys(self):
        return OrderedDict((k['key'], k['description']) for k in self._call(operation='keys'))

    def get_datasets(self, where=None, page=0, limit=None, order_by=None, ascending=True):
        order = list(self.key_names) + ['path'] if order_by is None else order_by
        if set(order) - set(self.key_names) - {'path'}:
            raise exceptions.InvalidKeyError(f'Encountered unrecognized keys (available keys: {self.key_names})')
        where = {k: v if isinstance(v, list) else [v] for k, v in (where or {}).items()}
        rows = self._call(operation='datasets', where=where, page=page, limit=limit, order_by=order, ascending=ascending)
        return {tuple(row['keys']): row['path'] for row in rows}

    def get_metadata(self, keys):
        result = decode_metadata(self._call(operation='metadata' if self._WRITABLE else 'metadata_stored', keys=[keys[k] for k in self.key_names]))
        if result is not None and self.SQL_DIALECT == 'mysql' and not self._created:
            for field in ['bounds', 'range', 'mean', 'stdev', 'valid_percentage']:
                value = result[field]
                convert = lambda v: Decimal(f'{v:.10f}')
                result[field] = tuple(map(convert, value)) if isinstance(value, tuple) else convert(value)
        return result

    @requires_writable
    def insert(self, keys, path, *, metadata=None):
        # Legacy operations commit independently inside a surrounding connect().
        return self._call(operation='insert', keys=[keys[k] for k in self.key_names], raster=str(path),
                          metadata=metadata, skip_metadata=metadata is None)

    @requires_writable
    def delete(self, keys):
        return self._call(operation='delete', keys=[keys[k] for k in self.key_names])
