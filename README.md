# gecko-suite

Shared infrastructure for the Gecko Suite: [gecko-notes](https://github.com/davior/gecko-notes),
[GAM](https://github.com/davior/gam) and [GVC](https://github.com/davior/gvc).

It starts with AI. You register providers and models here once, decide which model does
which job (chat, drafting, image descriptions, embeddings, image and video generation,
speech, web search), and see what each app spent. Every app sends its AI work through
the suite, so API keys live in one place and never leave it. Sign-in and shared
preferences such as themes move here later.

**Status:** M1, the AI core. Providers, models, task routing, the chat gateway (tool
calling, streaming), embeddings, web search, prompts and personas, the usage ledger, the
importer for the three apps' existing settings, and the settings screens. The apps start
calling it in M2. See [`docs/plan-of-attack.md`](docs/plan-of-attack.md) for the design
and the milestones.

The apps call it with [`gecko-suite-client`](client/README.md).

## Running it

Copy `.env.example` to `.env` and set:

- `JWT_SECRET_KEY`: the same value gecko-notes uses.
- `SUITE_ENCRYPTION_KEYS`: a Fernet key, generated with
  `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`.
  Back it up separately from `data/`.

Then either:

```bash
docker compose up --build -d
curl -f http://localhost:18084/api/health
```

or, for development (Python 3.13, Node 20):

```bash
cd backend
uv venv && uv pip install -r requirements-dev.txt
alembic upgrade head
uvicorn app.main:app --reload --port 8003
```

```bash
cd frontend && npm install && npm run dev    # http://localhost:5176
```

Bring across what Notes, GAM and GVC already have (read-only; safe to re-run):

```bash
cd backend && python -m app.tools.import_legacy --notes notes.db --gam gam.db --gvc gvc.db --dry-run
```

Tests: `cd backend && pytest -q`, and `cd frontend && npm test`.
