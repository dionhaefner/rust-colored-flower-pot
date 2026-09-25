from .relational_meta_store import RelationalMetaStore
class SQLiteMetaStore(RelationalMetaStore):
    SQL_DIALECT = 'sqlite'
    SQL_DRIVER = 'pysqlite'
