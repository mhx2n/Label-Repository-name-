import logging, os, asyncio, re
from telegram import Update, Poll, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------- CONFIG -----------------
ADMINS = set([8389621809])   # শুরুতে owner এর user_id বসাও
CHANNELS = {}               # {channel_id: channel_name}
QUESTION_PREFIX = "[✨𝙏𝘼𝙍𝙂𝙀𝙏 🎯]"
AUTO_EXPLANATION_LINK = "@FX_Ur_Target"
QUIZ_DELIMITER = "n"

OPTION_LABEL_RE = re.compile(r"^\s*(\([a-jA-J]\)|[a-jA-J][\.\)])\s+")

# ----------------- PARSER -----------------
def parse_single_quiz_block(block: str):
    lines = [l.strip() for l in block.splitlines() if l.strip()]
    if len(lines) < 3:
        raise ValueError("❌ প্রশ্ন + অন্তত ২টি অপশন থাকতে হবে।")
    question = lines[0]
    rest_lines = lines[1:]
    options, explanation_lines = [], []
    correct_idx, explanation_started = -1, False
    for raw_line in rest_lines:
        line = raw_line.strip()
        if not line: continue
        if not explanation_started:
            has_star = "*" in line
            clean_line = line.replace("*", "").strip()
            has_option_label = OPTION_LABEL_RE.match(clean_line) is not None
            if has_star or has_option_label or len(options) < 2:
                options.append(clean_line)
                if has_star:
                    if correct_idx != -1:
                        raise ValueError("❌ একাধিক সঠিক উত্তর দেওয়া হয়েছে।")
                    correct_idx = len(options) - 1
            else:
                explanation_started = True
                explanation_lines.append(line)
        else:
            explanation_lines.append(line)
    if correct_idx == -1:
        raise ValueError("❌ সঠিক উত্তর `*` দিয়ে চিহ্নিত করতে হবে।")
    if len(options) < 2:
        raise ValueError("❌ কমপক্ষে ২টি অপশন থাকতে হবে।")
    if len(options) > 10:
        raise ValueError("❌ Telegram quiz সর্বোচ্চ ১০টি অপশন নিতে পারে।")
    return question, options, correct_idx, "\n".join(explanation_lines).strip()

def parse_multiple_quizzes(raw_text: str):
    quiz_blocks = [b.strip() for b in raw_text.split(f"\n{QUIZ_DELIMITER}\n") if b.strip()]
    parsed, errors = [], []
    for i, block in enumerate(quiz_blocks, start=1):
        try:
            q, opts, idx, exp = parse_single_quiz_block(block)
            parsed.append({
                "question": f"{QUESTION_PREFIX}\n{q}",
                "options": opts,
                "correct_option_index": idx,
                "explanation": f"{exp}\n{AUTO_EXPLANATION_LINK}" if exp else AUTO_EXPLANATION_LINK,
            })
        except Exception as e:
            errors.append(f"কুইজ {i} ত্রুটি: {e}")
    return parsed, errors

# ----------------- HANDLERS -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return await update.message.reply_text(
            "❌ This bot is owned by (@Your_Himus). Please request permission from the owner before using it."
        )

    # Admins get a professional welcome
    welcome_text = (
        "✨ Welcome to <b>Ultron Advanced Quiz Bot</b> ✨\n\n"
        "This bot is designed to help you manage quizzes across multiple channels with ease.\n"
        "You can add admins, configure channels, customize prefixes, and automate quiz creation.\n\n"
        "👉 To explore all available commands and features, simply type <b>/help</b>.\n\n"
        "Let's make your academic and community quizzes more professional, engaging, and error‑free 🚀"
    )

    await update.message.reply_text(welcome_text, parse_mode="HTML")


# Admin management
async def add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return await update.message.reply_text("❌ অনুমতি নেই।")
    if not context.args:
        return await update.message.reply_text("Usage: /addadmin <user_id>")
    new_admin = int(context.args[0])
    ADMINS.add(new_admin)
    await update.message.reply_text(f"✅ Admin added: {new_admin}")

async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return await update.message.reply_text("❌ অনুমতি নেই।")
    if not context.args:
        return await update.message.reply_text("Usage: /removeadmin <user_id>")
    rem_admin = int(context.args[0])
    ADMINS.discard(rem_admin)
    await update.message.reply_text(f"✅ Admin removed: {rem_admin}")

# Channel management
async def add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return await update.message.reply_text("❌ অনুমতি নেই।")
    if len(context.args) < 2:
        return await update.message.reply_text("Usage: /addchannel <channel_id> <name>")
    cid, name = context.args[0], " ".join(context.args[1:])
    CHANNELS[cid] = name
    await update.message.reply_text(f"✅ Channel added: {name} ({cid})")

async def list_channels(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not CHANNELS:
        return await update.message.reply_text("❌ কোনো চ্যানেল এড করা হয়নি।")
    txt = "📡 Channels:\n" + "\n".join([f"{k}: {v}" for k,v in CHANNELS.items()])
    await update.message.reply_text(txt)

# Prefix & Explanation
async def set_prefix(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global QUESTION_PREFIX
    if update.effective_user.id not in ADMINS:
        return await update.message.reply_text("❌ অনুমতি নেই।")
    QUESTION_PREFIX = " ".join(context.args)
    await update.message.reply_text(f"✅ Prefix updated: {QUESTION_PREFIX}")

async def set_explanation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global AUTO_EXPLANATION_LINK
    if update.effective_user.id not in ADMINS:
        return await update.message.reply_text("❌ অনুমতি নেই।")
    AUTO_EXPLANATION_LINK = " ".join(context.args)
    await update.message.reply_text(f"✅ Explanation link updated: {AUTO_EXPLANATION_LINK}")

# Quiz creation
async def create_quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return await update.message.reply_text("❌ অনুমতি নেই।")
    if not update.message.reply_to_message:
        return await update.message.reply_text("❌ কুইজ ফরমেট রিপ্লাই করে /createquiz দিন।")
    raw = update.message.reply_to_message.text
    quizzes, errors = parse_multiple_quizzes(raw)
    if not quizzes:
        return await update.message.reply_text("❌ কোনো কুইজ পাওয়া যায়নি।")
    # Show buttons for channels + inbox
    buttons = [[InlineKeyboardButton("📥 Inbox", callback_data="target_inbox")]]
    for cid, name in CHANNELS.items():
        buttons.append([InlineKeyboardButton(f"📡 {name}", callback_data=f"target_{cid}")])
    context.user_data["pending_quizzes"] = quizzes
    await update.message.reply_text("📤 কোথায় পাঠাতে চান?", reply_markup=InlineKeyboardMarkup(buttons))

async def target_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    quizzes = context.user_data.get("pending_quizzes", [])
    target = q.data.replace("target_", "")
    if target == "inbox":
        chat_id = q.message.chat_id
    else:
        chat_id = target
    ok, bad = 0, 0
    for quiz in quizzes:
        try:
            await context.bot.send_poll(
                chat_id=chat_id,
                question=quiz["question"],
                options=quiz["options"],
                type=Poll.QUIZ,
                correct_option_id=quiz["correct_option_index"],
                explanation=quiz["explanation"],
                is_anonymous=True
            )
            ok += 1
            await asyncio.sleep(0.3)
        except:
            bad += 1
    await q.message.reply_text(f"📤 কুইজ পাঠানো সম্পন্ন!\n✔️ সফল: {ok}\n❌ ব্যর্থ: {bad}")

async def extract_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return await update.message.reply_text("❌ অনুমতি নেই।")

    messages = []

    # যদি reply করা মেসেজ থাকে
    if update.message.reply_to_message:
        messages.append(update.message.reply_to_message)

    # যদি current মেসেজে poll থাকে
    if update.message.poll:
        messages.append(update.message)

    if not messages:
        return await update.message.reply_text("❌ কোনো forwarded quiz poll পাওয়া যায়নি।")

    ok, bad = 0, 0
    for msg in messages:
        if not msg.poll or msg.poll.type != "quiz":
            bad += 1
            continue

        poll = msg.poll
        question = poll.question
        options = [opt.text for opt in poll.options]
        correct_idx = poll.correct_option_id if poll.correct_option_id is not None else 0

        # পুরনো prefix থাকলে সরাও
        if question.startswith("["):
            question = re.sub(r"^\[.*?\]\s*\n*", "", question).strip()

        # নতুন prefix বসাও
        final_question = f"{QUESTION_PREFIX}\n\n{question}"

        # explanation বাদ যাবে, শুধু তোমার link বসবে
        final_explanation = AUTO_EXPLANATION_LINK

        try:
            await context.bot.send_poll(
                chat_id=update.effective_chat.id,
                question=final_question,
                options=options,
                type=Poll.QUIZ,
                correct_option_id=correct_idx,
                explanation=final_explanation,
                is_anonymous=True
            )
            ok += 1
            await asyncio.sleep(0.3)
        except Exception as e:
            bad += 1

    await update.message.reply_text(f"📤 Extracted quizzes!\n✔️ সফল: {ok}\n❌ ব্যর্থ: {bad}")

# forwarded polls জমা রাখার জন্য
FORWARDED_POLLS = []

async def collect_forwarded(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.poll and update.message.poll.type == "quiz":
        FORWARDED_POLLS.append(update.message.poll)

async def extract_batch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMINS:
        return await update.message.reply_text("❌ অনুমতি নেই।")

    if not FORWARDED_POLLS:
        return await update.message.reply_text("❌ কোনো forwarded quiz poll পাওয়া যায়নি।")

    ok, bad = 0, 0
    for poll in FORWARDED_POLLS:
        question = poll.question
        options = [opt.text for opt in poll.options]
        correct_idx = poll.correct_option_id if poll.correct_option_id is not None else 0

        # prefix বসাও
        if question.startswith("["):
            question = re.sub(r"^\[.*?\]\s*\n*", "", question).strip()
        final_question = f"{QUESTION_PREFIX}\n\n{question}"
        final_explanation = AUTO_EXPLANATION_LINK

        try:
            await context.bot.send_poll(
                chat_id=update.effective_chat.id,
                question=final_question,
                options=options,
                type=Poll.QUIZ,
                correct_option_id=correct_idx,
                explanation=final_explanation,
                is_anonymous=True
            )
            ok += 1
            await asyncio.sleep(0.3)
        except:
            bad += 1

    # প্রসেস শেষে list খালি করে দাও
    FORWARDED_POLLS.clear()

    await update.message.reply_text(f"📤 Extracted quizzes!\n✔️ সফল: {ok}\n❌ ব্যর্থ: {bad}")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "<b>📖 Ultron Bot Help</b>\n\n"
        "এই বট দিয়ে আপনি যা করতে পারবেন:\n\n"
        "👑 <b>Admin Management</b>\n"
        "  • /addadmin &lt;user_id&gt; → নতুন অ্যাডমিন যোগ করুন\n"
        "  • /removeadmin &lt;user_id&gt; → অ্যাডমিন রিমুভ করুন\n\n"
        "📡 <b>Channel Management</b>\n"
        "  • /addchannel &lt;channel_id&gt; &lt;name&gt; → নতুন চ্যানেল যোগ করুন\n"
        "  • /listchannels → সব চ্যানেল লিস্ট দেখুন\n\n"
        "✏️ <b>Customization</b>\n"
        "  • /setprefix &lt;text&gt; → প্রশ্নের prefix পরিবর্তন করুন\n"
        "  • /setexplanation &lt;text&gt; → explanation link পরিবর্তন করুন\n\n"
        "🧠 <b>Quiz Creation</b>\n"
        "  • /createquiz → কুইজ ফরমেট রিপ্লাই করে দিন, তারপর target বেছে নিন\n"
        "  • /extractquiz → ফরোয়ার্ড করা quiz রিপ্লাই করে নতুন prefix সহ বানান\n"
        "  • /extractbatch → একসাথে অনেক forwarded quiz প্রসেস করুন\n\n"
        "ℹ️ <b>General</b>\n"
        "  • /start → বট শুরু করুন\n"
        "  • /help → এই সাহায্য মেনু দেখুন\n\n"
        "✅ সব quiz prefix + explanation link সহ পাঠানো হবে\n"
        "✅ target হিসেবে Inbox বা যেকোনো চ্যানেল বেছে নিতে পারবেন\n"
        "✅ errorless advanced bot 🎯"
    )
    await update.message.reply_text(help_text, parse_mode="HTML")



# ----------------- MAIN -----------------
def main():
    token = "8403692899:AAGqYW4zObZD8631cn6XF-_YfJjrFsLLHPc"  # অথবা সরাসরি "YOUR_TOKEN"
    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("addadmin", add_admin))
    app.add_handler(CommandHandler("removeadmin", remove_admin))
    app.add_handler(CommandHandler("addchannel", add_channel))
    app.add_handler(CommandHandler("listchannels", list_channels))
    app.add_handler(CommandHandler("setprefix", set_prefix))
    app.add_handler(CommandHandler("setexplanation", set_explanation))
    app.add_handler(CommandHandler("createquiz", create_quiz))
        # CallbackQueryHandler for target selection
    app.add_handler(CallbackQueryHandler(target_selected, pattern="^target_"))
    app.add_handler(CommandHandler("extractbatch", extract_batch))
    from telegram.ext import MessageHandler, filters

    app.add_handler(MessageHandler(filters.POLL, collect_forwarded))
    app.add_handler(CommandHandler("help", help_command))

    # Run the bot
    app.run_polling()

if __name__ == "__main__":
    main()
