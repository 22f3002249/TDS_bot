import json
import time
import os
import requests
import subprocess
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, ContextTypes, filters

# --- Configuration ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "PASTE_YOUR_BOTFATHER_TOKEN_HERE")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "PASTE_YOUR_AI_STUDIO_API_KEY_HERE")
LOG_URL = "https://raw.githubusercontent.com/22f3002249/TDS_bot/main/run.jsonl"

GEMINI_MODELS = [
    os.environ.get("PRIMARY_MODEL", "gemini-3.6-flash"),         # Primary Model
    os.environ.get("FALLBACK_MODEL_1", "gemini-3.5-flash-lite"), # Fallback 1
    os.environ.get("FALLBACK_MODEL_2", "gemini-3.1-flash-lite"), # Fallback 2
]

LOG_FILE = "run.jsonl"

# Keeps the last few messages per chat, so multi-turn questions work
conversation_history = {}

def log_event(event: dict):
    event["timestamp"] = time.time()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(event) + "\n")
    
    try:
        subprocess.run(["git", "add", LOG_FILE], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "commit", "-m", "auto-update run.jsonl"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["git", "push"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass

def call_gemini_with_fallbacks(contents: list) -> tuple[str, str]:
    """Loops through Gemini models sequentially until a response is successfully generated."""
    llm_output = None
    success_model = None

    for model_name in GEMINI_MODELS:
        try:
            log_event({"event": "trying_model", "model": model_name})
            
            # Google AI Studio REST API endpoint
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}"
            
            payload = {
                "contents": contents,
                "generationConfig": {
                    "temperature": 0.0
                }
            }

            response = requests.post(url, json=payload, timeout=45)
            response_data = response.json()
            
            # Extract response from Gemini structure
            if "candidates" in response_data and len(response_data["candidates"]) > 0:
                llm_output = response_data["candidates"][0]["content"]["parts"][0]["text"].strip()
                success_model = model_name
                log_event({"event": "model_success", "model": model_name, "output": llm_output})
                break
            else:
                log_event({"event": "model_failed", "model": model_name, "response": response_data})
                
        except Exception as e:
            log_event({"event": "model_exception", "model": model_name, "error": str(e)})
            continue
            
    return llm_output, success_model

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Clear log file on new interaction if desired, or keep appending
    if os.path.exists(LOG_FILE):
        os.remove(LOG_FILE)

    chat_id = update.effective_chat.id
    user_text = update.message.text
    log_event({"type": "incoming", "chat_id": chat_id, "text": user_text})

    history = conversation_history.setdefault(chat_id, [])
    history.append({"role": "user", "content": user_text})

    system_prompt = (
        "You are a careful data analyst agent. The user's LAST message asks a data-analysis "
        "question and tells you exactly what JSON shape to reply with. Work out the "
        "real answer (use any public data you know, e.g. MOSPI statistics, general "
        "world knowledge, or arithmetic on numbers given in the message). "
        "Return ONLY a valid JSON string matching the requested output format. "
        "Do not include markdown code block ticks like ```json in your response, just the raw JSON object."
    )

    # Format conversation contents structure for Gemini REST API
    gemini_contents = []
    for msg in history[-6:]:
        role_tag = "User" if msg["role"] == "user" else "Assistant"
        gemini_contents.append({"parts": [{"text": f"{role_tag}: {msg['content']}"}]})
    
    # Append the direct system instructions & query instruction at the end
    gemini_contents.append({"parts": [{"text": f"{system_prompt}\n\nQuestion: {user_text}"}]})

    # Call your Gemini fallback loop
    llm_output, success_model = call_gemini_utils = call_gemini_with_fallbacks(gemini_contents)

    if not llm_output:
        reply_text = json.dumps({"error": "All Gemini models failed to generate a response."})
    else:
        # Robustly parse and clean output (stripping markdown fences if model added them)
        try:
            cleaned_output = llm_output
            if "```json" in cleaned_output:
                start = cleaned_output.find("```json") + 7
                end = cleaned_output.find("```", start)
                cleaned_output = cleaned_output[start:end].strip()
            elif "```" in cleaned_output:
                start = cleaned_output.find("```") + 3
                end = cleaned_output.find("```", start)
                cleaned_output = cleaned_output[start:end].strip()

            if "{" in cleaned_output and "}" in cleaned_output:
                start, end = cleaned_output.find("{"), cleaned_output.rfind("}")
                cleaned_output = cleaned_output[start:end + 1]

            parsed = json.loads(cleaned_output)
        except json.JSONDecodeError:
            parsed = {"error": "Failed to parse LLM output", "raw": llm_output}

        # Inject mandatory log_url field
        if isinstance(parsed, dict):
            parsed["log_url"] = LOG_URL
            
        final_reply = json.dumps(parsed)
        history.append({"role": "assistant", "content": final_reply})
        reply_text = final_reply

    log_event({"type": "outgoing", "chat_id": chat_id, "model_used": success_model, "text": reply_text})
    await update.message.reply_text(reply_text)

# --- Start Bot Polling ---
if __name__ == "__main__":
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "PASTE_YOUR_BOTFATHER_TOKEN_HERE":
        print("Error: Please set your TELEGRAM_BOT_TOKEN.")
        exit(1)
        
    if not GEMINI_API_KEY or GEMINI_API_KEY == "PASTE_YOUR_AI_STUDIO_API_KEY_HERE":
        print("Error: Please set your GEMINI_API_KEY.")
        exit(1)

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Gemini Telegram Bot is running via Polling... (Ctrl+C to stop)")
    app.run_polling()