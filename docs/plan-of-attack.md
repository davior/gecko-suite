# gecko-suite — plan of attack

gecko-suite is the shared infrastructure for the Gecko Suite: [gecko-notes](https://github.com/davior/gecko-notes)
(scripts, and today the identity provider), [GAM](https://github.com/davior/gam) (the
media library) and [GVC](https://github.com/davior/gvc) (the video creator).

It starts with **AI**: one place to register providers and models, decide which model
does which job, keep the keys, and count the cost. Every app sends its AI work through
it. **Identity** (sign-in, accounts, 2FA) moves here later, then shared preferences
such as themes.

This document records where things stand, the milestone order, and the reasoning behind
decisions that would otherwise look arbitrary.

## Where things stand

| Milestone | Status |
|---|---|
| M0 Bootstrap | Done. Backend skeleton, auth (verifying Notes' JWT), the encryption scheme, CI, this plan |
| M1 AI core | Done. Registry, chat gateway with tools and streaming, embeddings, usage ledger, app credentials, prompts and personas, web search, importer, key rotation, `gecko-suite-client`, the settings frontend. See "M1 as built" |
| M2 First consumers | Next. Needs SA-1/SA-2 and SV-1/SV-2 in GAM and GVC |
| M3–M8 | Not started |

## Why this exists

All three apps manage AI separately, and the copies have already drifted:

| | gecko-notes | GAM | GVC |
|---|---|---|---|
| `AIProvider` (per user, one model per row, Fernet key derived from `JWT_SECRET_KEY`) | the original | ported, with fixes | ported again |
| `UsageEvent` + `pricing.py` (substring price table) | tts/stt/ai/image | adds `asset_id`, fal billed cost | `ai` only |
| Chat client | body built in the browser; backend proxies with streaming; server-side web search only | `LLMProvider.complete()`: one prompt, no tools | `ChatProvider.conversation()`: tools, vision, cache_control, thinking replay |
| Image / video generation | fal (`ModelCatalogEntry`) | fal (`GenerationModel`, M8, `/api/generate`) | through GAM |
| TTS / STT | fal + Deepgram | Deepgram (STT) | through Notes (GVC open decision #3) |
| Embeddings | none | `UserSetting` keys, OpenAI / Ollama | none |
| Prompts / personas | `SystemPrompt`, `Recipe`, `ai_prefill` | none | none |
| Web search | brave / tavily / searxng / ddg | none | none |

An assistant for GAM would have been a fourth copy. GAM's own plan deferred extracting a
shared `gecko-core` until GVC existed as a second consumer; GVC now exists, and a
service rather than a library is what lets a provider be registered once instead of once
per app.

## Decisions

**D1 — A service, not a shared library.** A pip package would remove the duplicated
code, but every app would keep its own provider rows, and an AI would still be
registered three times. A service owns the rows once.

**D2 — A gateway, not a registry.** Apps send the AI request here and the suite calls the
vendor. The alternative, apps asking which key to use and calling the vendor themselves,
is less latency and less to build, but every decrypted key then travels to every app,
and every app has to report usage back honestly. As a gateway, keys never leave this
service and the usage ledger is complete by construction.

**D3 — Admin-shared providers plus personal overrides.** An admin registers providers
everyone can use (instance scope); any user can add their own key, which wins for them.
This covers one person running the suite today and several people later.

**D4 — The suite carries the tool-call protocol; the apps run the tools.** A tool touches
app data (a note, an asset, a spec), so the app runs it. The suite never calls back into
an app. See "Tool calling" below.

**D5 — Data stays where it belongs.** The suite computes embeddings, but the vectors stay
in GAM. It generates an image, but GAM ingests it as an asset. Conversations stay in each
app (`AISession`, `AssistantSession`). The suite refers to app content only by opaque
`subject_ref` strings (`gam:asset:<id>`, `gvc:project:<id>`), for usage attribution.

**D6 — Its own encryption key.** The siblings derive their Fernet key from
`JWT_SECRET_KEY`, so rotating the session secret wipes every stored key, and leaking it
leaks them. The suite holds every key in the suite, so it uses `SUITE_ENCRYPTION_KEYS`
(a `MultiFernet` list, so keys rotate) and refuses to start without it. Implemented in
`app/crypto.py`. The importer reads the siblings' values with `decrypt_legacy`, which
reproduces their derived key; they share one JWT secret, so one function reads all three.

**D7 — Identity moves later, without the apps noticing.** When sign-in moves here (M6),
user ids are preserved and the token keeps the same secret and claims
(`{sub, username, exp}`). GAM and GVC change nothing, and every `user_id` column in every
app stays valid. That is why `User.id` here is Notes' id, never a new one.

**D8 — Same skeleton as GAM and GVC.** FastAPI + SQLModel on SQLite (WAL), Alembic,
`app/config.py`, `CurrentUser`, `app.clock.utcnow`, the suite's envelope and error shape,
Python 3.13, Docker + Nginx, CI. Copied from GVC, the newest of the three.

**D9 — If the suite is down, apps degrade; they don't crash.** The same way they behave
today with no provider configured (`build_provider` returns None). GVC's "a video can be
made entirely by hand" keeps holding.

## Architecture

- **Ports:** 8003 (API) and 5176 (Vite) in development; 18084 on the host under Docker.
  Notes is 8000/5173/18081, GAM 8001/5174/18082, GVC 8002/5175/18083.
- **Auth into the suite:**
  1. *User calls:* the app forwards the caller's JWT, as GVC's `GamClient` does today.
  2. *Background jobs* (GAM enrichment, the GVC and Notes workers) can outlive a token.
     Each app gets a service credential (`SUITE_APP_ID` + `SUITE_APP_SECRET`, stored
     hashed in `AppClient`) and sends `X-Gecko-App` + `X-On-Behalf-Of: <user_id>`. Only
     registered apps may do this, and the app id is recorded on every usage row. (M1)

### Data model (M1 unless noted)

- **Provider** — one credential, any number of models. This fixes "one model per row".
  `id`, `scope` (instance | user), `user_id` (null for instance), `name`, `vendor`
  (anthropic, openai, deepseek, ollama, fal, deepgram, custom), `protocol` (anthropic |
  openai | ollama | fal | deepgram), `base_url`, `api_key` (encrypted), `extra_params`
  (JSON-as-TEXT), `enabled`, timestamps.
  Keep the rules GAM learnt (`providers/endpoints.py`, `safe_url.py`): fixed endpoints
  for openai and deepseek; custom endpoints https-only and SSRF-checked; only Ollama may
  use a private address; route by `speaks_anthropic()`, never by vendor name.
- **Model** — `provider_id`, `model_id`, `label`, `capabilities` (a JSON set: `chat`,
  `vision`, `tools`, `thinking`, `web_search`, `embed`, `image_gen`, `image_edit`,
  `video_gen`, `tts`, `stt`), `max_tokens`, `context_window`, `embed_dims`, `voices`,
  `pricing` (JSON: per-million in/out, per image, per megapixel, per second),
  `fal_fields` (text_field, voice_field, default params), `is_active`.
  Merges Notes' `ModelCatalogEntry`, GAM's `GenerationModel` and the three `pricing.py`
  tables into data. Ships a seed migration with current models and prices (GAM's table
  stops at `claude-opus-4-1`).
- **Assignment** (which model does which job) — `scope`, `user_id`, `app` (null | notes |
  gam | gvc), `task`, `model_id`, `params` (JSON).
  Tasks: `chat.assistant`, `chat.draft`, `chat.edit`, `chat.quick`, `enrich.describe`,
  `enrich.summarize`, `enrich.autotag`, `embed.text`, `image.generate`, `image.edit`,
  `video.generate`, `speech.tts`, `speech.stt`. (Web search is not a routed task: it has
  its own providers, `WebSearchProvider`, and its own resolution.)
  Resolution: user+app → user → instance+app → instance. A request may name a model
  directly, if the caller can see it. This settles GVC's open decision #4 (a capable
  model for drafts, a cheap one for edits) with no GVC code.
- **UsageEvent** — the union of the three: `user_id`, `app`, `task`, `kind` (ai, embed,
  image, video, tts, stt, search), `provider`, `model`, `units`, `unit_type`, `cost`
  (null when unknown, never 0), `currency`, `cost_estimated`, `external_ref`,
  `subject_ref`, `created_at`. Report `priced_events` next to `total_events` (GAM's
  rule), and record usage even when a reply fails to parse (GAM's known gap).
- **SystemPrompt, Recipe** — from Notes, with an `app` scope column.
- **Persona** — `name`, `avatar` (a file under `data/`), a system prompt (by id or
  inline), per-task model overrides, `app` scope, `scope`.
- **WebSearchProvider** — brave, tavily, searxng or ddg; same scope and override rules as
  Provider, key encrypted.
- **AppClient** — the registered apps and their hashed secrets.
- *M6:* `User` gains accounts proper, plus `AuthToken` and TOTP. *M7:* `UserSetting`,
  `AppSetting`, `Theme`.

### Gateway API (`/api/v1`, all in the suite's envelope)

| Endpoint | Purpose | Built from |
|---|---|---|
| `POST /ai/chat` (`stream: true` → SSE `delta` / `tool_call` / `final` / `error`) | Normalised messages, tools, images, thinking, cache hints; `task` or `model` | GVC `providers/` + Notes' SSE framing + GAM `_upstream.py` retries |
| `POST /ai/proxy/{anthropic,openai,ollama}[/stream]` | Raw passthrough, so Notes' browser-built bodies keep working during its migration | Notes `routers/settings.py` proxies |
| `POST /ai/embed` | Batch text → vectors, with model and dims | GAM `embeddings/` |
| `POST /ai/images`, `POST /ai/videos`, `GET` / `DELETE /ai/jobs/{id}` | fal jobs; the result is a short-lived URL the caller ingests | GAM `generation/` + `generation/pricing.py` |
| `POST /ai/speech/tts`, `POST /ai/speech/stt` | TTS returns audio + `X-Speech-Provider` / `X-Speech-Voice`; STT is Deepgram | Notes speech (the GN-V4 contract) + GAM Deepgram |
| `POST /search/web` | Unified web search | Notes `web_search.py` |
| `GET /ai/models?capability=`, `GET /ai/resolve?task=&app=` | What can I use, and what will this task use | new |
| `GET /prompts`, `/recipes`, `/personas` | Read by the apps' assistants | Notes models |
| `GET /usage?app=&subject=&days=` | Totals, per provider, per day, per subject | Notes `get_usage` + GAM `totals_for` |
| CRUD for all of the above, `POST /providers/{id}/test` | Management | Notes + GAM routers |

**Frontend (M1):** one AI settings area: Providers (with their models), Task routing,
Prompts & Personas, Web search, Usage (filterable by app) and, for admins, Apps. The three
apps' own provider settings are replaced by a link here.

**`gecko-suite-client` (M1):** a small Python package (installed from git) that wraps the
API: `SuiteChat` (GVC's `ChatProvider` shape), `SuiteEmbedder`, `SuiteMedia`,
`SuiteSpeech`, `SuiteSearch`, with the token / on-behalf-of plumbing built in and a
swappable transport for tests (as GVC's `outbound.client()`). It keeps each app's change
small.

## Tool calling

**Where things stand.**
- *GVC* has real tool calling: `assistant/loop.py` runs rounds, hands the model its own
  mistakes back to fix (`MAX_REPAIR_NUDGES`), and offers about twenty tools.
- *Notes* does not. Its assistant asks the model to write a JSON plan as text and parses
  it (`assistant/plan_parse.py`); `assistant/continuation.py` exists to recover turns
  where the model got that format wrong. Its only tool is Anthropic's server-side web
  search, and its OpenAI proxy has no `tools` field.
- *GAM* has none; its `LLMProvider` takes a single prompt.

**From here on, every assistant uses real tool calls.**
1. `/ai/chat` accepts `tools` and returns `tool_call`s in one shape for every protocol
   (Anthropic, OpenAI-compatible including DeepSeek, and Ollama models that support
   it). A model's `tools` capability tells an app whether it can rely on them.
   Server-side tools (Anthropic `web_search`) and the suite's own `search.web` can be
   offered as built-ins.
2. GVC's loop moves into `gecko-suite-client` as
   `run_agent(chat, system, tools, handlers, history, max_rounds, on_round)`: rounds, the
   call/result exchange, repair nudges, per-tool caps (as `ASSISTANT_MAX_GENERATIONS`)
   and progress callbacks. Each app supplies only its tool specs and handlers. GVC moves
   first, with no behaviour change, so its tests prove the extraction.
3. Notes moves to tools in M5 (`find_notes`, `read_note`, `create_note`, `update_note`,
   `link_notes`, `web_search`, `generate_image`). Its approval step stays: write tools
   record proposed changes, and a turn ends with one proposal to approve or discard,
   which is GVC's pattern and maps onto Notes' `awaiting_approval` phase. The text-plan
   path is retired behind a per-user flag once the tool path is proven.
4. GAM's new assistant starts on tools (M2): `search_assets`, `get_asset`,
   `read_transcript`, `update_metadata` (proposed, not applied), `run_enrichment`,
   `generate_image`.
5. Cross-app tools (Notes' assistant searching GAM) come later. Since tools run in the
   app, that is one app calling another's API with the user's token, as GVC already
   calls GAM. No suite change.

## Milestones

**M0 — Bootstrap.** The backend skeleton, auth verifying Notes' JWT, `/api/health`,
`/api/config`, `/api/me` with the shadow user, `app/crypto.py` (D6), the first migration,
CI, Docker, this plan and `CLAUDE.md`.

**M1 — AI core.** Provider, Model, Assignment, UsageEvent, AppClient, SystemPrompt,
Recipe, Persona and WebSearchProvider tables; CRUD, the connection test, the seed
catalogue and prices; `/ai/chat` (streaming + tools), `/ai/embed`, the usage ledger and
`/usage`; the frontend for these; `gecko-suite-client`; key rotation over the tables;
and the importer:

```bash
python -m app.tools.import_legacy --notes notes.db --gam gam.db --gvc gvc.db
```

It copies providers, de-duplicated by (user, vendor, key hash), folding "same key,
different model" rows into one provider with several models; usage history, tagged with
its app; Notes' system prompts and recipes; and the fal, Deepgram and embedding keys out
of the `UserSetting` tables.

**M1 as built.** What differs from the design above, and why:
- **Two kinds of row the design did not name.** `CatalogEntry` holds the known models and
  their list prices as migration-seeded data; a model with no price of its own is priced
  from it at call time, so a later price migration reaches every model. Family rows
  (`claude-opus-*`) price ids the catalogue does not list.
- **One level of task fallback** (`chat.draft` → `chat.assistant`, `image.edit` →
  `image.generate`), so one assignment makes every chat-shaped task work. The task's own
  four rules are tried before its fallback's.
- **The gateway is stateless, and replays verbatim.** Every reply carries the vendor's raw
  assistant message; callers send it back unchanged, so thinking blocks survive. Switching
  protocol mid-conversation falls back to the neutral fields.
- **Anthropic's server-side refusal fallback** is on for the models the Claude API
  reference names. A fallback answer is recorded and priced as the model that served.
- **App credentials reach only the gateway** and read-only lookups (an allowlist in
  `app/auth.py`), and the app they name is the app usage is credited to.
- **The raw passthrough (`/ai/proxy/*`) is not built yet.** Notes is the only caller, and
  its cutover is M4; it lands with SN-1.
- **fal and Deepgram are registered vendors** (the importer brings their keys across) but
  are not called until M3; their connection test says so.
- **Persona avatars are a URL field for now.** Upload needs file storage the suite does
  not have yet.

**M2 — First consumers**, behind `AI_BACKEND=local|suite` in each app, so the cutover is
reversible.
- GAM: `build_provider` → `SuiteChat`, `build_embedder` → `SuiteEmbedder`. **The new GAM
  assistant is built directly on `/ai/chat` and `run_agent`**, so it never has local
  provider code.
- GVC: `providers.build_provider` → `SuiteChat`; `assistant/loop.py` → `run_agent`.
  Per-task routing replaces "one active provider".

**M3 — Media, speech, search.** fal generation moves here; GAM's `/api/generate` stays as
a facade (call the suite, ingest the result), so GVC's `GamClient.start_generation` is
unchanged. TTS / STT move here; GVC's `render/tts.py` points at the suite, closing its
open decision #3, and Notes' speech routes forward. Web search for all three; personas in
each assistant's picker.

**M4 — Notes cutover.** Notes' `/api/settings/ai-providers/proxy/*` routes forward to the
raw passthrough, so its frontend `AIService` is untouched at first. `_record_ai_usage`
goes, since the suite records usage. Its settings pages link here.

**M5 — Notes assistant on tool calls.** As "Tool calling" item 3.

**M6 — Identity.** `User` accounts, `AuthToken`, 2FA, sign-in and registration,
`/api/auth/session` and the `gecko_session` cookie move here from Notes, ids preserved
(D7). Notes becomes a verifier like GAM and GVC. Possible follow-up: RS256, so apps verify
with a public key and stop sharing a signing secret.

**M7 — Shared preferences.** UI themes and other cross-app settings. GVC's
prompt-modifier themes (its D5) stay in GVC unless there is a reason to share them.

**M8 — Remove the legacy code** in each app: local `AIProvider`, `UsageEvent`,
`pricing.py`, provider routers and settings UIs, with drop migrations in GAM and GVC.

## Changes needed in the other apps

Applied in those repositories, not from here. Ids are stable; reference them in PRs.

**gecko-notes**

| Id | Change | Milestone | Status |
|---|---|---|---|
| SN-1 | Proxy routes (`/api/settings/ai-providers/proxy/*`) forward to the suite's raw passthrough; drop `_record_ai_usage` | M4 | Open |
| SN-2 | Speech, image generation and web search call the suite | M3 / M4 | Open |
| SN-3 | Assistant moves from plan-as-text to tool calls on `run_agent`, keeping approval | M5 | Open |
| SN-4 | Hand sign-in, accounts and 2FA to the suite; keep verifying the JWT | M6 | Open |
| SN-5 | Settings → AI pages link to the suite | M4 | Open |
| SN-6 | Add the suite's origin to `CORS_ORIGIN` / CSP | M1 | Open |
| SN-7 | `SUITE_BASE_URL`, `SUITE_APP_ID=notes` and `SUITE_APP_SECRET` in `app/config.py` and `.env.example` | M4 | Open |

**infra** (the reverse proxy)

| Id | Change | Milestone | Status |
|---|---|---|---|
| SI-1 | A `suite.geckopico.com` site in the `Caddyfile`, proxying to `suite-frontend:80` (see `docs/deploying.md`) | M1 | Open |

**GAM**

| Id | Change | Milestone | Status |
|---|---|---|---|
| SA-1 | `build_provider` / `build_embedder` behind `AI_BACKEND`, using `gecko-suite-client` | M2 | Open |
| SA-2 | The new assistant, built on `/ai/chat` + `run_agent` with GAM's own tools | M2 | Open |
| SA-3 | `/api/generate` becomes a facade over the suite's fal jobs | M3 | Open |
| SA-4 | Deepgram transcription through the suite | M3 | Open |
| SA-5 | Settings → AI / embeddings / generation pages link to the suite | M2 | Open |
| SA-6 | Supersede "copy now, extract later" in `docs/plan-of-attack.md` and `docs/m6-ai-enrichment.md` | M2 | Open |
| SA-7 | `SUITE_BASE_URL`, `SUITE_APP_ID=gam` and `SUITE_APP_SECRET` in `app/config.py` and `.env.example`; enrichment jobs call as the app on the user's behalf | M2 | Open |

**GVC**

| Id | Change | Milestone | Status |
|---|---|---|---|
| SV-1 | `providers.build_provider` behind `AI_BACKEND`; per-task routing replaces the single active provider | M2 | Open |
| SV-2 | `assistant/loop.py` moves to `run_agent` in `gecko-suite-client`, behaviour unchanged | M2 | Open |
| SV-3 | `render/tts.py` calls the suite's TTS (closes open decision #3) | M3 | Open |
| SV-4 | `CLAUDE.md`: "the one runtime dependency on Notes is login" becomes "on gecko-suite, for login and AI"; update D3, D4 and open decision #4 | M2 | Open |
| SV-5 | `SUITE_BASE_URL`, `SUITE_APP_ID=gvc` and `SUITE_APP_SECRET` in `app/config.py` and `.env.example`; the assistant worker calls as the app, since it holds the token only in memory | M2 | Open |

## Open questions

1. **Streaming through two hops.** Notes' browser streams from Notes, which would stream
   from the suite. Fine on one host; worth measuring before M4.
2. **Per-app spending limits.** The ledger makes them possible; nothing asks for them
   yet.
3. **Embedding dimensions.** GAM asks for 512 dimensions from a model whose native size is
   1536. The suite passes `dimensions` through per request; GAM must keep asking for the
   same number, and store the model beside every vector, or its index mixes sizes.
4. **Ollama on a private address.** Only Ollama may use one today. If the suite runs on a
   different machine from the Ollama server, the provider's `base_url` must be reachable
   from the suite, not from the app.
