#!/usr/bin/env bash
# Disposable local engines only. All integration databases use p06_* names.
set -euo pipefail
cd "$(dirname "$0")/../../.."
case "${1:-}" in
  start)
    docker run -d --name terracotta-p06-postgres --label org.terracotta.validation=P06 \
      -e POSTGRES_PASSWORD=p06-disposable -p 127.0.0.1:55436:5432 \
      postgres@sha256:21f6013073bc6b92830a2129570e2f5ec42a6c734b5a985a41e83aa58f54c3c1
    docker run -d --name terracotta-p06-mysql --label org.terracotta.validation=P06 \
      -e MYSQL_ROOT_PASSWORD=p06-disposable -e MYSQL_ROOT_HOST=% -p 127.0.0.1:53316:3306 \
      mysql@sha256:0744ee5ef89ce6ccfa13de3e579fe6b9e27f93dd70da9c06d2c908b1b193fb8d
    ;;
  tls)
    mkdir -p process/validation/artifacts/p06
    docker exec -u postgres terracotta-p06-postgres openssl req -x509 -newkey rsa:2048 -noenc -days 2 \
      -subj /CN=localhost -addext subjectAltName=DNS:localhost \
      -keyout /var/lib/postgresql/data/server.key -out /var/lib/postgresql/data/server.crt
    docker exec -u postgres terracotta-p06-postgres psql -c "ALTER SYSTEM SET ssl='on'"
    docker exec -u postgres terracotta-p06-postgres psql -c 'SELECT pg_reload_conf()'
    docker cp terracotta-p06-postgres:/var/lib/postgresql/data/server.crt process/validation/artifacts/p06/postgres-ca.crt
    docker cp terracotta-p06-mysql:/var/lib/mysql/ca.pem process/validation/artifacts/p06/mysql-ca.pem
    ;;
  status)
    docker exec terracotta-p06-postgres pg_isready
    docker exec terracotta-p06-mysql mysqladmin -uroot -pp06-disposable ping
    ;;
  stop)
    docker rm -fv terracotta-p06-postgres terracotta-p06-mysql
    ;;
  *) echo 'Usage: p06-services.sh start|status|tls|stop' >&2; exit 2 ;;
esac
