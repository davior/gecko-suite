import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { assignmentsApi } from '@/api/assignments'
import { modelsApi } from '@/api/models'
import { providersApi } from '@/api/providers'
import { useAuthStore } from '@/stores/auth'
import { useRegistryStore } from '@/stores/registry'
import RoutingView from '@/views/RoutingView'
import { model, provider } from '@/test-fixtures'

beforeEach(() => {
  vi.spyOn(providersApi, 'list').mockResolvedValue([provider()])
  vi.spyOn(modelsApi, 'list').mockResolvedValue([
    model(),
    model({
      id: 'm2',
      model_id: 'deepseek-chat',
      label: 'DeepSeek Chat',
      capabilities: ['chat', 'tools'],
    }),
    model({
      id: 'm3',
      model_id: 'nomic-embed-text',
      label: 'nomic-embed-text',
      capabilities: ['embed'],
    }),
  ])
  vi.spyOn(assignmentsApi, 'list').mockResolvedValue([])
  vi.spyOn(modelsApi, 'catalog').mockResolvedValue([])
  vi.spyOn(assignmentsApi, 'resolve').mockImplementation(async (task) => ({
    task,
    app: null,
    model: task.startsWith('chat') ? model() : null,
    matched_task: 'chat.assistant',
    rule: task.startsWith('chat') ? 'instance' : null,
  }))
  useAuthStore.setState({
    user: { id: 'u', username: 'u', is_admin: false },
    status: 'authenticated',
  })
})

afterEach(() => {
  // Unmount first: a store reset re-renders whatever is still mounted, and with the
  // mocks restored its effects would reach for the real network.
  cleanup()
  useRegistryStore.getState().reset()
  useAuthStore.getState().reset()
  vi.restoreAllMocks()
})

function optionsFor(label: string): string[] {
  const select = screen.getByRole('combobox', { name: label }) as HTMLSelectElement
  return Array.from(select.options).map((o) => o.textContent ?? '')
}

describe('RoutingView', () => {
  it('offers only models that can do the job', async () => {
    render(<RoutingView />)
    await screen.findAllByText('Claude Opus 5.5')
    expect(optionsFor('Your model for enrich.describe')).toEqual([
      'Use the default',
      'Claude Opus 5.5 (Claude)',
    ])
    expect(optionsFor('Your model for embed.text')).toEqual([
      'Use the default',
      'nomic-embed-text (Claude)',
    ])
  })

  it('shows what a job uses now, and why', async () => {
    render(<RoutingView />)
    expect(
      (await screen.findAllByText('the shared choice (fallback)')).length
    ).toBeGreaterThan(0)
    expect(screen.getAllByText('Nothing — this job is off').length).toBeGreaterThan(0)
  })

  it('does not let a non-admin change the shared default', async () => {
    render(<RoutingView />)
    await screen.findAllByText('Claude Opus 5.5')
    expect(screen.queryByRole('combobox', { name: /shared model/i })).toBeNull()
  })
})
