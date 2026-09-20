# Voice Business Assistant for Merchants — SPEC

## Goal
Merchants speak in any Indian language (Hindi, Tamil, Telugu, Kannada, Marathi, Bengali, Gujarati, Punjabi, Malayalam, English/Hinglish). The app transcribes, understands, fetches REAL numbers from the merchant's transaction database, and SPEAKS a short answer back in the same language. No menus, no typing.

## Stack
- **Frontend**: React (Vite) + Tailwind CSS
- **Backend**: FastAPI (Python 3.11), SQLAlchemy, simple DB init (no Alembic)
- **DB**: PostgreSQL (docker-compose)
- **STT**: Whisper via Groq API (`whisper-large-v3`)
- **LLM**: Gemini Flash with function calling; LLM must NEVER invent numbers
- **TTS**: Google Cloud Text-to-Speech, voice chosen by detected language
- **Config**: `.env` file (never commit keys); `.env.example` provided

## Pipeline
```
audio blob → POST /api/voice/ask
  → Groq Whisper (text + detected_language)
  → Gemini with tools (sales, stock, dues, etc.)
  → short answer
  → Google TTS (mp3)
  → return { transcript, language, display_text, speech_text, audio_base64, data_used, actions }
```

## Rules
1. Answers max 2 short sentences, spoken style, lead with the number.
2. Reply in the SAME language and script the merchant used.
3. Every figure must come from a tool call result. If data is missing, say so.
4. `speech_text` must be TTS-friendly: no emojis, no markdown, currency as "rupees" in the target language.
5. Data layer sits behind a `TransactionRepository` interface (replaceable with real Paytm API).
6. Target end-to-end latency under 5 seconds.
7. Clean code, type hints, small modules, README with run instructions.

## Language → TTS Voice Map
| Language | BCP-47 | Google Voice |
|---|---|---|
| Hindi | hi-IN | hi-IN-Wavenet-C |
| Tamil | ta-IN | ta-IN-Wavenet-C |
| Telugu | te-IN | te-IN-Standard-B |
| Kannada | kn-IN | kn-IN-Wavenet-B |
| Marathi | mr-IN | mr-IN-Wavenet-C |
| Bengali | bn-IN | bn-IN-Wavenet-C |
| Gujarati | gu-IN | gu-IN-Wavenet-B |
| Punjabi | pa-IN | pa-Guru-IN-Wavenet-B |
| Malayalam | ml-IN | ml-IN-Wavenet-C |
| English/Hinglish | en-IN | en-IN-Wavenet-D |

## DB Schema
```
merchants (id, name, shop_name, city, phone)
transactions (id, merchant_id, amount, payment_mode, status, created_at)
transaction_items (id, transaction_id, product_id, quantity, unit_price)
products (id, merchant_id, name, category, stock_qty, reorder_level)
customers (id, merchant_id, phone, first_seen, last_seen)
dues (id, merchant_id, customer_id, amount_due, due_date, status)
```

## Gemini Tools (7)
| Tool | Args | Returns |
|------|------|---------|
| `get_sales_summary` | days | total_revenue, num_orders, avg_order_value |
| `get_top_products` | days, limit | list of product + revenue |
| `get_pending_dues` | — | total_due, num_customers |
| `get_stock_levels` | — | list of product + quantity |
| `get_customer_stats` | days | new, returning, total |
| `get_payment_mode_breakdown` | days | UPI%, card%, cash% |
| `get_refund_stats` | days | count, total_amount |

## Seeded Data
- 1 demo merchant: Ramesh Kirana Store, Mumbai
- 90 days of synthetic transactions (~25 orders/day, ₹150–₹2000)
- Products: Basmati Rice, Toor Dal, Refined Oil, Sugar, Atta, etc.
- Payment modes: UPI 60%, Cash 25%, Card 15%
- 5 customers with recurring dues
- 3 products near/at reorder level
