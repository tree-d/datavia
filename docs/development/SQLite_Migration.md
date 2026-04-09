## Steps

### Rewrite init.sql

Drop CREATE EXTENSION IF NOT EXISTS postgis
Change bbox geometry(POLYGON, 4326) → bbox TEXT
Change id SERIAL PRIMARY KEY → id INTEGER PRIMARY KEY AUTOINCREMENT (SQLite syntax)
Remove CREATE INDEX ... USING GIST (not supported in SQLite; keep the two regular (source_name) indexes)
Change timestamptz → TEXT (SQLite stores datetimes as ISO-8601 text or numeric; SQLAlchemy handles the Python datetime ↔ string mapping transparently)

### Rewrite connection.py

In _build_engine(): detect if database_url starts with sqlite and, if so, skip all pool_size/max_overflow/pool_recycle/pool_pre_ping kwargs (SQLite does not support them). Use connect_args={"check_same_thread": False} for SQLite thread safety.
Keep the PostgreSQL pool-config path so it still works for anyone who explicitly sets a postgresql:// URL in their datavia.conf.
Update the module docstring to replace "PostgreSQL+PostGIS" with "SQLite or PostgreSQL".

### Rewrite start.py

Replace conn.execute(text("SELECT to_regclass('public.raster_layers');")) with sqlalchemy.inspect(get_engine()).has_table("raster_layers") — this is fully cross-backend (SQLite and PostgreSQL both work).
Remove the isolation_level="AUTOCOMMIT" execution option; SQLite does not use it and the has_table check no longer needs it. Regular DDL within a transaction is fine for SQLite.

### Update saver_tiff.py

In _import_raster_metadata(): change ST_GeomFromText(:bbox_wkt, 4326) → :bbox_wkt in the INSERT statement (store WKT directly as text).
Update the INSERT parameter dict: drop any geometry cast, pass bbox_wkt string as-is.
In check_data_exists() and related SELECT queries: no changes needed — they never referenced PostGIS functions.
Update the module docstring and any inline comments referencing "PostGIS" or "geometry column".
Update query.py

In get_raster_metadata(): change ST_AsText(bbox) → bbox in the SELECT column list.
No other query function references PostGIS-specific SQL.
Update module docstring.

### Rewrite config.py

Change the database_url property: instead of always building postgresql://user:pass@host:port/db, check if a [database] section with key url exists and use it directly; otherwise fall back to constructing either a PostgreSQL URL (if host/port/user are set) or a SQLite  URL as the new default.
New default: sqlite:/// + (data_directory / "datavia.db") — so the DB lives next to the TIFF files.
Remove the port hash-derivation logic (and project_port) since it was only needed to avoid Docker port collisions between projects.
Keep all PostgreSQL-related config keys as optional, not mandatory.

### Update datavia.conf

Remove the [database] section entirely (SQLite path is auto-derived from [paths] data_directory).
Add a comment block explaining how to opt back into PostgreSQL with [database] url = postgresql://...
Remove POSTGRES_PASSWORD from the default config.
Update interfaces.py

Update the Saver class docstring: replace "PostGIS database" with "SQLite metadata database".
Delete runner.py — entire file. It is 100% Docker/PostgreSQL lifecycle management with no remaining purpose. All 335 lines of test_runner.py test only this module and are deleted with it.

### Update cli.py

Remove the start and stop CLI commands (they delegate to runner.start_container() / runner.stop_container()).
Remove the import runner / from .runner import ... import.
Remove the install command if it only installs Docker dependencies.
Keep all pipeline commands unchanged.

### Update cli_config.py

Remove the generated DATABASE_URL = get_config().database_url line from the template, or replace it with the SQLite URL.

### Delete legacy files

docker-compose.yml (root)
docker-compose.yml
.env.example
enhanced_schema_draft.sql

### Remove dead dependencies

pyproject.toml: remove psycopg2-binary, remove docker from [project.optional-dependencies].dev, remove docker-compose.yml from wheel include list
pixi.toml: remove psycopg2, psycopg2-binary, libpq, docker

### Replace test suite for DB and runner

Delete test_runner.py entirely.
Create tests/conftest.py with a shared sqlite_db pytest fixture that configures get_config() to use sqlite:///:memory: and calls initialize_database() + teardown — avoiding per-file duplication.
Rewrite test_e2e_workflows.py: remove TestContainerIntegration and test_container_failure_workflow; replace the patch('datavia.core.datavia.initialize_database') mock with a real in-memory SQLite call using the conftest fixture.
Rewrite live_database fixture in test_soil_e2e.py: replace Docker container start/stop with SQLite in-memory init; keep DATAVIA_E2E=1 guard for the actual network downloads.

### Update docs

README.md: remove Docker setup section, replace with "no setup required — SQLite DB is created automatically in your data directory"
quick_start.rst: remove docker compose up step
changelog.rst: add entry for this migration

### Verification

pixi run test (or pytest tests/ -v) — all tests pass without Docker running
datavia update elevation — produces data/elevation_dgm200.tif and data/datavia.db (inspectable with sqlite3 data/datavia.db "SELECT * FROM raster_layers;")
Confirm get_raster_metadata("elevation") returns bbox as a WKT string
Confirm soil pipeline multi-band metadata writes correctly to raster_band_metadata
CI pipeline: remove the docker compose up step from cd-main.yml