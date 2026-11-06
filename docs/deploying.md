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
| `COMPOSE_FILE` | `docker-compose.yml:docker-compose.prod.yml`, so the frontend joins the `web` network. **Without it the site answers 502**: Caddy cannot find `suite-frontend`, although the suite itself runs and passes its health check. It is commented out in `.env.example`, so uncomment it. |
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

Before touching `infra`, check the frontend joined the `web` network, which is the one
thing the health check above cannot tell you:

```bash
docker network inspect web --format '{{range .Containers}}{{.Name}} {{end}}'
```

It should list `gecko-suite-frontend-1`. If it does not, `COMPOSE_FILE` is missing from
`.env`; set it and run `docker compose up -d`, which recreates only the frontend.
Otherwise Caddy logs `lookup suite-frontend ... no such host` and every request is a 502.

Now deploy the `infra` change from step 1. Then open `https://suite.geckopico.com`. You
should arrive signed in (through the Notes cookie) and see "(admin)" beside your name.

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

## 5. Connect the apps

Each app is switched to the suite by its own `.env`, and switched back the same way. Nothing in
an app changes until its `AI_BACKEND` is set to `suite`.

### Gecko Notes

Notes in suite mode stops using its own AI keys: chat, web search, read-aloud, narration,
transcription and image generation all go through the suite, which holds the keys and counts the
spend. So the suite needs Notes' setup before Notes is switched. In order:

**1. Bring the suite up to date** (in the suite's checkout):

```bash
git pull origin main
```

```bash
docker compose up --build -d
```

**2. Import Notes' AI setup.** This copies its providers and keys, the fal.ai and Deepgram keys,
prompts, recipes and usage history. It only reads the copy, and running it again adds nothing new,
so it is safe if section 4 already ran it. Adjust the path to where Notes lives:

```bash
mkdir -p data/import
```

```bash
sqlite3 ../gecko-notes/data/db/notes.db ".backup data/import/notes.db"
```

```bash
docker compose exec suite-backend python -m app.tools.import_legacy --notes /app/data/import/notes.db --dry-run
```

```bash
docker compose exec suite-backend python -m app.tools.import_legacy --notes /app/data/import/notes.db
```

```bash
rm -r data/import
```

A key that "could not be decrypted" was saved under an older `JWT_SECRET_KEY`; enter it again on
the Providers screen.

**3. Check three screens.**
- **Providers:** the vendors Notes chats with, with the same model names, plus fal.ai (images,
  voices) and Deepgram (transcription).
- **Routing:** `speech.tts` routed to a model with the `tts` capability. Without it, read-aloud
  and narration answer "No text-to-speech model is set up".
- **Search** (optional): Notes' search settings are not imported. Add a Brave, Tavily or SearXNG
  backend here if Notes used one; otherwise the suite searches with DuckDuckGo.

**4. Issue Notes' credential.** On the Apps screen (as an administrator), issue a secret for the
app `notes` and copy it. It is shown once.

**5. Switch Notes** (in Notes' checkout). Add to its `.env`:

```
AI_BACKEND=suite
SUITE_APP_SECRET=<the secret from step 4>
```

`SUITE_BASE_URL` defaults to `https://suite.geckopico.com` and `SUITE_APP_ID` to `notes`; set them
only if yours differ. `SUITE_PUBLIC_URL` is only for when `SUITE_BASE_URL` is an address a browser
cannot open. Notes refuses to start with `AI_BACKEND=suite` and no secret, by design. Then:

```bash
git pull origin main
```

```bash
docker compose up --build -d
```

```bash
curl -f http://localhost:18081/api/health
```

**6. Check it.** Ask the assistant something: the answer comes back and the suite's Usage screen
shows it under app `notes`. Read a note aloud, generate an image, run a web search from the
assistant. Settings → AI Services in Notes now carries a note on each tab saying what the suite
holds.

**7. Optionally, tool calling.** Settings → Assistant → "Tool calling (beta)" is a per-person
switch for the assistant planning by tool calls. Everything above works without it; switch it off
to go straight back.

**Still on Notes' own keys:** live transcription and voice mode use the Deepgram key saved in
Notes, so leave it there.

**Switching back:** set `AI_BACKEND=local` in Notes' `.env` and `docker compose up -d`. Notes' own
keys and settings were never touched.

### GAM and GVC

The same idea: `AI_BACKEND=suite` in each one's `.env` (GAM also needs its `SUITE_APP_SECRET`, as
its background jobs call as the app). Their own `docs` and `.env.example` say what each needs.

## 6. Move sign-in to the suite

Until now Gecko Notes signs everyone in, and the suite, GAM and GVC check its token. This
step makes the suite the place where accounts live: sign-in, registration, password
recovery, two-step verification and the Users screen. Notes becomes a checker like the
others. Nobody is signed out: both sign the same token with the same `JWT_SECRET_KEY`, so
a session from either side keeps working everywhere. Each step below is undone by putting
the old value back.

Needs: the suite at the version with accounts (M6a and M6b) and Notes with
`IDENTITY_PROVIDER` (M6c), both deployed.

**1. Check the suite's account settings.** In the suite's `.env`, `AUTH_COOKIE_DOMAIN` must be
exactly Notes' (e.g. `.geckopico.com`), or a sign-in at the suite never reaches the other
apps. `LOGIN_REDIRECT_ORIGINS` lists the apps it may send people back to, and `SMTP_*` lets
it send reset links (copy Notes' values). Rebuild after changing any of them.

**2. Bring the accounts across.** The importer copies every Notes account by id, with its
password (the same password works), admin flag, email verification and two-step settings,
and Notes' two sign-up switches. Accounts already set up in the suite are never
overwritten, so it is safe to run again. Take a fresh copy, as in section 4:

```bash
mkdir -p data/import
```

```bash
sqlite3 ../gecko-notes/data/db/notes.db ".backup data/import/notes.db"
```

```bash
docker compose exec suite-backend python -m app.tools.import_legacy --notes /app/data/import/notes.db --dry-run
```

```bash
docker compose exec suite-backend python -m app.tools.import_legacy --notes /app/data/import/notes.db
```

```bash
rm -r data/import
```

A warning that two-step verification was switched off for someone means their
authenticator secret could not be read (it was written under an older `JWT_SECRET_KEY`);
they sign in with their password and set it up again on the Account page.

**3. Try it before switching anyone.** Open `https://suite.geckopico.com/login` in a private
window and sign in with a Notes account (and its authenticator, if it has one). The Users
screen should list everyone. Then, in the same window, open GAM or GVC: you should already
be signed in, because the suite set the shared cookie. (Not Notes yet: until step 4 it keeps
its own sign-in and does not look at the cookie.) If GAM asks you to sign in, the cookie
domains differ: fix step 1 before going on.

**4. Switch Notes.** In Notes' `.env`:

```bash
IDENTITY_PROVIDER=suite
SUITE_PUBLIC_URL=https://suite.geckopico.com
```

Then, in Notes:

```bash
docker compose up -d
```

Notes' login, registration and recovery pages now send people to the suite, and its
account settings link there. Someone signed in to Notes stays signed in.

**5. The suite, GAM and GVC.** Nothing to set: the suite signs people in on its own pages,
and GAM and GVC send people to the suite's address (`SUITE_PUBLIC_URL`, else
`SUITE_BASE_URL`), as Notes does. An old `NOTES_BASE_URL` in any of their `.env` files is
ignored, and each logs a reminder at startup until it is removed. In GAM and GVC the Notes
app's own address is `NOTES_URL` (default `https://notes.geckopico.com`).

**6. Verify.** In a private window: open GAM, which should send you to the suite's sign-in
and back to GAM once signed in; then open Notes and GVC, which should need no second
sign-in. Sign out in any of them and reload the others: they should ask you to sign in.

**Undo:** `IDENTITY_PROVIDER=notes` in Notes, restarting it. The suite, GAM and GVC keep
sending people to the suite's sign-in, which still works for every account it imported;
there is no longer a setting that points them back at Notes. Passwords changed in the suite since the
cutover are not copied back to Notes: those people use their old Notes password again.

**A limit to know:** a session lasts 30 days wherever it is checked. Disabling someone on
the Users screen stops them signing in again, but a session they already hold keeps working
in Notes, GAM and GVC until it expires. That was already true with Notes signing people in.

## 7. Themes

Themes live in the suite (its **Themes** page), and each app wears its own choice, picked from
the palette in that app's header. The apps' pages ask the suite for their theme directly, so:

1. In the suite's `.env`, `CORS_ORIGIN` lists the three apps (comma-separated, exact):

   ```bash
   CORS_ORIGIN=https://notes.geckopico.com,https://assets.geckopico.com,https://gvc.geckopico.com
   ```

   Then `docker compose up -d`. An app missing from it shows its themes but cannot save a
   choice ("Could not save that choice").
2. To bring Notes' themes across, with each person's current one as what Notes wears, run the
   importer again as in section 6 step 2 (it skips everything already imported). Theme
   backgrounds Notes stored stay on Notes, at `https://notes.geckopico.com/media/…`; pass
   `--notes-public-url` if Notes lives elsewhere.
3. Deploy the apps' theme changes (each app's PR says what it needs). Until then they look as
   they do now.

## Rolling back

```bash
docker compose down
```

Switch every app that uses the suite back first (`AI_BACKEND=local` in its `.env`, then
`docker compose up -d`): an app pointed at a suite that is down keeps working, but without AI.
`data/` keeps the database for next time.
Remove the site from `infra`'s Caddyfile too, or Caddy logs a failed upstream on every
visit.
