from __future__ import annotations

from sqlalchemy import Column, DateTime, ForeignKey, Integer, MetaData, String, Table, UniqueConstraint, inspect, text
from sqlalchemy.engine import Engine


SCHEMA_VERSION = 2


def _columns(engine: Engine, table: str) -> set[str]:
    return {col["name"] for col in inspect(engine).get_columns(table)}


def _execute(engine: Engine, sql: str) -> None:
    with engine.begin() as conn:
        conn.execute(text(sql))


def _add_column(engine: Engine, table: str, name: str, ddl: str) -> bool:
    if name in _columns(engine, table):
        return False
    _execute(engine, f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
    return True


def _ensure_version_table(engine: Engine) -> None:
    _execute(
        engine,
        """
        CREATE TABLE IF NOT EXISTS lms_schema_version (
            id INTEGER PRIMARY KEY,
            version INTEGER NOT NULL
        )
        """,
    )
    with engine.begin() as conn:
        row = conn.execute(text("SELECT version FROM lms_schema_version WHERE id = 1")).first()
        if row is None:
            conn.execute(
                text("INSERT INTO lms_schema_version (id, version) VALUES (1, 0)")
            )


def _set_version(engine: Engine, version: int) -> None:
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE lms_schema_version SET version = :version WHERE id = 1"),
            {"version": version},
        )


def current_schema_version(engine: Engine) -> int:
    _ensure_version_table(engine)
    with engine.begin() as conn:
        row = conn.execute(text("SELECT version FROM lms_schema_version WHERE id = 1")).first()
    return int(row[0]) if row else 0


def _migration_1(engine: Engine) -> None:
    """Upgrade databases created by the earliest LMS prototype to 1.0.0-rc1."""

    tables = set(inspect(engine).get_table_names())
    if "courses" in tables:
        _add_column(engine, "courses", "owner_user_id", "INTEGER")
        _add_column(engine, "courses", "source_type", "VARCHAR(40) NOT NULL DEFAULT 'lti'")
        _add_column(engine, "courses", "description", "TEXT NOT NULL DEFAULT ''")
        _add_column(engine, "courses", "academic_year", "VARCHAR(40)")
        _add_column(engine, "courses", "join_code", "VARCHAR(40)")
        _add_column(engine, "courses", "settings_json", "JSON NOT NULL DEFAULT '{}'")
        _execute(
            engine,
            "CREATE INDEX IF NOT EXISTS ix_courses_owner_user_id ON courses (owner_user_id)",
        )
        _execute(
            engine,
            "CREATE INDEX IF NOT EXISTS ix_courses_source_type ON courses (source_type)",
        )
        _execute(
            engine,
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_courses_join_code "
            "ON courses (join_code) WHERE join_code IS NOT NULL",
        )

    if "lti_resource_links" in tables:
        _add_column(engine, "lti_resource_links", "learning_result_id", "INTEGER")
        _execute(
            engine,
            "CREATE INDEX IF NOT EXISTS ix_lti_resource_links_learning_result_id "
            "ON lti_resource_links (learning_result_id)",
        )

    if "scorm_packages" in tables:
        _add_column(engine, "scorm_packages", "owner_user_id", "INTEGER")
        _add_column(engine, "scorm_packages", "description", "TEXT NOT NULL DEFAULT ''")
        _add_column(engine, "scorm_packages", "original_filename", "VARCHAR(500)")
        _add_column(engine, "scorm_packages", "lineage_root_id", "INTEGER")
        _add_column(engine, "scorm_packages", "supersedes_id", "INTEGER")
        _add_column(engine, "scorm_packages", "revision_number", "INTEGER NOT NULL DEFAULT 1")
        _add_column(engine, "scorm_packages", "is_current", "BOOLEAN NOT NULL DEFAULT TRUE")
        _add_column(
            engine,
            "scorm_packages",
            "lifecycle_status",
            "VARCHAR(30) NOT NULL DEFAULT 'published'",
        )
        _add_column(
            engine,
            "scorm_packages",
            "visibility",
            "VARCHAR(30) NOT NULL DEFAULT 'private'",
        )
        if "uploaded_at" not in _columns(engine, "scorm_packages"):
            if engine.dialect.name == "postgresql":
                _add_column(
                    engine,
                    "scorm_packages",
                    "uploaded_at",
                    "TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP",
                )
            else:
                _add_column(
                    engine,
                    "scorm_packages",
                    "uploaded_at",
                    "DATETIME",
                )
                _execute(
                    engine,
                    "UPDATE scorm_packages SET uploaded_at = CURRENT_TIMESTAMP "
                    "WHERE uploaded_at IS NULL",
                )

        _execute(
            engine,
            "CREATE INDEX IF NOT EXISTS ix_scorm_packages_owner_user_id "
            "ON scorm_packages (owner_user_id)",
        )
        _execute(
            engine,
            "CREATE INDEX IF NOT EXISTS ix_scorm_packages_lineage_root_id "
            "ON scorm_packages (lineage_root_id)",
        )
        _execute(
            engine,
            "CREATE INDEX IF NOT EXISTS ix_scorm_packages_is_current "
            "ON scorm_packages (is_current)",
        )
        _execute(
            engine,
            "CREATE INDEX IF NOT EXISTS ix_scorm_packages_lifecycle_status "
            "ON scorm_packages (lifecycle_status)",
        )
        _execute(
            engine,
            "CREATE INDEX IF NOT EXISTS ix_scorm_packages_visibility "
            "ON scorm_packages (visibility)",
        )

        # Old PostgreSQL installations had module_id NOT NULL. Personal libraries
        # and the central catalogue require packages without a direct module owner.
        if engine.dialect.name == "postgresql":
            _execute(
                engine,
                "ALTER TABLE scorm_packages ALTER COLUMN module_id DROP NOT NULL",
            )

        # Existing packages become their own lineage root.
        _execute(
            engine,
            "UPDATE scorm_packages SET lineage_root_id = id "
            "WHERE lineage_root_id IS NULL",
        )


def _migration_2(engine: Engine) -> None:
    """Add local credentials without altering LTI identities or memberships."""
    metadata = MetaData()
    # Reflect users so SQLAlchemy can resolve the foreign key even when
    # migrate_schema() is invoked directly in a legacy database test/tool.
    Table("users", metadata, autoload_with=engine)
    table = Table(
        "local_credentials",
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        Column("login_display", String(320), nullable=False),
        Column("login_normalized", String(320), nullable=False),
        Column("password_hash", String(500), nullable=False),
        Column("failed_attempts", Integer, nullable=False, default=0),
        Column("locked_until", DateTime(timezone=True), nullable=True),
        Column("last_login_at", DateTime(timezone=True), nullable=True),
        Column("updated_at", DateTime(timezone=True), nullable=True),
        UniqueConstraint("user_id", name="uq_local_credential_user"),
        UniqueConstraint("login_normalized", name="uq_local_credential_login"),
    )
    table.create(bind=engine, checkfirst=True)
    _execute(
        engine,
        "CREATE INDEX IF NOT EXISTS ix_local_credentials_user_id "
        "ON local_credentials (user_id)",
    )
    _execute(
        engine,
        "CREATE INDEX IF NOT EXISTS ix_local_credentials_login_normalized "
        "ON local_credentials (login_normalized)",
    )


MIGRATIONS = {
    1: _migration_1,
    2: _migration_2,
}


def migrate_schema(engine: Engine) -> int:
    """Apply all known idempotent schema upgrades after metadata.create_all()."""
    _ensure_version_table(engine)
    version = current_schema_version(engine)
    for target in range(version + 1, SCHEMA_VERSION + 1):
        migration = MIGRATIONS[target]
        migration(engine)
        _set_version(engine, target)
    return current_schema_version(engine)
