import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Set, Any, Tuple

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message, PollAnswer,
    InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, FSInputFile
)

# ===================== CONFIG =====================

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

OWNER_FILE = os.path.join(DATA_DIR, "owner.json")
GROUPS_FILE = os.path.join(DATA_DIR, "groups.json")
PRESETS_FILE = os.path.join(DATA_DIR, "presets.json")
QUESTIONS_FILE = os.path.join(DATA_DIR, "questions.json")

BOT_TOKEN = "8318888870:AAG_HjP0ucgmq4zDUKsXgEFjj5371LffnZI"  # windows powershell uses $env:BOT_TOKEN
RIGHT_MARK_DEFAULT = 1.0
NEGATIVE_DEFAULT = 0.25
TIME_PER_Q_DEFAULT = 30

# =================== DATA MODELS ===================

@dataclass
class Question:
    text: str
    options: List[str]   # ["a","b","c","d"]
    correct_id: int      # 0..3
    # optional: source info
    source_chat_id: Optional[int] = None
    source_message_id: Optional[int] = None


@dataclass
class UserResult:
    user_id: int
    full_name: str
    username: Optional[str] = None
    correct: int = 0
    wrong: int = 0
    skipped: int = 0
    score: float = 0.0
    answers: Dict[int, str] = field(default_factory=dict)  # q_index -> "C/W/S"


@dataclass
class ExamPreset:
    exam_name: str = "Untitled Exam"
    time_per_q: int = TIME_PER_Q_DEFAULT
    mark_per_q: float = RIGHT_MARK_DEFAULT
    negative_mark: float = NEGATIVE_DEFAULT
    ready: bool = False


@dataclass
class ExamSession:
    chat_id: int
    questions: List[Question]
    exam_name: str
    time_per_question: int
    mark_per_q: float
    negative_mark: float
    active: bool = False
    finished: bool = False

    current_index: int = 0
    poll_id_to_q_idx: Dict[str, int] = field(default_factory=dict)
    results: Dict[int, UserResult] = field(default_factory=dict)
    answered_users_per_q: Dict[int, Set[int]] = field(default_factory=dict)

    admin_id: Optional[int] = None  # who started
    started_at: Optional[datetime] = None


# =================== GLOBAL STATE ===================

router = Router()

EXAMS: Dict[int, ExamSession] = {}                    # chat_id -> session
SCHEDULE_TASKS: Dict[Tuple[int, str], asyncio.Task] = {}  # (chat_id, key) -> task

# For DM announcement draft: admin_id -> (message_chat_id, message_id)
ANNOUNCE_DRAFT: Dict[int, Tuple[int, int]] = {}
# For DM wizard input: user_id -> dict state
WIZARD_STATE: Dict[int, Dict[str, Any]] = {}

# =================== STORAGE HELPERS ===================

def _load_json(path: str, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _save_json(path: str, data) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)

def get_owner_id() -> int:
    data = _load_json(OWNER_FILE, {"owner_id": 0})
    return int(data.get("owner_id", 0) or 0)

def set_owner_id(owner_id: int) -> None:
    _save_json(OWNER_FILE, {"owner_id": int(owner_id)})

def get_bound_groups() -> Dict[str, Dict[str, Any]]:
    # {"-100xxx": {"title": "...", "type": "supergroup", "bound_at": "..."}}
    return _load_json(GROUPS_FILE, {})

def save_bound_groups(groups: Dict[str, Dict[str, Any]]) -> None:
    _save_json(GROUPS_FILE, groups)

def get_presets() -> Dict[str, Dict[str, Any]]:
    return _load_json(PRESETS_FILE, {})

def save_presets(presets: Dict[str, Dict[str, Any]]) -> None:
    _save_json(PRESETS_FILE, presets)

def load_question_bank() -> List[Question]:
    raw = _load_json(QUESTIONS_FILE, [])
    out: List[Question] = []
    for it in raw:
        try:
            out.append(Question(
                text=str(it["text"]),
                options=list(it["options"]),
                correct_id=int(it["correct_id"]),
                source_chat_id=it.get("source_chat_id"),
                source_message_id=it.get("source_message_id"),
            ))
        except Exception:
            continue
    return out

def save_question_bank(qs: List[Question]) -> None:
    raw = []
    for q in qs:
        raw.append({
            "text": q.text,
            "options": q.options,
            "correct_id": q.correct_id,
            "source_chat_id": q.source_chat_id,
            "source_message_id": q.source_message_id,
        })
    _save_json(QUESTIONS_FILE, raw)

# load once
QUESTION_BANK: List[Question] = load_question_bank()

# =================== AUTH HELPERS ===================

async def is_owner(user_id: int) -> bool:
    return user_id == get_owner_id()

async def is_admin_or_owner(bot: Bot, chat_id: int, user_id: int) -> bool:
    if await is_owner(user_id):
        return True
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False

def ensure_owner_claim_hint() -> str:
    return (
        "⚠️ Owner is not set yet.\n"
        "Open bot inbox and send: /claim_owner\n"
    )

# =================== UI HELPERS ===================

def kb_groups(page: int = 0, per_page: int = 6, prefix: str = "grp") -> InlineKeyboardMarkup:
    groups = get_bound_groups()
    items = list(groups.items())
    total = len(items)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    start = page * per_page
    part = items[start:start + per_page]

    rows = []
    for chat_id, meta in part:
        title = meta.get("title", chat_id)
        rows.append([InlineKeyboardButton(text=title, callback_data=f"{prefix}:pick:{chat_id}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"{prefix}:page:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"Page {page+1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"{prefix}:page:{page+1}"))

    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton(text="Close", callback_data=f"{prefix}:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_admin_panel(page: int = 0) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📌 Groups (Start/Setup)", callback_data="panel:groups:0")],
        [InlineKeyboardButton(text="🧾 Question Count", callback_data="panel:qcount")],
        [InlineKeyboardButton(text="🧹 Clear Questions", callback_data="panel:qclear")],
        [InlineKeyboardButton(text="📣 Announcement", callback_data="panel:announce")],
        [InlineKeyboardButton(text="❌ Close", callback_data="panel:close")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_group_actions(chat_id: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✅ Mark READY", callback_data=f"gact:ready:{chat_id}")],
        [InlineKeyboardButton(text="📝 Set Exam Name", callback_data=f"gact:setname:{chat_id}")],
        [InlineKeyboardButton(text="⏱ Set Time/Question", callback_data=f"gact:settime:{chat_id}")],
        [InlineKeyboardButton(text="🎯 Set Marks (+/-)", callback_data=f"gact:setmarks:{chat_id}")],
        [InlineKeyboardButton(text="🗓 Schedule Start", callback_data=f"gact:schedule:{chat_id}")],
        [InlineKeyboardButton(text="🚀 Start Now", callback_data=f"gact:start:{chat_id}")],
        [InlineKeyboardButton(text="↩️ Back", callback_data="panel:groups:0")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_schedule(chat_id: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="5 min", callback_data=f"sch:{chat_id}:5"),
         InlineKeyboardButton(text="10 min", callback_data=f"sch:{chat_id}:10"),
         InlineKeyboardButton(text="30 min", callback_data=f"sch:{chat_id}:30")],
        [InlineKeyboardButton(text="1 hour", callback_data=f"sch:{chat_id}:60")],
        [InlineKeyboardButton(text="Custom (type in DM)", callback_data=f"sch:{chat_id}:custom")],
        [InlineKeyboardButton(text="↩️ Back", callback_data=f"gact:open:{chat_id}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

def preset_for(chat_id: str) -> ExamPreset:
    presets = get_presets()
    d = presets.get(chat_id) or {}
    return ExamPreset(
        exam_name=d.get("exam_name", "Untitled Exam"),
        time_per_q=int(d.get("time_per_q", TIME_PER_Q_DEFAULT)),
        mark_per_q=float(d.get("mark_per_q", RIGHT_MARK_DEFAULT)),
        negative_mark=float(d.get("negative_mark", NEGATIVE_DEFAULT)),
        ready=bool(d.get("ready", False)),
    )

def save_preset(chat_id: str, preset: ExamPreset) -> None:
    presets = get_presets()
    presets[chat_id] = {
        "exam_name": preset.exam_name,
        "time_per_q": preset.time_per_q,
        "mark_per_q": preset.mark_per_q,
        "negative_mark": preset.negative_mark,
        "ready": preset.ready,
    }
    save_presets(presets)

def format_preset(chat_id: str) -> str:
    p = preset_for(chat_id)
    return (
        f"📌 Group: `{chat_id}`\n"
        f"🧪 Exam Name: {p.exam_name}\n"
        f"⏱ Time/Question: {p.time_per_q}s\n"
        f"✅ Mark/Question: +{p.mark_per_q}\n"
        f"❌ Negative: -{p.negative_mark}\n"
        f"🚦 READY: {'YES' if p.ready else 'NO'}\n"
        f"📚 Questions: {len(QUESTION_BANK)}"
    )

# ===================== BASIC COMMANDS =====================

@router.message(Command("start"))
async def cmd_start(message: Message):
    txt = (
        "👋 Advanced Exam Bot (aiogram v3)\n\n"
        "First time setup:\n"
        "1) DM me: /claim_owner\n"
        "2) In your group: /bind_group\n"
        "3) DM: send quiz polls or JSON file to add questions\n"
        "4) DM: /admin_panel → setup group preset → READY\n"
        "5) Group: /start_exam\n\n"
        "DM Commands:\n"
        "• /admin_panel\n"
        "• /question_count\n"
        "• /json_template\n"
        "• /validate_json (reply to a JSON file)\n\n"
        "Group Commands (owner/admin only):\n"
        "• /bind_group\n"
        "• /start_exam\n"
        "• /stop_exam\n"
    )
    await message.answer(txt)

@router.message(Command("whoami"))
async def cmd_whoami(message: Message):
    u = message.from_user
    if not u:
        return
    await message.answer(f"Your ID: `{u.id}`", parse_mode="Markdown")

@router.message(Command("claim_owner"), F.chat.type == ChatType.PRIVATE)
async def cmd_claim_owner(message: Message):
    u = message.from_user
    if not u:
        return
    current = get_owner_id()
    if current and current != u.id:
        await message.answer("❌ Owner already set. Only current owner can change it.")
        return
    set_owner_id(u.id)
    await message.answer("✅ You are now set as OWNER.\nOpen /admin_panel to manage everything.")

@router.message(Command("admin_panel"), F.chat.type == ChatType.PRIVATE)
async def cmd_admin_panel(message: Message):
    u = message.from_user
    if not u:
        return
    if get_owner_id() == 0:
        await message.answer(ensure_owner_claim_hint())
        return
    # allow owner + admins of any bound group (practically owner only needed)
    # we allow only owner for panel to keep simple
    if not await is_owner(u.id):
        await message.answer("❌ Only OWNER can use Admin Panel.")
        return

    await message.answer("⚙️ Admin Panel", reply_markup=kb_admin_panel())

@router.message(Command("question_count"))
async def cmd_question_count(message: Message):
    await message.answer(f"📚 Saved Questions: {len(QUESTION_BANK)}")

@router.message(Command("clear_questions"), F.chat.type == ChatType.PRIVATE)
async def cmd_clear_questions(message: Message):
    u = message.from_user
    if not u or not await is_owner(u.id):
        await message.answer("❌ Only OWNER can clear questions.")
        return
    QUESTION_BANK.clear()
    save_question_bank(QUESTION_BANK)
    await message.answer("🧹 Question bank cleared.")

# ===================== GROUP BIND =====================

@router.message(Command("bind_group"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def cmd_bind_group(message: Message, bot: Bot):
    u = message.from_user
    if not u:
        return

    # must be owner/admin of the group
    if not await is_admin_or_owner(bot, message.chat.id, u.id):
        try:
            await message.delete()
        except Exception:
            pass
        await bot.send_message(
            u.id,
            "⚠️ You are not allowed to use this command.\nContact: @Your_Himus / @Probaho_Robot"
        )
        return

    groups = get_bound_groups()
    cid = str(message.chat.id)
    groups[cid] = {
        "title": message.chat.title or cid,
        "type": message.chat.type,
        "bound_at": datetime.utcnow().isoformat()
    }
    save_bound_groups(groups)

    # ensure preset exists
    p = preset_for(cid)
    save_preset(cid, p)

    try:
        await message.delete()
    except Exception:
        pass

    await bot.send_message(u.id, f"✅ Group bound: {message.chat.title}\nNow open /admin_panel → Groups → Setup → READY")

# ===================== QUESTIONS: QUIZ POLL (DM) =====================

@router.message(F.poll, F.chat.type == ChatType.PRIVATE)
async def handle_quiz_poll_in_dm(message: Message):
    u = message.from_user
    if not u:
        return
    if get_owner_id() == 0:
        await message.answer(ensure_owner_claim_hint())
        return
    if not await is_owner(u.id):
        await message.answer("❌ Only OWNER can add questions.")
        return

    poll = message.poll
    if poll.type != "quiz" or poll.correct_option_id is None:
        await message.answer("❌ Only QUIZ polls with correct answer can be saved.")
        return

    options = [o.text for o in poll.options]
    q = Question(
        text=poll.question,
        options=options,
        correct_id=int(poll.correct_option_id),
        source_chat_id=message.chat.id,
        source_message_id=message.message_id
    )
    QUESTION_BANK.append(q)
    save_question_bank(QUESTION_BANK)

    await message.answer(f"✅ Saved question.\nTotal: {len(QUESTION_BANK)}")

# ===================== QUESTIONS: JSON IMPORT (DM) =====================

def validate_json_questions(obj: Any) -> Tuple[bool, str, List[Question]]:
    """
    Expected format:
    [
      {"question":"...", "options":{"A":"..","B":"..","C":"..","D":".."}, "correct_answer":"A"}
    ]
    """
    if not isinstance(obj, list):
        return False, "Root must be a JSON array (list).", []
    out: List[Question] = []
    for i, it in enumerate(obj, start=1):
        if not isinstance(it, dict):
            return False, f"Item #{i} must be an object.", []
        qtext = it.get("question")
        opts = it.get("options")
        ca = it.get("correct_answer")
        if not isinstance(qtext, str) or not qtext.strip():
            return False, f"Item #{i}: 'question' must be a non-empty string.", []
        if not isinstance(opts, dict):
            return False, f"Item #{i}: 'options' must be an object with keys A/B/C/D.", []
        for k in ["A", "B", "C", "D"]:
            if k not in opts or not isinstance(opts[k], str):
                return False, f"Item #{i}: options must include '{k}' as string.", []
        if ca not in ["A", "B", "C", "D"]:
            return False, f"Item #{i}: 'correct_answer' must be one of A/B/C/D.", []

        options_list = [opts["A"], opts["B"], opts["C"], opts["D"]]
        correct_id = ["A", "B", "C", "D"].index(ca)
        out.append(Question(text=qtext.strip(), options=options_list, correct_id=correct_id))
    return True, f"✅ Valid JSON. Questions: {len(out)}", out

@router.message(F.document, F.chat.type == ChatType.PRIVATE)
async def handle_json_file_dm(message: Message, bot: Bot):
    u = message.from_user
    if not u:
        return
    if get_owner_id() == 0:
        await message.answer(ensure_owner_claim_hint())
        return
    if not await is_owner(u.id):
        await message.answer("❌ Only OWNER can import JSON.")
        return

    doc = message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".json"):
        return  # ignore other files

    file = await bot.get_file(doc.file_id)
    data = await bot.download_file(file.file_path)
    raw = data.read()

    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        await message.answer("❌ Invalid JSON (decode/parse failed).")
        return

    ok, msg, questions = validate_json_questions(obj)
    if not ok:
        await message.answer(f"❌ JSON Validation Failed:\n{msg}")
        return

    QUESTION_BANK.extend(questions)
    save_question_bank(QUESTION_BANK)
    await message.answer(f"✅ Imported successfully.\nNow total questions: {len(QUESTION_BANK)}")

@router.message(Command("json_template"), F.chat.type == ChatType.PRIVATE)
async def cmd_json_template(message: Message):
    tpl = [
        {
            "question": "What is the capital of France?",
            "options": {"A": "Paris", "B": "London", "C": "Rome", "D": "Berlin"},
            "correct_answer": "A",
            "explanation": "Paris is the capital of France."
        }
    ]
    await message.answer("📄 JSON Template (example):\n\n" + json.dumps(tpl, ensure_ascii=False, indent=2))

@router.message(Command("validate_json"), F.chat.type == ChatType.PRIVATE)
async def cmd_validate_json(message: Message, bot: Bot):
    u = message.from_user
    if not u:
        return
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.answer("Reply to a .json file with /validate_json")
        return

    doc = message.reply_to_message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".json"):
        await message.answer("❌ That replied file is not .json")
        return

    file = await bot.get_file(doc.file_id)
    data = await bot.download_file(file.file_path)
    raw = data.read()

    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        await message.answer("❌ Invalid JSON (parse failed).")
        return

    ok, msg, _ = validate_json_questions(obj)
    await message.answer(msg if ok else f"❌ {msg}")

# ===================== ADMIN PANEL CALLBACKS =====================

@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()

@router.callback_query(F.data == "panel:close")
async def cb_panel_close(call: CallbackQuery):
    try:
        await call.message.edit_text("✅ Closed.")
    except Exception:
        pass
    await call.answer()

@router.callback_query(F.data.startswith("panel:groups:"))
async def cb_panel_groups(call: CallbackQuery):
    u = call.from_user
    if not u or not await is_owner(u.id):
        await call.answer("Not allowed", show_alert=True)
        return
    page = int(call.data.split(":")[-1])
    await call.message.edit_text("📌 Select a group:", reply_markup=kb_groups(page, prefix="grp"))
    await call.answer()

@router.callback_query(F.data == "panel:qcount")
async def cb_panel_qcount(call: CallbackQuery):
    u = call.from_user
    if not u or not await is_owner(u.id):
        await call.answer("Not allowed", show_alert=True)
        return
    await call.message.edit_text(f"📚 Saved Questions: {len(QUESTION_BANK)}", reply_markup=kb_admin_panel())
    await call.answer()

@router.callback_query(F.data == "panel:qclear")
async def cb_panel_qclear(call: CallbackQuery):
    u = call.from_user
    if not u or not await is_owner(u.id):
        await call.answer("Not allowed", show_alert=True)
        return
    QUESTION_BANK.clear()
    save_question_bank(QUESTION_BANK)
    await call.message.edit_text("🧹 Question bank cleared.", reply_markup=kb_admin_panel())
    await call.answer()

@router.callback_query(F.data == "panel:announce")
async def cb_panel_announce(call: CallbackQuery):
    u = call.from_user
    if not u or not await is_owner(u.id):
        await call.answer("Not allowed", show_alert=True)
        return
    txt = (
        "📣 Announcement mode:\n\n"
        "1) Send me ANY content (text/photo/video/file).\n"
        "2) Reply to that message with /announce or /announce_pin.\n"
        "3) I will show bound group list → click to post.\n"
    )
    await call.message.edit_text(txt, reply_markup=kb_admin_panel())
    await call.answer()

# ---------- group list pagination ----------
@router.callback_query(F.data.startswith("grp:page:"))
async def cb_grp_page(call: CallbackQuery):
    page = int(call.data.split(":")[-1])
    await call.message.edit_reply_markup(reply_markup=kb_groups(page, prefix="grp"))
    await call.answer()

@router.callback_query(F.data == "grp:close")
async def cb_grp_close(call: CallbackQuery):
    try:
        await call.message.edit_text("✅ Closed.")
    except Exception:
        pass
    await call.answer()

@router.callback_query(F.data.startswith("grp:pick:"))
async def cb_grp_pick(call: CallbackQuery):
    u = call.from_user
    if not u or not await is_owner(u.id):
        await call.answer("Not allowed", show_alert=True)
        return
    chat_id = call.data.split(":")[-1]
    await call.message.edit_text(format_preset(chat_id), reply_markup=kb_group_actions(chat_id), parse_mode="Markdown")
    await call.answer()

@router.callback_query(F.data.startswith("gact:open:"))
async def cb_gact_open(call: CallbackQuery):
    chat_id = call.data.split(":")[-1]
    await call.message.edit_text(format_preset(chat_id), reply_markup=kb_group_actions(chat_id), parse_mode="Markdown")
    await call.answer()

@router.callback_query(F.data.startswith("gact:ready:"))
async def cb_gact_ready(call: CallbackQuery):
    chat_id = call.data.split(":")[-1]
    p = preset_for(chat_id)
    p.ready = True
    save_preset(chat_id, p)
    await call.message.edit_text(format_preset(chat_id), reply_markup=kb_group_actions(chat_id), parse_mode="Markdown")
    await call.answer("READY ✅")

@router.callback_query(F.data.startswith("gact:setname:"))
async def cb_gact_setname(call: CallbackQuery):
    chat_id = call.data.split(":")[-1]
    WIZARD_STATE[call.from_user.id] = {"mode": "setname", "chat_id": chat_id, "panel_msg": (call.message.chat.id, call.message.message_id)}
    await call.answer()
    await call.message.edit_text("📝 Send exam name in this DM (just type).", reply_markup=None)

@router.callback_query(F.data.startswith("gact:settime:"))
async def cb_gact_settime(call: CallbackQuery):
    chat_id = call.data.split(":")[-1]
    WIZARD_STATE[call.from_user.id] = {"mode": "settime", "chat_id": chat_id, "panel_msg": (call.message.chat.id, call.message.message_id)}
    await call.answer()
    await call.message.edit_text("⏱ Send time per question in seconds (e.g., 30).", reply_markup=None)

@router.callback_query(F.data.startswith("gact:setmarks:"))
async def cb_gact_setmarks(call: CallbackQuery):
    chat_id = call.data.split(":")[-1]
    WIZARD_STATE[call.from_user.id] = {"mode": "setmarks", "chat_id": chat_id, "panel_msg": (call.message.chat.id, call.message.message_id)}
    await call.answer()
    await call.message.edit_text("🎯 Send marks format: +mark -negative\nExample: 1 0.25", reply_markup=None)

@router.callback_query(F.data.startswith("gact:schedule:"))
async def cb_gact_schedule(call: CallbackQuery):
    chat_id = call.data.split(":")[-1]
    await call.message.edit_text("🗓 Choose schedule:", reply_markup=kb_schedule(chat_id))
    await call.answer()

@router.callback_query(F.data.startswith("sch:"))
async def cb_schedule_pick(call: CallbackQuery, bot: Bot):
    # sch:<chat_id>:<minutes or custom>
    _, chat_id, mins = call.data.split(":")
    if mins == "custom":
        WIZARD_STATE[call.from_user.id] = {"mode": "schedule_custom", "chat_id": chat_id, "panel_msg": (call.message.chat.id, call.message.message_id)}
        await call.message.edit_text("🗓 Send date-time in format: YYYY-MM-DD HH:MM\nTimezone: Asia/Dhaka", reply_markup=None)
        await call.answer()
        return

    minutes = int(mins)
    run_at = datetime.now() + timedelta(minutes=minutes)
    key = f"scheduled:{chat_id}"
    # cancel previous
    t = SCHEDULE_TASKS.pop((int(chat_id), key), None)
    if t:
        t.cancel()

    async def _job():
        await asyncio.sleep(minutes * 60)
        await start_exam_in_group(bot, int(chat_id), initiator_id=call.from_user.id)

    SCHEDULE_TASKS[(int(chat_id), key)] = asyncio.create_task(_job())
    await call.message.edit_text(f"✅ Scheduled in {minutes} minutes.\n(At: {run_at.strftime('%Y-%m-%d %H:%M')})",
                                reply_markup=kb_group_actions(chat_id))
    await call.answer("Scheduled ✅")

@router.callback_query(F.data.startswith("gact:start:"))
async def cb_gact_start(call: CallbackQuery, bot: Bot):
    chat_id = int(call.data.split(":")[-1])
    await call.answer("Starting...")
    ok, msg = await start_exam_in_group(bot, chat_id, initiator_id=call.from_user.id)
    await call.message.edit_text(msg, reply_markup=kb_admin_panel())

# ===================== WIZARD INPUT HANDLER (DM TEXT) =====================

@router.message(F.text, F.chat.type == ChatType.PRIVATE)
async def handle_wizard_text(message: Message):
    u = message.from_user
    if not u:
        return
    st = WIZARD_STATE.get(u.id)
    if not st:
        return  # normal text ignored

    mode = st.get("mode")
    chat_id = st.get("chat_id")
    panel = st.get("panel_msg")
    if not chat_id or not panel:
        WIZARD_STATE.pop(u.id, None)
        return

    try:
        if mode == "setname":
            name = message.text.strip()
            p = preset_for(chat_id)
            p.exam_name = name
            p.ready = False
            save_preset(chat_id, p)

        elif mode == "settime":
            t = int(message.text.strip())
            if t < 5 or t > 600:
                await message.answer("❌ Time must be between 5 and 600 seconds.")
                return
            p = preset_for(chat_id)
            p.time_per_q = t
            p.ready = False
            save_preset(chat_id, p)

        elif mode == "setmarks":
            parts = message.text.strip().split()
            if len(parts) != 2:
                await message.answer("❌ Format must be: <mark_per_q> <negative>  e.g., 1 0.25")
                return
            mpq = float(parts[0])
            neg = float(parts[1])
            if mpq <= 0 or neg < 0:
                await message.answer("❌ Invalid values.")
                return
            p = preset_for(chat_id)
            p.mark_per_q = mpq
            p.negative_mark = neg
            p.ready = False
            save_preset(chat_id, p)

        elif mode == "schedule_custom":
            # YYYY-MM-DD HH:MM
            dt = datetime.strptime(message.text.strip(), "%Y-%m-%d %H:%M")
            now = datetime.now()
            if dt <= now:
                await message.answer("❌ Time must be in the future.")
                return
            delay = int((dt - now).total_seconds())
            key = f"scheduled:{chat_id}"

            # cancel previous
            t = SCHEDULE_TASKS.pop((int(chat_id), key), None)
            if t:
                t.cancel()

            async def _job():
                await asyncio.sleep(delay)
                await start_exam_in_group(message.bot, int(chat_id), initiator_id=u.id)

            SCHEDULE_TASKS[(int(chat_id), key)] = asyncio.create_task(_job())

        else:
            pass

        # update the same panel message (edit)
        WIZARD_STATE.pop(u.id, None)
        try:
            await message.bot.edit_message_text(
                chat_id=panel[0],
                message_id=panel[1],
                text=format_preset(chat_id),
                reply_markup=kb_group_actions(chat_id),
                parse_mode="Markdown"
            )
        except Exception:
            pass

        await message.answer("✅ Saved. Panel updated.")

    except Exception as e:
        WIZARD_STATE.pop(u.id, None)
        await message.answer(f"❌ Error: {e}")

# ===================== ANNOUNCEMENT (DM REPLY COMMAND) =====================

@router.message(Command("announce"), F.chat.type == ChatType.PRIVATE)
async def cmd_announce(message: Message):
    u = message.from_user
    if not u or not await is_owner(u.id):
        await message.answer("❌ Only OWNER can announce.")
        return
    if not message.reply_to_message:
        await message.answer("Reply to a content message (photo/video/file/text) with /announce")
        return

    # store draft reference
    ANNOUNCE_DRAFT[u.id] = (message.reply_to_message.chat.id, message.reply_to_message.message_id)
    await message.answer("📣 Select a group to post:", reply_markup=kb_groups(0, prefix="ann"))

@router.message(Command("announce_pin"), F.chat.type == ChatType.PRIVATE)
async def cmd_announce_pin(message: Message):
    u = message.from_user
    if not u or not await is_owner(u.id):
        await message.answer("❌ Only OWNER can announce.")
        return
    if not message.reply_to_message:
        await message.answer("Reply to a content message with /announce_pin")
        return
    ANNOUNCE_DRAFT[u.id] = (message.reply_to_message.chat.id, message.reply_to_message.message_id)
    await message.answer("📌 Select a group to post & pin:", reply_markup=kb_groups(0, prefix="annpin"))

@router.callback_query(F.data.startswith("ann:page:"))
async def cb_ann_page(call: CallbackQuery):
    page = int(call.data.split(":")[-1])
    await call.message.edit_reply_markup(reply_markup=kb_groups(page, prefix="ann"))
    await call.answer()

@router.callback_query(F.data.startswith("annpin:page:"))
async def cb_annpin_page(call: CallbackQuery):
    page = int(call.data.split(":")[-1])
    await call.message.edit_reply_markup(reply_markup=kb_groups(page, prefix="annpin"))
    await call.answer()

@router.callback_query(F.data.startswith("ann:pick:"))
async def cb_ann_pick(call: CallbackQuery, bot: Bot):
    u = call.from_user
    draft = ANNOUNCE_DRAFT.get(u.id)
    if not draft:
        await call.answer("No draft found. Reply /announce again.", show_alert=True)
        return
    group_id = int(call.data.split(":")[-1])
    src_chat, src_msg = draft
    try:
        await bot.copy_message(chat_id=group_id, from_chat_id=src_chat, message_id=src_msg)
        await call.message.edit_text("✅ Posted successfully.")
    except Exception as e:
        await call.message.edit_text(f"❌ Failed: {e}")
    await call.answer()

@router.callback_query(F.data.startswith("annpin:pick:"))
async def cb_annpin_pick(call: CallbackQuery, bot: Bot):
    u = call.from_user
    draft = ANNOUNCE_DRAFT.get(u.id)
    if not draft:
        await call.answer("No draft found. Reply /announce_pin again.", show_alert=True)
        return
    group_id = int(call.data.split(":")[-1])
    src_chat, src_msg = draft
    try:
        sent = await bot.copy_message(chat_id=group_id, from_chat_id=src_chat, message_id=src_msg)
        # try pin
        try:
            await bot.pin_chat_message(chat_id=group_id, message_id=sent.message_id)
        except Exception:
            pass
        await call.message.edit_text("✅ Posted (pin attempted).")
    except Exception as e:
        await call.message.edit_text(f"❌ Failed: {e}")
    await call.answer()

@router.callback_query(F.data == "ann:close")
async def cb_ann_close(call: CallbackQuery):
    try:
        await call.message.edit_text("✅ Closed.")
    except Exception:
        pass
    await call.answer()

@router.callback_query(F.data == "annpin:close")
async def cb_annpin_close(call: CallbackQuery):
    try:
        await call.message.edit_text("✅ Closed.")
    except Exception:
        pass
    await call.answer()

# ===================== EXAM COMMANDS (GROUP) =====================

async def start_exam_in_group(bot: Bot, chat_id: int, initiator_id: int) -> Tuple[bool, str]:
    # validate questions
    if not QUESTION_BANK:
        return False, "❌ No questions saved. Add quiz polls or JSON in bot DM."

    cid = str(chat_id)
    p = preset_for(cid)
    if not p.ready:
        return False, "❌ Preset is NOT READY.\nOpen bot DM → /admin_panel → Groups → Setup → READY."

    # block if already active
    if chat_id in EXAMS and EXAMS[chat_id].active:
        return False, "⚠️ Exam already running."

    session = ExamSession(
        chat_id=chat_id,
        questions=list(QUESTION_BANK),
        exam_name=p.exam_name,
        time_per_question=p.time_per_q,
        mark_per_q=p.mark_per_q,
        negative_mark=p.negative_mark,
        active=True,
        admin_id=initiator_id,
        started_at=datetime.utcnow()
    )
    EXAMS[chat_id] = session

    await bot.send_message(
        chat_id,
        f"📝 Exam Started: {session.exam_name}\n\n"
        f"Total Questions: {len(session.questions)}\n"
        f"Time/Question: {session.time_per_question}s\n"
        f"Mark: +{session.mark_per_q}\n"
        f"Negative: -{session.negative_mark}\n\n"
        "⚠️ During exam, messages are locked for non-admins."
    )
    asyncio.create_task(run_exam(session, bot))
    return True, "✅ Exam started."

@router.message(Command("start_exam"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def cmd_start_exam_group(message: Message, bot: Bot, command: CommandObject):
    u = message.from_user
    if not u:
        return
    if not await is_admin_or_owner(bot, message.chat.id, u.id):
        try:
            await message.delete()
        except Exception:
            pass
        await bot.send_message(u.id, "⚠️ Not allowed. Contact: @Your_Himus / @Probaho_Robot")
        return

    # allow override: /start_exam "Name" 30 1 0.25
    args = command.args or ""
    if args.strip():
        # very simple parser: name inside quotes optional
        name = None
        rest = args.strip()
        if rest.startswith('"') and '"' in rest[1:]:
            end = rest[1:].index('"') + 1
            name = rest[1:end]
            rest = rest[end+1:].strip()
        parts = rest.split()
        try:
            t = int(parts[0]) if len(parts) >= 1 else TIME_PER_Q_DEFAULT
            mpq = float(parts[1]) if len(parts) >= 2 else RIGHT_MARK_DEFAULT
            neg = float(parts[2]) if len(parts) >= 3 else NEGATIVE_DEFAULT
        except Exception:
            await message.answer("❌ Invalid args. Example: /start_exam \"English Model Test\" 30 1 0.25")
            return

        cid = str(message.chat.id)
        p = preset_for(cid)
        if name:
            p.exam_name = name
        p.time_per_q = t
        p.mark_per_q = mpq
        p.negative_mark = neg
        p.ready = True
        save_preset(cid, p)

    ok, msg = await start_exam_in_group(bot, message.chat.id, u.id)
    if not ok:
        await message.answer(msg)

@router.message(Command("stop_exam"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def cmd_stop_exam(message: Message, bot: Bot):
    u = message.from_user
    if not u:
        return
    if not await is_admin_or_owner(bot, message.chat.id, u.id):
        try:
            await message.delete()
        except Exception:
            pass
        await bot.send_message(u.id, "⚠️ Not allowed. Contact: @Your_Himus / @Probaho_Robot")
        return

    session = EXAMS.get(message.chat.id)
    if not session or not session.active:
        await message.answer("ℹ️ No active exam.")
        return

    session.active = False
    await message.answer("⛔ Exam stopped. Generating results...")
    await finish_exam(session, bot)

# ===================== GROUP LOCK (EXAM ACTIVE) =====================

@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def group_lock_handler(message: Message, bot: Bot):
    session = EXAMS.get(message.chat.id)
    if not session or not session.active:
        return

    u = message.from_user
    if not u:
        return

    allowed = await is_admin_or_owner(bot, message.chat.id, u.id)
    if allowed:
        return

    # delete any message
    try:
        await message.delete()
    except Exception:
        pass

    # warning DM (do not spam: basic)
    try:
        await bot.send_message(
            u.id,
            "⚠️ Exam is running. Messaging is disabled.\nContact: @Your_Himus / @Probaho_Robot"
        )
    except Exception:
        pass

# ===================== POLL ANSWERS =====================

@router.poll_answer()
async def handle_poll_answer(poll_answer: PollAnswer):
    poll_id = poll_answer.poll_id
    user = poll_answer.user
    chosen_option_ids = poll_answer.option_ids or []

    target_session: Optional[ExamSession] = None
    q_idx: Optional[int] = None

    for s in EXAMS.values():
        if poll_id in s.poll_id_to_q_idx:
            target_session = s
            q_idx = s.poll_id_to_q_idx[poll_id]
            break
    if target_session is None or q_idx is None:
        return

    s = target_session
    answered_set = s.answered_users_per_q.setdefault(q_idx, set())
    if user.id in answered_set:
        return
    answered_set.add(user.id)

    if user.id not in s.results:
        s.results[user.id] = UserResult(
            user_id=user.id,
            full_name=(user.full_name or "Unknown"),
            username=user.username
        )
    r = s.results[user.id]

    if not chosen_option_ids:
        return

    chosen = chosen_option_ids[0]
    correct_id = s.questions[q_idx].correct_id

    if chosen == correct_id:
        r.correct += 1
        r.score += s.mark_per_q
        r.answers[q_idx] = "C"
    else:
        r.wrong += 1
        r.score -= s.negative_mark
        r.answers[q_idx] = "W"

# ===================== EXAM FLOW =====================

async def run_exam(session: ExamSession, bot: Bot):
    try:
        total_q = len(session.questions)
        for idx, q in enumerate(session.questions):
            if not session.active:
                break
            session.current_index = idx

            msg = await bot.send_poll(
                chat_id=session.chat_id,
                question=f"Q{idx+1}/{total_q}: {q.text}",
                options=q.options,
                type="quiz",
                correct_option_id=q.correct_id,
                is_anonymous=False,
                open_period=session.time_per_question,
            )
            session.poll_id_to_q_idx[msg.poll.id] = idx
            session.answered_users_per_q.setdefault(idx, set())

            await asyncio.sleep(session.time_per_question + 2)

        session.active = False
        await finish_exam(session, bot)

    except Exception as e:
        logging.exception("run_exam error: %s", e)
        try:
            await bot.send_message(session.chat_id, "❌ Unexpected error occurred.")
        except Exception:
            pass

async def finish_exam(session: ExamSession, bot: Bot):
    if session.finished:
        return
    session.finished = True
    total_q = len(session.questions)

    # fill skipped
    for r in session.results.values():
        answered = r.correct + r.wrong
        r.skipped = total_q - answered
        for qi in range(total_q):
            if qi not in r.answers:
                r.answers[qi] = "S"

    sorted_results = sorted(session.results.values(), key=lambda x: (-x.score, -x.correct))
    if not sorted_results:
        await bot.send_message(session.chat_id, "ℹ️ No one answered.")
    else:
        top_n = min(10, len(sorted_results))
        lines = [f"🏆 Leaderboard: {session.exam_name}\n"]
        for i, r in enumerate(sorted_results[:top_n], start=1):
            name = r.full_name + (f" (@{r.username})" if r.username else "")
            lines.append(f"{i}. {name} — {r.score:.2f} (C:{r.correct} W:{r.wrong} S:{r.skipped})")
        await bot.send_message(session.chat_id, "\n".join(lines))

        # DM detailed results
        for rank, r in enumerate(sorted_results, start=1):
            msg = (
                f"📌 Exam: {session.exam_name}\n"
                f"Rank: {rank}\n"
                f"Score: {r.score:.2f}\n"
                f"Correct: {r.correct}\nWrong: {r.wrong}\nSkipped: {r.skipped}\n\n"
                "Motivation:\n"
                "Keep going — consistency beats talent when talent doesn’t stay consistent."
            )
            try:
                await bot.send_message(r.user_id, msg)
            except Exception:
                pass

    # clear everything after exam (as you requested)
    EXAMS.pop(session.chat_id, None)
    QUESTION_BANK.clear()
    save_question_bank(QUESTION_BANK)

    # preset becomes not ready (must setup again)
    cid = str(session.chat_id)
    p = preset_for(cid)
    p.ready = False
    save_preset(cid, p)

    await bot.send_message(session.chat_id, "✅ Exam finished. Data cleared. Setup again for next exam.")

# ====================== MAIN ======================

async def main():
    logging.basicConfig(level=logging.INFO)
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Set it in Windows PowerShell: $env:BOT_TOKEN='...'.")
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    logging.info("Bot starting (aiogram v3 polling)...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
