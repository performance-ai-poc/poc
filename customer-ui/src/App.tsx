import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from 'react'
import { sendChat } from './api'
import './App.css'

type ChatRole = 'user' | 'assistant' | 'error'

interface ChatMessage {
  id: string
  role: ChatRole
  content: string
}

const MAX_INPUT_HEIGHT_PX = 160
const EMPTY_REPLY_FALLBACK = 'I did not get a reply back from the assistant.'

function makeMessageId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }

  return `msg-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function App() {
  const [message, setMessage] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string | undefined>(undefined)
  const [copiedId, setCopiedId] = useState<string | null>(null)

  const threadRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // Keep the latest turn in view as the thread grows.
  useEffect(() => {
    threadRef.current?.scrollTo({ top: threadRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, loading])

  // Auto-grow the composer with its content, capped so it can't swallow the screen.
  useEffect(() => {
    const el = inputRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, MAX_INPUT_HEIGHT_PX)}px`
  }, [message])

  async function submit() {
    const trimmed = message.trim()
    if (!trimmed || loading) return

    setMessages((prev) => [...prev, { id: makeMessageId(), role: 'user', content: trimmed }])
    setMessage('')
    setLoading(true)

    try {
      const res = await sendChat(trimmed, sessionId)
      setSessionId(res.session_id)
      setMessages((prev) => [
        ...prev,
        {
          id: makeMessageId(),
          role: 'assistant',
          content: res.reply.trim() || EMPTY_REPLY_FALLBACK,
        },
      ])
    } catch (err) {
      const detail = err instanceof Error ? err.message : 'Something went wrong.'
      setMessages((prev) => [...prev, { id: makeMessageId(), role: 'error', content: detail }])
    } finally {
      setLoading(false)
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    void submit()
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // Ctrl/Cmd + Enter sends without leaving the keyboard; a bare Enter inserts a newline.
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault()
      void submit()
    }
  }

  async function handleCopy(id: string, content: string) {
    await navigator.clipboard.writeText(content)
    setCopiedId(id)
    setTimeout(() => setCopiedId((cur) => (cur === id ? null : cur)), 1500)
  }

  function startNewChat() {
    setMessages([])
    setMessage('')
    setSessionId(undefined)
    setCopiedId(null)
    inputRef.current?.focus()
  }

  return (
    <main className="chat">
      <div className="chat-container">
        <header className="chat-header">
          <span className="chat-mark" aria-hidden="true" />
          <div>
            <h1>AI Chat</h1>
            <p className="subtitle">Ask a question and get a reply from the assistant.</p>
          </div>
          <button
            type="button"
            className="chat-new"
            onClick={startNewChat}
            disabled={messages.length === 0 && !sessionId}
          >
            New chat
          </button>
        </header>
      </div>

      <div className="chat-thread" ref={threadRef} aria-live="polite">
        <div className="chat-container chat-thread-inner">
          {messages.length === 0 && !loading && (
            <div className="chat-empty-state">
              <span className="chat-mark chat-empty-mark" aria-hidden="true" />
              <p>Start a conversation below.</p>
            </div>
          )}

          {messages.map((m) =>
            m.role === 'error' ? (
              <div key={m.id} className="chat-message chat-message--error">
                <p className="chat-error" role="alert">
                  {m.content}
                </p>
              </div>
            ) : (
              <div key={m.id} className={`chat-message chat-message--${m.role}`}>
                {m.role === 'assistant' && (
                  <span className="chat-avatar" aria-hidden="true">
                    AI
                  </span>
                )}
                <div className="chat-bubble-col">
                  <div className="chat-bubble">
                    <p>{m.content}</p>
                  </div>
                  <button
                    type="button"
                    className="chat-copy"
                    onClick={() => void handleCopy(m.id, m.content)}
                    aria-label={copiedId === m.id ? 'Copied' : 'Copy message'}
                  >
                    {copiedId === m.id ? (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                        <path
                          d="M20 6L9 17l-5-5"
                          stroke="currentColor"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeLinejoin="round"
                        />
                      </svg>
                    ) : (
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                        <rect x="9" y="9" width="12" height="12" rx="2" stroke="currentColor" strokeWidth="2" />
                        <path
                          d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"
                          stroke="currentColor"
                          strokeWidth="2"
                        />
                      </svg>
                    )}
                    <span className="chat-tooltip" aria-hidden="true">
                      {copiedId === m.id ? 'Copied!' : 'Copy message'}
                    </span>
                  </button>
                </div>
              </div>
            ),
          )}

          {loading && (
            <div className="chat-message chat-message--assistant">
              <span className="chat-avatar" aria-hidden="true">
                AI
              </span>
              <div className="chat-bubble chat-typing" aria-label="Assistant is typing">
                <span className="dot" />
                <span className="dot" />
                <span className="dot" />
              </div>
            </div>
          )}

          {!loading && messages.length > 0 && (
            <div className="chat-status" role="status" aria-live="polite">
              Waiting for the next turn.
            </div>
          )}
        </div>
      </div>

      <div className="chat-container">
        <form className="chat-form" onSubmit={handleSubmit}>
          {sessionId && (
            <div className="chat-session" title={sessionId}>
              Session {sessionId.slice(0, 8)}
            </div>
          )}
          <div className="chat-field">
            <textarea
              ref={inputRef}
              className="chat-input"
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Type your message…"
              rows={1}
              disabled={loading}
              autoFocus
            />
            <button
              type="submit"
              className="chat-send"
              disabled={loading || !message.trim()}
              aria-label={loading ? 'Sending' : 'Send message'}
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <path
                  d="M12 19V5M12 5L5 12M12 5l7 7"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              <span className="chat-tooltip" aria-hidden="true">
                {loading ? 'Sending…' : message.trim() ? 'Send message' : 'Message is empty'}
              </span>
            </button>
          </div>
          <span className="chat-hint">Ctrl / Cmd + Enter to send</span>
        </form>
      </div>
    </main>
  )
}

export default App
