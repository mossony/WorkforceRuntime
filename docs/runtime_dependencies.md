# Runtime Dependencies

Workforce Runtime now expects RabbitMQ and MySQL for the default local backend.
SQLite is still available for tests and legacy `.sqlite` paths, but the normal
configured runtime uses MySQL.

## Docker Services

RabbitMQ:

```bash
docker run -d --name workforce-rabbitmq \
  -p 5672:5672 -p 15672:15672 \
  -e RABBITMQ_DEFAULT_USER=workforce \
  -e RABBITMQ_DEFAULT_PASS=workforce \
  rabbitmq:3-management
```

MySQL:

```bash
docker run -d --name workforce-mysql \
  -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=workforce_root \
  -e MYSQL_DATABASE=workforce_runtime \
  -e MYSQL_USER=workforce \
  -e MYSQL_PASSWORD=workforce \
  mysql:8.4
```

Health checks:

```bash
docker exec workforce-rabbitmq rabbitmq-diagnostics -q ping
docker exec workforce-mysql mysqladmin ping -h 127.0.0.1 -uworkforce -pworkforce --silent
```

## Default Connection Settings

RabbitMQ:

- AMQP: `127.0.0.1:5672`
- Management UI: `http://127.0.0.1:15672`
- Username: `workforce`
- Password: `workforce`
- Exchange: `workforce.agent_inbox`
- Queue prefix: `workforce.agent.`

MySQL:

- Host: `127.0.0.1`
- Port: `3306`
- Database: `workforce_runtime`
- Username: `workforce`
- Password: `workforce`

These values live in `workforce_runtime_config.json` and the template at
`examples/workforce_runtime_config.json`.

## Storage Behavior

The default runtime storage backend is MySQL:

```json
{
  "runtime": {
    "store_backend": "mysql",
    "db_path": "workforce_runtime"
  }
}
```

Passing a `.sqlite`, `.sqlite3`, or `.db` path still forces the SQLite adapter.
This keeps tests and old one-file demos working:

```bash
workforce-runtime --db .workforce_runtime/demo.sqlite dashboard
```

Omitting `--db` uses the configured MySQL database:

```bash
workforce-runtime --config workforce_runtime_config.json dashboard --serve
```

## Responsibility Split

- MySQL is the durable source of truth for agents, tasks, reports, events,
  work queue items, and agent inbox item status.
- RabbitMQ carries per-agent inbox delivery messages for assignment, discussion,
  report review, human steering, and system notices.
- Event logs and trace exports remain in MySQL-backed runtime state so V2 replay,
  benchmarking, and dashboard inspection can reconstruct what happened.
