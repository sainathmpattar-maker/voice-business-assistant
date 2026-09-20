import { useEffect, useRef, useState } from 'react'
import {
  askVoice,
  askChat,
  getInsights,
  getTodaySales,
  clearSession,
  type AssistantResponseData,
  type InsightItem,
  type TodaySalesData,
  type ActionSuggestion,
} from './api/client'

type MicState = 'idle' | 'listening' | 'thinking' | 'speaking'

interface Message {
  id: string
  sender: 'user' | 'assistant'
  text: string
  speechText?: string
  language?: string
  audioBase64?: string
  dataUsed?: string[]
  actions?: ActionSuggestion[]
  timings?: {
    stt: number
    llm: number
    tts: number
    total: number
  }
  latencyMs?: number
  timestamp: Date
}

// ── Demo Question Chips ────────────────────────────────────────────────────────
const DEMO_QUESTIONS = [
  { label: 'Aaj ki sale?', lang: 'hindi', text: 'Aaj ki sale kitni rahi?', icon: '🇮🇳' },
  { label: 'இந்த வாரம் விற்பனை?', lang: 'tamil', text: 'Intha vaaram enge nalla vilkiradu?', icon: '🇮🇳' },
  { label: 'ఎవరికి బాకీ ఉంది?', lang: 'telugu', text: 'Evariki baaki undi?', icon: '🇮🇳' },
  { label: 'Top Selling Item?', lang: 'english', text: 'Which item sells the most this week?', icon: '🛒' },
  { label: 'Aur kal ka?', lang: 'hindi', text: 'Aur kal ka?', icon: '🔄' },
  { label: 'Peer Benchmark', lang: 'hindi', text: 'Mere jaise dukaan se main kaisa hoon?', icon: '📊' },
  { label: 'Loan Eligibility', lang: 'hindi', text: 'Mujhe working capital loan mil sakta hai kya?', icon: '💼' },
]

export default function App() {
  const [micState, setMicState] = useState<MicState>('idle')
  const [messages, setMessages] = useState<Message[]>([])
  const [sessionId, setSessionId] = useState<string>(() => 'sess-' + Math.random().toString(36).substring(2, 9))
  const [isPremium, setIsPremium] = useState<boolean>(true)
  const [todayData, setTodayData] = useState<TodaySalesData | null>(null)
  const [insights, setInsights] = useState<InsightItem[]>([])
  const [briefingActive, setBriefingActive] = useState<boolean>(false)
  const [briefingIndex, setBriefingIndex] = useState<number>(0)
  const [errorBanner, setErrorBanner] = useState<string | null>(null)
  const [actionToast, setActionToast] = useState<string | null>(null)
  const [expandedDataUsed, setExpandedDataUsed] = useState<Record<string, boolean>>({})

  // Audio & recording refs
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const audioChunksRef = useRef<Blob[]>([])
  const currentAudioRef = useRef<HTMLAudioElement | null>(null)
  const messagesEndRef = useRef<HTMLDivElement | null>(null)
  const isHoldingRef = useRef<boolean>(false)

  // ── 1. Initial Data Fetch ──────────────────────────────────────────────────
  useEffect(() => {
    fetchTodaySales()
    fetchInsights()
  }, [])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, micState])

  const fetchTodaySales = async () => {
    try {
      const data = await getTodaySales(1)
      setTodayData(data)
    } catch (err) {
      console.warn('Today sales fetch error:', err)
    }
  }

  const fetchInsights = async () => {
    try {
      const res = await getInsights(1, 'hi-IN')
      setInsights(res.insights)
    } catch (err) {
      console.warn('Insights fetch error:', err)
    }
  }

  // ── 2. Audio Playback Helper ───────────────────────────────────────────────
  const playBase64Audio = (base64Mp3: string, onEnded?: () => void) => {
    if (!base64Mp3) {
      if (onEnded) onEnded()
      return
    }

    try {
      if (currentAudioRef.current) {
        currentAudioRef.current.pause()
        currentAudioRef.current = null
      }

      const audio = new Audio(`data:audio/mp3;base64,${base64Mp3}`)
      currentAudioRef.current = audio
      setMicState('speaking')

      audio.onended = () => {
        setMicState('idle')
        if (onEnded) onEnded()
      }
      audio.onerror = () => {
        setMicState('idle')
        if (onEnded) onEnded()
      }

      audio.play().catch((err) => {
        console.warn('Audio autoplay blocked or failed:', err)
        setMicState('idle')
        if (onEnded) onEnded()
      })
    } catch (e) {
      console.error('Audio playback exception:', e)
      setMicState('idle')
      if (onEnded) onEnded()
    }
  }

  // ── 3. Voice Recording (MediaRecorder) ──────────────────────────────────────
  const startRecording = async () => {
    setErrorBanner(null)
    if (currentAudioRef.current) {
      currentAudioRef.current.pause()
      currentAudioRef.current = null
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      audioChunksRef.current = []

      // Choose supported mimeType
      let mimeType = 'audio/webm'
      if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
        mimeType = 'audio/webm;codecs=opus'
      } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
        mimeType = 'audio/mp4'
      }

      const recorder = new MediaRecorder(stream, { mimeType })
      mediaRecorderRef.current = recorder

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data)
        }
      }

      recorder.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: mimeType })
        // Stop all tracks to release mic
        stream.getTracks().forEach((track) => track.stop())

        if (audioBlob.size < 100) {
          setErrorBanner('No audio recorded. Please try again.')
          setMicState('idle')
          return
        }

        await handleAudioSubmit(audioBlob)
      }

      recorder.start()
      setMicState('listening')
    } catch (err: any) {
      console.error('Microphone access error:', err)
      setErrorBanner(
        err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError'
          ? 'Microphone permission denied. Please allow mic access in your browser settings.'
          : 'Could not access microphone.'
      )
      setMicState('idle')
    }
  }

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      mediaRecorderRef.current.stop()
      setMicState('thinking')
    }
  }

  const handleMicClick = () => {
    if (micState === 'idle') {
      startRecording()
    } else if (micState === 'listening') {
      stopRecording()
    } else if (micState === 'speaking') {
      if (currentAudioRef.current) {
        currentAudioRef.current.pause()
        currentAudioRef.current = null
      }
      setMicState('idle')
    }
  }

  // ── 4. Process Voice / Chat Input ──────────────────────────────────────────
  const handleAudioSubmit = async (audioBlob: Blob) => {
    setMicState('thinking')
    try {
      const resp = await askVoice(audioBlob, 1, sessionId, isPremium)
      handleAssistantResponse(resp)
    } catch (err: any) {
      console.error('Voice ask error:', err)
      setErrorBanner(err.message || 'Error processing speech. Please try again.')
      setMicState('idle')
    }
  }

  const handleTextQuery = async (queryText: string, lang = 'hindi') => {
    setErrorBanner(null)
    setMicState('thinking')

    // Add immediate user message bubble
    const userMsg: Message = {
      id: 'msg-' + Date.now(),
      sender: 'user',
      text: queryText,
      timestamp: new Date(),
    }
    setMessages((prev) => [...prev, userMsg])

    try {
      const resp = await askChat(queryText, lang, 1, sessionId, isPremium)
      handleAssistantResponse(resp, false)
    } catch (err: any) {
      console.error('Chat error:', err)
      setErrorBanner(err.message || 'Failed to get answer. Please try again.')
      setMicState('idle')
    }
  }

  const handleAssistantResponse = (resp: AssistantResponseData, isFromAudio = true) => {
    const userMsg: Message | null = isFromAudio
      ? {
          id: 'user-' + Date.now(),
          sender: 'user',
          text: resp.transcript,
          language: resp.language,
          timestamp: new Date(),
        }
      : null

    const assistantMsg: Message = {
      id: 'asst-' + Date.now(),
      sender: 'assistant',
      text: resp.display_text,
      speechText: resp.speech_text,
      language: resp.language,
      audioBase64: resp.audio_base64,
      dataUsed: resp.data_used,
      actions: resp.actions,
      timings: resp.timings_ms,
      latencyMs: resp.latency_ms,
      timestamp: new Date(),
    }

    setMessages((prev) => (userMsg ? [...prev, userMsg, assistantMsg] : [...prev, assistantMsg]))
    setSessionId(resp.session_id)

    // Play response audio if present
    if (resp.audio_base64) {
      playBase64Audio(resp.audio_base64)
    } else {
      setMicState('idle')
    }
  }

  // ── 5. Action Button Click Handlers ────────────────────────────────────────
  const handleActionClick = (action: ActionSuggestion) => {
    if (action.action_type === 'upgrade_premium') {
      setIsPremium(true)
      showToast('🎉 Upgraded to Polaris Premium!')
    } else if (action.action_type === 'contact_advisor') {
      showToast('📞 Request sent to Paytm merchant advisor. You will receive a call within 10 minutes.')
    } else if (action.action_type === 'send_due_reminder') {
      showToast('📲 WhatsApp reminder & payment link sent to customer!')
    } else if (action.action_type === 'reorder_stock') {
      showToast('📦 Restock purchase order created on Paytm Wholesale!')
    } else {
      showToast(`⚡ Action executed: ${action.label}`)
    }
  }

  const showToast = (msg: string) => {
    setActionToast(msg)
    setTimeout(() => setActionToast(null), 4000)
  }

  // ── 6. Morning Briefing Sequential Player ──────────────────────────────────
  const startMorningBriefing = () => {
    if (insights.length === 0) return
    setBriefingActive(true)
    setBriefingIndex(0)
    playBriefingItem(0)
  }

  const playBriefingItem = (idx: number) => {
    if (idx >= insights.length) {
      setBriefingActive(false)
      showToast('✅ Morning Briefing complete!')
      setMicState('idle')
      return
    }

    setBriefingIndex(idx)
    const item = insights[idx]
    if (item.audio_base64) {
      playBase64Audio(item.audio_base64, () => {
        // Wait 1.2s before next insight
        setTimeout(() => {
          playBriefingItem(idx + 1)
        }, 1200)
      })
    } else {
      setTimeout(() => {
        playBriefingItem(idx + 1)
      }, 3500)
    }
  }

  const stopBriefing = () => {
    setBriefingActive(false)
    if (currentAudioRef.current) {
      currentAudioRef.current.pause()
      currentAudioRef.current = null
    }
    setMicState('idle')
  }

  // Reset conversation memory
  const handleResetConversation = async () => {
    await clearSession(sessionId)
    const newSess = 'sess-' + Math.random().toString(36).substring(2, 9)
    setSessionId(newSess)
    setMessages([])
    showToast('🔄 Conversation reset.')
  }

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col items-center justify-between p-3 sm:p-5 max-w-3xl mx-auto selection:bg-brand/30 selection:text-white">
      {/* ── Top Header ────────────────────────────────────────────────────────── */}
      <header className="w-full mb-3 space-y-3">
        <div className="flex items-center justify-between border-b border-white/10 pb-3">
          <div className="flex items-center gap-2.5">
            <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-brand to-amber-500 flex items-center justify-center font-bold text-white shadow-lg shadow-brand/30 text-lg">
              P
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-lg sm:text-xl font-bold font-display text-white tracking-tight">
                  {todayData?.shop_name || 'Ramesh Kirana Store'}
                </h1>
                <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 mr-1 animate-ping" />
                  Live Paytm Soundbox
                </span>
              </div>
              <p className="text-xs text-slate-400">Multilingual Voice Intelligence • 10 Indian Languages</p>
            </div>
          </div>

          {/* Premium Demo Toggle */}
          <div className="flex items-center gap-2">
            <button
              onClick={() => setIsPremium(!isPremium)}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold transition-all border ${
                isPremium
                  ? 'bg-amber-500/20 border-amber-500/40 text-amber-300 shadow-md shadow-amber-500/10'
                  : 'bg-white/5 border-white/10 text-slate-400'
              }`}
            >
              <span>{isPremium ? '👑 Premium (Active)' : 'Free Tier'}</span>
            </button>
            <button
              onClick={handleResetConversation}
              title="Reset conversation"
              className="p-1.5 rounded-lg bg-white/5 hover:bg-white/10 text-slate-400 hover:text-white transition-colors"
            >
              <svg xmlns="http://www.w3.org/2000/svg" className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
                <path d="M21 3v5h-5" />
                <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
                <path d="M3 21v-5h5" />
              </svg>
            </button>
          </div>
        </div>

        {/* ── Today's Sales Metrics Tile ────────────────────────────────────────── */}
        <div className="grid grid-cols-3 gap-2 sm:gap-3 bg-gradient-to-r from-slate-900/90 via-navy-900/70 to-slate-900/90 border border-white/10 rounded-2xl p-3 shadow-xl backdrop-blur-md">
          <div className="flex flex-col">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">Aaj Ki Sale</span>
            <span className="text-base sm:text-xl font-bold font-mono text-emerald-400">
              ₹{todayData ? Number(todayData.today_revenue).toLocaleString('en-IN') : '2,640'}
            </span>
            <span className="text-[10px] text-slate-400">{todayData?.today_orders || 18} orders today</span>
          </div>

          <div className="flex flex-col border-x border-white/10 px-2 sm:px-3">
            <span className="text-[11px] font-medium text-slate-400 uppercase tracking-wider">Avg Basket</span>
            <span className="text-base sm:text-xl font-bold font-mono text-cyan-300">
              ₹{todayData ? Math.round(todayData.today_avg_order) : '147'}
            </span>
            <span className="text-[10px] text-slate-400">per customer</span>
          </div>

          <div className="flex flex-col justify-between items-end">
            <button
              onClick={startMorningBriefing}
              disabled={briefingActive}
              className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-bold bg-gradient-to-r from-amber-500 to-brand text-white shadow-lg shadow-brand/20 hover:brightness-110 active:scale-95 transition-all"
            >
              <span>⚡ Briefing</span>
            </button>
            <span className="text-[10px] text-slate-400">7-Day: ₹{todayData ? Number(todayData.period_7d_revenue).toLocaleString('en-IN') : '18,400'}</span>
          </div>
        </div>
      </header>

      {/* ── Error Toast Banner ────────────────────────────────────────────────── */}
      {errorBanner && (
        <div className="w-full mb-3 p-3 rounded-xl bg-red-500/10 border border-red-500/30 text-red-300 flex items-center justify-between text-xs sm:text-sm animate-fade-in">
          <div className="flex items-center gap-2">
            <svg xmlns="http://www.w3.org/2000/svg" className="w-5 h-5 shrink-0 text-red-400" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <span>{errorBanner}</span>
          </div>
          <button onClick={() => setErrorBanner(null)} className="text-red-400 hover:text-red-200">✕</button>
        </div>
      )}

      {/* ── Action Success Toast ──────────────────────────────────────────────── */}
      {actionToast && (
        <div className="w-full mb-3 p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-300 flex items-center gap-2 text-xs sm:text-sm animate-fade-in">
          <span>{actionToast}</span>
        </div>
      )}

      {/* ── Morning Briefing Overlay Modal ────────────────────────────────────── */}
      {briefingActive && insights.length > 0 && (
        <div className="w-full mb-4 p-4 rounded-2xl bg-gradient-to-br from-indigo-950/90 to-purple-950/90 border border-purple-500/30 shadow-2xl backdrop-blur-xl animate-fade-in">
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <span className="text-lg">🌅</span>
              <h3 className="font-bold text-sm text-purple-200">
                Morning Audio Briefing ({briefingIndex + 1}/{insights.length})
              </h3>
            </div>
            <button
              onClick={stopBriefing}
              className="px-2.5 py-1 rounded-lg bg-white/10 hover:bg-white/20 text-xs font-semibold text-slate-300"
            >
              Stop
            </button>
          </div>

          <div className="space-y-2">
            <div className="p-3 rounded-xl bg-black/40 border border-white/10">
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-bold text-amber-300 uppercase tracking-wide">
                  {insights[briefingIndex]?.title}
                </span>
                <span className="text-[10px] text-slate-400">Playing voice...</span>
              </div>
              <p className="text-sm text-slate-100 font-medium">
                {insights[briefingIndex]?.display_text}
              </p>
            </div>

            {insights[briefingIndex]?.action && (
              <button
                onClick={() => handleActionClick(insights[briefingIndex].action!)}
                className="w-full py-2 rounded-xl bg-purple-600 hover:bg-purple-500 text-white font-semibold text-xs transition-colors shadow-md"
              >
                {insights[briefingIndex].action!.label}
              </button>
            )}
          </div>
        </div>
      )}

      {/* ── Chat Messages Container ───────────────────────────────────────────── */}
      <main className="w-full flex-1 overflow-y-auto space-y-3 pr-1 my-2 max-h-[42vh] sm:max-h-[46vh] scrollbar-thin scrollbar-thumb-white/10">
        {messages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center p-6 text-slate-400 space-y-3">
            <div className="w-14 h-14 rounded-2xl bg-white/5 border border-white/10 flex items-center justify-center text-2xl shadow-inner">
              🎙️
            </div>
            <div>
              <p className="text-sm font-medium text-slate-200">Bol kar poochiye ya neeche diye gaye demo sawal tap karein</p>
              <p className="text-xs text-slate-400 mt-1">Hindi, Tamil, Telugu, Kannada, Marathi, Bengali, English</p>
            </div>
          </div>
        ) : (
          messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex flex-col ${msg.sender === 'user' ? 'items-end' : 'items-start'} animate-fade-in`}
            >
              {msg.sender === 'user' ? (
                <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-gradient-to-br from-brand/90 to-amber-600 p-3.5 text-white shadow-lg shadow-brand/10">
                  <p className="text-sm font-medium">{msg.text}</p>
                  {msg.language && (
                    <span className="inline-block mt-1 text-[10px] bg-black/20 px-2 py-0.5 rounded-full uppercase tracking-wider">
                      {msg.language}
                    </span>
                  )}
                </div>
              ) : (
                <div className="max-w-[92%] rounded-2xl rounded-tl-sm bg-slate-900 border border-white/10 p-4 shadow-xl text-slate-100 space-y-2.5">
                  <div className="flex items-center justify-between border-b border-white/5 pb-2">
                    <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[10px] font-semibold bg-cyan-500/10 text-cyan-300 border border-cyan-500/20">
                      <span>🇮🇳</span> {msg.language || 'hi-IN'}
                    </span>
                    {msg.audioBase64 && (
                      <button
                        onClick={() => playBase64Audio(msg.audioBase64!)}
                        className="flex items-center gap-1 text-[11px] font-medium text-brand hover:text-amber-400 transition-colors"
                      >
                        <span>🔊 Replay</span>
                      </button>
                    )}
                  </div>

                  <p className="text-sm sm:text-base font-medium leading-relaxed text-slate-50">
                    {msg.text}
                  </p>

                  {/* Expandable Data Used Chip */}
                  {msg.dataUsed && msg.dataUsed.length > 0 && (
                    <div>
                      <button
                        onClick={() =>
                          setExpandedDataUsed((prev) => ({
                            ...prev,
                            [msg.id]: !prev[msg.id],
                          }))
                        }
                        className="text-[11px] font-medium text-slate-400 hover:text-slate-300 flex items-center gap-1"
                      >
                        <span>📊 Data used: {msg.dataUsed.length} query</span>
                        <span className="text-[9px]">{expandedDataUsed[msg.id] ? '▲' : '▼'}</span>
                      </button>
                      {expandedDataUsed[msg.id] && (
                        <div className="mt-1 flex flex-wrap gap-1 p-2 rounded-lg bg-black/30 border border-white/5">
                          {msg.dataUsed.map((tool) => (
                            <span key={tool} className="px-2 py-0.5 rounded text-[10px] font-mono bg-white/10 text-cyan-200">
                              {tool}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}

                  {/* Dynamic Action Buttons */}
                  {msg.actions && msg.actions.length > 0 && (
                    <div className="pt-2 flex flex-wrap gap-2">
                      {msg.actions.map((act) => (
                        <button
                          key={act.action_type}
                          onClick={() => handleActionClick(act)}
                          className="px-3 py-1.5 rounded-xl text-xs font-semibold bg-brand/20 hover:bg-brand/30 text-brand-light border border-brand/40 shadow-sm active:scale-95 transition-all"
                        >
                          ⚡ {act.label}
                        </button>
                      ))}
                    </div>
                  )}

                  {/* Latency Timing Breakdown */}
                  {msg.timings && (
                    <div className="text-[10px] text-slate-500 font-mono pt-1">
                      ⚡ {msg.timings.total}ms (STT: {msg.timings.stt}ms | LLM: {msg.timings.llm}ms | TTS: {msg.timings.tts}ms)
                    </div>
                  )}
                </div>
              )}
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
      </main>

      {/* ── Demo Question Chips ────────────────────────────────────────────────── */}
      <section className="w-full my-2">
        <div className="flex gap-1.5 overflow-x-auto pb-1 scrollbar-none">
          {DEMO_QUESTIONS.map((q) => (
            <button
              key={q.text}
              onClick={() => handleTextQuery(q.text, q.lang)}
              disabled={micState !== 'idle'}
              className="shrink-0 flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-medium bg-slate-900/90 hover:bg-slate-800 text-slate-300 border border-white/10 hover:border-white/20 active:scale-95 transition-all disabled:opacity-50"
            >
              <span>{q.icon}</span>
              <span>{q.label}</span>
            </button>
          ))}
        </div>
      </section>

      {/* ── Huge Centered Microphone Touch Target ──────────────────────────────── */}
      <footer className="w-full flex flex-col items-center justify-center pt-2 pb-1">
        <div className="relative flex items-center justify-center">
          {/* Animated pulse wave rings when listening */}
          {micState === 'listening' && (
            <>
              <span className="absolute w-36 h-36 rounded-full border-2 border-red-500/40 animate-ping" />
              <span className="absolute w-44 h-44 rounded-full border border-red-500/20 animate-pulse" />
            </>
          )}

          {/* Radar spinner when thinking */}
          {micState === 'thinking' && (
            <span className="absolute w-32 h-32 rounded-full border-2 border-amber-500 border-t-transparent animate-spin" />
          )}

          {/* Soundwave equalizer glow when speaking */}
          {micState === 'speaking' && (
            <span className="absolute w-32 h-32 rounded-full bg-emerald-500/20 blur-xl animate-pulse" />
          )}

          <button
            onClick={handleMicClick}
            onMouseDown={() => {
              isHoldingRef.current = true
              if (micState === 'idle') startRecording()
            }}
            onMouseUp={() => {
              if (isHoldingRef.current && micState === 'listening') {
                isHoldingRef.current = false
                stopRecording()
              }
            }}
            onTouchStart={() => {
              isHoldingRef.current = true
              if (micState === 'idle') startRecording()
            }}
            onTouchEnd={() => {
              if (isHoldingRef.current && micState === 'listening') {
                isHoldingRef.current = false
                stopRecording()
              }
            }}
            className={`relative z-10 w-24 h-24 sm:w-28 sm:h-28 rounded-full flex flex-col items-center justify-center transition-all duration-300 shadow-2xl active:scale-95 ${
              micState === 'listening'
                ? 'bg-gradient-to-br from-red-600 to-rose-700 shadow-red-500/50 scale-105'
                : micState === 'thinking'
                ? 'bg-gradient-to-br from-amber-600 to-orange-700 shadow-amber-500/50 scale-100'
                : micState === 'speaking'
                ? 'bg-gradient-to-br from-emerald-600 to-teal-700 shadow-emerald-500/50 scale-105'
                : 'bg-gradient-to-br from-brand to-amber-600 shadow-brand/40 hover:scale-105'
            }`}
          >
            {micState === 'listening' ? (
              <div className="flex flex-col items-center">
                <div className="flex items-center gap-1 h-6">
                  <span className="w-1.5 bg-white rounded-full animate-[bounce_0.6s_infinite_100ms] h-6" />
                  <span className="w-1.5 bg-white rounded-full animate-[bounce_0.6s_infinite_200ms] h-4" />
                  <span className="w-1.5 bg-white rounded-full animate-[bounce_0.6s_infinite_300ms] h-7" />
                  <span className="w-1.5 bg-white rounded-full animate-[bounce_0.6s_infinite_400ms] h-3" />
                </div>
                <span className="text-[10px] font-bold uppercase tracking-wider text-white mt-1">Sun Raha Hoon</span>
              </div>
            ) : micState === 'thinking' ? (
              <div className="flex flex-col items-center">
                <svg className="animate-spin w-8 h-8 text-white mb-1" viewBox="0 0 24 24" fill="none">
                  <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                  <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                </svg>
                <span className="text-[10px] font-bold uppercase tracking-wider text-white">Soch Raha Hoon</span>
              </div>
            ) : micState === 'speaking' ? (
              <div className="flex flex-col items-center">
                <svg xmlns="http://www.w3.org/2000/svg" className="w-8 h-8 text-white mb-1 animate-pulse" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5" />
                  <path d="M15.54 8.46a5 5 0 0 1 0 7.07" />
                  <path d="M19.07 4.93a10 10 0 0 1 0 14.14" />
                </svg>
                <span className="text-[10px] font-bold uppercase tracking-wider text-white">Bol Raha Hoon</span>
              </div>
            ) : (
              <div className="flex flex-col items-center">
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" className="w-10 h-10 text-white">
                  <path d="M12 1a4 4 0 0 1 4 4v6a4 4 0 0 1-8 0V5a4 4 0 0 1 4-4Z" />
                  <path d="M19 10a1 1 0 0 1 2 0 9 9 0 0 1-8 8.94V21h2a1 1 0 1 1 0 2H9a1 1 0 1 1 0-2h2v-2.06A9 9 0 0 1 3 10a1 1 0 0 1 2 0 7 7 0 0 0 14 0Z" />
                </svg>
                <span className="text-[10px] font-bold uppercase tracking-wider text-white mt-1">Tap & Speak</span>
              </div>
            )}
          </button>
        </div>
        <p className="text-[11px] text-slate-400 mt-2">
          {micState === 'idle'
            ? 'Tap or hold mic to ask in your language'
            : micState === 'listening'
            ? 'Tap mic again to finish'
            : micState === 'speaking'
            ? 'Tap mic to stop audio'
            : 'Fetching real database numbers...'}
        </p>
      </footer>
    </div>
  )
}
