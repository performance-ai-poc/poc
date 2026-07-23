import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent } from 'react'
import './App.css'

type ChatRole = 'assistant' | 'user'

type ChatMessage = {
  id: string
  role: ChatRole
  content: string
}

type ChatResponse = {
  reply: string
  run_id?: string
  request_id?: string
  session_id?: string
  tenant_id?: string
}

const starterMessage =
  'Ask me about orders, shipments, or policy lookups. I will send the request to the backend chat endpoint and show the response here.'

function uid() {
  return Math.random().toString(36).slice(2, 10)
}

function App() {
  const [messages, setMessages] = useState<ChatMessage[]>([
    { id: uid(), role: 'assistant', content: starterMessage },
  ])
  const [input, setInput] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [sessionId] = useState(() => uid())
  const endRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, isLoading])

  const canSend = useMemo(() => input.trim().length > 0 && !isLoading, [input, isLoading])

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const message = input.trim()
    if (!message || isLoading) return

    setError(null)
    setInput('')
    setIsLoading(true)

    const userMessage: ChatMessage = { id: uid(), role: 'user', content: message }
    setMessages((current) => [...current, userMessage])

    try {
      const response = await fetch('/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message,
          session_id: sessionId,
        }),
      })

      if (!response.ok) {
        throw new Error(`Request failed with status ${response.status}`)
      }

      const data = (await response.json()) as ChatResponse
      setMessages((current) => [
        ...current,
        {
          id: uid(),
          role: 'assistant',
          content: data.reply || 'No reply was returned.',
        },
      ])
    } catch (err) {
      const messageText =
        err instanceof Error ? err.message : 'Something went wrong while calling the backend.'
      setError(messageText)
      setMessages((current) => [
        ...current,
        {
          id: uid(),
          role: 'assistant',
          content:
            'I could not reach the backend just now. Please try again in a moment.',
        },
      ])
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <main className="chat-shell">
      <section className="chat-hero">
        <div className="brand-row">
          <div className="brand-mark">AI</div>
          <div>
            <p className="eyebrow">Customer UI</p>
            <h1>Chat with your backend</h1>
          </div>
        </div>
        <p className="hero-copy">
          A focused chat surface that sends each prompt through the orchestrator and
          renders responses inline, without making the page feel like a demo stub.
        </p>
        <div className="hero-stats" aria-label="Capabilities">
          <span className="hero-pill">
            <span className="hero-dot" />
            Live backend chat
          </span>
          <span className="hero-pill">Orchestrator proxy</span>
          <span className="hero-pill">Minikube-ready</span>
        </div>
      </section>

      <section className="chat-panel" aria-label="Chat conversation">
        <div className="messages">
          {messages.map((message) => (
            <article
              key={message.id}
              className={`message ${message.role === 'user' ? 'message-user' : 'message-assistant'}`}
            >
              <div className="message-badge">
                {message.role === 'user' ? 'You' : 'Assistant'}
              </div>
              <p>{message.content}</p>
            </article>
          ))}

          {isLoading ? (
            <article className="message message-assistant message-thinking">
              <div className="message-badge">Assistant</div>
              <p>Thinking...</p>
            </article>
          ) : null}
          <div ref={endRef} />
        </div>

        <form className="composer" onSubmit={handleSubmit}>
          <textarea
            value={input}
            onChange={(event) => setInput(event.target.value)}
            placeholder="Ask a question about data, orders, shipments, or policies..."
            rows={3}
          />
          <div className="composer-meta">
            <div className="status-row">
              <span className={`status-dot ${isLoading ? 'busy' : 'idle'}`} />
              <span>{isLoading ? 'Waiting for backend response' : 'Ready'}</span>
            </div>
            {error ? <span className="error-text">{error}</span> : null}
            <button type="submit" disabled={!canSend}>
              Send
            </button>
          </div>
        </form>
      </section>
    </main>
  )
}

export default App
