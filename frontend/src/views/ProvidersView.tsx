import { useEffect, useMemo, useState } from 'react'
import { CheckCircle2, Plus, Trash2, XCircle } from 'lucide-react'
import type { Scope } from '@/api/envelope'
import {
  CAPABILITIES,
  describePrice,
  modelsApi,
  type Capability,
  type Model,
} from '@/api/models'
import {
  providersApi,
  vendorLabel,
  VENDORS,
  type Provider,
  type TestResult,
  type Vendor,
} from '@/api/providers'
import { ErrorNote, Loading, PageHeader, ScopeBadge } from '@/components/Bits'
import { useAuthStore } from '@/stores/auth'
import { useRegistryStore } from '@/stores/registry'

/**
 * Providers (a key and an endpoint) and the models reached through each.
 *
 * A key is entered once and never shown again: the suite returns only whether one is
 * set. Shared providers are everyone's and only an admin edits them; anyone can add
 * their own.
 */
export default function ProvidersView() {
  const { providers, models, status, error, load } = useRegistryStore()

  useEffect(() => {
    void load()
  }, [load])

  const shared = providers.filter((p) => p.scope === 'instance')
  const own = providers.filter((p) => p.scope === 'user')

  return (
    <>
      <PageHeader title="Providers">
        A provider is one API key and where it is used. Add the models it can reach, then
        choose which model does which job under Routing.
      </PageHeader>
      <ErrorNote message={error} />
      {status === 'loading' && <Loading />}
      <AddProvider />
      {[
        { title: 'Shared with everyone', rows: shared },
        { title: 'Yours', rows: own },
      ].map(
        ({ title, rows }) =>
          rows.length > 0 && (
            <section key={title} className="space-y-3">
              <h3 className="text-sm font-semibold uppercase tracking-wide text-gray-500">
                {title}
              </h3>
              {rows.map((p) => (
                <ProviderCard
                  key={p.id}
                  provider={p}
                  models={models.filter((m) => m.provider_id === p.id)}
                />
              ))}
            </section>
          )
      )}
      {status === 'ready' && providers.length === 0 && (
        <p className="text-sm text-gray-500">
          No providers yet. Add one above to get started.
        </p>
      )}
    </>
  )
}

function AddProvider() {
  const isAdmin = useAuthStore((s) => s.user?.is_admin ?? false)
  const run = useRegistryStore((s) => s.run)
  const [open, setOpen] = useState(false)
  const [vendor, setVendor] = useState<Vendor>('anthropic')
  const [name, setName] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [baseUrl, setBaseUrl] = useState('')
  const [anthropicApi, setAnthropicApi] = useState(false)
  const [scope, setScope] = useState<Scope>('user')
  const [error, setError] = useState<string | null>(null)
  const spec = VENDORS.find((v) => v.id === vendor)!

  if (!open) {
    return (
      <button type="button" className="btn btn-primary" onClick={() => setOpen(true)}>
        <Plus className="h-4 w-4" /> Add a provider
      </button>
    )
  }

  async function save() {
    const message = await run(() =>
      providersApi.create({
        scope,
        vendor,
        name: name || spec.label,
        api_key: apiKey,
        base_url: baseUrl || null,
        use_anthropic_api: spec.anthropicSwitch && anthropicApi,
      })
    )
    setError(message)
    if (!message) {
      setOpen(false)
      setName('')
      setApiKey('')
      setBaseUrl('')
    }
  }

  return (
    <div className="card space-y-3 p-4">
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="block">
          <span className="label">Vendor</span>
          <select
            className="input"
            value={vendor}
            onChange={(e) => setVendor(e.target.value as Vendor)}
          >
            {VENDORS.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
              </option>
            ))}
          </select>
        </label>
        <label className="block">
          <span className="label">Name</span>
          <input
            className="input"
            value={name}
            placeholder={spec.label}
            onChange={(e) => setName(e.target.value)}
          />
        </label>
        {spec.needsKey && (
          <label className="block">
            <span className="label">API key</span>
            <input
              className="input"
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
            />
          </label>
        )}
        {spec.baseUrl !== 'none' && (
          <label className="block">
            <span className="label">
              Address{spec.baseUrl === 'optional' ? ' (optional)' : ''}
            </span>
            <input
              className="input"
              value={baseUrl}
              placeholder={vendor === 'ollama' ? 'http://localhost:11434' : 'https://…'}
              onChange={(e) => setBaseUrl(e.target.value)}
            />
          </label>
        )}
      </div>
      {spec.anthropicSwitch && (
        <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input
            type="checkbox"
            checked={anthropicApi}
            onChange={(e) => setAnthropicApi(e.target.checked)}
          />
          Use the Anthropic-compatible endpoint
          {vendor === 'deepseek' && ' (gives DeepSeek built-in web search)'}
        </label>
      )}
      {isAdmin && (
        <label className="flex items-center gap-2 text-sm text-gray-700 dark:text-gray-300">
          <input
            type="checkbox"
            checked={scope === 'instance'}
            onChange={(e) => setScope(e.target.checked ? 'instance' : 'user')}
          />
          Share with everyone
        </label>
      )}
      <ErrorNote message={error} />
      <div className="flex gap-2">
        <button type="button" className="btn btn-primary" onClick={() => void save()}>
          Save
        </button>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => setOpen(false)}
        >
          Cancel
        </button>
      </div>
    </div>
  )
}

function ProviderCard({ provider, models }: { provider: Provider; models: Model[] }) {
  const run = useRegistryStore((s) => s.run)
  const [newKey, setNewKey] = useState('')
  const [test, setTest] = useState<TestResult | null>(null)
  const [testing, setTesting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function runTest() {
    setTesting(true)
    try {
      setTest(await providersApi.test(provider.id, models[0]?.model_id))
    } catch {
      setTest({ success: false, message: 'The test could not be run' })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="card space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h4 className="font-medium text-gray-900 dark:text-gray-100">{provider.name}</h4>
        <ScopeBadge scope={provider.scope} />
        <span className="text-sm text-gray-500">{vendorLabel(provider.vendor)}</span>
        {provider.base_url && (
          <span className="text-xs text-gray-400">{provider.base_url}</span>
        )}
        {!provider.enabled && (
          <span className="text-xs font-medium text-amber-600">Switched off</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-gray-500">
            {provider.api_key_configured ? 'Key set' : 'No key'}
          </span>
          <button
            type="button"
            className="btn btn-secondary px-3 py-1"
            disabled={testing}
            onClick={() => void runTest()}
          >
            {testing ? 'Testing…' : 'Test'}
          </button>
          {provider.writable && (
            <>
              <button
                type="button"
                className="btn btn-ghost px-3 py-1"
                onClick={() =>
                  void run(() =>
                    providersApi.update(provider.id, { enabled: !provider.enabled })
                  )
                }
              >
                {provider.enabled ? 'Switch off' : 'Switch on'}
              </button>
              <button
                type="button"
                className="btn btn-ghost p-2 text-red-600"
                aria-label={`Delete ${provider.name}`}
                onClick={() => {
                  if (
                    window.confirm(
                      `Delete ${provider.name}, its models, and the routing that uses them?`
                    )
                  ) {
                    void run(() => providersApi.remove(provider.id))
                  }
                }}
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </>
          )}
        </div>
      </div>
      {test && (
        <p
          className={`flex items-center gap-1 text-sm ${test.success ? 'text-green-700' : 'text-red-700'}`}
        >
          {test.success ? (
            <CheckCircle2 className="h-4 w-4" />
          ) : (
            <XCircle className="h-4 w-4" />
          )}
          {test.message}
        </p>
      )}
      {provider.writable && (
        <div className="flex gap-2">
          <input
            className="input max-w-xs"
            type="password"
            autoComplete="off"
            placeholder={provider.api_key_configured ? 'Replace the key' : 'Set a key'}
            value={newKey}
            onChange={(e) => setNewKey(e.target.value)}
          />
          <button
            type="button"
            className="btn btn-secondary"
            disabled={!newKey}
            onClick={async () => {
              setError(
                await run(() => providersApi.update(provider.id, { api_key: newKey }))
              )
              setNewKey('')
            }}
          >
            Save key
          </button>
        </div>
      )}
      <ErrorNote message={error} />
      <ModelList provider={provider} models={models} />
    </div>
  )
}

function ModelList({ provider, models }: { provider: Provider; models: Model[] }) {
  const { catalog, run } = useRegistryStore()
  const [modelId, setModelId] = useState('')
  const [error, setError] = useState<string | null>(null)
  const known = useMemo(
    () => catalog.filter((c) => c.vendor === provider.vendor),
    [catalog, provider.vendor]
  )

  return (
    <div className="space-y-2">
      {models.map((m) => (
        <ModelRow key={m.id} model={m} />
      ))}
      {provider.writable && (
        <div className="flex flex-wrap gap-2">
          <input
            className="input max-w-xs"
            list={`catalog-${provider.id}`}
            placeholder="Add a model (e.g. claude-opus-5-5)"
            value={modelId}
            onChange={(e) => setModelId(e.target.value)}
          />
          <datalist id={`catalog-${provider.id}`}>
            {known.map((c) => (
              <option key={c.model_id} value={c.model_id}>
                {c.label}
              </option>
            ))}
          </datalist>
          <button
            type="button"
            className="btn btn-secondary"
            disabled={!modelId.trim()}
            onClick={async () => {
              // A known model comes back filled in from the catalogue; an unknown one
              // gets "chat" to start with, and its capabilities can be ticked below.
              const isKnown = known.some((c) => c.model_id === modelId.trim())
              setError(
                await run(() =>
                  modelsApi.create(provider.id, {
                    model_id: modelId.trim(),
                    ...(isKnown ? {} : { capabilities: ['chat'] as Capability[] }),
                  })
                )
              )
              setModelId('')
            }}
          >
            <Plus className="h-4 w-4" /> Add model
          </button>
        </div>
      )}
      <ErrorNote message={error} />
    </div>
  )
}

/**
 * The voices a text-to-speech model offers and how to ask it for one.
 *
 * Speech is routed (`speech.tts`) to a model row, and this is what the row carries: Video
 * Creator has no voice setting of its own to send. The request format differs per fal
 * endpoint (`text` or `prompt`, `voice` or `voice_id`), so it is data here and not code.
 */
function SpeechSettings({ model }: { model: Model }) {
  const run = useRegistryStore((s) => s.run)
  const [voices, setVoices] = useState(() =>
    (model.voices ?? []).map((v) => (typeof v === 'string' ? v : v.id)).join('\n')
  )
  const [params, setParams] = useState(() =>
    model.params ? JSON.stringify(model.params, null, 2) : ''
  )
  const [error, setError] = useState<string | null>(null)

  async function save() {
    let parsed: Record<string, unknown> = {}
    if (params.trim()) {
      try {
        const value: unknown = JSON.parse(params)
        if (typeof value !== 'object' || value === null || Array.isArray(value)) {
          throw new Error('not an object')
        }
        parsed = value as Record<string, unknown>
      } catch {
        setError(
          'The request settings must be a JSON object, like {"default_voice": "Aria"}.'
        )
        return
      }
    }
    setError(
      await run(() =>
        modelsApi.update(model.id, {
          voices: voices
            .split('\n')
            .map((v) => v.trim())
            .filter(Boolean),
          params: parsed,
        })
      )
    )
  }

  const deepgram = model.vendor === 'deepgram'
  return (
    <div className="mt-3 space-y-2 border-t border-gray-100 pt-3 dark:border-gray-700">
      <label className="label" htmlFor={`voices-${model.id}`}>
        Voices (one per line)
      </label>
      <textarea
        id={`voices-${model.id}`}
        className="input h-24 font-mono text-xs"
        value={voices}
        onChange={(e) => setVoices(e.target.value)}
      />
      <label className="label" htmlFor={`params-${model.id}`}>
        Request settings (JSON)
      </label>
      <textarea
        id={`params-${model.id}`}
        className="input h-28 font-mono text-xs"
        placeholder={
          deepgram
            ? '{"default_voice": "flux-haley-en", "expressivity": 0, "speed": 1.0}'
            : '{"default_voice": "Aria", "text_field": "text", "voice_field": "voice"}'
        }
        value={params}
        onChange={(e) => setParams(e.target.value)}
      />
      <p className="text-xs text-gray-500">
        {deepgram
          ? 'The voice is Deepgram’s model name. expressivity is -2 to 2, speed 0.85 to 1.15.'
          : 'default_voice is used when a caller names none. text_field and voice_field are the endpoint’s own field names; extra_params are sent with every request.'}
      </p>
      <ErrorNote message={error} />
      <button type="button" className="btn btn-secondary" onClick={() => void save()}>
        Save voices and settings
      </button>
    </div>
  )
}

function ModelRow({ model }: { model: Model }) {
  const run = useRegistryStore((s) => s.run)
  const [editing, setEditing] = useState(false)

  function toggle(cap: Capability) {
    const next = model.capabilities.includes(cap)
      ? model.capabilities.filter((c) => c !== cap)
      : [...model.capabilities, cap]
    void run(() => modelsApi.update(model.id, { capabilities: next }))
  }

  return (
    <div className="rounded-lg border border-gray-100 px-3 py-2 dark:border-gray-700">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium text-gray-800 dark:text-gray-200">
          {model.label}
        </span>
        {model.label !== model.model_id && (
          <code className="text-xs text-gray-500">{model.model_id}</code>
        )}
        <span className="text-xs text-gray-500">
          {model.capabilities.join(' · ') || 'no capabilities set'}
        </span>
        <span className="text-xs text-gray-400">
          {describePrice(model.effective_pricing, model.vendor)}
        </span>
        {!model.is_active && (
          <span className="text-xs font-medium text-amber-600">Off</span>
        )}
        {model.writable && (
          <div className="ml-auto flex gap-1">
            <button
              type="button"
              className="btn btn-ghost px-2 py-1"
              onClick={() => setEditing(!editing)}
            >
              {editing ? 'Done' : 'Edit'}
            </button>
            <button
              type="button"
              className="btn btn-ghost px-2 py-1"
              onClick={() =>
                void run(() =>
                  modelsApi.update(model.id, { is_active: !model.is_active })
                )
              }
            >
              {model.is_active ? 'Switch off' : 'Switch on'}
            </button>
            <button
              type="button"
              className="btn btn-ghost p-1 text-red-600"
              aria-label={`Remove ${model.label}`}
              onClick={() => void run(() => modelsApi.remove(model.id))}
            >
              <Trash2 className="h-4 w-4" />
            </button>
          </div>
        )}
      </div>
      {editing && (
        <div className="mt-2 flex flex-wrap gap-3">
          {CAPABILITIES.map((cap) => (
            <label
              key={cap}
              className="flex items-center gap-1 text-xs text-gray-700 dark:text-gray-300"
            >
              <input
                type="checkbox"
                checked={model.capabilities.includes(cap)}
                onChange={() => toggle(cap)}
              />
              {cap}
            </label>
          ))}
        </div>
      )}
      {editing && model.capabilities.includes('tts') && <SpeechSettings model={model} />}
    </div>
  )
}
