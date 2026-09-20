"""
app/assistant.py — Gemini function-calling orchestrator for Polaris.

Flow:
  transcript + detected_language + merchant_id + session_id
    → build tool declarations
    → send to Gemini Flash with conversation history
    → tool-call loop (multi-tool per turn supported)
    → structured JSON response: {display_text, speech_text, language_code,
                                  data_used, actions}

SPEC.md rules enforced in the system prompt:
  - Reply in SAME language/script as the merchant
  - Max 2 short spoken sentences, lead with the number
  - NEVER invent figures — only use tool results
  - speech_text: TTS-friendly (no emoji, no markdown, rupees in target language)
  - Suggest concrete causes when data shows one (e.g. stockout → revenue drop)
  - Return {display_text, speech_text, language_code, data_used, actions}
"""
from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

import google.generativeai as genai
from google.generativeai.types import FunctionDeclaration, Tool

from config import get_settings
from repository.base import TransactionRepository

logger = logging.getLogger(__name__)

# ── Language → BCP-47 map ─────────────────────────────────────────────────────
LANG_CODE_MAP: dict[str, str] = {
    "hindi":     "hi-IN",
    "hindi-en":  "hi-IN",
    "hinglish":  "hi-IN",
    "tamil":     "ta-IN",
    "telugu":    "te-IN",
    "kannada":   "kn-IN",
    "marathi":   "mr-IN",
    "bengali":   "bn-IN",
    "gujarati":  "gu-IN",
    "punjabi":   "pa-IN",
    "malayalam": "ml-IN",
    "english":   "en-IN",
    "en":        "en-IN",
    "hi":        "hi-IN",
    "ta":        "ta-IN",
    "te":        "te-IN",
    "kn":        "kn-IN",
    "mr":        "mr-IN",
    "bn":        "bn-IN",
    "gu":        "gu-IN",
    "pa":        "pa-IN",
    "ml":        "ml-IN",
}

# ── Conversation memory ───────────────────────────────────────────────────────
# {session_id: [{"role": "user"|"model", "parts": [...]}]}
_memory: dict[str, list[dict]] = defaultdict(list)
MEMORY_MAX_TURNS = 6   # 6 user+model pairs = 12 entries max


# ── Decimal serialiser ────────────────────────────────────────────────────────
def _jsonable(obj: Any) -> Any:
    """Recursively convert Decimal / date / dataclass → JSON-safe types."""
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if hasattr(obj, "__dataclass_fields__"):
        return {k: _jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [_jsonable(i) for i in obj]
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


# ── Response dataclass ────────────────────────────────────────────────────────
@dataclass
class AssistantResponse:
    display_text: str          # rich text for UI (can include formatting)
    speech_text: str           # TTS-safe plain text
    language_code: str         # BCP-47: hi-IN, ta-IN, etc.
    data_used: list[str]       # which tools were actually called
    actions: list[dict]        # 0-2 button suggestions [{label, action_type}]
    latency_ms: int            # total wall-clock time


# ── System prompt ─────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """\
You are Polaris, a concise voice business assistant for Indian kirana merchants.
You have access to the merchant's real transaction database via tools.

STRICT RULES (violating any rule is unacceptable):
1. LANGUAGE: Reply in the EXACT same language and script the merchant used.
   - Hindi (Devanagari) if they spoke Hindi or Hinglish.
   - Tamil script if Tamil, Telugu script if Telugu, and so on.
   - For Hinglish (mixed Hindi-English in Roman), reply naturally in Hinglish.
2. LENGTH: Maximum 2 short spoken sentences. Lead with the number or key fact.
3. TRUTHFULNESS: NEVER invent or estimate numbers. Use ONLY values from tool
   call results. If a tool returns no data, say so honestly.
4. TTS FORMAT: speech_text must be TTS-friendly:
   - No emojis, no markdown (*bold*, #headings), no bullet points.
   - Write currency as the word for "rupees" in the reply language
     (e.g. Hindi: "rupaye", Tamil: "rubai", Telugu: "rupayalu", English: "rupees").
   - Write numbers as words where natural (e.g. "saarah hazaar" in Hindi).
5. CAUSES & DOMAIN: When data shows a clear cause (e.g. rice stockout → revenue drop),
   mention it in one clause.
6. FINANCIAL GUIDANCE / LOANS: If merchant asks about loans, credit lines or insurance:
   - Use financial_guidance tool.
   - State the indicative credit limit based on 90-day sales.
   - Always state that figures are indicative.
   - Include action button: {"label": "Talk to Paytm advisor", "action_type": "contact_advisor"}.
7. PEER BENCHMARKING & PREMIUM:
   - If peer_benchmarking returns access_granted=False / is_premium_feature=True:
     - Provide a friendly 1-2 sentence spoken upsell in the user's language explaining that peer comparisons are part of Polaris Premium (₹99/month).
     - Include action button: {"label": "Upgrade to Premium (Rs 99/mo)", "action_type": "upgrade_premium"}.
8. OUTPUT: Always respond with a JSON object (no surrounding text, no markdown
   fences) with exactly these keys:
   {
     "display_text": "<text for UI — may use rupee symbol ₹>",
     "speech_text":  "<TTS-safe text — no symbols, no markdown>",
     "language_code": "<BCP-47 code: hi-IN | ta-IN | te-IN | kn-IN | mr-IN |
                        bn-IN | gu-IN | pa-IN | ml-IN | en-IN>",
     "data_used":    ["tool_name_1", "tool_name_2"],
     "actions":      [
       {"label": "<short button label>", "action_type": "<snake_case_id>"}
     ]
   }
   - actions: 0-2 concrete next-step suggestions relevant to the answer.
     Examples: "Send reminder to Suresh Kumar", "Reorder Basmati Rice",
               "Talk to Paytm advisor", "Upgrade to Premium (Rs 99/mo)".
   - data_used must list every tool you actually called in this turn.

TOOL CALLING BEHAVIOUR:
- Call as many tools as needed to answer the question fully.
- If a follow-up question refers to a previous answer ("kal ka?" means
  "what about yesterday?"), use conversation context to infer the topic and
  call the appropriate tool with the right parameters.
"""


# ── Tool declarations ─────────────────────────────────────────────────────────
def _make_tools() -> list[Tool]:
    """
    Build Gemini Tool declarations that map 1:1 to TransactionRepository methods.
    Rich descriptions help Gemini pick the right tool and parameters.
    """
    decls = [
        FunctionDeclaration(
            name="get_sales_summary",
            description=(
                "Get total revenue, number of orders, and average order value for "
                "the merchant over the last N days. Use for questions like: "
                "'aaj ki sale?', 'is hafte kitna mila?', 'today sales', "
                "'last 7 days revenue', 'is mahine ki kamai?'"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "period_days": {
                        "type": "integer",
                        "description": (
                            "Number of days to look back from now. "
                            "Use 1 for today, 7 for this week, 30 for this month, "
                            "90 for this quarter."
                        ),
                    }
                },
                "required": ["period_days"],
            },
        ),
        FunctionDeclaration(
            name="compare_periods",
            description=(
                "Compare revenue between two consecutive periods. "
                "Use for questions like: 'is hafte vs pichle hafte?', "
                "'how does this week compare to last week?', "
                "'kya sale badhi ya ghati?', 'growth this month vs last?'"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "period_a_days": {
                        "type": "integer",
                        "description": "Length of the RECENT period in days (e.g. 7 for this week).",
                    },
                    "period_b_days": {
                        "type": "integer",
                        "description": "Length of the PRIOR period in days to compare against (e.g. 7 for last week).",
                    },
                },
                "required": ["period_a_days", "period_b_days"],
            },
        ),
        FunctionDeclaration(
            name="get_top_items",
            description=(
                "Get the top-selling items by revenue for the last N days. "
                "Use for: 'sabse zyada kya bika?', 'best selling items', "
                "'top products this week', 'kaunsa maal zyada chala?', "
                "'enge nalla vilkiradu?'"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "period_days": {
                        "type": "integer",
                        "description": "Number of days to analyse (e.g. 7, 30).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of top items to return. Default 5.",
                    },
                },
                "required": ["period_days"],
            },
        ),
        FunctionDeclaration(
            name="get_stock_status",
            description=(
                "Get current stock quantity for ALL items in the merchant's inventory. "
                "Use for: 'kitna stock hai?', 'stock check karo', "
                "'stock levels', 'stok enta undi?'"
            ),
            parameters={"type": "object", "properties": {}},
        ),
        FunctionDeclaration(
            name="get_low_stock",
            description=(
                "Get items that are AT or BELOW their reorder level — i.e. items "
                "that need to be restocked soon. "
                "Use for: 'kya khatam hone wala hai?', 'low stock items', "
                "'reorder karna hai kya?', 'stock kum hai kya?'"
            ),
            parameters={"type": "object", "properties": {}},
        ),
        FunctionDeclaration(
            name="get_stockout_impact",
            description=(
                "Find out if a specific item had a stockout event recently, "
                "and estimate how much revenue was lost because it was unavailable. "
                "Use for: 'chawal khatam hone se kitna nuksan?', "
                "'rice stockout impact', 'out of stock loss', "
                "'kab stock khatam hua aur kitna loss hua?'"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "item": {
                        "type": "string",
                        "description": (
                            "Name or partial name of the item to check. "
                            "E.g. 'rice', 'chawal', 'atta', 'oil'."
                        ),
                    }
                },
                "required": ["item"],
            },
        ),
        FunctionDeclaration(
            name="get_dues_summary",
            description=(
                "Get total pending dues (udhaar / credit) across all customers: "
                "total amount, number of customers, and per-customer breakdown. "
                "Use for: 'kitna udhaar baaki hai?', 'dues pending', "
                "'kaun kitna dena hai?', 'pending collections?', "
                "'bakaya kitna hai?'"
            ),
            parameters={"type": "object", "properties": {}},
        ),
        FunctionDeclaration(
            name="get_overdue_customers",
            description=(
                "List customers whose payment is OVERDUE (past the due date). "
                "Use for: 'kaun late hai?', 'overdue customers', "
                "'whose payment is pending the longest?', "
                "'sabse zyada kisko remind karna chahiye?'"
            ),
            parameters={"type": "object", "properties": {}},
        ),
        FunctionDeclaration(
            name="peer_benchmarking",
            description=(
                "Compare the merchant's metrics (daily sales, average order value/basket size) "
                "against anonymised averages of similar kirana stores in the area. "
                "Use for: 'mere jaise dukaan se main kaisa hoon?', 'how do I compare to peers?', "
                "'is my store doing better than average?', 'peer comparison'."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "metric": {
                        "type": "string",
                        "description": "Metric to compare: 'daily_sales' or 'avg_order_value'. Default 'daily_sales'.",
                    }
                },
            },
        ),
        FunctionDeclaration(
            name="financial_guidance",
            description=(
                "Check indicative working capital loan eligibility or financial guidance based on 90-day sales. "
                "Use for: 'mujhe loan mil sakta hai kya?', 'working capital limit', "
                "'loan eligibility', 'can I get business loan / insurance?'"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "intent": {
                        "type": "string",
                        "description": "Financial intent: 'loan', 'credit', or 'insurance'. Default 'loan'.",
                    }
                },
            },
        ),
    ]
    return [Tool(function_declarations=decls)]


# ── Tool executor ─────────────────────────────────────────────────────────────
def _execute_tool(
    name: str,
    args: dict,
    merchant_id: int,
    repo: TransactionRepository,
    is_premium: bool = False,
) -> dict:
    """
    Dispatch a Gemini tool call to the correct TransactionRepository method.
    Returns a JSON-serialisable dict.
    """
    try:
        if name == "get_sales_summary":
            result = repo.sales_summary(merchant_id, args.get("period_days", 1))
            return _jsonable(result)

        elif name == "compare_periods":
            result = repo.compare_periods(
                merchant_id,
                args.get("period_a_days", 7),
                args.get("period_b_days", 7),
            )
            return _jsonable(result)

        elif name == "get_top_items":
            result = repo.top_items(
                merchant_id,
                args.get("period_days", 7),
                args.get("limit", 5),
            )
            return {"items": _jsonable(result)}

        elif name == "get_stock_status":
            result = repo.stock_status(merchant_id)
            return {"items": _jsonable(result)}

        elif name == "get_low_stock":
            result = repo.low_stock(merchant_id)
            return {"items": _jsonable(result)}

        elif name == "get_stockout_impact":
            result = repo.stockout_impact(merchant_id, args.get("item", "rice"))
            if result is None:
                return {"found": False, "message": f"No stockout event found for '{args.get('item', '')}'."}
            return {"found": True, **_jsonable(result)}

        elif name == "get_dues_summary":
            result = repo.dues_summary(merchant_id)
            return _jsonable(result)

        elif name == "get_overdue_customers":
            result = repo.overdue_customers(merchant_id)
            return {"customers": _jsonable(result)}

        elif name == "peer_benchmarking":
            if not is_premium:
                return {
                    "is_premium_feature": True,
                    "access_granted": False,
                    "message": "Peer benchmarking is a Polaris Premium feature (₹99/month). Kindly ask the user to upgrade to unlock local peer comparisons.",
                }
            metric = args.get("metric", "daily_sales")
            result = repo.peer_benchmarking(merchant_id, metric)
            return _jsonable(result)

        elif name == "financial_guidance":
            intent = args.get("intent", "loan")
            result = repo.financial_guidance(merchant_id, intent)
            return _jsonable(result)

        else:
            return {"error": f"Unknown tool: {name}"}

    except Exception as exc:
        logger.exception("Tool %s raised an exception", name)
        return {"error": str(exc)}

    except Exception as exc:
        logger.exception("Tool %s raised an exception", name)
        return {"error": str(exc)}


# ── Response parser ───────────────────────────────────────────────────────────
def _parse_response(text: str, detected_language: str) -> dict:
    """
    Extract the structured JSON from Gemini's final text response.
    Falls back gracefully if JSON is malformed.
    """
    # Strip markdown code fences if model wrapped the JSON
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # remove first and last fence lines
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        # Try to extract JSON object from surrounding text
        import re
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group())
            except json.JSONDecodeError:
                obj = {}
        else:
            obj = {}

    # Ensure all required keys are present with sane defaults
    lang_code = obj.get("language_code") or LANG_CODE_MAP.get(
        detected_language.lower(), "en-IN"
    )
    return {
        "display_text": obj.get("display_text", text),
        "speech_text":  obj.get("speech_text", obj.get("display_text", text)),
        "language_code": lang_code,
        "data_used":    obj.get("data_used", []),
        "actions":      obj.get("actions", [])[:2],   # cap at 2
    }


# ── Main orchestrator ─────────────────────────────────────────────────────────
def run_assistant(
    transcript: str,
    detected_language: str,
    merchant_id: int,
    repo: TransactionRepository,
    session_id: str = "default",
    is_premium: bool = False,
) -> AssistantResponse:
    """
    Full Gemini function-calling loop for one merchant query.

    Args:
        transcript:        Text of what the merchant said (from Whisper or text).
        detected_language: Language name or code (e.g. "hindi", "hi", "tamil").
        merchant_id:       Which merchant's data to query.
        repo:              Injected TransactionRepository.
        session_id:        Conversation session key for memory.
        is_premium:        Whether the merchant is on Polaris Premium tier.

    Returns:
        AssistantResponse with display_text, speech_text, language_code,
        data_used, actions, latency_ms.
    """
    t0 = time.time()
    settings = get_settings()

    # ── Initialise Gemini ─────────────────────────────────────────────────────
    genai.configure(api_key=settings.gemini_api_key)
    model = genai.GenerativeModel(
        model_name="gemini-3.5-flash-lite",
        system_instruction=SYSTEM_PROMPT,
        tools=_make_tools(),
    )

    # ── Build conversation history ────────────────────────────────────────────
    history = list(_memory[session_id])   # copy — don't mutate cache during turn

    # Add a language hint to the user message so Gemini always knows
    lang_hint = f"[Merchant is speaking {detected_language}. Reply ONLY in {detected_language}.]\n"
    user_message = lang_hint + transcript

    def _send_with_retry(chat_obj, msg, max_retries=3):
        for attempt in range(max_retries):
            try:
                return chat_obj.send_message(msg)
            except Exception as e:
                err_str = str(e)
                if ("429" in err_str or "quota" in err_str.lower() or "resourceexhausted" in err_str.lower()) and attempt < max_retries - 1:
                    wait_time = 5 * (attempt + 1)
                    # Extract suggested retry delay if present
                    import re
                    match = re.search(r"retry_delay\s*\{\s*seconds:\s*(\d+)", err_str)
                    if match:
                        wait_time = min(int(match.group(1)) + 1, 35)
                    logger.warning("Gemini 429 rate limit hit. Retrying in %ds (attempt %d/%d)...", wait_time, attempt + 1, max_retries)
                    time.sleep(wait_time)
                else:
                    raise

    # ── Start chat with history ───────────────────────────────────────────────
    chat = model.start_chat(history=history)

    # ── Send first message ────────────────────────────────────────────────────
    response = _send_with_retry(chat, user_message)

    tools_called: list[str] = []
    max_tool_rounds = 8   # safety cap

    # ── Tool-call loop ────────────────────────────────────────────────────────
    for _ in range(max_tool_rounds):
        # Collect all function calls in this response
        calls = []
        for part in response.candidates[0].content.parts:
            if hasattr(part, "function_call") and part.function_call.name:
                calls.append(part.function_call)

        if not calls:
            break  # Model produced a text response — we're done

        # Execute all tool calls in this round
        tool_results = []
        for fc in calls:
            name = fc.name
            args = dict(fc.args)
            logger.info("Calling tool %s with args %s", name, args)
            result = _execute_tool(name, args, merchant_id, repo, is_premium=is_premium)
            tools_called.append(name)
            tool_results.append(
                genai.protos.Part(
                    function_response=genai.protos.FunctionResponse(
                        name=name,
                        response={"result": result},
                    )
                )
            )

        # Feed all results back to the model in one message
        response = _send_with_retry(chat, tool_results)

    # ── Extract final text ────────────────────────────────────────────────────
    final_text = ""
    for part in response.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            final_text += part.text

    # ── Parse structured JSON ─────────────────────────────────────────────────
    parsed = _parse_response(final_text, detected_language)

    # Merge tools_called into data_used (model may list them too)
    data_used = list(dict.fromkeys(tools_called + parsed["data_used"]))

    # ── Update conversation memory ────────────────────────────────────────────
    history_entry_user  = {"role": "user",  "parts": [user_message]}
    history_entry_model = {"role": "model", "parts": [final_text]}
    _memory[session_id].append(history_entry_user)
    _memory[session_id].append(history_entry_model)

    # Trim to last MEMORY_MAX_TURNS*2 entries (user+model pairs)
    if len(_memory[session_id]) > MEMORY_MAX_TURNS * 2:
        _memory[session_id] = _memory[session_id][-(MEMORY_MAX_TURNS * 2):]

    latency_ms = int((time.time() - t0) * 1000)
    logger.info(
        "Assistant done | lang=%s | tools=%s | latency=%dms",
        parsed["language_code"], tools_called, latency_ms,
    )

    return AssistantResponse(
        display_text  = parsed["display_text"],
        speech_text   = parsed["speech_text"],
        language_code = parsed["language_code"],
        data_used     = data_used,
        actions       = parsed["actions"],
        latency_ms    = latency_ms,
    )


def clear_session(session_id: str) -> None:
    """Wipe conversation memory for a session (e.g. on new merchant login)."""
    _memory.pop(session_id, None)
