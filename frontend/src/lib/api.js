/** Thin fetch wrapper around the VoiceOps API. */

const BASE = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
const PREFIX = `${BASE}/api/v1`

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

function buildQuery(params = {}) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue
    if (Array.isArray(value)) value.forEach((item) => search.append(key, item))
    else search.append(key, value)
  }
  const query = search.toString()
  return query ? `?${query}` : ''
}

async function request(path, { method = 'GET', body, params, signal } = {}) {
  const response = await fetch(`${path.startsWith('/health') ? BASE : PREFIX}${path}${buildQuery(params)}`, {
    method,
    signal,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })

  if (response.status === 204) return null

  const text = await response.text()
  const payload = text ? JSON.parse(text) : null
  if (!response.ok) {
    throw new ApiError(detailOf(payload) || `Request failed (${response.status})`, response.status, payload)
  }
  return payload
}

function detailOf(payload) {
  if (!payload) return null
  const { detail } = payload
  if (typeof detail === 'string') return detail
  // FastAPI validation errors arrive as a list of {loc, msg}.
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join('; ')
  return null
}

export const api = {
  health: () => request('/health/ready'),

  listAgents: (params) => request('/agents', { params }),
  getAgent: (id) => request(`/agents/${id}`),
  createAgent: (body) => request('/agents', { method: 'POST', body }),
  updateAgent: (id, body) => request(`/agents/${id}`, { method: 'PATCH', body }),
  deleteAgent: (id) => request(`/agents/${id}`, { method: 'DELETE' }),
  agentStats: (id) => request(`/agents/${id}/stats`),
  simulateAgent: (id, body) => request(`/agents/${id}/simulate`, { method: 'POST', body }),

  listCalls: (params) => request('/calls', { params }),
  getCall: (id) => request(`/calls/${id}`),
  createCall: (body) => request('/calls', { method: 'POST', body }),
  createCallsBulk: (body) => request('/calls/bulk', { method: 'POST', body }),
  cancelCall: (id) => request(`/calls/${id}/cancel`, { method: 'POST' }),
  retryCall: (id) => request(`/calls/${id}/retry`, { method: 'POST' }),

  queueStats: () => request('/queue/stats'),
  deadLetter: (params) => request('/queue/dead-letter', { params }),
  requeueDeadLetter: (id) => request(`/queue/dead-letter/${id}/requeue`, { method: 'POST' }),
  purgeDeadLetter: (id) => request(`/queue/dead-letter/${id}`, { method: 'DELETE' }),

  overview: (params) => request('/analytics/overview', { params }),
  timeseries: (params) => request('/analytics/timeseries', { params }),
  agentPerformance: (params) => request('/analytics/agents', { params }),
}

export function eventStreamUrl() {
  if (BASE) return `${BASE.replace(/^http/, 'ws')}/ws/events`
  const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws'
  return `${protocol}://${window.location.host}/ws/events`
}
