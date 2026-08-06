const API_BASE = '/api/v1'

interface RequestOptions {
  method?: string
  body?: unknown
  headers?: Record<string, string>
  skipAuth?: boolean
}

export interface StreamEvent {
  event: string
  data: Record<string, any>
}

export interface StreamHandlers {
  onEvent: (event: StreamEvent) => void | Promise<void>
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, headers = {}, skipAuth = false } = options

  const token = localStorage.getItem('access_token')
  const hdrs: Record<string, string> = {
    'Content-Type': 'application/json',
    ...headers,
  }

  if (token && !skipAuth) {
    hdrs['Authorization'] = `Bearer ${token}`
  }

  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: hdrs,
    body: body ? JSON.stringify(body) : undefined,
  })

  if (!response.ok) {
    const errorData = await response.json().catch(() => null)
    if (response.status === 401 && !skipAuth) {
      const refreshed = await attemptRefresh()
      if (refreshed) {
        hdrs['Authorization'] = `Bearer ${localStorage.getItem('access_token')}`
        const retry = await fetch(`${API_BASE}${path}`, {
          method,
          headers: hdrs,
          body: body ? JSON.stringify(body) : undefined,
        })
        if (retry.ok) return retry.json()
      }
    }
    throw new Error(parseErrorDetail(errorData, response.status))
  }

  if (response.status === 204) return undefined as unknown as T
  return response.json()
}

async function attemptRefresh(): Promise<boolean> {
  const refreshToken = localStorage.getItem('refresh_token')
  if (!refreshToken) return false
  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
    if (!res.ok) return false
    const data = await res.json()
    localStorage.setItem('access_token', data.access_token)
    localStorage.setItem('refresh_token', data.refresh_token)
    return true
  } catch {
    return false
  }
}

function parseErrorDetail(errorData: any, status: number): string {
  const detail = errorData?.detail
  if (typeof detail === 'string') return detail
  if (detail && typeof detail.message === 'string') return detail.message
  return `请求失败 (${status})`
}

async function openStream(
  path: string,
  body: unknown,
  signal?: AbortSignal,
): Promise<Response> {
  const token = localStorage.getItem('access_token')
  return fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
    signal,
  })
}

async function streamPost(
  path: string,
  body: unknown,
  handlers: StreamHandlers,
  signal?: AbortSignal,
): Promise<void> {
  let response = await openStream(path, body, signal)
  if (response.status === 401 && await attemptRefresh()) {
    response = await openStream(path, body, signal)
  }
  if (!response.ok) {
    const errorData = await response.json().catch(() => null)
    throw new Error(parseErrorDetail(errorData, response.status))
  }
  if (!response.body) throw new Error('浏览器未提供流式响应能力')

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const dispatchBlock = async (block: string) => {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
    }
    if (!dataLines.length) return
    const raw = dataLines.join('\n')
    const data = JSON.parse(raw)
    await handlers.onEvent({ event, data })
  }

  while (true) {
    const { value, done } = await reader.read()
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done })
    const blocks = buffer.split(/\r?\n\r?\n/)
    buffer = blocks.pop() || ''
    for (const block of blocks) await dispatchBlock(block)
    if (done) break
  }
  if (buffer.trim()) await dispatchBlock(buffer)
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body }),
  put: <T>(path: string, body: unknown) => request<T>(path, { method: 'PUT', body }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
  streamPost,
}
