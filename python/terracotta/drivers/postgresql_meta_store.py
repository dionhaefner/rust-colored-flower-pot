from .relational_meta_store import RelationalMetaStore
class PostgreSQLMetaStore(RelationalMetaStore):
    SQL_DIALECT = 'postgresql'
    SQL_DRIVER = 'psycopg2'
    DEFAULT_PORT = 5432
