import { useState, type FormEvent, type KeyboardEvent } from 'react'
import { sendChat } from './api'
import './App.css'


function App() {
  const [message, setMessage] = useState('')
  const [reply, setReply] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [sessionId, setSessionId] = useState<string | undefined>(undefined)

  async function submit() {
    const trimmed = message.trim()
    if (!trimmed || loading) return

    setLoading(true)
    setError(null)
    setReply(null)

    try {
      const res = await sendChat(trimmed, sessionId)
      setReply(res.reply)
      // Preserve the session across turns (see customer-ui README integration note).
      setSessionId(res.session_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Something went wrong.')
    } finally {
      setLoading(false)
    }
  }

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    void submit()
  }

  function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // Ctrl/Cmd + Enter sends without leaving the keyboard.
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault()
      void submit()
    }
  }

  return (
    <main className="chat">
      <header className="chat-header">
        <span className="chat-mark" aria-hidden="true" />
        <div>
          <h1>AI Chat</h1>
          <p className="subtitle">Ask a question and get a reply from the assistant.</p>
        </div>
      </header>

      <form className="chat-form" onSubmit={handleSubmit}>
        <div className="chat-field">
          <textarea
            className="chat-input"
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Type your message…"
            rows={4}
            disabled={loading}
            autoFocus
          />
          <div className="chat-actions">
            <span className="chat-hint">Ctrl / Cmd + Enter to send</span>
            <button
              type="submit"
              className="chat-send"
              disabled={loading || !message.trim()}
            >
              {loading ? 'Sending…' : 'Send'}
            </button>
          </div>
        </div>
      </form>

      <section className="chat-output" aria-live="polite">
        {error && <div className="chat-error" role="alert">{error}</div>}

        {reply && (
          <div className="chat-reply">
            <span className="chat-reply-label">Reply</span>
            <p>{reply}</p>
          </div>
        )}

        {!error && !reply && !loading && (
          <p className="chat-empty">Your reply will appear here.</p>
        )}
      </section>
    </main>
  )
}

export default App
