from .relational_meta_store import RelationalMetaStore
class MySQLMetaStore(RelationalMetaStore):
    SQL_DIALECT = 'mysql'
    SQL_DRIVER = 'pymysql'
    DEFAULT_PORT = 3306
