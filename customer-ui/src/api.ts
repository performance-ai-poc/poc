// Single API integration point for the customer UI.
//
// Talks to the orchestrator's public contract (orchestrator-svc/app/schemas.py):
//   POST /chat  { message, session_id?, tenant_id? }
//     -> { reply, run_id, request_id, session_id, tenant_id }
//
// Base URL is configurable via VITE_API_BASE_URL (see .env.example); defaults
// to the orchestrator's local dev address.

const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL?.replace(/\/$/, '') ?? 'http://localhost:8001'

export interface ChatResponse {
  reply: string
  run_id: string
  request_id: string
  session_id: string
  tenant_id: string
}

export async function sendChat(
  message: string,
  sessionId?: string,
): Promise<ChatResponse> {
  const res = await fetch(`${API_BASE_URL}/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(
      sessionId ? { message, session_id: sessionId } : { message },
    ),
  })

  if (!res.ok) {
    let detail = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      // response had no JSON body; keep the status-based message
    }
    throw new Error(detail)
  }

  return (await res.json()) as ChatResponse
}
