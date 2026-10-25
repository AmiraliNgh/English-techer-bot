bot.py
import os
import re
import requests
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
QWEN_API_KEY = os.getenv("QWEN_API_KEY")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus")  # qwen-plus بهتره برای استدلال
BOT_USERNAME = os.getenv("BOT_USERNAME", "").lower()  # بدون @

QWEN_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
HEADERS = {"Authorization": f"Bearer {QWEN_API_KEY}", "Content-Type": "application/json"}

# --- پرامپت اصلی (قلب ربات) ---
SYSTEM_PROMPT = """You are an expert English Teacher in a Telegram group.
Your tasks:
1. CORRECTION: Fix grammar, spelling, punctuation, and unnatural phrasing. Keep the original meaning.
2. NATIVE VERSION: Provide 1-2 natural, idiomatic alternatives a native speaker would use (casual/formal).
3. EXPLANATION: Briefly explain *why* (key grammar rule, vocab, preposition).
4. Q&A MODE: If the user asks a question (starts with ? or wh-words), ANSWER IT DIRECTLY as a teacher. Do NOT correct the question unless it's unintelligible.

FORMAT (MarkdownV2):
*✅ Correction:* `corrected sentence`
*🗣 Native:* `natural version 1` | `natural version 2`
*💡 Tip:* short explanation

Keep it SHORT. No long lectures. Group friendly tone."""

def is_trigger(text: str, is_reply: bool, mentioned: bool) -> tuple[bool, str]:
    """بررسی می‌کند آیا ربات باید پاسخ دهد یا خیر. خروجی: (آیا_فعالمه، متن_پاک_شده)"""
    if not text: return False, ""
    clean = text.strip()

    # 1. منشن صریح: @botname fix ...
    if mentioned:
        # حذف منشن از اول متن
        clean = re.sub(rf'^@{re.escape(BOT_USERNAME)}\s*', '', clean, flags=re.IGNORECASE).strip()
        return True, clean

    # 2. ریپلای با کلمات کلیدی
    if is_reply:
        triggers = ['fix', 'اصلاح', 'correct', 'improve', 'check', 'teacher', 'native']
        if any(t in clean.lower() for t in triggers):
            return True, clean

    # 3. پیشوندهای صریح در گروه
    explicit_prefixes = ['fix:', 'correct:', 'native:', 'تحویل:', 'اصلاح:']
    for p in explicit_prefixes:
        if clean.lower().startswith(p):
            return True, clean[len(p):].strip()

    # 4. سوال مستقیم به ربات (با منشن یا در پیوی)
    # در گروه حتما باید منشن شده باشد تا جواب سوال بدهد، وگرنه اسپم میشه
    # این بخش در handler چک میشه

    return False, ""

async def call_qwen(messages: list) -> str:
    payload = {"model": QWEN_MODEL, "messages": messages, "temperature": 0.3}
    try:
        r = requests.post(QWEN_URL, headers=HEADERS, json=payload, timeout=25)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        return f"⚠️ API Error: {e}"

def escape_md(text: str) -> str:
    # تلگرام MarkdownV2 کاراکترهای خاص رو escape میخواد
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or not msg.text: return

    chat_type = update.effective_chat.type
    text = msg.text
    user = update.effective_user

    # تشخیص منشن
    mentioned = False
    if msg.entities:
        for ent in msg.entities:
            if ent.type == "mention" and text[ent.offset:ent.offset+ent.length].lower() == f"@{BOT_USERNAME}":
                mentioned = True
                break

    # تشخیص ریپلای
    is_reply = msg.reply_to_message is not None
    target_text = msg.reply_to_message.text if is_reply else text

    # --- LOGIC GROUP ---
    if chat_type in ["group", "supergroup"]:
        should_respond, prompt_text = is_trigger(text, is_reply, mentioned)

        # اگر سوال پرسیده شده (با منشن) اما trigger معمولی نداشته
        if mentioned and not should_respond:
             # اگر متن فقط منشن هست یا سوال هست
             if len(text.split()) > 1 or '?' in text or text.lower().startswith(('what','how','why','when','where','who','which','can you','could you')):
                 should_respond = True
                 prompt_text = re.sub(rf'^@{re.escape(BOT_USERNAME)}\s*', '', text, flags=re.IGNORECASE).strip()

        if not should_respond:
            return  # نادیده بگیر (مهم برای گروه)

        # اگر ریپلای هست، متن مقصد متنِ ریپلای شده است
        if is_reply:
            content_to_process = target_text
            # پرامپت یوزر (مثل fix:) به عنوان دستورالعمل اضافه میشه
            user_instruction = f"User instruction: {prompt_text}\n\nText to process:" if prompt_text != target_text else ""
        else:
            content_to_process = prompt_text
            user_instruction = ""

    # --- LOGIC PRIVATE ---
    else: # Private chat: همیشه جواب بده
        content_to_process = text
        user_instruction = ""

    # ساخت پیام برای API
    api_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"{user_instruction}\n{content_to_process}"}
    ]

    # ارسال "در حال تایپ..."
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    response = await call_qwen(api_messages)

    # ارسال پاسخ
    try:
        await msg.reply_text(response, parse_mode="MarkdownV2")
    except Exception as e:
        # فالبک بدون مارکداون
        await msg.reply_text(response.replace('*', '').replace('`', ''))

def main():
    if not BOT_TOKEN or not QWEN_API_KEY or not BOT_USERNAME:
        raise ValueError("Check .env variables: BOT_TOKEN, QWEN_API_KEY, BOT_USERNAME")

    app = Application.builder().token(BOT_TOKEN).build()
    # همه پیام‌های متنی (دستورات هم می‌رسند اینجا، فیلتر نکردیم تا منشن کار کنه)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.StatusUpdate.ALL, handle_message))
    print("Bot running...")
    app.run_polling()

if __name__ == "__main__":
    main()
```

4.  فایل جدید بساز: `requirements.txt`
    ```text
    python-telegram-bot==20.7
    requests
    python-dotenv
    ```
5.  فایل جدید بساز: `.env.example` (برای یادآوری متغیرها)
    ```text
    BOT_TOKEN=your_telegram_bot_token
    QWEN_API_KEY=your_qwen_api_key
    QWEN_MODEL=qwen-plus
    BOT_USERNAME=YourBotUsernameWithoutAt
    ```
