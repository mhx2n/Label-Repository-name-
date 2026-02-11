import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    PollAnswer,
)
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# =========================================================
#  CODE-FIXED CONFIG (NO ENV, NO CLAIM OWNER)
# =========================================================
BOT_TOKEN = "8427023407:AAFagu6UcMGJAI2_jJksQTZ0P_Hj9JQTWrI"  # <-- paste token here (keep quotes)

OWNER_ID = 8389621809  # ✅ fixed owner (INT)

# Put your internal admins here (INT user IDs). Only these can use bot.
INTERNAL_ADMINS: Set[int] = {
    # 111111111,
    # 222222222,
}

CONTACT_OWNER_1 = "@Your_Himus"
CONTACT_OWNER_2 = "@Probaho_Robot"

DEFAULT_TIME_PER_Q = 30
DEFAULT_MARK_PER_Q = 1.0
DEFAULT_NEGATIVE = 0.25

DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)

FONTS_DIR = "fonts"
os.makedirs(FONTS_DIR, exist_ok=True)

# Put fonts inside ./fonts (recommended for perfect Bangla/emoji)
# - fonts/NotoSansBengali-Regular.ttf  (Bangla)
# - fonts/NotoEmoji-Regular.ttf        (Emoji mono; best for PDF)
BENGALI_FONT_FILES = [
    os.path.join(FONTS_DIR, "NotoSansBengali-Regular.ttf"),
    os.path.join(FONTS_DIR, "SolaimanLipi.ttf"),
    os.path.join(FONTS_DIR, "Nikosh.ttf"),
    "C:/Windows/Fonts/vrinda.ttf",
]
EMOJI_FONT_FILES = [
    os.path.join(FONTS_DIR, "NotoEmoji-Regular.ttf"),
    "C:/Windows/Fonts/seguiemj.ttf",
]
LATIN_FONT_FILES = [
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/calibri.ttf",
]

GROUPS_FILE = os.path.join(DATA_DIR, "groups.json")
PRESETS_FILE = os.path.join(DATA_DIR, "presets.json")
QUESTIONS_FILE = os.path.join(DATA_DIR, "questions.json")


# =========================================================
#  MODELS
# =========================================================
@dataclass
class Question:
    text: str
    options: List[str]
    correct_id: int  # 0..3
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
    answers: Dict[int, str] = field(default_factory=dict)  # q_idx -> C/W/S


@dataclass
class ExamPreset:
    exam_name: str = "Untitled Exam"
    time_per_q: int = DEFAULT_TIME_PER_Q
    mark_per_q: float = DEFAULT_MARK_PER_Q
    negative: float = DEFAULT_NEGATIVE
    ready: bool = False


@dataclass
class ExamSession:
    chat_id: int
    exam_name: str
    time_per_q: int
    mark_per_q: float
    negative: float
    questions: List[Question]
    active: bool = False
    finished: bool = False
    current_index: int = 0

    poll_id_to_q_idx: Dict[str, int] = field(default_factory=dict)
    posted_message_ids: Dict[int, int] = field(default_factory=dict)  # q_idx -> msg_id

    intro_message_id: Optional[int] = None
    pin_cleanup_until: Optional[datetime] = None

    results: Dict[int, UserResult] = field(default_factory=dict)
    answered_users_per_q: Dict[int, Set[int]] = field(default_factory=dict)


# =========================================================
#  STATE
# =========================================================
router = Router()
EXAMS: Dict[int, ExamSession] = {}

ANNOUNCE_DRAFT: Dict[int, Tuple[int, int]] = {}
LAST_DM_CONTENT: Dict[int, Tuple[int, int]] = {}
WIZARD: Dict[int, Dict[str, Any]] = {}
SCHEDULE_TASKS: Dict[Tuple[int, str], asyncio.Task] = {}


# =========================================================
#  STORAGE
# =========================================================
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


def load_groups() -> Dict[str, Dict[str, Any]]:
    return _load_json(GROUPS_FILE, {})


def save_groups(d: Dict[str, Dict[str, Any]]) -> None:
    _save_json(GROUPS_FILE, d)


def load_presets() -> Dict[str, Dict[str, Any]]:
    return _load_json(PRESETS_FILE, {})


def save_presets(d: Dict[str, Dict[str, Any]]) -> None:
    _save_json(PRESETS_FILE, d)


def load_questions() -> List[Question]:
    raw = _load_json(QUESTIONS_FILE, [])
    out: List[Question] = []
    for it in raw:
        try:
            out.append(
                Question(
                    text=str(it["text"]),
                    options=list(it["options"]),
                    correct_id=int(it["correct_id"]),
                    source_chat_id=it.get("source_chat_id"),
                    source_message_id=it.get("source_message_id"),
                )
            )
        except Exception:
            continue
    return out


def save_questions(qs: List[Question]) -> None:
    raw = []
    for q in qs:
        raw.append(
            {
                "text": q.text,
                "options": q.options,
                "correct_id": q.correct_id,
                "source_chat_id": q.source_chat_id,
                "source_message_id": q.source_message_id,
            }
        )
    _save_json(QUESTIONS_FILE, raw)


QUESTION_BANK: List[Question] = load_questions()


# =========================================================
#  AUTH
# =========================================================
def is_internal_admin(user_id: int) -> bool:
    return user_id == OWNER_ID or user_id in INTERNAL_ADMINS


def unauthorized_text() -> str:
    return (
        "⚠️ You are not allowed to use this bot.\n"
        f"Contact {CONTACT_OWNER_1} / {CONTACT_OWNER_2}"
    )


async def deny_and_warn(message: Message, bot: Bot, delete_in_group: bool = True):
    try:
        if delete_in_group and message.chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            await message.delete()
    except Exception:
        pass
    try:
        await bot.send_message(message.from_user.id, unauthorized_text())
    except Exception:
        pass


# =========================================================
#  LINKS
# =========================================================
def make_message_link(chat_id: int, msg_id: int, public_username: Optional[str]) -> str:
    if public_username:
        return f"https://t.me/{public_username}/{msg_id}"
    if str(chat_id).startswith("-100"):
        internal = str(chat_id)[4:]
        return f"https://t.me/c/{internal}/{msg_id}"
    return ""


# =========================================================
#  UI helpers
# =========================================================
def kb_groups(page: int = 0, per_page: int = 6, prefix: str = "grp") -> InlineKeyboardMarkup:
    groups = load_groups()
    items = list(groups.items())
    total = len(items)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, pages - 1))
    start = page * per_page
    part = items[start : start + per_page]

    rows = []
    for gid, meta in part:
        title = meta.get("title", gid)
        rows.append([InlineKeyboardButton(text=title, callback_data=f"{prefix}:pick:{gid}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"{prefix}:page:{page-1}"))
    nav.append(InlineKeyboardButton(text=f"Page {page+1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"{prefix}:page:{page+1}"))
    rows.append(nav)

    rows.append([InlineKeyboardButton(text="Close", callback_data=f"{prefix}:close")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_admin_panel() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="📌 Groups", callback_data="panel:groups:0")],
        [InlineKeyboardButton(text="🧾 Question Count", callback_data="panel:qcount")],
        [InlineKeyboardButton(text="🧹 Clear Questions", callback_data="panel:qclear")],
        [InlineKeyboardButton(text="📣 Announcement", callback_data="panel:announce")],
        [InlineKeyboardButton(text="❌ Close", callback_data="panel:close")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_group_actions(gid: str) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text="✅ READY", callback_data=f"g:ready:{gid}")],
        [InlineKeyboardButton(text="📝 Exam Name", callback_data=f"g:setname:{gid}")],
        [InlineKeyboardButton(text="⏱ Time/Q", callback_data=f"g:settime:{gid}")],
        [InlineKeyboardButton(text="🎯 Marks (+/-)", callback_data=f"g:setmarks:{gid}")],
        [InlineKeyboardButton(text="🗓 Schedule", callback_data=f"g:schedule:{gid}")],
        [InlineKeyboardButton(text="🚀 Start Now", callback_data=f"g:start:{gid}")],
        [InlineKeyboardButton(text="↩️ Back", callback_data="panel:groups:0")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def kb_schedule(gid: str) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="5 min", callback_data=f"sch:{gid}:5"),
            InlineKeyboardButton(text="10 min", callback_data=f"sch:{gid}:10"),
            InlineKeyboardButton(text="30 min", callback_data=f"sch:{gid}:30"),
        ],
        [InlineKeyboardButton(text="1 hour", callback_data=f"sch:{gid}:60")],
        [InlineKeyboardButton(text="Custom (type)", callback_data=f"sch:{gid}:custom")],
        [InlineKeyboardButton(text="↩️ Back", callback_data=f"g:open:{gid}")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_preset(gid: str) -> ExamPreset:
    presets = load_presets()
    d = presets.get(gid) or {}
    return ExamPreset(
        exam_name=d.get("exam_name", "Untitled Exam"),
        time_per_q=int(d.get("time_per_q", DEFAULT_TIME_PER_Q)),
        mark_per_q=float(d.get("mark_per_q", DEFAULT_MARK_PER_Q)),
        negative=float(d.get("negative", DEFAULT_NEGATIVE)),
        ready=bool(d.get("ready", False)),
    )


def save_preset(gid: str, p: ExamPreset) -> None:
    presets = load_presets()
    presets[gid] = {
        "exam_name": p.exam_name,
        "time_per_q": p.time_per_q,
        "mark_per_q": p.mark_per_q,
        "negative": p.negative,
        "ready": p.ready,
    }
    save_presets(presets)


def preset_text(gid: str) -> str:
    p = get_preset(gid)
    return (
        f"<b>Group:</b> <code>{gid}</code>\n"
        f"<b>Exam:</b> {p.exam_name}\n"
        f"<b>Time/Q:</b> {p.time_per_q}s\n"
        f"<b>Mark/Q:</b> +{p.mark_per_q}\n"
        f"<b>Negative:</b> -{p.negative}\n"
        f"<b>READY:</b> {'YES' if p.ready else 'NO'}\n"
        f"<b>Questions:</b> {len(QUESTION_BANK)}"
    )


# =========================================================
#  DM: /start + Participant guard
# =========================================================
@router.message(Command("start"))
async def cmd_start(message: Message):
    if message.chat.type != ChatType.PRIVATE:
        return

    if is_internal_admin(message.from_user.id):
        text = (
            "✅ <b>Extreme Exam Bot</b>\n\n"
            "<b>Admin DM Commands</b>\n"
            "• /admin_panel\n"
            "• /question_count\n"
            "• /json_template\n"
            "• /validate_json (reply to JSON file)\n"
            "• /announce /announce_pin (after sending content)\n"
            "• /diagnose\n\n"
            "<b>Group Commands</b> (Owner/Admin only)\n"
            "• /bind_group\n"
            "• /start_exam\n"
            "• /stop_exam\n"
        )
    else:
        text = (
            "✅ <b>Extreme Exam Bot</b>\n\n"
            "This bot is used by the exam owner/admin.\n"
            "You can participate in exams from the group.\n\n"
            "📩 To receive your full exam analysis in DM, keep this chat open (done).\n"
            "⚠️ Other commands are not available for participants.\n\n"
            f"Contact {CONTACT_OWNER_1} / {CONTACT_OWNER_2}"
        )

    await message.answer(text)

def admin_id_list() -> List[int]:
    return [OWNER_ID, *list(INTERNAL_ADMINS)]

@router.message(
    F.chat.type == ChatType.PRIVATE,
    Command(),  # যেকোনো command ধরবে
    ~F.from_user.id.in_(admin_id_list())  # কিন্তু শুধু non-admin হলে
)
async def participant_command_guard(message: Message):
    # participants শুধু /start চালাতে পারবে
    if message.text and message.text.strip().startswith("/start"):
        return
    await message.answer(unauthorized_text())




@router.message(Command("admin_panel"), F.chat.type == ChatType.PRIVATE)
async def cmd_admin_panel(message: Message):
    if not is_internal_admin(message.from_user.id):
        await message.answer(unauthorized_text())
        return
    await message.answer("⚙️ <b>Admin Panel</b>", reply_markup=kb_admin_panel())


@router.message(Command("question_count"))
async def cmd_question_count(message: Message):
    if message.chat.type == ChatType.PRIVATE and not is_internal_admin(message.from_user.id):
        await message.answer(unauthorized_text())
        return
    await message.answer(f"📚 Saved Questions: <b>{len(QUESTION_BANK)}</b>")


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
    if not is_internal_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True)
        return
    page = int(call.data.split(":")[-1])
    await call.message.edit_text("📌 <b>Select a group</b>", reply_markup=kb_groups(page, prefix="grp"))
    await call.answer()


@router.callback_query(F.data == "panel:qcount")
async def cb_panel_qcount(call: CallbackQuery):
    if not is_internal_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True)
        return
    await call.message.edit_text(f"📚 Saved Questions: <b>{len(QUESTION_BANK)}</b>", reply_markup=kb_admin_panel())
    await call.answer()


@router.callback_query(F.data == "panel:qclear")
async def cb_panel_qclear(call: CallbackQuery):
    if not is_internal_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True)
        return
    QUESTION_BANK.clear()
    save_questions(QUESTION_BANK)
    await call.message.edit_text("🧹 Question bank cleared.", reply_markup=kb_admin_panel())
    await call.answer()


@router.callback_query(F.data == "panel:announce")
async def cb_panel_announce(call: CallbackQuery):
    if not is_internal_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True)
        return
    txt = (
        "📣 <b>Announcement</b>\n\n"
        "1) Send me any content (text/photo/video/file).\n"
        "2) Then type /announce or /announce_pin\n"
        "3) Select a bound group → click → posted.\n"
    )
    await call.message.edit_text(txt, reply_markup=kb_admin_panel())
    await call.answer()


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
    if not is_internal_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True)
        return
    gid = call.data.split(":")[-1]
    await call.message.edit_text(preset_text(gid), reply_markup=kb_group_actions(gid))
    await call.answer()


@router.callback_query(F.data.startswith("g:open:"))
async def cb_g_open(call: CallbackQuery):
    gid = call.data.split(":")[-1]
    await call.message.edit_text(preset_text(gid), reply_markup=kb_group_actions(gid))
    await call.answer()


@router.callback_query(F.data.startswith("g:ready:"))
async def cb_g_ready(call: CallbackQuery):
    gid = call.data.split(":")[-1]
    p = get_preset(gid)
    p.ready = True
    save_preset(gid, p)
    await call.message.edit_text(preset_text(gid), reply_markup=kb_group_actions(gid))
    await call.answer("READY ✅")


@router.callback_query(F.data.startswith("g:setname:"))
async def cb_g_setname(call: CallbackQuery):
    gid = call.data.split(":")[-1]
    WIZARD[call.from_user.id] = {"mode": "name", "gid": gid, "panel": (call.message.chat.id, call.message.message_id)}
    await call.message.edit_text("📝 Send <b>Exam Name</b> now (type here).")
    await call.answer()


@router.callback_query(F.data.startswith("g:settime:"))
async def cb_g_settime(call: CallbackQuery):
    gid = call.data.split(":")[-1]
    WIZARD[call.from_user.id] = {"mode": "time", "gid": gid, "panel": (call.message.chat.id, call.message.message_id)}
    await call.message.edit_text("⏱ Send <b>Time per question</b> in seconds (e.g., 30).")
    await call.answer()


@router.callback_query(F.data.startswith("g:setmarks:"))
async def cb_g_setmarks(call: CallbackQuery):
    gid = call.data.split(":")[-1]
    WIZARD[call.from_user.id] = {"mode": "marks", "gid": gid, "panel": (call.message.chat.id, call.message.message_id)}
    await call.message.edit_text("🎯 Send marks as: <code>mark_per_q negative</code>\nExample: <code>1 0.25</code>")
    await call.answer()


@router.callback_query(F.data.startswith("g:schedule:"))
async def cb_g_schedule(call: CallbackQuery):
    gid = call.data.split(":")[-1]
    await call.message.edit_text("🗓 Choose schedule:", reply_markup=kb_schedule(gid))
    await call.answer()


@router.callback_query(F.data.startswith("sch:"))
async def cb_schedule(call: CallbackQuery, bot: Bot):
    _, gid, mins = call.data.split(":")
    if mins == "custom":
        WIZARD[call.from_user.id] = {"mode": "schedule_custom", "gid": gid, "panel": (call.message.chat.id, call.message.message_id)}
        await call.message.edit_text("🗓 Send date-time: <code>YYYY-MM-DD HH:MM</code>\nTimezone: <b>Asia/Dhaka</b>")
        await call.answer()
        return

    minutes = int(mins)
    run_at = datetime.now() + timedelta(minutes=minutes)
    key = "scheduled_start"
    old = SCHEDULE_TASKS.pop((int(gid), key), None)
    if old:
        old.cancel()

    async def _job():
        await asyncio.sleep(minutes * 60)
        await start_exam_in_group(bot, int(gid), initiator_id=call.from_user.id, silent=True)

    SCHEDULE_TASKS[(int(gid), key)] = asyncio.create_task(_job())
    await call.message.edit_text(
        f"✅ Scheduled for <b>{run_at.strftime('%Y-%m-%d %H:%M')}</b>",
        reply_markup=kb_group_actions(gid),
    )
    await call.answer("Scheduled ✅")


@router.callback_query(F.data.startswith("g:start:"))
async def cb_g_start(call: CallbackQuery, bot: Bot):
    gid = int(call.data.split(":")[-1])
    ok, msg = await start_exam_in_group(bot, gid, initiator_id=call.from_user.id, silent=False)
    await call.message.edit_text(msg, reply_markup=kb_admin_panel())
    await call.answer()


@router.message(F.chat.type == ChatType.PRIVATE, F.text)
async def dm_wizard_text(message: Message, bot: Bot):
    if is_internal_admin(message.from_user.id):
        LAST_DM_CONTENT[message.from_user.id] = (message.chat.id, message.message_id)

    st = WIZARD.get(message.from_user.id)
    if not st:
        return

    mode = st["mode"]
    gid = st["gid"]
    panel_chat, panel_msg = st["panel"]

    try:
        p = get_preset(gid)

        if mode == "name":
            p.exam_name = message.text.strip()
            p.ready = False
            save_preset(gid, p)

        elif mode == "time":
            t = int(message.text.strip())
            if t < 5 or t > 600:
                await message.answer("❌ Time must be 5..600 seconds.")
                return
            p.time_per_q = t
            p.ready = False
            save_preset(gid, p)

        elif mode == "marks":
            parts = message.text.strip().split()
            if len(parts) != 2:
                await message.answer("❌ Format: <code>1 0.25</code>")
                return
            p.mark_per_q = float(parts[0])
            p.negative = float(parts[1])
            p.ready = False
            save_preset(gid, p)

        elif mode == "schedule_custom":
            dt = datetime.strptime(message.text.strip(), "%Y-%m-%d %H:%M")
            if dt <= datetime.now():
                await message.answer("❌ Time must be in the future.")
                return
            delay = int((dt - datetime.now()).total_seconds())
            key = "scheduled_start"
            old = SCHEDULE_TASKS.pop((int(gid), key), None)
            if old:
                old.cancel()

            async def _job():
                await asyncio.sleep(delay)
                await start_exam_in_group(bot, int(gid), initiator_id=message.from_user.id, silent=True)

            SCHEDULE_TASKS[(int(gid), key)] = asyncio.create_task(_job())

        WIZARD.pop(message.from_user.id, None)

        try:
            await bot.edit_message_text(
                chat_id=panel_chat,
                message_id=panel_msg,
                text=preset_text(gid),
                reply_markup=kb_group_actions(gid),
            )
        except Exception:
            pass

        await message.answer("✅ Saved & panel updated.")

    except Exception as e:
        WIZARD.pop(message.from_user.id, None)
        await message.answer(f"❌ Error: {e}")


# =========================================================
#  GROUP: bind
# =========================================================
@router.message(Command("bind_group"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def cmd_bind_group(message: Message, bot: Bot):
    if not is_internal_admin(message.from_user.id):
        await deny_and_warn(message, bot, delete_in_group=True)
        return

    groups = load_groups()
    gid = str(message.chat.id)
    chat = await bot.get_chat(message.chat.id)

    groups[gid] = {
        "title": message.chat.title or gid,
        "type": message.chat.type,
        "bound_at": datetime.utcnow().isoformat(),
        "public_username": getattr(chat, "username", None),
    }
    save_groups(groups)

    p = get_preset(gid)
    save_preset(gid, p)

    try:
        await message.delete()
    except Exception:
        pass
    try:
        await bot.send_message(message.from_user.id, f"✅ Group bound: <b>{message.chat.title}</b>")
    except Exception:
        pass


# =========================================================
#  QUESTIONS: DM QUIZ POLL
# =========================================================
@router.message(F.chat.type == ChatType.PRIVATE, F.poll)
async def dm_quiz_poll(message: Message):
    if not is_internal_admin(message.from_user.id):
        return

    poll = message.poll
    if poll.type != "quiz" or poll.correct_option_id is None:
        await message.answer("❌ Only QUIZ polls with correct answer can be saved.")
        return

    q = Question(
        text=poll.question,
        options=[o.text for o in poll.options],
        correct_id=int(poll.correct_option_id),
    )
    QUESTION_BANK.append(q)
    save_questions(QUESTION_BANK)
    await message.answer(f"✅ Question saved. Total: <b>{len(QUESTION_BANK)}</b>")


# =========================================================
#  QUESTIONS: JSON import + validator
# =========================================================
def validate_json_questions(obj: Any) -> Tuple[bool, str, List[Question]]:
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


@router.message(Command("json_template"), F.chat.type == ChatType.PRIVATE)
async def cmd_json_template(message: Message):
    tpl = [
        {
            "question": "বাংলাদেশের রাজধানী কোনটি? 🇧🇩",
            "options": {"A": "ঢাকা", "B": "চট্টগ্রাম", "C": "খুলনা", "D": "রাজশাহী"},
            "correct_answer": "A",
            "explanation": "Optional"
        }
    ]
    await message.answer("<b>JSON Template</b>\n\n<pre>" + json.dumps(tpl, ensure_ascii=False, indent=2) + "</pre>")


@router.message(Command("validate_json"), F.chat.type == ChatType.PRIVATE)
async def cmd_validate_json(message: Message, bot: Bot):
    if not is_internal_admin(message.from_user.id):
        return
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.answer("Reply to a .json file with /validate_json")
        return
    doc = message.reply_to_message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".json"):
        await message.answer("❌ Replied file is not .json")
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


@router.message(F.chat.type == ChatType.PRIVATE, F.document)
async def dm_json_import(message: Message, bot: Bot):
    if not is_internal_admin(message.from_user.id):
        return
    doc = message.document
    if not doc.file_name or not doc.file_name.lower().endswith(".json"):
        return
    file = await bot.get_file(doc.file_id)
    data = await bot.download_file(file.file_path)
    raw = data.read()
    try:
        obj = json.loads(raw.decode("utf-8"))
    except Exception:
        await message.answer("❌ Invalid JSON (parse failed).")
        return
    ok, msg, questions = validate_json_questions(obj)
    if not ok:
        await message.answer(f"❌ JSON Validation Failed:\n{msg}")
        return
    QUESTION_BANK.extend(questions)
    save_questions(QUESTION_BANK)
    await message.answer(f"✅ Imported. Total questions: <b>{len(QUESTION_BANK)}</b>")


# =========================================================
#  ANNOUNCEMENT (DM: content -> /announce -> pick group)
# =========================================================
@router.message(F.chat.type == ChatType.PRIVATE)
async def cache_any_dm_content(message: Message):
    if not is_internal_admin(message.from_user.id):
        return
    if message.text and message.text.startswith("/"):
        return
    LAST_DM_CONTENT[message.from_user.id] = (message.chat.id, message.message_id)


@router.message(Command("announce"), F.chat.type == ChatType.PRIVATE)
async def cmd_announce(message: Message):
    if not is_internal_admin(message.from_user.id):
        await message.answer(unauthorized_text())
        return

    if message.reply_to_message:
        ANNOUNCE_DRAFT[message.from_user.id] = (message.reply_to_message.chat.id, message.reply_to_message.message_id)
    else:
        draft = LAST_DM_CONTENT.get(message.from_user.id)
        if not draft:
            await message.answer("❌ Send content first (text/photo/video/file), then type /announce.")
            return
        ANNOUNCE_DRAFT[message.from_user.id] = draft

    groups = load_groups()
    if not groups:
        await message.answer("❌ No bound groups. Bind a group first: /bind_group (in group).")
        return

    await message.answer("📣 Select a group:", reply_markup=kb_groups(0, prefix="ann"))


@router.message(Command("announce_pin"), F.chat.type == ChatType.PRIVATE)
async def cmd_announce_pin(message: Message):
    if not is_internal_admin(message.from_user.id):
        await message.answer(unauthorized_text())
        return

    if message.reply_to_message:
        ANNOUNCE_DRAFT[message.from_user.id] = (message.reply_to_message.chat.id, message.reply_to_message.message_id)
    else:
        draft = LAST_DM_CONTENT.get(message.from_user.id)
        if not draft:
            await message.answer("❌ Send content first (text/photo/video/file), then type /announce_pin.")
            return
        ANNOUNCE_DRAFT[message.from_user.id] = draft

    groups = load_groups()
    if not groups:
        await message.answer("❌ No bound groups. Bind a group first: /bind_group (in group).")
        return

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


@router.callback_query(F.data.startswith("ann:pick:"))
async def cb_ann_pick(call: CallbackQuery, bot: Bot):
    if not is_internal_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True)
        return
    draft = ANNOUNCE_DRAFT.get(call.from_user.id)
    if not draft:
        await call.answer("No draft. Send content then /announce.", show_alert=True)
        return
    gid = int(call.data.split(":")[-1])
    src_chat, src_msg = draft
    try:
        await bot.copy_message(chat_id=gid, from_chat_id=src_chat, message_id=src_msg)
        await call.message.edit_text("✅ Posted.")
    except Exception as e:
        await call.message.edit_text(f"❌ Failed: {e}")
    await call.answer()


@router.callback_query(F.data.startswith("annpin:pick:"))
async def cb_annpin_pick(call: CallbackQuery, bot: Bot):
    if not is_internal_admin(call.from_user.id):
        await call.answer("Not allowed", show_alert=True)
        return
    draft = ANNOUNCE_DRAFT.get(call.from_user.id)
    if not draft:
        await call.answer("No draft. Send content then /announce_pin.", show_alert=True)
        return
    gid = int(call.data.split(":")[-1])
    src_chat, src_msg = draft
    try:
        sent = await bot.copy_message(chat_id=gid, from_chat_id=src_chat, message_id=src_msg)
        try:
            await bot.pin_chat_message(chat_id=gid, message_id=sent.message_id, disable_notification=True)
        except Exception:
            pass
        await call.message.edit_text("✅ Posted (pin attempted).")
    except Exception as e:
        await call.message.edit_text(f"❌ Failed: {e}")
    await call.answer()


# =========================================================
#  GROUP: delete pin service message (pinned a message)
# =========================================================
@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}), F.pinned_message)
async def cleanup_pin_service(message: Message):
    session = EXAMS.get(message.chat.id)
    if not session or not session.pin_cleanup_until:
        return
    if datetime.utcnow() <= session.pin_cleanup_until:
        try:
            await message.delete()
        except Exception:
            pass


# =========================================================
#  GROUP: exam lock (delete all non-admin messages during exam)
# =========================================================
@router.message(F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def group_lock(message: Message, bot: Bot):
    session = EXAMS.get(message.chat.id)
    if not session or not session.active:
        return
    if not message.from_user:
        return
    if is_internal_admin(message.from_user.id):
        return
    try:
        await message.delete()
    except Exception:
        pass
    try:
        await bot.send_message(message.from_user.id, unauthorized_text())
    except Exception:
        pass


# =========================================================
#  EXAM start/stop
# =========================================================
async def start_exam_in_group(bot: Bot, chat_id: int, initiator_id: int, silent: bool) -> Tuple[bool, str]:
    if not QUESTION_BANK:
        return False, "❌ No questions available. Add QUIZ polls or JSON in bot DM first."

    gid = str(chat_id)
    p = get_preset(gid)
    if not p.ready:
        return False, "❌ Preset is NOT READY. Open bot DM → /admin_panel → Groups → Setup → READY"

    if chat_id in EXAMS and EXAMS[chat_id].active:
        return False, "⚠️ Exam already running."

    session = ExamSession(
        chat_id=chat_id,
        exam_name=p.exam_name,
        time_per_q=p.time_per_q,
        mark_per_q=p.mark_per_q,
        negative=p.negative,
        questions=[Question(q.text, q.options, q.correct_id) for q in QUESTION_BANK],
        active=True,
    )
    EXAMS[chat_id] = session

    async def _start_sequence():
        total_q = len(session.questions)
        intro_text = (
            f"📝 <b>{session.exam_name}</b>\n"
            f"• Questions: <b>{total_q}</b>\n"
            f"• Time/Q: <b>{session.time_per_q}s</b>\n"
            f"• Mark: <b>+{session.mark_per_q}</b>\n"
            f"• Negative: <b>-{session.negative}</b>\n\n"
            f"⏳ Starting in <b>{{sec}}</b> seconds..."
        )
        try:
            intro = await bot.send_message(chat_id, intro_text.format(sec=5))
            session.intro_message_id = intro.message_id

            for sec in range(4, 0, -1):
                await asyncio.sleep(1)
                try:
                    await bot.edit_message_text(chat_id=chat_id, message_id=intro.message_id, text=intro_text.format(sec=sec))
                except Exception:
                    pass

            # Pin intro message
            try:
                session.pin_cleanup_until = datetime.utcnow() + timedelta(seconds=15)
                await bot.pin_chat_message(chat_id=chat_id, message_id=intro.message_id, disable_notification=True)
            except Exception:
                pass

            await asyncio.sleep(1)
        except Exception:
            pass

        asyncio.create_task(run_exam(bot, session))

    asyncio.create_task(_start_sequence())
    return True, "✅ Exam started."


@router.message(Command("start_exam"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def cmd_start_exam(message: Message, bot: Bot, command: CommandObject):
    if not is_internal_admin(message.from_user.id):
        await deny_and_warn(message, bot, delete_in_group=True)
        return
    try:
        await message.delete()
    except Exception:
        pass

    # Optional override: /start_exam "Name" 30 1 0.25
    args = (command.args or "").strip()
    if args:
        name = None
        rest = args
        if rest.startswith('"') and '"' in rest[1:]:
            end = rest[1:].index('"') + 1
            name = rest[1:end]
            rest = rest[end + 1 :].strip()
        parts = rest.split()
        try:
            t = int(parts[0]) if len(parts) >= 1 else DEFAULT_TIME_PER_Q
            mpq = float(parts[1]) if len(parts) >= 2 else DEFAULT_MARK_PER_Q
            neg = float(parts[2]) if len(parts) >= 3 else DEFAULT_NEGATIVE
        except Exception:
            await bot.send_message(message.from_user.id, "❌ Invalid args. Example: /start_exam \"English Model Test\" 30 1 0.25")
            return

        gid = str(message.chat.id)
        p = get_preset(gid)
        if name:
            p.exam_name = name
        p.time_per_q = t
        p.mark_per_q = mpq
        p.negative = neg
        p.ready = True
        save_preset(gid, p)

    ok, _ = await start_exam_in_group(bot, message.chat.id, initiator_id=message.from_user.id, silent=False)
    if not ok:
        await bot.send_message(message.from_user.id, "❌ Failed to start exam. Make sure preset is READY and questions exist.")


@router.message(Command("stop_exam"), F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP}))
async def cmd_stop_exam(message: Message, bot: Bot):
    if not is_internal_admin(message.from_user.id):
        await deny_and_warn(message, bot, delete_in_group=True)
        return
    try:
        await message.delete()
    except Exception:
        pass

    session = EXAMS.get(message.chat.id)
    if not session or not session.active:
        return
    session.active = False
    await finish_exam(bot, session, stopped=True)


# =========================================================
#  Poll answers
# =========================================================
@router.poll_answer()
async def poll_answer(poll_answer: PollAnswer):
    poll_id = poll_answer.poll_id
    user = poll_answer.user
    chosen_option_ids = poll_answer.option_ids or []

    target: Optional[ExamSession] = None
    q_idx: Optional[int] = None
    for s in EXAMS.values():
        if poll_id in s.poll_id_to_q_idx:
            target = s
            q_idx = s.poll_id_to_q_idx[poll_id]
            break
    if target is None or q_idx is None:
        return

    s = target
    answered = s.answered_users_per_q.setdefault(q_idx, set())
    if user.id in answered:
        return
    answered.add(user.id)

    if user.id not in s.results:
        s.results[user.id] = UserResult(
            user_id=user.id,
            full_name=user.full_name or "Unknown",
            username=user.username,
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
        r.score -= s.negative
        r.answers[q_idx] = "W"


# =========================================================
#  Exam engine
# =========================================================
async def run_exam(bot: Bot, session: ExamSession):
    total = len(session.questions)
    for idx, q in enumerate(session.questions):
        if not session.active:
            break
        session.current_index = idx

        try:
            msg = await bot.send_poll(
                chat_id=session.chat_id,
                question=f"Q{idx+1}/{total}: {q.text}",
                options=q.options,
                type="quiz",
                correct_option_id=q.correct_id,
                is_anonymous=False,
                open_period=session.time_per_q,
            )
            session.poll_id_to_q_idx[msg.poll.id] = idx
            session.posted_message_ids[idx] = msg.message_id
            session.answered_users_per_q.setdefault(idx, set())
        except Exception as e:
            logging.exception("send_poll failed: %s", e)
            session.active = False
            break

        await asyncio.sleep(session.time_per_q + 2)

    session.active = False
    await finish_exam(bot, session, stopped=False)


# =========================================================
#  Fonts + mixed rendering (Image)
# =========================================================
def _pick_font_path(candidates: List[str]) -> Optional[str]:
    for p in candidates:
        try:
            if p and os.path.exists(p):
                return p
        except Exception:
            continue
    return None


def _load_font(size: int, kind: str = "latin") -> ImageFont.FreeTypeFont:
    if kind == "bengali":
        path = _pick_font_path(BENGALI_FONT_FILES) or _pick_font_path(LATIN_FONT_FILES)
    elif kind == "emoji":
        path = _pick_font_path(EMOJI_FONT_FILES) or _pick_font_path(LATIN_FONT_FILES)
    else:
        path = _pick_font_path(LATIN_FONT_FILES) or _pick_font_path(BENGALI_FONT_FILES) or _pick_font_path(EMOJI_FONT_FILES)

    if path:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            pass
    return ImageFont.load_default()


def _is_emoji(ch: str) -> bool:
    o = ord(ch)
    return (
        0x1F300 <= o <= 0x1FAFF
        or 0x2600 <= o <= 0x27BF
        or 0xFE00 <= o <= 0xFE0F
        or 0x1F1E6 <= o <= 0x1F1FF
    )


def draw_text_mixed(draw: ImageDraw.ImageDraw, xy: Tuple[int, int], text: str,
                    font_text: ImageFont.FreeTypeFont, font_emoji: ImageFont.FreeTypeFont, fill) -> None:
    x, y = xy
    for ch in text:
        font = font_emoji if _is_emoji(ch) else font_text
        draw.text((x, y), ch, font=font, fill=fill)
        try:
            w = draw.textlength(ch, font=font)
        except Exception:
            w = font.getlength(ch) if hasattr(font, "getlength") else font.getsize(ch)[0]
        x += int(w)


def generate_leaderboard_image(exam_name: str, rows: List[Tuple[int, str, float, int, int, int]], out_path: str) -> None:
    W, H = 1080, 1350
    bg = Image.new("RGB", (W, H), (10, 12, 18))
    draw = ImageDraw.Draw(bg)

    title_font = _load_font(54, "bengali")
    sub_font = _load_font(28, "latin")
    row_font = _load_font(34, "bengali")

    emoji_title = _load_font(54, "emoji")
    emoji_row = _load_font(34, "emoji")
    emoji_sub = _load_font(28, "emoji")

    title = f"LEADERBOARD — {exam_name}"
    tx, ty = 60, 50

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    draw_text_mixed(gdraw, (tx, ty), title, title_font, emoji_title, (120, 200, 255, 220))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=8))
    bg = Image.alpha_composite(bg.convert("RGBA"), glow).convert("RGB")
    draw = ImageDraw.Draw(bg)
    draw_text_mixed(draw, (tx, ty), title, title_font, emoji_title, (230, 245, 255))

    draw_text_mixed(draw, (60, 120), "Top performers (score includes negative marking)", sub_font, emoji_sub, (170, 180, 200))

    y = 190
    draw.rounded_rectangle((50, y, W - 50, y + 70), radius=18, fill=(18, 22, 32))
    draw_text_mixed(draw, (70, y + 18), "Rank", sub_font, emoji_sub, (220, 225, 240))
    draw_text_mixed(draw, (210, y + 18), "Name", sub_font, emoji_sub, (220, 225, 240))
    draw_text_mixed(draw, (840, y + 18), "Score", sub_font, emoji_sub, (220, 225, 240))

    y += 90
    card_h = 90
    for rank, name, score, c, w, s in rows:
        draw.rounded_rectangle((50, y, W - 50, y + card_h), radius=18, fill=(14, 18, 28))
        if rank <= 3:
            g = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            gd = ImageDraw.Draw(g)
            gd.rounded_rectangle((50, y, W - 50, y + card_h), radius=18, fill=(80, 180, 255, 40))
            g = g.filter(ImageFilter.GaussianBlur(radius=6))
            bg = Image.alpha_composite(bg.convert("RGBA"), g).convert("RGB")
            draw = ImageDraw.Draw(bg)

        draw_text_mixed(draw, (75, y + 25), f"{rank}", row_font, emoji_row, (230, 235, 250))

        n = name
        if len(n) > 28:
            n = n[:27] + "…"
        draw_text_mixed(draw, (210, y + 25), n, row_font, emoji_row, (230, 235, 250))

        draw.text((840, y + 25), f"{score:.2f}", font=row_font, fill=(210, 255, 220))
        draw.text((210, y + 60), f"C:{c}  W:{w}  S:{s}", font=_load_font(22, "latin"), fill=(150, 160, 185))

        y += card_h + 18
        if y > H - 140:
            break

    draw.text((60, H - 60), "Generated by Extreme Exam Bot", font=_load_font(20, "latin"), fill=(120, 130, 150))
    bg.save(out_path, format="PNG")


# =========================================================
#  PDF (Bangla/Emoji best effort + nicer design)
# =========================================================
def _register_pdf_fonts() -> Tuple[str, str]:
    text_font_name = "Helvetica"
    emoji_font_name = "Helvetica"

    bn_path = _pick_font_path(BENGALI_FONT_FILES)
    if bn_path:
        try:
            pdfmetrics.registerFont(TTFont("BN", bn_path))
            text_font_name = "BN"
        except Exception:
            pass

    em_path = _pick_font_path(EMOJI_FONT_FILES)
    if em_path:
        try:
            pdfmetrics.registerFont(TTFont("EMOJI", em_path))
            emoji_font_name = "EMOJI"
        except Exception:
            pass

    return text_font_name, emoji_font_name


def pdf_draw_mixed(c: canvas.Canvas, x: float, y: float, text: str,
                   font_text: str, font_emoji: str, size: int, color=colors.black) -> None:
    c.setFillColor(color)
    cx = x
    for ch in text:
        fn = font_emoji if _is_emoji(ch) else font_text
        try:
            c.setFont(fn, size)
            c.drawString(cx, y, ch)
            # accurate width if possible
            try:
                w = pdfmetrics.stringWidth(ch, fn, size)
            except Exception:
                w = size * 0.55
        except Exception:
            c.setFont("Helvetica", size)
            c.drawString(cx, y, ch)
            w = size * 0.55
        cx += w


def generate_pdf_report(exam_name: str, results: List[UserResult], total_q: int,
                        mark_per_q: float, negative: float, out_path: str) -> None:
    font_text, font_emoji = _register_pdf_fonts()

    c = canvas.Canvas(out_path, pagesize=A4)
    w, h = A4
    margin = 1.6 * cm

    # Header
    c.setFillColor(colors.HexColor("#0B1020"))
    c.rect(0, h - 4.2 * cm, w, 4.2 * cm, stroke=0, fill=1)
    pdf_draw_mixed(c, margin, h - 2.2 * cm, f"Exam Report — {exam_name}", font_text, font_emoji, 18, color=colors.white)

    c.setStrokeColor(colors.HexColor("#22305A"))
    c.setLineWidth(1)
    c.roundRect(margin, h - 3.5 * cm, w - 2 * margin, 1.0 * cm, 10, stroke=1, fill=0)

    info = (
        f"Total Questions: {total_q}   |   Mark/Q: +{mark_per_q}   |   Negative: -{negative}   |   "
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    pdf_draw_mixed(c, margin + 0.4 * cm, h - 3.15 * cm, info, font_text, font_emoji, 10, color=colors.HexColor("#C7D2FE"))

    # Table container
    table_top = h - 5.0 * cm
    c.setFillColor(colors.HexColor("#F8FAFC"))
    c.roundRect(margin, 1.6 * cm, w - 2 * margin, table_top - 1.6 * cm, 12, stroke=0, fill=1)

    # Header row
    header_h = 0.9 * cm
    c.setFillColor(colors.HexColor("#111827"))
    c.roundRect(margin, table_top - header_h, w - 2 * margin, header_h, 12, stroke=0, fill=1)

    cols = [margin + 0.4 * cm, margin + 2.0 * cm, margin + 12.2 * cm, margin + 14.4 * cm, margin + 16.0 * cm, margin + 17.6 * cm]
    headers = ["#", "Name", "Score", "C", "W", "S"]
    for i, head in enumerate(headers):
        pdf_draw_mixed(c, cols[i], table_top - 0.62 * cm, head, font_text, font_emoji, 10, color=colors.white)

    y = table_top - header_h - 0.55 * cm
    row_h = 0.62 * cm

    for rank, r in enumerate(results, start=1):
        if y < 2.3 * cm:
            c.showPage()
            c.setFillColor(colors.HexColor("#0B1020"))
            c.rect(0, h - 2.3 * cm, w, 2.3 * cm, stroke=0, fill=1)
            pdf_draw_mixed(c, margin, h - 1.5 * cm, f"Exam Report — {exam_name} (cont.)", font_text, font_emoji, 14, color=colors.white)

            table_top = h - 3.0 * cm
            c.setFillColor(colors.HexColor("#F8FAFC"))
            c.roundRect(margin, 1.6 * cm, w - 2 * margin, table_top - 1.6 * cm, 12, stroke=0, fill=1)
            c.setFillColor(colors.HexColor("#111827"))
            c.roundRect(margin, table_top - header_h, w - 2 * margin, header_h, 12, stroke=0, fill=1)
            for i, head in enumerate(headers):
                pdf_draw_mixed(c, cols[i], table_top - 0.62 * cm, head, font_text, font_emoji, 10, color=colors.white)
            y = table_top - header_h - 0.55 * cm

        if rank % 2 == 0:
            c.setFillColor(colors.HexColor("#EEF2FF"))
            c.roundRect(margin + 0.15 * cm, y - 0.1 * cm, w - 2 * margin - 0.3 * cm, row_h + 0.25 * cm, 8, stroke=0, fill=1)

        name = r.full_name + (f" (@{r.username})" if r.username else "")
        if len(name) > 60:
            name = name[:59] + "…"

        pdf_draw_mixed(c, cols[0], y, str(rank), font_text, font_emoji, 10, color=colors.HexColor("#0F172A"))
        pdf_draw_mixed(c, cols[1], y, name, font_text, font_emoji, 10, color=colors.HexColor("#0F172A"))
        pdf_draw_mixed(c, cols[2], y, f"{r.score:.2f}", font_text, font_emoji, 10, color=colors.HexColor("#065F46"))
        pdf_draw_mixed(c, cols[3], y, str(r.correct), font_text, font_emoji, 10, color=colors.HexColor("#0F172A"))
        pdf_draw_mixed(c, cols[4], y, str(r.wrong), font_text, font_emoji, 10, color=colors.HexColor("#B91C1C"))
        pdf_draw_mixed(c, cols[5], y, str(r.skipped), font_text, font_emoji, 10, color=colors.HexColor("#0F172A"))

        y -= row_h

    c.save()


# =========================================================
#  Finish exam: image + DM analysis + PDF
# =========================================================
def motivational_speech() -> str:
    return (
        "Keep going.\n"
        "Small progress every day becomes massive results over time.\n"
        "Focus, practice, and improve—your next attempt will be better."
    )


async def finish_exam(bot: Bot, session: ExamSession, stopped: bool):
    if session.finished:
        return
    session.finished = True

    total_q = len(session.questions)

    for r in session.results.values():
        answered = r.correct + r.wrong
        r.skipped = total_q - answered
        for qi in range(total_q):
            if qi not in r.answers:
                r.answers[qi] = "S"

    sorted_results = sorted(session.results.values(), key=lambda x: (-x.score, -x.correct))

    top_n = min(10, len(sorted_results))
    rows = []
    for i in range(top_n):
        r = sorted_results[i]
        name = r.full_name + (f" (@{r.username})" if r.username else "")
        rows.append((i + 1, name, r.score, r.correct, r.wrong, r.skipped))

    img_path = os.path.join(DATA_DIR, f"leaderboard_{session.chat_id}.png")
    if rows:
        generate_leaderboard_image(session.exam_name, rows, img_path)
    else:
        generate_leaderboard_image(session.exam_name, [(1, "No participants", 0.0, 0, 0, total_q)], img_path)

    # Group: only image
    try:
        from aiogram.types import FSInputFile
        await bot.send_photo(session.chat_id, FSInputFile(img_path), caption=f"🏆 {session.exam_name} — Top {top_n if rows else 0}")
    except Exception:
        pass

    # DM analysis
    groups = load_groups()
    meta = groups.get(str(session.chat_id), {})
    public_username = meta.get("public_username")

    for rank, r in enumerate(sorted_results, start=1):
        right_links, wrong_links, skip_links = [], [], []

        for qi in range(total_q):
            status = r.answers.get(qi, "S")
            msg_id = session.posted_message_ids.get(qi)
            link = make_message_link(session.chat_id, msg_id, public_username) if msg_id else ""
            item = f"{qi+1}" + (f" — {link}" if link else "")
            if status == "C":
                right_links.append(item)
            elif status == "W":
                wrong_links.append(item)
            else:
                skip_links.append(item)

        dm = (
            f"✅ <b>Exam:</b> {session.exam_name}\n"
            f"🏅 <b>Rank:</b> {rank}\n\n"
            f"📌 <b>Total Questions:</b> {total_q}\n"
            f"✅ <b>Correct:</b> {r.correct}\n"
            f"❌ <b>Wrong:</b> {r.wrong}\n"
            f"⏭️ <b>Skipped:</b> {r.skipped}\n\n"
            f"🎯 <b>Mark/Q:</b> +{session.mark_per_q}\n"
            f"⚠️ <b>Negative:</b> -{session.negative}\n"
            f"🏁 <b>Total Score:</b> <b>{r.score:.2f}</b>\n\n"
            f"💬 <b>Motivation</b>\n{motivational_speech()}\n\n"
            f"✅ <b>Correct (serial + link)</b>\n" + ("\n".join(right_links) if right_links else "None") + "\n\n"
            f"❌ <b>Wrong (serial + link)</b>\n" + ("\n".join(wrong_links) if wrong_links else "None") + "\n\n"
            f"⏭️ <b>Skipped (serial + link)</b>\n" + ("\n".join(skip_links) if skip_links else "None")
        )
        try:
            await bot.send_message(r.user_id, dm, disable_web_page_preview=True)
        except Exception:
            pass

    # PDF to owner/admins
    pdf_path = os.path.join(DATA_DIR, f"report_{session.chat_id}.pdf")
    generate_pdf_report(session.exam_name, sorted_results, total_q, session.mark_per_q, session.negative, pdf_path)

    recipients = {OWNER_ID} | set(INTERNAL_ADMINS)
    from aiogram.types import FSInputFile
    for uid in recipients:
        try:
            await bot.send_document(uid, FSInputFile(pdf_path), caption=f"📄 Exam Report — {session.exam_name}")
        except Exception:
            pass

    # Auto clear after finish (your rule)
    EXAMS.pop(session.chat_id, None)
    QUESTION_BANK.clear()
    save_questions(QUESTION_BANK)

    gid = str(session.chat_id)
    p = get_preset(gid)
    p.ready = False
    save_preset(gid, p)


# =========================================================
#  Diagnostics (DM)
# =========================================================
@router.message(Command("diagnose"), F.chat.type == ChatType.PRIVATE)
async def cmd_diagnose(message: Message):
    if not is_internal_admin(message.from_user.id):
        await message.answer(unauthorized_text())
        return
    groups = load_groups()
    presets = load_presets()
    txt = (
        f"<b>Diagnostics</b>\n\n"
        f"Owner ID: <code>{OWNER_ID}</code>\n"
        f"Internal Admins: <b>{len(INTERNAL_ADMINS)}</b>\n"
        f"Bound Groups: <b>{len(groups)}</b>\n"
        f"Presets: <b>{len(presets)}</b>\n"
        f"Questions: <b>{len(QUESTION_BANK)}</b>\n"
        f"Active Exams: <b>{sum(1 for s in EXAMS.values() if s.active)}</b>\n\n"
        f"<b>Announcement</b>: send content → /announce → pick group\n"
        f"<b>Exam</b>: /admin_panel → Groups → Setup → READY → group /start_exam\n"
        f"<b>Fonts</b>: put Bangla/Emoji TTF into <code>./fonts</code>\n"
    )
    await message.answer(txt)


# =========================================================
#  MAIN
# =========================================================
async def main():
    logging.basicConfig(level=logging.INFO)

    if not BOT_TOKEN or BOT_TOKEN == "PUT_YOUR_BOT_TOKEN_HERE":
        raise RuntimeError("Set BOT_TOKEN at top of file (hardcoded).")

    bot = Bot(
        token=BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp = Dispatcher()
    dp.include_router(router)

    logging.info("Extreme Exam Bot starting...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())


