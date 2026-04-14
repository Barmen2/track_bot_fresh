import asyncio
import os
import re
from datetime import datetime, timedelta, timezone
from io import BytesIO
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, BufferedInputFile
from supabase import create_client, Client
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment, PatternFill
import aiohttp
from aiohttp import web

# === Конфигурация ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 6810564564))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# Тарифы
CARGO_RATE = float(os.getenv("CARGO_RATE", 12.0))
DELIVERY_MOSCOW_MINSK = float(os.getenv("DELIVERY_MOSCOW_MINSK", 1.6))
DELIVERY_MINSK_LIDA = float(os.getenv("DELIVERY_MINSK_LIDA", 0.8))
TRANSFER_FEE = float(os.getenv("TRANSFER_FEE", 10.0))
EXTRA_RATE = float(os.getenv("EXTRA_RATE", 0.0))
FIXED_COST = float(os.getenv("FIXED_COST", 0.0))

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# === Клавиатуры ===
# Базовая клавиатура для всех пользователей
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Новый трек")],
        [KeyboardButton(text="📋 Мои треки"), KeyboardButton(text="📊 Мои треки (Excel)")],
        [KeyboardButton(text="Редактировать профиль"), KeyboardButton(text="Удалить трек")],
        [KeyboardButton(text="💱 Конвертер валют"), KeyboardButton(text="📦 Калькулятор доставки")],
        [KeyboardButton(text="✅ Завершить и отправить")]
    ],
    resize_keyboard=True
)

# Расширенная клавиатура для владельца
owner_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Новый трек")],
        [KeyboardButton(text="📋 Мои треки"), KeyboardButton(text="📊 Мои треки (Excel)")],
        [KeyboardButton(text="Редактировать профиль"), KeyboardButton(text="Удалить трек")],
        [KeyboardButton(text="💱 Конвертер валют"), KeyboardButton(text="📦 Калькулятор доставки")],
        [KeyboardButton(text="✅ Завершить и отправить")],
        [KeyboardButton(text="📢 Сделать рассылку")]
    ],
    resize_keyboard=True
)

cancel_keyboard = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="Отмена")]],
    resize_keyboard=True
)
group_keyboard = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="📦 Загрузить трек", url="https://t.me/little_Bro_track_bot")]]
)

# === FSM ===
class ProfileForm(StatesGroup):
    waiting_for_fullname = State()
    waiting_for_phone = State()

class TrackForm(StatesGroup):
    waiting_for_track = State()
    waiting_for_product = State()
    waiting_for_price = State()
    waiting_for_quantity_type = State()
    waiting_for_quantity = State()

class DeleteTrackForm(StatesGroup):
    waiting_for_track_id = State()

class CurrencyForm(StatesGroup):
    waiting_for_amount = State()

class CalcForm(StatesGroup):
    waiting_for_city = State()
    waiting_for_weight = State()

class BroadcastForm(StatesGroup):
    waiting_for_start_date = State()
    waiting_for_end_date = State()
    waiting_for_text = State()

# === Supabase функции (московское время) ===
def get_msk_time():
    return datetime.now(timezone.utc) + timedelta(hours=3)

def get_user_profile(user_id):
    res = supabase.table("users").select("full_name, phone").eq("user_id", user_id).execute()
    if res.data:
        return res.data[0]["full_name"], res.data[0]["phone"]
    return None

def save_user_profile(user_id, username, full_name, phone):
    supabase.table("users").upsert({
        "user_id": user_id,
        "username": username,
        "full_name": full_name,
        "phone": phone,
        "created_at": get_msk_time().isoformat()
    }).execute()

def add_track(user_id, track, product, price, qty_type, quantity):
    supabase.table("tracks").insert({
        "user_id": user_id,
        "track_number": track,
        "product_name": product,
        "price": price,
        "quantity": quantity,
        "quantity_type": qty_type,
        "created_at": get_msk_time().isoformat()
    }).execute()

def get_user_tracks(user_id):
    res = supabase.table("tracks").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
    return res.data

def delete_track(track_id, user_id):
    supabase.table("tracks").delete().eq("id", track_id).eq("user_id", user_id).execute()

def get_total_sum(user_id):
    tracks = get_user_tracks(user_id)
    return round(sum(t["price"] * t["quantity"] for t in tracks), 2)

# === Конвертер валют ===
async def get_exchange_rates():
    apis = [
        "https://api.exchangerate.host/latest?base=USD",
        "https://api.exchangerate-api.com/v4/latest/USD"
    ]
    for url in apis:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        rates = data.get("rates", {})
                        usd_to_byn = rates.get("BYN")
                        usd_to_cny = rates.get("CNY")
                        if usd_to_byn and usd_to_cny:
                            return usd_to_byn, usd_to_cny
        except:
            continue
    return None, None

# === Вспомогательная функция для создания Excel-файла ===
def create_user_excel(user_id, full_name, phone, tracks):
    wb = Workbook()
    ws = wb.active
    ws.title = "Треки пользователя"

    ws.merge_cells('A1:G1')
    ws['A1'] = f"ФИО: {full_name}"
    ws.merge_cells('A2:G2')
    ws['A2'] = f"Телефон: {phone}"
    ws.merge_cells('A3:G3')
    ws['A3'] = f"ID пользователя: {user_id}"
    ws['A5'] = "Трек-номер"
    ws['B5'] = "Товар"
    ws['C5'] = "Цена за ед. ($)"
    ws['D5'] = "Количество"
    ws['E5'] = "Ед. изм."
    ws['F5'] = "Дата"
    ws['G5'] = "Общая сумма ($)"
    for col in ['A', 'B', 'C', 'D', 'E', 'F', 'G']:
        ws[f'{col}5'].font = Font(bold=True)
        ws[f'{col}5'].alignment = Alignment(horizontal="center")
        ws[f'{col}5'].fill = PatternFill(start_color="DDDDDD", end_color="DDDDDD", fill_type="solid")

    row = 6
    for track in tracks:
        ws[f'A{row}'] = track["track_number"]
        ws[f'B{row}'] = track["product_name"]
        ws[f'C{row}'] = float(track["price"])
        ws[f'D{row}'] = int(track["quantity"])
        ws[f'E{row}'] = track["quantity_type"]
        dt = datetime.fromisoformat(track['created_at'])
        ws[f'F{row}'] = dt.strftime("%Y-%m-%d %H:%M:%S")
        ws[f'G{row}'] = float(track["price"]) * int(track["quantity"])
        row += 1

    ws[f'F{row}'] = "ИТОГО:"
    ws[f'F{row}'].font = Font(bold=True)
    ws[f'G{row}'] = f"=SUM(G6:G{row-1})"
    ws[f'G{row}'].font = Font(bold=True)

    for col in ['A', 'B', 'C', 'D', 'E', 'F', 'G']:
        max_len = 0
        for r in range(1, row+1):
            val = ws[f'{col}{r}'].value
            if val:
                max_len = max(max_len, len(str(val)))
        ws.column_dimensions[col].width = min(max_len + 2, 30)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output

# === Хендлеры ===
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    if message.chat.type in ["group", "supergroup"]:
        await message.answer("📦 **Загрузка треков**\n\nНажми на кнопку:", reply_markup=group_keyboard, parse_mode="Markdown")
        return
    profile = get_user_profile(message.from_user.id)
    keyboard = owner_keyboard if message.from_user.id == OWNER_ID else main_keyboard
    if profile:
        await message.answer(f"👋 С возвращением!\n\nФИО: {profile[0]}\nТелефон: {profile[1]}", reply_markup=keyboard)
    else:
        await state.set_state(ProfileForm.waiting_for_fullname)
        await message.answer("Введи твоё ФИО:", reply_markup=cancel_keyboard)

@dp.message(F.text == "💱 Конвертер валют")
async def currency_button(message: types.Message, state: FSMContext):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="CNY → USD → BYN", callback_data="convert_cny_to_all")],
        [InlineKeyboardButton(text="USD → BYN", callback_data="convert_usd_to_byn")],
        [InlineKeyboardButton(text="BYN → USD", callback_data="convert_byn_to_usd")],
        [InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_currency")]
    ])
    await message.answer("Выберите операцию конвертации:", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data.startswith('convert_') or c.data == 'cancel_currency')
async def currency_callback(callback_query: CallbackQuery, state: FSMContext):
    await callback_query.answer()
    action = callback_query.data
    if action == 'cancel_currency':
        await callback_query.message.edit_text("❌ Конвертация отменена.")
        return
    await state.update_data(currency_action=action)
    await state.set_state(CurrencyForm.waiting_for_amount)
    await callback_query.message.edit_text("Введите сумму цифрами (например, 100):")

@dp.message(CurrencyForm.waiting_for_amount)
async def process_currency_amount(message: types.Message, state: FSMContext):
    try:
        amount = float(message.text.replace(',', '.'))
    except ValueError:
        await message.answer("❌ Пожалуйста, введите число (например, 100 или 50.5).")
        return
    data = await state.get_data()
    action = data.get('currency_action')
    if not action:
        await state.clear()
        await message.answer("Ошибка. Начните заново: /currency")
        return
    usd_to_byn, usd_to_cny = await get_exchange_rates()
    if usd_to_byn is None or usd_to_cny is None:
        await message.answer("❌ Не удалось получить курсы валют. Попробуйте позже.")
        await state.clear()
        return
    result = ""
    if action == "convert_cny_to_all":
        usd_amount = amount / usd_to_cny
        byn_amount = usd_amount * usd_to_byn
        result = f"{amount:.2f} CNY = {usd_amount:.2f} USD\n{amount:.2f} CNY = {byn_amount:.2f} BYN"
    elif action == "convert_usd_to_byn":
        byn_amount = amount * usd_to_byn
        result = f"{amount:.2f} USD = {byn_amount:.2f} BYN"
    elif action == "convert_byn_to_usd":
        usd_amount = amount / usd_to_byn
        result = f"{amount:.2f} BYN = {usd_amount:.2f} USD"
    else:
        result = "Неизвестная операция."
    await message.answer(f"💱 Результат конвертации:\n{result}")
    await state.clear()

@dp.message(F.text == "Редактировать профиль")
async def edit_profile(message: types.Message, state: FSMContext):
    await state.set_state(ProfileForm.waiting_for_fullname)
    await message.answer("Введи новое ФИО:", reply_markup=cancel_keyboard)

@dp.message(F.text == "Отмена")
async def cancel_action(message: types.Message, state: FSMContext):
    await state.clear()
    keyboard = owner_keyboard if message.from_user.id == OWNER_ID else main_keyboard
    await message.answer("Действие отменено", reply_markup=keyboard)

@dp.message(F.text == "Новый трек")
async def new_track(message: types.Message, state: FSMContext):
    profile = get_user_profile(message.from_user.id)
    if not profile:
        await state.set_state(ProfileForm.waiting_for_fullname)
        await message.answer("Сначала заполни профиль. Введи ФИО:", reply_markup=cancel_keyboard)
        return
    await state.set_state(TrackForm.waiting_for_track)
    await message.answer("Введи трек-номер:", reply_markup=cancel_keyboard)

@dp.message(F.text == "📋 Мои треки")
async def my_tracks(message: types.Message):
    tracks = get_user_tracks(message.from_user.id)
    if not tracks:
        await message.answer("У тебя пока нет треков.", reply_markup=main_keyboard)
        return
    text = "📋 ТВОИ ТРЕКИ:\n\n"
    for i, t in enumerate(tracks, 1):
        dt = datetime.fromisoformat(t['created_at'])
        date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        text += f"{i}. Трек: {t['track_number']}\n   Товар: {t['product_name']}\n   Цена за ед.: {t['price']:.2f} $\n   Кол-во: {t['quantity']} {t['quantity_type']}\n   Дата: {date_str}\n\n"
    total = get_total_sum(message.from_user.id)
    text += f"💰 **Общая сумма (цена × кол-во): {total:.2f} $**"
    await message.answer(text, reply_markup=main_keyboard, parse_mode="Markdown")

@dp.message(F.text == "Удалить трек")
async def delete_track_start(message: types.Message, state: FSMContext):
    tracks = get_user_tracks(message.from_user.id)
    if not tracks:
        await message.answer("Нет треков для удаления.", reply_markup=main_keyboard)
        return
    text = "🗑 Выбери номер трека для удаления:\n"
    for i, t in enumerate(tracks, 1):
        text += f"{i}. {t['track_number']} - {t['product_name']}\n"
    await state.set_state(DeleteTrackForm.waiting_for_track_id)
    await state.update_data(tracks=tracks)
    await message.answer(text, reply_markup=cancel_keyboard)

@dp.message(F.text == "📊 Мои треки (Excel)")
async def export_to_excel(message: types.Message):
    user_id = message.from_user.id
    profile = get_user_profile(user_id)
    if not profile:
        await message.answer("Сначала заполните профиль (команда /start).")
        return
    full_name, phone = profile
    tracks = get_user_tracks(user_id)
    if not tracks:
        await message.answer("У вас пока нет треков для выгрузки.")
        return
    output = create_user_excel(user_id, full_name, phone, tracks)
    await message.answer_document(
        BufferedInputFile(output.getvalue(), filename=f"my_tracks_{user_id}.xlsx"),
        caption=f"📊 Ваши треки в Excel\nФИО: {full_name}\nТелефон: {phone}"
    )

@dp.message(F.text == "📦 Калькулятор доставки")
async def calc_button(message: types.Message, state: FSMContext):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Минск"), KeyboardButton(text="Лида")]],
        resize_keyboard=True
    )
    await state.set_state(CalcForm.waiting_for_city)
    await message.answer("Выберите город назначения:", reply_markup=keyboard)

@dp.message(Command("calc"))
async def calc_command(message: types.Message, state: FSMContext):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Минск"), KeyboardButton(text="Лида")]],
        resize_keyboard=True
    )
    await state.set_state(CalcForm.waiting_for_city)
    await message.answer("Выберите город назначения:", reply_markup=keyboard)

@dp.message(CalcForm.waiting_for_city)
async def calc_weight(message: types.Message, state: FSMContext):
    city = message.text
    if city not in ["Минск", "Лида"]:
        await message.answer("Пожалуйста, выберите город из кнопок.")
        return
    await state.update_data(city=city)
    await state.set_state(CalcForm.waiting_for_weight)
    await message.answer("Введите вес посылки в килограммах (например, 2.5):", reply_markup=cancel_keyboard)

@dp.message(CalcForm.waiting_for_weight)
async def calc_result(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.replace(',', '.'))
    except:
        await message.answer("❌ Пожалуйста, введите число (например, 2.5).", reply_markup=cancel_keyboard)
        return
    data = await state.get_data()
    city = data.get("city")
    cost = weight * CARGO_RATE
    cost += weight * DELIVERY_MOSCOW_MINSK
    if city == "Лида":
        cost += weight * DELIVERY_MINSK_LIDA
    cost += TRANSFER_FEE
    cost += weight * EXTRA_RATE
    cost += FIXED_COST
    await message.answer(
        f"📦 Примерная стоимость доставки до {city} для веса {weight:.2f} кг:\n"
        f"💰 {cost:.2f} руб.\n\n"
        f"* Карго (Китай→Москва): {CARGO_RATE:.2f} руб/кг\n"
        f"* Москва→Минск: {DELIVERY_MOSCOW_MINSK:.2f} руб/кг\n"
        f"{'* Минск→Лида: ' + str(DELIVERY_MINSK_LIDA) + ' руб/кг' if city == 'Лида' else ''}\n"
        f"* Плата за передачу: {TRANSFER_FEE:.2f} руб\n"
        f"* Доп. расходы: {EXTRA_RATE:.2f} руб/кг + {FIXED_COST:.2f} руб\n"
        f"Точная стоимость может отличаться.",
        reply_markup=main_keyboard
    )
    await state.clear()

# === Кнопка завершить и отправить (владельцу: текст + Excel) ===
@dp.message(F.text == "✅ Завершить и отправить")
async def finish_and_send(message: types.Message):
    user_id = message.from_user.id
    profile = get_user_profile(user_id)
    if not profile:
        await message.answer("Сначала заполните профиль (команда /start).")
        return
    full_name, phone = profile
    tracks = get_user_tracks(user_id)
    if not tracks:
        await message.answer("У вас пока нет треков для отправки.")
        return

    # Текстовый список
    text = f"📦 **ТРЕКИ ПОЛЬЗОВАТЕЛЯ**\n\n"
    text += f"👤 **Участник:** {message.from_user.full_name} (@{message.from_user.username or 'нет'}) [ID: {user_id}]\n"
    text += f"📞 **ФИО:** {full_name}\n"
    text += f"📞 **Телефон:** {phone}\n"
    text += f"─────────────────\n"
    for i, t in enumerate(tracks, 1):
        dt = datetime.fromisoformat(t['created_at'])
        date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        text += f"{i}. **Трек:** `{t['track_number']}`\n"
        text += f"   📦 **Товар:** {t['product_name']}\n"
        text += f"   💰 **Цена за ед.:** {t['price']:.2f} $\n"
        text += f"   📊 **Кол-во:** {t['quantity']} {t['quantity_type']}\n"
        text += f"   🕒 **Дата:** {date_str}\n\n"
    total = get_total_sum(user_id)
    text += f"💰 **Общая сумма (цена × кол-во): {total:.2f} $**"

    # Excel-файл
    excel_file = create_user_excel(user_id, full_name, phone, tracks)

    try:
        await bot.send_message(OWNER_ID, text, parse_mode="Markdown")
        await bot.send_document(
            OWNER_ID,
            BufferedInputFile(excel_file.getvalue(), filename=f"tracks_{user_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"),
            caption=f"📊 Excel-файл для {full_name} (ID: {user_id})"
        )
        await message.answer("✅ Ваши треки успешно отправлены! Спасибо.")
    except Exception as e:
        await message.answer("⚠️ Не удалось отправить треки. Попробуйте позже.")
        print(f"Ошибка отправки владельцу: {e}")

# === Массовая рассылка (только для владельца) ===
@dp.message(F.text == "📢 Сделать рассылку")
async def broadcast_start(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await message.answer("⛔ Эта функция доступна только владельцу.")
        return
    await state.set_state(BroadcastForm.waiting_for_start_date)
    await message.answer("Введите начальную дату в формате ГГГГ-ММ-ДД (например, 2026-04-01):", reply_markup=cancel_keyboard)

@dp.message(BroadcastForm.waiting_for_start_date)
async def broadcast_start_date(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await cancel_action(message, state)
        return
    try:
        start_date = datetime.strptime(message.text.strip(), "%Y-%m-%d")
        await state.update_data(start_date=start_date)
        await state.set_state(BroadcastForm.waiting_for_end_date)
        await message.answer("Введите конечную дату в формате ГГГГ-ММ-ДД (например, 2026-04-10):")
    except ValueError:
        await message.answer("❌ Неверный формат даты. Используйте ГГГГ-ММ-ДД")

@dp.message(BroadcastForm.waiting_for_end_date)
async def broadcast_end_date(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await cancel_action(message, state)
        return
    try:
        end_date = datetime.strptime(message.text.strip(), "%Y-%m-%d")
        await state.update_data(end_date=end_date)
        await state.set_state(BroadcastForm.waiting_for_text)
        await message.answer("Введите текст сообщения для рассылки:")
    except ValueError:
        await message.answer("❌ Неверный формат даты. Используйте ГГГГ-ММ-ДД")

@dp.message(BroadcastForm.waiting_for_text)
async def broadcast_text(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await cancel_action(message, state)
        return
    data = await state.get_data()
    start_date = data["start_date"]
    end_date = data["end_date"]
    text = message.text.strip()
    start_datetime = start_date.replace(tzinfo=timezone(timedelta(hours=3)))
    end_datetime = end_date.replace(hour=23, minute=59, second=59, tzinfo=timezone(timedelta(hours=3)))
    res = supabase.table("tracks").select("user_id").gte("created_at", start_datetime.isoformat()).lte("created_at", end_datetime.isoformat()).execute()
    user_ids = list(set(t["user_id"] for t in res.data))
    if not user_ids:
        await message.answer("📭 Нет пользователей с треками в указанном диапазоне.")
        await state.clear()
        return
    await message.answer(f"👥 Найдено {len(user_ids)} пользователей. Начинаю рассылку...")
    sent = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, f"📢 {text}")
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await message.answer(f"✅ Рассылка завершена. Отправлено {sent} сообщений.")
    await state.clear()

# === FSM обработчики (профиль, треки, удаление) ===
@dp.message(ProfileForm.waiting_for_fullname)
async def process_fullname(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await cancel_action(message, state)
        return
    await state.update_data(fullname=message.text)
    await state.set_state(ProfileForm.waiting_for_phone)
    await message.answer("Введи номер телефона:", reply_markup=cancel_keyboard)

@dp.message(ProfileForm.waiting_for_phone)
async def process_phone(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await cancel_action(message, state)
        return
    data = await state.get_data()
    save_user_profile(message.from_user.id, message.from_user.username or "нет", data["fullname"], message.text)
    await state.clear()
    keyboard = owner_keyboard if message.from_user.id == OWNER_ID else main_keyboard
    await message.answer("✅ Профиль сохранён!", reply_markup=keyboard)

@dp.message(TrackForm.waiting_for_track)
async def process_track(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await cancel_action(message, state)
        return
    await state.update_data(track=message.text)
    await state.set_state(TrackForm.waiting_for_product)
    await message.answer("Введи наименование товара:", reply_markup=cancel_keyboard)

@dp.message(TrackForm.waiting_for_product)
async def process_product(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await cancel_action(message, state)
        return
    await state.update_data(product=message.text)
    await state.set_state(TrackForm.waiting_for_price)
    await message.answer("Введи цену в долларах (например: 25):", reply_markup=cancel_keyboard)

@dp.message(TrackForm.waiting_for_price)
async def process_price(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await cancel_action(message, state)
        return
    match = re.search(r"(\d+(?:[.,]\d+)?)", message.text)
    if not match:
        await message.answer("Введи число!", reply_markup=cancel_keyboard)
        return
    price = float(match.group(1).replace(",", "."))
    await state.update_data(price=price)
    await state.set_state(TrackForm.waiting_for_quantity_type)
    await message.answer("Выбери: шт или пара", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="шт"), KeyboardButton(text="пара")]], resize_keyboard=True))

@dp.message(TrackForm.waiting_for_quantity_type)
async def process_quantity_type(message: types.Message, state: FSMContext):
    if message.text not in ["шт", "пара"]:
        await message.answer("Выбери кнопку: шт или пара")
        return
    await state.update_data(qtype=message.text)
    await state.set_state(TrackForm.waiting_for_quantity)
    await message.answer(f"Введи количество в {message.text}:", reply_markup=cancel_keyboard)

@dp.message(TrackForm.waiting_for_quantity)
async def process_quantity(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await cancel_action(message, state)
        return
    try:
        quantity = int(message.text)
    except:
        await message.answer("Введи целое число!", reply_markup=cancel_keyboard)
        return
    data = await state.get_data()
    add_track(message.from_user.id, data["track"], data["product"], data["price"], data["qtype"], quantity)
    await state.clear()
    keyboard = owner_keyboard if message.from_user.id == OWNER_ID else main_keyboard
    await message.answer("✅ Трек загружен! Вы можете продолжить добавлять треки или нажать «✅ Завершить и отправить», когда закончите.", reply_markup=keyboard)

@dp.message(DeleteTrackForm.waiting_for_track_id)
async def process_delete_track(message: types.Message, state: FSMContext):
    data = await state.get_data()
    tracks = data["tracks"]
    try:
        idx = int(message.text) - 1
        if idx < 0 or idx >= len(tracks):
            raise ValueError
        track_id = tracks[idx]["id"]
        delete_track(track_id, message.from_user.id)
        keyboard = owner_keyboard if message.from_user.id == OWNER_ID else main_keyboard
        await message.answer("✅ Трек удалён!", reply_markup=keyboard)
    except:
        await message.answer("Введи правильный номер!", reply_markup=cancel_keyboard)
    await state.clear()

# === Веб-сервер и самопинг ===
async def handle_web(request):
    return web.Response(text="Bot is running")

async def start_web():
    app = web.Application()
    app.router.add_get("/", handle_web)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8000)
    await site.start()
    print("Веб-сервер запущен на порту 8000")

async def keep_alive():
    url = os.getenv("RENDER_EXTERNAL_URL", "https://track-bot-fresh.onrender.com")  # замените на ваш URL
    while True:
        await asyncio.sleep(600)
        try:
            async with aiohttp.ClientSession() as session:
                await session.get(url, timeout=5)
                print("Keep-alive ping sent")
        except Exception as e:
            print(f"Keep-alive error: {e}")

async def run_bot():
    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            print("Вебхук сброшен, запускаем polling...")
            await dp.start_polling(bot)
        except Exception as e:
            print(f"Бот упал с ошибкой: {e}. Перезапуск через 5 секунд...")
            await asyncio.sleep(5)

async def main():
    print("Бот запущен...")
    await asyncio.gather(keep_alive(), run_bot(), start_web())

if __name__ == "__main__":
    asyncio.run(main())
