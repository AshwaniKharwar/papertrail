import { useEffect, useRef, useState } from 'react'
import { ArrowRight, BookOpen, Check, ChevronDown, CircleHelp, FileText, LogOut, MessageCircle, PanelLeftClose, PanelLeftOpen, Plus, RotateCcw, Send, Sparkles, Tag, Trash2, UploadCloud, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { readAnswerStream } from './stream'

const API = import.meta.env.VITE_API_URL || 'http://localhost:8000'

type User = { id: number; name: string; email: string; picture?: string }
type Document = { id: string; filename: string; size: number; created_at: string; status: string; error: string | null; page_count: number | null; tags: string[] }
type Source = { page: number; snippet: string }
type Message = { id: number; role: 'user' | 'assistant'; content: string; sources: Source[]; created_at: string }

function MessageContent({ message, pending }: { message: Message; pending: boolean }) {
  if (pending && !message.content) return <div className="thinking"><i /><i /><i /></div>
  return <div className="message-content">{message.role === 'assistant'
    ? <ReactMarkdown remarkPlugins={[remarkGfm]} components={{ img: () => null }}>{message.content}</ReactMarkdown>
    : message.content}</div>
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, { credentials: 'include', ...options })
  if (!response.ok) {
    const error = await response.json().catch(() => null)
    throw new Error(error?.detail || `Request failed (${response.status})`)
  }
  return response.json()
}

function formatSize(bytes: number) {
  return bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export default function App() {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [googleConfigured, setGoogleConfigured] = useState(false)
  const [documents, setDocuments] = useState<Document[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [question, setQuestion] = useState('')
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [showPdf, setShowPdf] = useState(false)
  const [pdfPage, setPdfPage] = useState(1)
  const [tagEditing, setTagEditing] = useState(false)
  const [tagDraft, setTagDraft] = useState('')
  const fileInput = useRef<HTMLInputElement>(null)
  const bottom = useRef<HTMLDivElement>(null)
  const streamRequest = useRef<AbortController | null>(null)

  useEffect(() => {
    Promise.all([api<User>('/auth/me').catch(() => null), api<{ google_configured: boolean }>('/config')])
      .then(([currentUser, config]) => { setUser(currentUser); setGoogleConfigured(config.google_configured) })
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    if (!user) return
    api<Document[]>('/documents').then(setDocuments).catch((e) => setError(e.message))
  }, [user])

  useEffect(() => {
    if (!user || !documents.some((document) => !['ready', 'failed'].includes(document.status))) return
    const timer = window.setInterval(() => api<Document[]>('/documents').then(setDocuments).catch(() => {}), 3000)
    return () => window.clearInterval(timer)
  }, [user, documents])

  useEffect(() => {
    streamRequest.current?.abort()
    if (!selected) { setMessages([]); return }
    setTagEditing(false)
    setPdfPage(1)
    api<Message[]>(`/documents/${selected}/messages`).then(setMessages).catch((e) => setError(e.message))
  }, [selected])

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth' }) }, [messages, busy])

  const activeDocument = documents.find((document) => document.id === selected)

  async function upload(file: File | undefined) {
    if (!file) return
    setError('')
    setUploading(true)
    try {
      const form = new FormData()
      form.append('file', file)
      const document = await api<Document>('/documents', { method: 'POST', body: form })
      setDocuments((current) => [document, ...current])
      setSelected(document.id)
      setShowPdf(false)
    } catch (e) { setError((e as Error).message) }
    finally { setUploading(false); if (fileInput.current) fileInput.current.value = '' }
  }

  async function send() {
    const text = question.trim()
    if (!text || !selected || activeDocument?.status !== 'ready' || busy) return
    const documentId = selected
    const controller = new AbortController()
    streamRequest.current = controller
    setQuestion('')
    setError('')
    setBusy(true)
    const temporary: Message = { id: Date.now(), role: 'user', content: text, sources: [], created_at: new Date().toISOString() }
    const assistant: Message = { id: -temporary.id, role: 'assistant', content: '', sources: [], created_at: temporary.created_at }
    setMessages((current) => [...current, temporary, assistant])
    let completed = false
    try {
      const response = await fetch(`${API}/documents/${documentId}/messages`, {
        method: 'POST', credentials: 'include', signal: controller.signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question: text }),
      })
      if (!response.ok) {
        const error = await response.json().catch(() => null)
        throw new Error(error?.detail || `Request failed (${response.status})`)
      }
      if (!response.body) throw new Error('Your browser could not read the answer stream.')
      await readAnswerStream(response.body, (event) => {
        if (event.type === 'delta') setMessages((current) => current.map((message) => message.id === assistant.id ? { ...message, content: message.content + event.text } : message))
        if (event.type === 'done') {
          completed = true
          setMessages((current) => current.map((message) => message.id === assistant.id ? { ...message, sources: event.sources } : message))
        }
      })
      const saved = await api<Message[]>(`/documents/${documentId}/messages`, { signal: controller.signal })
      setMessages(saved)
    } catch (e) {
      if (controller.signal.aborted) return
      if (!completed) {
        setMessages((current) => current.filter((message) => message.id !== temporary.id && message.id !== assistant.id))
        setQuestion(text)
      }
      setError((e as Error).message)
    } finally { streamRequest.current = null; setBusy(false) }
  }

  async function removeDocument(document: Document) {
    if (!confirm(`Delete “${document.filename}” and its conversation?`)) return
    try {
      await api(`/documents/${document.id}`, { method: 'DELETE' })
      setDocuments((current) => current.filter((item) => item.id !== document.id))
      if (selected === document.id) setSelected(null)
    } catch (e) { setError((e as Error).message) }
  }

  async function saveTags(tags: string[]) {
    if (!selected) return
    try {
      const updated = await api<Document>(`/documents/${selected}`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ tags }) })
      setDocuments((current) => current.map((document) => document.id === selected ? updated : document))
      setTagDraft(''); setTagEditing(false)
    } catch (e) { setError((e as Error).message) }
  }

  async function retryDocument() {
    if (!selected) return
    try {
      const updated = await api<Document>(`/documents/${selected}/retry`, { method: 'POST' })
      setDocuments((current) => current.map((document) => document.id === selected ? updated : document))
    } catch (e) { setError((e as Error).message) }
  }

  async function signOut() {
    await api('/auth/logout', { method: 'POST' }).catch(() => {})
    setUser(null); setDocuments([]); setSelected(null)
  }

  if (loading) return <div className="loading-screen"><div className="logo-mark"><BookOpen size={24} /></div><span>Opening Papertrail...</span></div>

  if (!user) return <div className="sign-in-page">
    <div className="sign-in-top"><div className="brand"><span className="logo-mark"><BookOpen size={20} /></span> papertrail<span className="brand-dot">.</span></div><span className="top-note">YOUR DOCUMENTS, DECODED</span></div>
    <div className="sign-in-content">
      <div className="intro">
        <div className="eyebrow"><span className="eyebrow-line" /> A BETTER WAY TO READ</div>
        <h1>Every PDF has<br /><em>answers.</em> Find yours.</h1>
        <p>Upload a document. Ask what matters. Get clear answers grounded in the pages you already have.</p>
        <div className="feature-row"><span><Check size={15} /> Ask naturally</span><span><Check size={15} /> Keep every conversation</span><span><Check size={15} /> Private to you</span></div>
      </div>
      <div className="sign-in-card">
        <div className="card-icon"><FileText size={30} /></div>
        <div className="card-kicker">YOUR READING WORKSPACE</div>
        <h2>Make sense of any PDF.</h2>
        <p>Sign in to start a conversation with your documents.</p>
        {googleConfigured ? <a className="google-button" href={`${API}/auth/google/start`}><span className="google-g">G</span> Continue with Google</a> : <div className="setup-note">Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET to backend/.env to enable sign-in.</div>}
        {error && <div className="error-message">{error}</div>}
        <div className="card-footer"><span className="footer-line" /> Secure sign-in with Google <span className="footer-line" /></div>
      </div>
    </div>
    <div className="sign-in-bottom"><span>READ LESS. UNDERSTAND MORE.</span><span>BUILT FOR THE CURIOUS ↗</span></div>
  </div>

  return <div className={`app-shell ${sidebarOpen ? '' : 'sidebar-collapsed'}`}>
    <aside className="sidebar">
      <div className="sidebar-top"><div className="brand"><span className="logo-mark"><BookOpen size={19} /></span> papertrail<span className="brand-dot">.</span></div><button className="icon-button collapse-button" title="Collapse sidebar" onClick={() => setSidebarOpen(false)}><PanelLeftClose size={19} /></button></div>
      <button className="upload-button" onClick={() => fileInput.current?.click()} disabled={uploading}><Plus size={18} strokeWidth={2.3} /> {uploading ? 'Uploading...' : 'Add a PDF'} <span>↗</span></button>
      <input ref={fileInput} hidden type="file" accept="application/pdf,.pdf" onChange={(e) => upload(e.target.files?.[0])} />
      <div className="sidebar-section-label">YOUR LIBRARY <span>{documents.length}</span></div>
      <div className="document-list">{documents.length ? documents.map((document) => <div key={document.id} className={`document-item ${selected === document.id ? 'active' : ''}`}>
        <button className="document-select" onClick={() => { setSelected(document.id); setShowPdf(false) }}><span className="doc-icon"><FileText size={17} /></span><span className="document-text"><strong>{document.filename}</strong><small>{document.status === 'ready' ? (document.tags.length ? document.tags.join(', ') : `${document.page_count} pages · ${formatSize(document.size)}`) : `${document.status === 'failed' ? 'Index failed' : 'Indexing'} · ${formatSize(document.size)}`}</small></span></button>
        <button className="delete-button" title={`Delete ${document.filename}`} onClick={() => removeDocument(document)}><Trash2 size={15} /></button>
      </div>) : <div className="sidebar-empty"><FileText size={19} /> Your PDFs will live here.</div>}</div>
      <div className="sidebar-bottom"><div className="account"><div className="avatar">{user.picture ? <img src={user.picture} alt="" /> : user.name[0]?.toUpperCase()}</div><span><strong>{user.name}</strong><small>{user.email}</small></span><ChevronDown size={16} /></div><button className="sign-out" onClick={signOut}><LogOut size={15} /> Sign out</button></div>
    </aside>
    <main className="workspace">
      <header className="workspace-header"><div className="header-left">{!sidebarOpen && <button className="icon-button" title="Open sidebar" onClick={() => setSidebarOpen(true)}><PanelLeftOpen size={20} /></button>}<span className="header-crumb">Workspace</span><span className="header-slash">/</span><strong>{activeDocument?.filename || 'Overview'}</strong></div><div className="header-right"><span className="status-dot" /> AI READY <span className="header-divider" /> <CircleHelp size={17} /></div></header>
      {error && <div className="toast-error"><span>{error}</span><button onClick={() => setError('')} title="Dismiss"><X size={16} /></button></div>}
      {!activeDocument ? <div className="empty-workspace"><div className="empty-illustration"><div className="floating-page page-back" /><div className="floating-page page-front"><FileText size={44} strokeWidth={1.25} /><span /><span /><span /></div><div className="sparkle-circle"><Sparkles size={20} /></div></div><div className="empty-kicker">START A CONVERSATION</div><h2>Your documents have<br /><em>more to say.</em></h2><p>Drop in a PDF and turn its pages into answers.<br />Ask questions, uncover details, and move faster.</p><button className="primary-button" onClick={() => fileInput.current?.click()}><UploadCloud size={19} /> Upload your first PDF <ArrowRight size={17} /></button><span className="file-hint">PDF files up to 100 MB</span></div> : <div className="document-workspace">
        <div className="chat-pane"><div className="chat-title"><div><div className="chat-kicker">CONVERSATION WITH</div><h2>{activeDocument.filename}</h2><span>{formatSize(activeDocument.size)} · Added {new Date(activeDocument.created_at).toLocaleDateString()}</span></div><button className="view-pdf" onClick={() => setShowPdf((value) => !value)}><FileText size={16} /> {showPdf ? 'Hide PDF' : 'View PDF'}</button></div>
          <div className="tag-bar"><Tag size={14} /> {activeDocument.tags.map((tag) => <span className="tag-chip" key={tag}>{tag}<button title={`Remove ${tag}`} onClick={() => saveTags(activeDocument.tags.filter((item) => item !== tag))}><X size={11} /></button></span>)}{tagEditing ? <form onSubmit={(e) => { e.preventDefault(); if (tagDraft.trim()) saveTags([...activeDocument.tags, tagDraft.trim()]) }}><input autoFocus aria-label="New tag" placeholder="New tag" maxLength={32} value={tagDraft} onChange={(e) => setTagDraft(e.target.value)} /><button type="submit">Save</button><button type="button" onClick={() => setTagEditing(false)}>Cancel</button></form> : <button className="add-tag" onClick={() => setTagEditing(true)}>+ Add tag</button>}</div>
          <div className="conversation">{messages.length ? <div className="message-list">{messages.map((message) => <div key={message.id} className={`message-row ${message.role}`}><div className="message-avatar">{message.role === 'assistant' ? <Sparkles size={17} /> : user.picture ? <img src={user.picture} alt="" /> : user.name[0]?.toUpperCase()}</div><div className="message-body"><span className="message-name">{message.role === 'assistant' ? 'Papertrail' : 'You'}</span><MessageContent message={message} pending={busy && message.id < 0} />{message.sources?.length > 0 && <div className="source-list">{message.sources.map((source, index) => <button key={`${source.page}-${index}`} title={source.snippet} onClick={() => { setPdfPage(source.page); setShowPdf(true) }}>[{index + 1}] Page {source.page}</button>)}</div>}</div></div>)}<div ref={bottom} /></div> : activeDocument.status !== 'ready' ? <div className="chat-welcome"><div className="welcome-icon"><FileText size={25} /></div><div className="chat-kicker">{activeDocument.status === 'failed' ? 'INDEXING FAILED' : 'PREPARING YOUR PDF'}</div><h3>{activeDocument.status === 'failed' ? 'Could not index this PDF' : 'Finding the important parts...'}</h3><p>{activeDocument.status === 'failed' ? activeDocument.error : 'We are reading the pages and building a searchable index. Large books can take several minutes.'}</p>{activeDocument.status === 'failed' && <button className="retry-button" onClick={retryDocument}><RotateCcw size={15} /> Retry indexing</button>}</div> : <div className="chat-welcome"><div className="welcome-icon"><MessageCircle size={25} /></div><div className="chat-kicker">DOCUMENT READY</div><h3>What would you like to know?</h3><p>Ask about key ideas, details, or anything you want to understand better.</p><div className="suggestions">{['Summarize this document', 'What are the key takeaways?', 'Explain the main findings'].map((suggestion) => <button key={suggestion} onClick={() => setQuestion(suggestion)}>{suggestion} <ArrowRight size={14} /></button>)}</div></div>}</div>
          {activeDocument.status === 'ready' && <form className="composer" onSubmit={(e) => { e.preventDefault(); send() }}><div className="composer-inner"><textarea aria-label="Ask about this PDF" placeholder="Ask anything about this PDF..." value={question} onChange={(e) => setQuestion(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }} rows={2} /><button type="submit" title="Send question" disabled={!question.trim() || busy}><Send size={18} /></button></div><div className="composer-note"><span><Sparkles size={13} /> Answers are grounded in your PDF</span><span>Enter to send · Shift + Enter for new line</span></div></form>}
        </div>
        {showPdf && <div className="pdf-pane"><div className="pdf-header"><span><FileText size={16} /> PDF PREVIEW</span><button onClick={() => setShowPdf(false)} title="Close PDF"><X size={18} /></button></div><iframe title={activeDocument.filename} src={`${API}/documents/${activeDocument.id}/file#page=${pdfPage}`} /></div>}
      </div>}
    </main>
  </div>
}
