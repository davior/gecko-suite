# gecko-suite — Claude Instructions

The shared infrastructure for the Gecko Suite: [gecko-notes](https://github.com/davior/gecko-notes)
(scripts, and today the identity provider), [GAM](https://github.com/davior/gam) (the
media library) and [GVC](https://github.com/davior/gvc) (the video creator). It starts
with AI: providers, models, task routing, keys and usage for all three apps. Identity
and shared preferences move here later.

Read [`docs/plan-of-attack.md`](docs/plan-of-attack.md) before starting a milestone. It
records where things stand, the milestone order and the reasoning behind decisions that
would otherwise look arbitrary.

## What the suite is, and is not

- **A gateway.** Apps send AI requests here; the suite calls the vendor. Provider keys
  never leave this service: no endpoint returns one, and reads expose
  `api_key_configured` only.
- **The suite carries the tool-call protocol; the apps run the tools.** The suite never
  calls back into an app.
- **It keeps no app content.** Vectors stay in GAM, generated media is ingested by GAM,
  conversations stay in each app. Content is referenced by opaque `subject_ref` strings.
- **The apps must survive it being down**, the way they survive no provider being
  configured. Never design a feature that makes an app unusable without the suite.

Changes the suite needs from Notes, GAM or GVC are specified in the plan ("Changes needed
in the other apps"). Don't apply them from here, since those are separate repositories.
When you need to know how one of them does something, **read its source, not its
README**. GVC is the place to look first for a pattern; it is the newest of the three.

## Code conventions

**API contract**
- Single resources: `{"data": T}`. Lists: `{"data": [T], "total", "limit", "offset"}`.
- Errors: `{"detail": {"code": "snake_case_code", "message": "human sentence"}}`. The
  callers are mostly the other apps' servers, which pass these through to their own
  frontends, so the code and message must make sense there too.

**Backend**
- All environment configuration goes through `app/config.py`. Never `os.getenv` at
  module scope.
- Secrets at rest go through `app/crypto.py` (`encrypt` / `decrypt`), whose key is
  `SUITE_ENCRYPTION_KEYS`, never derived from `JWT_SECRET_KEY`.
- Schema changes are Alembic migrations. Never a `try: ALTER TABLE …; except: pass`.
- Get the caller with the `CurrentUser` dependency; admins with `AdminUser`.
- Structured data is JSON-as-TEXT in a `str` column, decoded on read.
- Primary keys are `str(uuid.uuid4())` generated at the call site. `User.id` is the
  exception: it is the token's `sub`, never generated, because M6 depends on it.
- Timestamps are naive UTC, named `created_at` / `updated_at`, and come from
  `app.clock.utcnow`: never `datetime.utcnow()`, never `datetime.now(UTC)`.
- An unknown cost is `None`, never 0. Ollama is a true 0.
- Model ids and prices are data (catalogue rows), not code.
- Tests go through `TestClient` against real routes. Upstream vendors are faked with a
  swappable httpx transport; no test reaches the network.

**Frontend**
- Types live beside the api module that owns them. There is no `types/` directory.
- One `xApi = { … }` object per backend router, in `src/api/`, over the shared axios
  client.
- Zustand stores without middleware. Guard every await with a staleness check before
  `set(...)`, expose `reset()`, and fan it out on logout.
- Imports use the `@/` alias. No barrel `index.ts` files.
- 2-space indent, single quotes, no semicolons, enforced by Prettier.

**Comments** explain *why*, not *what*. A comment that records a constraint, a rejected
alternative or a load-bearing subtlety is the most valuable thing in the file.

## After every change (dev environment)

Dev ports are **8003 and 5176**. Notes uses 8000/5173, GAM 8001/5174 and GVC 8002/5175,
and all four run at once. `vite.config.ts`'s proxy target has to match the port uvicorn
is started on, or the frontend reaches a sibling's backend, which shares
`JWT_SECRET_KEY` and answers instead of erroring.

The backend runs on **Python 3.13**, pinned by `.python-version` and matched by
`backend/Dockerfile` and CI. Build the venv with `uv venv` or `python3.13 -m venv`,
never a bare `python3`.

Migrations don't run on startup. `alembic upgrade head` lives in
`backend/entrypoint.sh`, and nothing runs it for a bare `uvicorn`.

```bash
# Backend — http://localhost:8003
cd backend
alembic upgrade head
uvicorn app.main:app --reload --port 8003

# Frontend — http://localhost:5176 (proxies /api to :8003)
cd frontend && npm run dev
```

The importer and key rotation are commands, run from `backend/`:

```bash
python -m app.tools.import_legacy --notes ../../gecko-notes/data/db/notes.db --gam ../../gam/data/db/gam.db --gvc ../../gvc/data/db/gvc.db --dry-run
python -m app.tools.rotate_keys --dry-run
```

`gecko-suite-client` lives in `client/` and is tested from the backend suite
(`tests/test_client_package.py`), against the real routes.

`.env` needs `JWT_SECRET_KEY` (the same as Notes') and `SUITE_ENCRYPTION_KEYS`. For local
use without Notes running, also set `ENVIRONMENT=development` and
`DEV_AUTH_USER=<any id>`.

Under Docker the frontend is a compiled bundle served by Nginx:
`docker compose up --build -d`, then hard-refresh.

## Before pushing

```bash
cd backend && pytest -q
cd frontend && npm test && npm run build    # build also typechecks
```

## After committing to a non-main branch

Report the pull request for that branch: number, URL, title, draft and CI status. If
none exists, open a **draft** PR and report it.

Keep the PR's **Deployment** section (from `.github/pull_request_template.md`) true to
what the branch now contains, updating it with each push, so the instructions are
reviewed with the code rather than written after the merge.

## After every commit to main (production deployment)

Whenever a commit lands on `main`, including a pull request merging (a merge event on a
watched PR), **output deployment instructions for that specific change**, worked out from
what it actually changed. Not the generic block on its own: a reader should be able to
deploy from the instructions without opening the diff.

Start from the PR's **Deployment** section (the template asks for one), check it against
the merged diff, and cover each of these that applies, in the order they must be done:

1. **Before pulling:** anything that must exist first, such as a sibling app's change
   (the plan's "Changes needed" ids), a DNS entry, or a backup. Back up
   `data/db/suite.db` before any migration that rewrites or drops data.
2. **Reverse proxy:** any change to how the suite is reached goes through Caddy in the
   [`infra`](https://github.com/davior/infra) repo (`Caddyfile`). Give the exact block,
   and the steps to deploy it there (`git pull`, then `docker compose restart caddy`: the
   Caddyfile is a single-file bind mount, so `caddy reload` keeps reading the old file).
3. **Configuration:** every new or changed variable in `.env.example` and
   `app/config.py`, what to set it to, and whether the app refuses to start without it.
   Never print a secret's value; say how to generate one.
4. **Deploy:** the commands below, saying which services the change rebuilds (the
   backend, the frontend, or both).
5. **Migrations:** name each new revision in `backend/alembic/versions/`. They run on
   container start (`entrypoint.sh`); say what each one does to existing data and whether
   it can be downgraded.
6. **One-off steps after deploying:** commands to run once (the importer, key rotation,
   issuing an app secret), with the exact `docker compose exec suite-backend …` line.
7. **Other apps:** what Notes, GAM or GVC must change, set or redeploy for this to take
   effect, or "nothing" if they are unaffected.
8. **Verify:** checks specific to this change, beyond `/api/health`.
9. **Roll back:** what undoing it takes (the previous commit, a downgrade, a restore).

If a step does not apply, leave it out rather than writing "none". The base commands:

```bash
git pull origin main
docker compose up --build -d
docker compose ps
curl -f http://localhost:${APP_PORT}/api/health
```

- `data/` is bind-mounted (the database), so it survives rebuilds.
- `JWT_SECRET_KEY` must be set and must **match gecko-notes'**. The app refuses to start
  without it.
- `SUITE_ENCRYPTION_KEYS` must be set; the app refuses to start without it. **Back it up
  separately from `data/`.** Without it, every stored provider key is unreadable.
- Behind the reverse proxy, set `COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml`
  in `.env`.

The first deployment of the suite is written out in [`docs/deploying.md`](docs/deploying.md).
