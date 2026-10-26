# Chore planner — local Docker deployment

Runs the existing planner and a Python/Waitress API in one container, with a SQLite database in the `planner_data` named Docker volume. All devices use the same chart. No Cloudflare account or service is involved.

## Standalone Docker container (no Compose required)

The image already contains both the web interface and SQLite backend. You can deploy it with Docker alone.

1. For a private package, log in to GitHub Container Registry as described below.
2. Put a `.env` file in the storage mounted at `/data`. The app reads `/data/.env` automatically; no `--env-file` option is needed. Set `APP_USER` and a strong `APP_PASSWORD`. For an existing bind-mounted data folder, create the file directly in that folder and make it readable by container UID 10001. For a Docker named volume, use the one-time copy command below.
3. Set `IMAGE_REF` to the **exact image reference** printed by a successful GitHub workflow (`latest`, a version tag, or `sha-` plus the full commit SHA).

```sh
# Replace this example with the tag from your successful GitHub workflow.
IMAGE_REF='ghcr.io/fordbd/chore-loop:latest'
docker pull "$IMAGE_REF"
docker volume create chore-loop-data

# One-time setup: create a local .env from .env.example and set APP_PASSWORD.
# Copy it into the volume as /data/.env with owner-only permissions.
docker run --rm -i \
  --mount type=volume,source=chore-loop-data,target=/data \
  --entrypoint python "$IMAGE_REF" \
  -c 'from pathlib import Path; import sys; p=Path("/data/.env"); p.write_bytes(sys.stdin.buffer.read()); p.chmod(0o600)' < .env

docker run -d \
  --name chore-loop \
  --restart unless-stopped \
  --publish 8080:8080 \
  --mount type=volume,source=chore-loop-data,target=/data \
  --read-only \
  --tmpfs /tmp \
  --cap-drop ALL \
  --security-opt no-new-privileges:true \
  "$IMAGE_REF"
```

The container reads `/data/.env` at startup. Supported keys are `AUTH_ENABLED`, `APP_USER`, `APP_PASSWORD`, `TZ`, `DB_PATH`, and `PORT`. Explicit container environment variables take precedence, so existing `-e` / Compose deployments remain compatible. Quotes and comments are supported; values are literal, with no shell execution or variable expansion. Restart the container after changing the file. Missing credentials cause startup to stop with a clear error rather than inventing a password.

Open `http://YOUR-SERVER-IP:8080`. The existing LAN/HTTPS guidance below also applies. To use a different host port, change only the left side of `--publish`, for example `8090:8080`. `APP_PORT` and `BIND_ADDRESS` in `.env.example` are Compose-only settings; standalone Docker uses `--publish`. To bind to loopback for a local reverse proxy, use `--publish 127.0.0.1:8080:8080`.

Check startup with `docker logs --tail=100 chore-loop`. Import your existing chart via **Restore backup** once it is running. The named volume stores both the database and `.env` and survives removal of the application container.

Take an online-safe backup with:

```sh
mkdir -p backups
docker exec chore-loop python backup.py > backups/chores.sqlite3
```

To update, back up first, pull the new image reference, stop and remove only the old container, then repeat `docker run` above with the new image and the **same volume**:

```sh
docker pull "$IMAGE_REF"
docker stop chore-loop
docker rm chore-loop
# Repeat the docker run command above with the new IMAGE_REF.
```

Removing the container does not remove the named volume. Do not remove `chore-loop-data`. If migrating an existing Compose deployment, use its actual volume name (shown by `docker inspect` or `docker volume ls`) instead of creating a fresh volume; Compose commonly prefixes volume names with its project name. Stop the old app before starting the replacement so both do not serve separate running copies.

## First deployment

On a machine with Docker Engine/Desktop and Docker Compose:

```sh
cp .env.example .env
```

Edit `.env` and set a strong `APP_PASSWORD`. The username defaults to `family`. Keep `.env` out of Git. Then:

```sh
docker compose up -d --build
docker compose ps
```

Open `http://YOUR-SERVER-IP:8080` from your computer or phone on the same network. Sign in with `APP_USER` and `APP_PASSWORD`. For example, if your Docker host is `192.168.1.20`, use `http://192.168.1.20:8080`. Reserve a stable address for that host on your router. Allow port 8080 through the host firewall only as appropriate for your home network.

The default port binds to all host interfaces for LAN access. Set `BIND_ADDRESS=127.0.0.1` when using a reverse proxy on the same host. This deployment has a single shared household login; it does not implement separate parent/child permissions. HTTP Basic authentication should be used over HTTPS or a trusted VPN when accessing it outside your trusted home LAN. Do not expose the HTTP port directly to the internet.

## Optional login

Login is enabled by default (`AUTH_ENABLED=true`). To disable it, add this to `/data/.env` and restart the container:

```dotenv
AUTH_ENABLED=false
```

Or explicitly pass `-e AUTH_ENABLED=false` to `docker run`. When disabled, neither `APP_USER` nor `APP_PASSWORD` is required, and anyone who can reach the app can view and edit the shared chart. The SQLite API uses the same setting as the web page.

To re-enable login, set `AUTH_ENABLED=true` and provide `APP_PASSWORD`, then restart. Invalid values stop startup instead of disabling authentication accidentally. Explicit container environment variables override `/data/.env`.

The supplied Compose files pass `AUTH_ENABLED` from the host's Compose `.env` (default `true`), so set it there when deploying with Compose. An empty password is allowed by Compose itself, but the application still requires a password whenever authentication is enabled.

## Move your existing chart

1. In the original local file, select **Family & chore settings → Back up chart**.
2. Open the Docker-hosted planner.
3. Choose **Restore backup** and select that JSON file.
4. Wait for **Saved to SQLite**. Other devices refresh within about five seconds when settings are closed.

Only the original browser has your current saved chart. Your personal settings are not embedded in the application source. The server starts with the existing sample chart until you restore your backup. Restoring replaces the shared household chart.

When two devices save the same revision, the later save is rejected instead of overwriting newer data. The affected device shows **NOT SAVED**. Export its unsaved chart if needed, then reload and reapply the desired changes. Connection failures also display an unsaved warning; offline changes are not silently queued for future replay.

## Updates from Git

Push application code to your own private Git repository. Do not commit `.env`, SQLite files, or backups. No repository or remote is created by these instructions.

On your Docker host, take a database backup, then:

```sh
git pull --ff-only
docker compose up -d --build
```

The named volume survives container recreation and `docker compose down`. **Do not use `docker compose down -v` unless you intend to delete the database.** Use the same project directory or Compose project name so subsequent deployments use the same named volume. Schema migrations run automatically before the web server starts; see Database migrations below.

## Database migrations

At startup, the app reads the database schema version and applies any pending migrations included in the image. Existing schema-v1 databases are recognized without resetting the chart. A new database is initialized at the current schema version.

Before upgrading an existing database, the app creates an integrity-checked SQLite backup in `/data/migration-backups/` (or `migration-backups/` beside a custom `DB_PATH`). The backup uses SQLite's online backup API, including committed WAL data. Backup failure stops startup before migrations run.

Pending schema changes and their version records run together inside one transaction, with a SQLite writer lock preventing simultaneous upgrades or saves. If a migration fails, all pending changes roll back, the pre-upgrade backup remains, and the app does not start. After success, restart does not reapply migrations or create another backup for the same version. Startup refuses databases newer than the app supports; selecting an older image does not downgrade the database.

Automatic backups are created only for actual upgrades of an existing database. They are retained until you remove them and should be included in your regular backups. Continue taking routine backups before upgrades. Stop the old container before starting the new version.

For future development, add the next numbered migration function to `MIGRATIONS` in `migrations.py`, together with migration tests. Use `execute`/`executemany` inside migration functions, never `executescript`, `commit`, or `rollback`. Data-format changes inside the saved chart JSON must also receive an explicit migration when needed. A Git push does not invent migrations: each database-changing release must include them. Schema v2 adds a default estimate of 10 minutes per occurrence to existing chores that have no estimate, preserving existing estimates and completion checkboxes. You can edit Minutes in chore settings. Assignment balances estimated minutes separately on each scheduled day, using rotation to break ties. Weekly totals are ignored. Flexible any-day chores rotate among eligible children because they have no scheduled day. Age rules and specific assignments remain in effect. Exact equality may be impossible when chores are indivisible or age restrictions apply.

If an upgrade fails, inspect container logs. Correct the cause and restart, or stop the container and restore the pre-upgrade database backup using the recovery instructions below. App-image rollback and database rollback are separate operations.

## Backups

The in-app **Back up chart** creates a portable JSON export. For an online-safe SQLite backup, run from the host:

```sh
mkdir -p backups
docker compose exec -T planner python backup.py > backups/chores.sqlite3
```

Use a different destination filename each time if you want backup history. Schedule this command with your host's backup scheduler and include the resulting files in your normal backups. The SQLite backup contains chart data only; back up `/data/.env` separately as a private configuration file. The helper uses SQLite's backup API so it includes committed data even while the database is in WAL mode; do not copy just the live `.sqlite3` file while the app is running.

For a portable restore, prefer **Restore backup** with a JSON export. For disaster recovery from a database backup:

```sh
docker compose stop planner
docker compose run --rm --no-deps --user root --entrypoint python planner -c 'import pathlib; import shutil; import sys; p=pathlib.Path("/data/chores.sqlite3"); p.with_name(p.name+"-wal").unlink(missing_ok=True); p.with_name(p.name+"-shm").unlink(missing_ok=True); p.write_bytes(sys.stdin.buffer.read()); p.chmod(0o600); shutil.chown(p, user="planner", group="planner")' < backups/chores.sqlite3
docker compose up -d
```

This replaces the current database. Verify the backup first and retain a backup of current data before replacing it.

## Verification and operations

```sh
docker compose logs --tail=100 planner
docker compose exec planner python -c 'from server import Store; import os; s=Store(os.environ["DB_PATH"]); print("SQLite revision:", s.read()["revision"])'
```

The image runs as a non-root user with a read-only application filesystem. The data volume is writable. The health check verifies that SQLite can be read. Keep the volume on disk local to the Docker host; do not place the live SQLite file on an SMB/NFS share.

Backend tests (no dependencies beyond Python):

```sh
python3 -m unittest discover -s tests -v
node tests/test_storage.cjs
node tests/test_assignment.cjs
```

The Docker image pins Waitress 3.0.2 with verified PyPI artifact hashes. Docker was unavailable in the authoring environment, so image build and Compose startup must be verified on your Docker host.

## Publish on GitHub with one button

The project is hosted at https://github.com/fordbd/chore-loop. The manual **Build and publish** workflow builds and publishes `ghcr.io/fordbd/chore-loop` for both Linux Intel/AMD (`amd64`) and ARM (`arm64`). It uses GitHub-hosted runners and GitHub's automatic token; no local runner or publishing token is required.

1. Open https://github.com/fordbd/chore-loop/actions.
2. Select **Build and publish**.
3. Click **Run workflow**, select `main`, and leave the tag as `latest` or enter a version such as `v1.0.0`.
4. Wait for the workflow to succeed. It runs the tests and starts a test container before publishing.
5. Copy the image reference from the workflow summary or your GitHub package page.

It runs only when you click **Run workflow**, not on every Git push. Each build publishes both the chosen tag and `sha-<full-commit-SHA>`. A moving tag such as `latest` is convenient; use version/commit tags or a digest when you want a fixed deployment version.

Use this reference in the standalone Docker command above:

```sh
IMAGE_REF='ghcr.io/fordbd/chore-loop:latest'
```

GitHub creates packages as private by default. For a private package, sign in on your Docker host:

```sh
docker login ghcr.io --username fordbd
```

Enter a GitHub personal access token (classic) with `read:packages` at the password prompt. Keep the token out of this repository and shell command arguments. Alternatively, explicitly make the **container package** public in GitHub's package settings to allow anonymous image downloads; source-repository visibility and package visibility are separate settings. Household records are stored only in your Docker host's SQLite volume and are not packaged in the image.

For deployment without a source checkout, copy `compose.registry.yaml` and `.env.example` onto the Docker host, set `APP_PASSWORD` and `APP_IMAGE=ghcr.io/fordbd/chore-loop:latest` in `.env`, then run:

```sh
docker compose -f compose.registry.yaml pull
docker compose -f compose.registry.yaml up -d
```

Compose is optional: the standalone `docker run` instructions above work with exactly the same image. Updates require pulling the new image and recreating the application container with the same data volume. GitHub publishes the image but does not restart your Docker host's application automatically.

If publishing fails with a package permission error, ensure Actions is enabled and the package grants this repository write access (particularly if the package was previously created by a manual push). The workflow requests `contents: read` and `packages: write`; no other secrets are required. GitHub-hosted Actions usage is subject to the account's included minutes and billing settings.

References: [GitHub Docker publishing](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images), [GitHub Container Registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry).

Flexible monthly chores are scheduled in the week beginning on the first Monday of their month; flexible quarterly chores use the first Monday of Jan, Apr, Jul, or Oct. Chores with an explicit weekday retain their first-occurrence weekday schedule.
