import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from .models import Base

DATA_DIR = os.environ.get(
    "PEOPLEINTEL_DATA",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"),
)
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "peopleintel.db")

# SQLite is fine here: this app is deployed on a host with a persistent disk
# (Railway / Render / Fly), not on a serverless platform with an ephemeral
# filesystem. Swapping to Postgres later is a URL change, nothing more.
engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


# Columns added after the first release. create_all() only creates missing
# tables, never missing columns, so a database made by an earlier version needs
# them added or every query against the table fails. There is no migration tool
# here on purpose; this stays a short, idempotent list.
ADDED_COLUMNS = {
    "linkedin_activities": [
        ("fetched_at", "DATETIME"),
        ("source_query", "VARCHAR"),
        ("corroborated", "BOOLEAN DEFAULT 0"),
        ("corroboration", "VARCHAR"),
        ("suggested_comment", "TEXT"),
        ("suggested_note", "VARCHAR"),
        ("suggested_at", "DATETIME"),
        ("suggested_model", "VARCHAR"),
    ],
    "people": [
        ("enrichment_status", "VARCHAR"),
        ("research_status", "VARCHAR"),
        ("email_source", "VARCHAR"),
        ("email_source_url", "VARCHAR"),
        ("email_found_at", "DATETIME"),
        ("linkedin_source", "VARCHAR"),
        ("linkedin_found_at", "DATETIME"),
        ("identity_note", "VARCHAR"),
    ],
    "web_findings": [
        ("corroborated", "BOOLEAN DEFAULT 0"),
        ("corroboration", "VARCHAR"),
    ],
}


def _add_missing_columns():
    with engine.begin() as conn:
        for table, columns in ADDED_COLUMNS.items():
            present = {
                r[1] for r in conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            if not present:
                continue  # table isn't there yet; create_all will make it whole
            for name, sqltype in columns:
                if name not in present:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}"
                    )


def init_db():
    Base.metadata.create_all(engine)
    _add_missing_columns()


def get_session():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
