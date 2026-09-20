/**
 * frontend/src/api/client.ts — API client for Polaris Voice Assistant backend.
 */

export interface ActionSuggestion {
  label: string
  action_type: string
}

export interface TimingsMs {
  stt: number
  llm: number
  tts: number
  total: number
}

export interface AssistantResponseData {
  transcript: string
  language: string
  display_text: string
  speech_text: string
  audio_base64: string
  data_used: string[]
  actions: ActionSuggestion[]
  timings_ms?: TimingsMs
  latency_ms?: number
  session_id: string
}

export interface InsightItem {
  id: string
  category: 'stock' | 'sales' | 'dues'
  title: string
  display_text: string
  speech_text: string
  audio_base64: string
  action?: ActionSuggestion
}

export interface InsightsResponse {
  merchant_id: number
  shop_name: string
  insights: InsightItem[]
}

export interface TodaySalesData {
  merchant_id: number
  merchant_name: string
  shop_name: string
  today_revenue: number
  today_orders: number
  today_avg_order: number
  period_7d_revenue: number
  is_premium_active: boolean
}

const rawApiUrl = import.meta.env.VITE_API_URL || ''
const API_BASE = rawApiUrl ? `${rawApiUrl.replace(/\/$/, '')}/api` : '/api'

export async function askVoice(
  audioBlob: Blob,
  merchantId = 1,
  sessionId?: string,
  isPremium = false
): Promise<AssistantResponseData> {
  const formData = new FormData()
  formData.append('audio', audioBlob, 'recording.webm')
  formData.append('merchant_id', String(merchantId))
  if (sessionId) formData.append('session_id', sessionId)
  formData.append('is_premium', String(isPremium))

  const res = await fetch(`${API_BASE}/voice/ask`, {
    method: 'POST',
    body: formData,
  })

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}))
    throw new Error(errorData.detail || `Voice request failed (${res.status})`)
  }

  return res.json()
}

export async function askChat(
  text: string,
  language = 'hindi',
  merchantId = 1,
  sessionId?: string,
  isPremium = false
): Promise<AssistantResponseData> {
  const res = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      text,
      language,
      merchant_id: merchantId,
      session_id: sessionId || '',
      is_premium: isPremium,
    }),
  })

  if (!res.ok) {
    const errorData = await res.json().catch(() => ({}))
    throw new Error(errorData.detail || `Chat request failed (${res.status})`)
  }

  const data = await res.json()
  return {
    transcript: text,
    language: data.language_code,
    display_text: data.display_text,
    speech_text: data.speech_text,
    audio_base64: '',
    data_used: data.data_used || [],
    actions: data.actions || [],
    latency_ms: data.latency_ms,
    session_id: data.session_id,
  }
}

export async function getInsights(merchantId = 1, language = 'hi-IN'): Promise<InsightsResponse> {
  const res = await fetch(`${API_BASE}/insights?merchant_id=${merchantId}&language=${encodeURIComponent(language)}`)
  if (!res.ok) {
    throw new Error(`Failed to fetch insights (${res.status})`)
  }
  return res.json()
}

export async function getTodaySales(merchantId = 1): Promise<TodaySalesData> {
  const res = await fetch(`${API_BASE}/merchant/today?merchant_id=${merchantId}`)
  if (!res.ok) {
    throw new Error(`Failed to fetch today sales (${res.status})`)
  }
  return res.json()
}

export async function clearSession(sessionId: string): Promise<void> {
  await fetch(`${API_BASE}/chat/session`, {
    method: 'DELETE',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sessionId }),
  }).catch(() => {})
}
