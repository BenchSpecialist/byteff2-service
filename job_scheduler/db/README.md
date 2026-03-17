# job_scheduler database setup

## Schema

Three tables, managed by SQLAlchemy ORM (`models.py`):

```
formulations
  uid              TEXT  PK          -- deterministic base64url hash of components
  formulation_id   TEXT  NOT NULL    -- user-facing label (e.g. "F1837")
  created_at       TIMESTAMP

components
  id               SERIAL PK
  formulation_uid  TEXT  FK -> formulations.uid
  component_id     TEXT  NOT NULL    -- "Solvent" | "Salt" | "Additive"
  name             TEXT  NOT NULL    -- "EC", "LiPF6", …
  weight_fraction  FLOAT NOT NULL

jobs
  id               SERIAL PK
  formulation_uid  TEXT  FK -> formulations.uid  UNIQUE
  status           ENUM('PENDING','RUNNING','SUCCESS','FAILED')
  progress_pct     FLOAT  DEFAULT 0
  stage_name       TEXT               -- "NPT" | "NVT" | "NEMD"
  message          TEXT
  k8s_pod_name     TEXT
  created_at       TIMESTAMP
  updated_at       TIMESTAMP

Indexes:
  ix_jobs_status  ON jobs(status)
```

## Local development (SQLite)

No setup needed — the default `DATABASE_URL` creates a local file:

```bash
export DATABASE_URL="sqlite:///jobcli.db"   # or just leave it unset
python -m cmdline.jobcli add formulations.csv
```

Tables are created automatically by `init_db()`.

---

## AWS RDS PostgreSQL setup

### 1. Create the RDS instance

```bash
aws rds create-db-instance \
  --db-instance-identifier jobcli-db \
  --db-instance-class db.t3.micro \
  --engine postgres \
  --engine-version 16.4 \
  --master-username jobcli_admin \
  --master-user-password '<PASSWORD>' \
  --allocated-storage 20 \
  --storage-type gp3 \
  --vpc-security-group-ids sg-xxxxxxxx \
  --db-name jobcli \
  --backup-retention-period 7 \
  --no-publicly-accessible \
  --storage-encrypted
```

Key flags:

| Flag | Purpose |
|------|---------|
| `--no-publicly-accessible` | Keep the instance inside the VPC; only reachable from the K8s cluster or a bastion host. |
| `--storage-encrypted` | Encrypt data at rest with the default AWS KMS key. |
| `--db-name jobcli` | Creates the `jobcli` database automatically on launch. |

Wait for the instance to become available:

```bash
aws rds wait db-instance-available --db-instance-identifier jobcli-db
```

### 2. Get the endpoint

```bash
aws rds describe-db-instances \
  --db-instance-identifier jobcli-db \
  --query 'DBInstances[0].Endpoint.Address' \
  --output text
# → jobcli-db.xxxxxxxxxxxx.us-east-1.rds.amazonaws.com
```

### 3. Security group rules

The RDS instance's security group must allow inbound TCP on port **5432** from:

- The K8s cluster node security group (so runner pods can reach the DB).
- The scheduler host or its security group.

```bash
aws ec2 authorize-security-group-ingress \
  --group-id sg-xxxxxxxx \
  --protocol tcp \
  --port 5432 \
  --source-group sg-yyyyyyyy   # K8s node SG
```

### 4. Create the application user

Connect to the instance (e.g. via a bastion or `kubectl port-forward`):

```bash
psql "host=jobcli-db.xxxxxxxxxxxx.us-east-1.rds.amazonaws.com \
      port=5432 dbname=jobcli user=jobcli_admin"
```

```sql
CREATE USER jobcli_app WITH PASSWORD '<APP_PASSWORD>';
GRANT CONNECT ON DATABASE jobcli TO jobcli_app;
GRANT USAGE  ON SCHEMA public TO jobcli_app;
GRANT CREATE ON SCHEMA public TO jobcli_app;

-- after init_db() has created the tables:
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA public TO jobcli_app;
GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA public TO jobcli_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES    TO jobcli_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT                  ON SEQUENCES TO jobcli_app;
```

### 5. Set `DATABASE_URL`

```bash
export DATABASE_URL="postgresql://jobcli_app:<APP_PASSWORD>@jobcli-db.xxxxxxxxxxxx.us-east-1.rds.amazonaws.com:5432/jobcli"
```

Or in `.env`:

```
DATABASE_URL=postgresql://jobcli_app:<APP_PASSWORD>@jobcli-db.xxxxxxxxxxxx.us-east-1.rds.amazonaws.com:5432/jobcli
```

> **Tip:** For production, store the password in AWS Secrets Manager and
> inject it at runtime rather than hard-coding it in `.env`.

### 6. Install the driver

SQLAlchemy needs `psycopg2` (or `psycopg`) to talk to PostgreSQL:

```bash
pip install psycopg2-binary   # pre-compiled, good for dev/containers
# or
pip install psycopg2          # requires libpq-dev, compiles from source
```

### 7. Create the tables

```bash
python -c "from job_scheduler.db.session import init_db; init_db()"
```

This runs `CREATE TABLE IF NOT EXISTS …` for all three tables plus the
`ix_jobs_status` index.  Safe to re-run — it is a no-op if tables already exist.

### 8. Verify

```bash
python -c "
from job_scheduler.db.session import SessionLocal
from job_scheduler.db.models import Job
with SessionLocal() as s:
    print('jobs:', s.query(Job).count())
"
```

---

## Connection pooling notes

The default SQLAlchemy `create_engine` pool (`QueuePool`, 5 connections) is
fine for a single scheduler process.  If you run multiple scheduler replicas
or many concurrent runner pods, consider:

```python
engine = create_engine(
    DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,   # detect stale connections after RDS failover
)
```

Or use **RDS Proxy** for connection multiplexing at the AWS level.
