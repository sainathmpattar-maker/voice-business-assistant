# Polaris — Multilingual Voice Business Assistant for Merchants

**Polaris** is a voice-first, multilingual business intelligence assistant designed specifically for Indian kirana store merchants and small business owners.

Merchants speak naturally in their native language (**Hindi, Tamil, Telugu, Kannada, Marathi, Bengali, Gujarati, Punjabi, Malayalam, English, or Hinglish**). Polaris transcribes the audio, understands the business intent, fetches **REAL** metrics from the merchant's transaction database via function calling, and **speaks a concise answer back in the same language and script** within seconds.

---

## 🏗️ Architecture & Pipeline

```mermaid
flowchart TD
    A[📱 Merchant Voice Audio<br/>MediaRecorder / Web Audio] -->|POST /api/voice/ask| B(FastAPI Gateway)
    
    subgraph Pipeline [Voice Intelligence Pipeline]
        B --> C[🎙️ Groq Whisper large-v3<br/>Domain Vocab Prompt]
        C -->|Transcript + Language| D[🧠 Gemini Flash LLM<br/>Function Calling & 6-Turn Memory]
        
        D <-->|Multi-Tool Execution Loop| E[(Postgres DB / Repository<br/>Sales, Stock, Dues, Benchmarks)]
        
        D -->|Structured Response<br/>speech_text + display_text + actions| F[🔊 Gemini TTS & gTTS<br/>Native Language Voice Audio]
    end
    
    F -->|Base64 MP3 Audio + Actions JSON| G[📱 Mobile UI<br/>Autoplay + Action Buttons]
```

### End-to-End Flow
1. **Audio Capture**: React frontend records audio blob via `MediaRecorder` API.
2. **Speech-to-Text (STT)**: Groq Whisper `whisper-large-v3` with domain vocabulary hints (`Paytm, kirana, chawal, atta, udhaar, dues, stock, etc.`) detects text and spoken language.
3. **Reasoning & Tool Execution (LLM)**: Gemini Flash decides which database tools to call (`sales_summary`, `compare_periods`, `top_items`, `stock_status`, `low_stock`, `stockout_impact`, `dues_summary`, `overdue_customers`, `peer_benchmarking`, `financial_guidance`). Zero hallucination — all numbers come directly from tool outputs.
4. **Text-to-Speech (TTS)**: Gemini TTS with gTTS (Google Translate Text-to-Speech) zero-credential fallback synthesizes native BCP-47 voice audio at 0.95 speaking rate with in-memory LRU caching.
5. **UI & Speech Output**: Response audio autoplays, formatted display cards show figures with rupee formatting, and interactive one-tap action buttons (e.g. WhatsApp due reminders, restock orders) are presented.

---

## 🚀 Key Features

- 🎙️ **Zero-Reading Voice UX**: Giant centered mic touch target with tap-to-talk, hold-to-talk, and visual state feedback (`listening`, `thinking`, `speaking`).
- 🇮🇳 **10 Indian Languages**: Native script replies across Hindi (Devanagari), Tamil, Telugu, Kannada, Marathi, Bengali, Gujarati, Punjabi, Malayalam, and English.
- ⚡ **Sub-5-Second Latency**: Optimized pipeline with per-stage latency breakdown (`stt`, `llm`, `tts`, `total`).
- 🌅 **Morning Audio Briefing**: `GET /api/insights` delivers 3 proactive spoken insights (Stockout impact loss, low-inventory restock alerts, overdue credit reminders) with sequential playback.
- 📊 **Peer Benchmarking (Premium)**: Compares merchant daily sales and average basket size against local store averages.
- 💼 **Financial Guidance**: Calculates indicative working capital credit limits from 90-day transaction volume with a direct "Talk to Paytm Advisor" CTA.
- 👑 **Free vs. Premium Demo Gating**: Interactive toggle demonstrating tiered monetization (`₹99–₹199/month`).
- 🔌 **Paytm API Ready**: Repository pattern (`TransactionRepository`) decouples database access from ORM models, making it a drop-in swap for the real Paytm Merchant APIs.

---

## 📊 Planted Demo Scenarios (Seed Data)

The synthetic seed generator (`backend/seed.py`) creates 90 days of realistic kirana store transactions for **Ramesh Kirana Store (Merchant 1)** and a benchmark peer **Sunita General Store (Merchant 2)** with planted patterns:
- **Last 7 Days Sales**: Total ~₹18,400 (roughly 12% lower than the prior 7 days).
- **Stockout Event**: Basmati Rice was out of stock last Tuesday, causing a sharp Tuesday sales drop (~₹1,275 estimated revenue loss).
- **Pending & Overdue Dues**: 6 customers have pending credit accounts, with **Suresh Kumar** overdue by 30+ days (₹1,200–₹1,850).
- **Top Staples**: Atta, Rice, Refined Oil, Sugar, and Tea make up the top sales volume.

---

## 🛠️ Quick Start Guide

### Prerequisites
- **Python 3.11+**
- **Node.js 18+**
- **Docker & Docker Compose** (or Neon PostgreSQL)

---

### Step 1: Clone & Configure Environment

```bash
# Clone the repository
git clone https://github.com/your-repo/polaris.git
cd polaris

# Configure backend environment
cp .env.example backend/.env
```

Edit `backend/.env` with your API keys:
```ini
DATABASE_URL=postgresql://polaris:polaris@localhost:5432/polaris
GROQ_API_KEY=gsk_your_groq_api_key
GEMINI_API_KEY=your_gemini_api_key
MERCHANT_ID=1
CORS_ORIGINS=http://localhost:5173
```

---

### Step 2: Launch PostgreSQL Database

```bash
docker compose up -d
```

---

### Step 3: Start the Backend

```bash
cd backend

# Setup Python virtual environment
python -m venv .venv
.venv\Scripts\activate       # Windows
# source .venv/bin/activate  # macOS / Linux

# Install dependencies
pip install -r requirements.txt

# Seed the database with 90 days of demo data
python seed.py

# Launch FastAPI server
uvicorn main:app --reload --port 8000
```

Verify backend health: [http://localhost:8000/api/health](http://localhost:8000/api/health)

---

### Step 4: Start the Frontend

```bash
cd ../frontend

# Install dependencies
npm install

# Start Vite dev server
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

---

## 🎯 How to Demo

1. **Morning Briefing**:
   - Tap **"⚡ Briefing"** at the top right to hear the automated 3-part daily briefing (Stockout loss analysis, low stock alert, overdue customer warning).
2. **Sales & Comparison (Hindi)**:
   - Tap mic or chip: *"Aaj ki sale kitni rahi?"* -> Returns today's sales figure.
   - Tap follow-up chip: *"Aur kal ka?"* -> Tests 6-turn session memory.
   - Speak/Type: *"Is hafte business kaisa raha?"* -> Explains ~12% drop and highlights the Tuesday rice stockout.
3. **Multilingual Inquiries**:
   - **Tamil**: *"இந்த வாரம் எவ்வளவு விற்பனை?"* / *"Intha vaaram enge nalla vilkiradu?"* -> Replies in Tamil script with top products.
   - **Telugu**: *"ఎవరికి బాకీ ఉంది?"* / *"Evariki baaki undi?"* -> Returns pending dues and flags Suresh Kumar's 30+ day overdue balance.
4. **Financial Guidance (Loans)**:
   - Ask: *"Mujhe loan mil sakta hai kya?"* -> Estimates working capital limit based on 90-day volume (~₹1,00,000 credit limit) with "Talk to Paytm advisor" button.
5. **Peer Benchmarking & Premium Gating**:
   - Toggle **Free Tier** -> Ask: *"Mere jaise dukaan se main kaisa hoon?"* -> Friendly voice upsell with "Upgrade to Premium (Rs 99/mo)" button.
   - Toggle **Premium Tier** -> Ask same question -> Spoken comparison against local peer averages.

---

## 🧪 Running Automated Tests

Run the complete test suite (39 automated tests in-memory SQLite):
```bash
cd backend
.venv\Scripts\python -m pytest tests/ -v
```

Run end-to-end pipeline verification:
```bash
.venv\Scripts\python test_full_pipeline.py
```

---

## 🚢 Production Deployment

### Backend (Render / Railway)
- Deploy using the included [`render.yaml`](render.yaml) blueprint.
- Set environment variables: `DATABASE_URL`, `GROQ_API_KEY`, `GEMINI_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `CORS_ORIGINS`.

### Frontend (Vercel)
- Deploy using the included [`frontend/vercel.json`](frontend/vercel.json).
- Set Vite proxy or backend URL in environment settings.
