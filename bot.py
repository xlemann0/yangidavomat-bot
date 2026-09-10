import asyncio
import logging
import sys
from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
import aiosqlite

# Konfiguratsiya
TOKEN = "8946349098:AAFQKMlUCyl3pFcYC5EEnzDPxlYKKvHMe_8"
SUPER_ADMIN_ID = 5874144878  # Asosiy admin ID si

router = Router()
DB_NAME = "davomat.db"

# --- BAZA BILAN ISHLASH ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            """CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER PRIMARY KEY
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS students (
                user_id INTEGER PRIMARY KEY,
                full_name TEXT
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS attendance (
                user_id INTEGER,
                date TEXT,
                PRIMARY KEY (user_id, date)
            )"""
        )
        await db.execute(
            """CREATE TABLE IF NOT EXISTS qr_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code_data TEXT UNIQUE
            )"""
        )
        # Super adminni bazaga qo'shish
        await db.execute(
            "INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (SUPER_ADMIN_ID,)
        )
        await db.commit()

async def is_admin(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT 1 FROM admins WHERE user_id = ?", (user_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

async def is_student(user_id: int) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(
            "SELECT 1 FROM students WHERE user_id = ?", (user_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

# --- FSM (Holatlar) ---
class AdminStates(StatesGroup):
    waiting_for_admin_id = State()
    waiting_for_del_admin_id = State()
    waiting_for_student_data = State()
    waiting_for_del_student_id = State()

# --- KLAVIATURALAR ---
def get_main_menu(user_is_admin: bool):
    keyboard = []
    if user_is_admin:
        keyboard.append(
            [InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin_panel")]])]
        )
    keyboard.append(
        [InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📷 QR skaner qilish", callback_data="scan_qr")]])]
    )
    # Oddiyroq inline ro'yxat yasash uchun:
    markup_buttons = []
    if user_is_admin:
        markup_buttons.append([InlineKeyboardButton(text="⚙️ Admin panel", callback_data="admin_panel")])
    markup_buttons.append([InlineKeyboardButton(text="📷 QR skaner qilish", callback_data="scan_qr")])
    return InlineKeyboardMarkup(inline_keyboard=markup_buttons)

def get_admin_menu():
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Admin qo'shish", callback_data="add_admin"),
             InlineKeyboardButton(text="➖ Admin o'chirish", callback_data="del_admin")],
            [InlineKeyboardButton(text="➕ Talaba qo'shish", callback_data="add_student"),
             InlineKeyboardButton(text="➖ Talaba o'chirish", callback_data="del_student")],
            [InlineKeyboardButton(text="🔑 Tayyor QR kodni olish", callback_data="get_qr")],
            [InlineKeyboardButton(text="📊 Statistika", callback_data="stats")],
            [InlineKeyboardButton(text="🔙 Asosiy menyu", callback_data="back_main")],
        ]
    )

# --- START VA ASOSIY MENYU ---
@router.message(CommandStart())
async def cmd_start(message: Message):
    user_id = message.from_user.id
    admin_check = await is_admin(user_id)
    student_check = await is_student(user_id)

    if not admin_check and not student_check and user_id != SUPER_ADMIN_ID:
        await message.answer("❌ Siz bu botda ro'yxatdan o'tmagansiz. Admin sizni qo'shishi kerak.")
        return

    await message.answer(
        "Salom! Yotoqxona davomat botiga xush kelibsiz. Kerakli bo'limni tanlang:",
        reply_markup=get_main_menu(admin_check or user_id == SUPER_ADMIN_ID)
    )

@router.callback_query(F.data == "back_main")
async def back_to_main(callback: CallbackQuery):
    user_id = callback.from_user.id
    admin_check = await is_admin(user_id)
    await callback.message.edit_text(
        "Asosiy menyu:",
        reply_markup=get_main_menu(admin_check or user_id == SUPER_ADMIN_ID)
    )

# --- TALABA: QR SKANER QILISH ---
@router.callback_query(F.data == "scan_qr")
async def scan_qr_prompt(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not await is_student(user_id) and not await is_admin(user_id):
        await callback.answer("Siz talaba ro'yxatida emassiz!", show_alert=True)
        return
    
    await callback.message.edit_text(
        "📷 QR kodni skaner qilish uchun admin taqdim etgan maxsus matnni yoki havolani yuboring (yoki QR kod ma'lumotini kiriting):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="back_main")]])
    )

@router.message(F.text & ~F.text.startswith("/"))
async def process_qr_scan(message: Message, state: FSMContext):
    current_state = await state.get_state()
    if current_state is not None:
        return  # Agar admin panel holatida bo'lsa aralashib ketmasligi uchun

    user_id = message.from_user.id
    if not await is_student(user_id):
        return

    scanned_data = message.text.strip()

    async with aiosqlite.connect(DB_NAME) as db:
        # QR kod bazada bormi tekshiramiz
        async with db.execute("SELECT 1 FROM qr_codes WHERE code_data = ?", (scanned_data,)) as cursor:
            qr_exists = await cursor.fetchone()

        if not qr_exists:
            await message.answer("❌ Noto'g'ri yoki eskirgan QR kod!")
            return

        # Bugungi sana
        import datetime
        today = datetime.date.today().isoformat()

        # Bugun allaqachon belgilaganmi tekshiramiz
        async with db.execute("SELECT 1 FROM attendance WHERE user_id = ? AND date = ?", (user_id, today)) as cursor:
            already_marked = await cursor.fetchone()

        if already_marked:
            await message.answer("⚠️ Diqqat: Davomat kun davomida bir marta qilinadi! Siz allaqachon bugun o'z davomatingizni belgilagansiz.")
            return

        # Davomatni yozamiz
        await db.execute("INSERT INTO attendance (user_id, date) VALUES (?, ?)", (user_id, today))
        await db.commit()
        await message.answer("✅ Davomatingiz muvaffaqiyatli belgilandi!")

# --- ADMIN PANEL ---
@router.callback_query(F.data == "admin_panel")
async def admin_panel(callback: CallbackQuery):
    if not await is_admin(callback.from_user.id):
        await callback.answer("Siz admin emassiz!", show_alert=True)
        return
    await callback.message.edit_text("⚙️ Admin panel:", reply_markup=get_admin_menu())

# 1. Admin qo'shish / o'chirish
@router.callback_query(F.data == "add_admin")
async def add_admin_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != SUPER_ADMIN_ID:
        await callback.answer("Faqat bosh admin yangi admin qo'sha oladi!", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_for_admin_id)
    await callback.message.edit_text("Yangi adminning Telegram ID raqamini kiriting:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_panel")]]))

@router.message(AdminStates.waiting_for_admin_id)
async def save_new_admin(message: Message, state: FSMContext):
    try:
        new_admin_id = int(message.text.strip())
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT OR IGNORE INTO admins (user_id) VALUES (?)", (new_admin_id,))
            await db.commit()
        await message.answer("✅ Yangi admin muvaffaqiyatli qo'shildi!", reply_markup=get_admin_menu())
    except ValueError:
        await message.answer("❌ Noto'g'ri ID format. Faqat raqam kiriting:")
        return
    await state.clear()

@router.callback_query(F.data == "del_admin")
async def del_admin_start(callback: CallbackQuery, state: FSMContext):
    if callback.from_user.id != SUPER_ADMIN_ID:
        await callback.answer("Faqat bosh admin adminlarni o'chira oladi!", show_alert=True)
        return
    await state.set_state(AdminStates.waiting_for_del_admin_id)
    await callback.message.edit_text("O'chirilishi kerak bo'lgan adminning Telegram ID raqamini kiriting:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_panel")]]))

@router.message(AdminStates.waiting_for_del_admin_id)
async def remove_admin(message: Message, state: FSMContext):
    try:
        del_id = int(message.text.strip())
        if del_id == SUPER_ADMIN_ID:
            await message.answer("❌ Bosh adminni o'chirib bo'lmaydi!")
            await state.clear()
            return
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("DELETE FROM admins WHERE user_id = ?", (del_id,))
            await db.commit()
        await message.answer("✅ Admin muvaffaqiyatli o'chirildi!", reply_markup=get_admin_menu())
    except ValueError:
        await message.answer("❌ Noto'g'ri ID format.")
        return
    await state.clear()

# 2. Talaba qo'shish / o'chirish
@router.callback_query(F.data == "add_student")
async def add_student_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_student_data)
    await callback.message.edit_text(
        "Talaba ma'lumotlarini quyidagi formatda kiriting (ID va F.I.O):\n\n`ID Ism Familiya`\nMisol: `123456789 Alisher Valiyev`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_panel")]])
    )

@router.message(AdminStates.waiting_for_student_data)
async def save_student(message: Message, state: FSMContext):
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("❌ Xato format! Qaytadan kiriting (`ID Ism`):")
        return
    try:
        student_id = int(parts[0])
        full_name = parts[1]
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("INSERT OR REPLACE INTO students (user_id, full_name) VALUES (?, ?)", (student_id, full_name))
            await db.commit()
        await message.answer(f"✅ Talaba ({full_name}) qo'shildi!", reply_markup=get_admin_menu())
    except ValueError:
        await message.answer("❌ ID raqam bo'lishi kerak. Qaytadan urinib ko'ring:")
        return
    await state.clear()

@router.callback_query(F.data == "del_student")
async def del_student_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(AdminStates.waiting_for_del_student_id)
    await callback.message.edit_text("O'chirilishi kerak bo'lgan talabaning Telegram ID raqamini kiriting:", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Orqaga", callback_data="admin_panel")]]))

@router.message(AdminStates.waiting_for_del_student_id)
async def remove_student(message: Message, state: FSMContext):
    try:
        student_id = int(message.text.strip())
        async with aiosqlite.connect(DB_NAME) as db:
            await db.execute("DELETE FROM students WHERE user_id = ?", (student_id,))
            await db.commit()
        await message.answer("✅ Talaba bazadan o'chirildi!", reply_markup=get_admin_menu())
    except ValueError:
        await message.answer("❌ Noto'g'ri ID format.")
        return
    await state.clear()

# 3. Tayyor QR kodni olish
@router.callback_query(F.data == "get_qr")
async def get_qr_code(callback: CallbackQuery):
    import uuid
    # Har safar yoki kunlik yangi noyob QR matn yaratamiz
    unique_code = f"dorm_attendance_{uuid.uuid4().hex[:8]}"
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Eskilarini tozalab, yangisini saqlaymiz (yoki saqlab qolamiz)
        await db.execute("DELETE FROM qr_codes")
        await db.execute("INSERT INTO qr_codes (code_data) VALUES (?)", (unique_code,))
        await db.commit()

    await callback.message.answer(
        f"🔑 **Bugungi faol QR kod matni:**\n\n`{unique_code}`\n\n"
        f"Talabalar ushbu matnni nusxalab yoki QR generator orqali skaner qilib davomat qilishlari mumkin.",
        parse_mode="Markdown",
        reply_markup=get_admin_menu()
    )

# 4. Statistika
@router.callback_query(F.data == "stats")
async def show_statistics(callback: CallbackQuery):
    import datetime
    today = datetime.date.today().isoformat()

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM students") as cursor:
            total_students = (await cursor.fetchone())[0]

        async with db.execute("SELECT COUNT(*) FROM attendance WHERE date = ?", (today,)) as cursor:
            present_today = (await cursor.fetchone())[0]

    absent_today = total_students - present_today

    stats_text = (
        f"📊 **Yotoqxona davomat statistikasi ({today}):**\n\n"
        f"👥 Jami ro'yxatdagi talabalar: **{total_students}** ta\n"
        f"✅ Bugun kelganlar (shu yerda): **{present_today}** ta\n"
        f"❌ Kelmaganlar: **{absent_today}** ta"
    )
    await callback.message.edit_text(stats_text, parse_mode="Markdown", reply_markup=get_admin_menu())


# --- ASOSIY ISHGA TUSHIRISH ---
async def main():
    await init_db()
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    print("Bot ishga tushdi...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
