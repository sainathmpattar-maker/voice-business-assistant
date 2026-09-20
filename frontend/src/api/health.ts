/**
 * api/health.ts
 * Typed wrapper for the GET /api/health endpoint.
 */

export interface HealthResponse {
  status: 'ok' | string
  service: string
  version: string
  database: 'connected' | string
}

export async function checkHealth(): Promise<HealthResponse> {
  const res = await fetch('/api/health')
  if (!res.ok) {
    throw new Error(`Health check failed: ${res.status} ${res.statusText}`)
  }
  return res.json() as Promise<HealthResponse>
}
