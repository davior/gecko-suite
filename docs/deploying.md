# Deploying gecko-suite

How to put the suite on the server for the first time. After that, every merge to `main`
comes with its own deployment steps (see `CLAUDE.md`, "After every commit to main").

Nothing in Notes, GAM or GVC calls the suite yet; they start to in M2. Deploying it now
changes nothing for them, and it can be taken down again with no effect on them.

## 1. Before you start

- **A name and a route.** Point `suite.geckopico.com` at the server. The reverse proxy is
  Caddy, in the [`infra`](https://github.com/davior/infra) repo: add a site to its
  `Caddyfile`, beside `notes.geckopico.com`:

  ```
  suite.geckopico.com {
      encode gzip

      log {
          output stdout
          level INFO
      }

      reverse_proxy suite-frontend:80
  }
  ```

  One `reverse_proxy` is all it needs, unlike Notes' entry: the suite's backend is not on
  the `web` network, and the frontend's nginx already passes `/api` to it, chat streams
  included. `suite-frontend` is the alias `docker-compose.prod.yml` gives the container
  on that network. `gecko-suite-frontend-1` would also work, but only for as long as the
  checkout directory is called `gecko-suite`.

  Commit that to `infra`. Deploy it on the server **after** the suite is running (step 3),
  because Caddy has nothing to route to before then:

  ```bash
  cd ../infra && git checkout -- Caddyfile && git pull origin main
  ```

  ```bash
  docker compose restart caddy
  ```

  A restart rather than `caddy reload`: the Caddyfile is a single-file bind mount, and
  `git pull` replaces the file rather than editing it in place, so a running container
  goes on seeing the old one. Check that it sees the new one, then that Caddy obtained
  the certificate:

  ```bash
  docker compose exec caddy grep -A1 suite.geckopico.com /etc/caddy/Caddyfile
  ```

  ```bash
  docker compose logs caddy | grep -i suite.geckopico.com
  ```

  [davior/infra#2](https://github.com/davior/infra/pull/2) makes both changes: it first
  brings the repo's Caddyfile in line with the server's (which had GAM's and GVC's sites
  uncommitted), then adds this site. Because the server's local copy is now in the
  repo, discard it before pulling (`git checkout -- Caddyfile`), or the pull conflicts.
  `infra`'s `docker-compose.yml` needs nothing: the suite joins the `web` network it
  already creates.

- **Sign-in works through Notes.** Notes must set its session cookie for the whole domain
  (`AUTH_COOKIE_DOMAIN=.geckopico.com` in Notes' `.env`; GAM already depends on this).
  Notes' login page already sends people back to any `*.geckopico.com` address, so the
  suite needs nothing added there.

## 2. Configure

On the server, next to the other apps:

```bash
git clone https://github.com/davior/gecko-suite.git
```

```bash
cd gecko-suite && cp .env.example .env
```

Then edit `.env`:

| Variable | Set it to |
|---|---|
| `JWT_SECRET_KEY` | **The same value as Notes', GAM's and GVC's.** Otherwise every sign-in is refused. |
| `SUITE_ENCRYPTION_KEYS` | A new key, from the command below. It encrypts every API key the suite holds. **Store a copy somewhere other than this server's `data/`**: a database backup without it is a backup of keys nobody can read. |
| `ADMIN_USERS` | Your Gecko Notes username. Admins set up the providers and routing everyone shares. |
| `NOTES_BASE_URL` | `https://notes.geckopico.com` (the default). |
| `COMPOSE_FILE` | `docker-compose.yml:docker-compose.prod.yml`, so the frontend joins the `web` network. |
| `APP_PORT` | Leave at `18084` unless it is taken. |

Generate the encryption key:

```bash
docker run --rm python:3.13-slim sh -c "pip install -q cryptography && python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'"
```

## 3. Start it

```bash
docker compose up --build -d
```

```bash
docker compose ps
```

```bash
curl -f http://localhost:18084/api/health
```

Migrations run when the backend container starts. On a new database that creates every
table and seeds the model catalogue (models, limits and list prices). If the backend
exits instead, `docker compose logs suite-backend` says why; a missing or malformed
`JWT_SECRET_KEY` or `SUITE_ENCRYPTION_KEYS` is the usual reason.

Now deploy the `infra` change from step 1. Then open `https://suite.geckopico.com`. You
should arrive signed in (through the Notes
cookie) and see "(admin)" beside your name.

## 4. Bring across what the apps already have (optional)

The importer copies the three apps' AI providers and keys, which model each app used,
GAM's embedding settings, fal and Deepgram keys, usage history, and Notes' prompts and
recipes. It only reads the old databases, and running it twice adds nothing the second
time.

The suite's container cannot see the other apps' files, so copy their databases into its
`data/` first. Use `sqlite3 .backup` rather than `cp`: the apps keep running, and a plain
copy of a database in WAL mode can come out inconsistent (`apt install sqlite3` if the
server lacks it). Adjust the paths to where the apps live:

```bash
mkdir -p data/import
```

```bash
sqlite3 ../gecko-notes/data/db/notes.db ".backup data/import/notes.db"
```

```bash
sqlite3 ../gam/data/db/gam.db ".backup data/import/gam.db"
```

```bash
sqlite3 ../gvc/data/db/gvc.db ".backup data/import/gvc.db"
```

See what it would do, then do it:

```bash
docker compose exec suite-backend python -m app.tools.import_legacy --notes /app/data/import/notes.db --gam /app/data/import/gam.db --gvc /app/data/import/gvc.db --dry-run
```

```bash
docker compose exec suite-backend python -m app.tools.import_legacy --notes /app/data/import/notes.db --gam /app/data/import/gam.db --gvc /app/data/import/gvc.db
```

A warning that a key "could not be decrypted" means that row was written under an older
`JWT_SECRET_KEY`; the provider comes across without its key, ready for it to be entered
again. Then delete the copies, which hold the apps' encrypted keys:

```bash
rm -r data/import
```

Check the result on the Providers, Routing and Usage screens.

## 5. App credentials (not yet)

Issuing secrets for Notes, GAM and GVC (the Apps screen) waits until they are changed to
use the suite in M2. Each one is shown once, when issued, and goes into that app's `.env`.

## Rolling back

```bash
docker compose down
```

Nothing else depends on the suite yet, and `data/` keeps the database for next time.
Remove the site from `infra`'s Caddyfile too, or Caddy logs a failed upstream on every
visit.
