# Chore planner — local Docker deployment

Runs the existing planner and a Python/Waitress API in one container, with a SQLite database in the `planner_data` named Docker volume. All devices use the same chart. No Cloudflare account or service is involved.

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

The named volume survives container recreation and `docker compose down`. **Do not use `docker compose down -v` unless you intend to delete the database.** Use the same project directory or Compose project name so subsequent deployments use the same named volume. Schema version 1 is initialized automatically; future schema changes should include explicit migrations and a pre-upgrade backup.

## Backups

The in-app **Back up chart** creates a portable JSON export. For an online-safe SQLite backup, run from the host:

```sh
mkdir -p backups
docker compose exec -T planner python backup.py > backups/chores.sqlite3
```

Use a different destination filename each time if you want backup history. Schedule this command with your host's backup scheduler and include the resulting files in your normal backups. The helper uses SQLite's backup API so it includes committed data even while the database is in WAL mode; do not copy just the live `.sqlite3` file while the app is running.

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
```

The Docker image pins Waitress 3.0.2 with verified PyPI artifact hashes. Docker was unavailable in the authoring environment, so image build and Compose startup must be verified on your Docker host.

## Publish images with GitLab

The included `.gitlab-ci.yml` runs backend and browser-storage tests, then builds and pushes the image to your project's GitLab Container Registry. No Docker-host login credentials are stored in the repository or CI. The pipeline uses GitLab's built-in `CI_REGISTRY`, `CI_REGISTRY_IMAGE`, `CI_REGISTRY_USER`, and `CI_REGISTRY_PASSWORD` variables.

### GitLab prerequisites

- Put the contents of this `chore-loop` directory at the root of your GitLab repository, so `.gitlab-ci.yml` and `Dockerfile` are at the repository root.
- Enable the project's Container Registry and CI/CD features. Self-managed GitLab needs its registry service configured and reachable.
- Provide a Linux GitLab Runner using the Docker executor that supports rootless BuildKit. The pipeline follows [GitLab's rootless BuildKit workflow](https://docs.gitlab.com/ci/docker/using_buildkit/); self-managed runners may require administrator configuration for user namespaces and mount operations. The publish job does not use Docker-in-Docker or mount a Docker socket.
- By default, the image architecture matches the build runner. Use a runner matching your Docker host (for example, Linux amd64 for an x86 NAS/server). ARM hosts require an ARM runner or a separately configured multi-platform build.
- For self-signed/internal registry certificates, configure the CA on both the runner's BuildKit environment and the deployment host using GitLab's documented custom-CA setup. Keep certificate verification enabled.

Push your code to the default branch. Its pipeline publishes an image tagged with the **full commit SHA**. To publish a named release:

```sh
git tag v1.0.0
git push origin v1.0.0
```

A successful release pipeline publishes `YOUR-PROJECT-REGISTRY-PATH:v1.0.0`. Find the exact image path in your project's **Deploy → Container Registry**, or the location shown for Container Registry in your GitLab version. The build job also prints the complete image reference after publication. Only `vMAJOR.MINOR.PATCH` tags trigger release publication; merge requests run tests without publishing. There is no moving `latest` tag, so deployments select an explicit version.

### Deploy a published image

Create a GitLab project deploy token with **read_registry** permission for your Docker host. This token only downloads images. Use the token's generated username with an interactive Docker login:

```sh
docker login YOUR-REGISTRY-HOST --username YOUR-DEPLOY-TOKEN-USERNAME
```

Enter the deploy token at the password prompt. The registry host may include a port; use the host from the actual image reference. Avoid using the short-lived CI registry password for your deployment host.

Copy `compose.registry.yaml` and `.env.example` to the Docker host, and create `.env` from the example. Set both:

```dotenv
APP_PASSWORD=your-household-password
APP_IMAGE=YOUR-PROJECT-REGISTRY-PATH:v1.0.0
```

Then deploy without building or needing the source checkout:

```sh
docker compose -f compose.registry.yaml pull
docker compose -f compose.registry.yaml up -d
docker compose -f compose.registry.yaml ps
```

To update, take a backup, change `APP_IMAGE` to the new release tag, and repeat `pull` and `up -d`. To revert the application image, select the previous tag and repeat those commands. A rollback to older code does not automatically undo future database schema migrations.

Use the same deployment directory/project name for updates so the SQLite named volume stays the same. If switching an existing source-build deployment to the registry file, run it from the same directory with the same Compose project name. All backup commands above also work with this deployment when you add `-f compose.registry.yaml` after `docker compose`.

GitLab builds and stores the application image; your Docker host runs the app and retains the household database. No automatic deployment to your host is configured, so a Git push does not restart your running household planner.

The pipeline and registry deployment files have not been run against your GitLab instance. Runner access, registry certificates, architecture compatibility, image build, and first container startup need verification there.
