const API_BASE = '/api/v1'

interface RequestOptions {
  method?: string
  body?: unknown
  headers?: Record<string, string>
  skipAuth?: boolean
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
    throw new Error(errorData?.detail || `请求失败 (${response.status})`)
  }

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

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body: unknown) => request<T>(path, { method: 'POST', body }),
  put: <T>(path: string, body: unknown) => request<T>(path, { method: 'PUT', body }),
  delete: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}
