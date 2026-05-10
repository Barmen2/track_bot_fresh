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

# === КОНФИГУРАЦИЯ ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 6810564564))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

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

# === КЛАВИАТУРЫ ===
main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Новый трек")],
        [KeyboardButton(text="📋 Мои треки"), KeyboardButton(text="📊 Мои треки (Excel)")],
        [KeyboardButton(text="Редактировать профиль"), KeyboardButton(text="Удалить трек")],
        [KeyboardButton(text="💱 Конвертер валют"), KeyboardButton(text="📦 Калькулятор доставки")],
        [KeyboardButton(text="✅ Завершить и отправить"), KeyboardButton(text="🗑 Удалить все треки")]
    ],
    resize_keyboard=True
)

owner_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Новый трек")],
        [KeyboardButton(text="📋 Мои треки"), KeyboardButton(text="📊 Мои треки (Excel)")],
        [KeyboardButton(text="Редактировать профиль"), KeyboardButton(text="Удалить трек")],
        [KeyboardButton(text="💱 Конвертер валют"), KeyboardButton(text="📦 Калькулятор доставки")],
        [KeyboardButton(text="✅ Завершить и отправить"), KeyboardButton(text="🗑 Удалить все треки")],
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
    waiting_for_price_cny = State()
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

class ConfirmDeleteAllForm(StatesGroup):
    waiting_for_confirmation = State()

# === ФУНКЦИИ SUPABASE ===
def get_msk_time():
    return datetime.now(timezone.utc) + timedelta(hours=3)

def get_user_profile(user_id):
    try:
        res = supabase.table("users").select("full_name, phone").eq("user_id", user_id).execute()
        if res.data:
            return res.data[0]["full_name"], res.data[0]["phone"]
        return None
    except Exception as e:
        print(f"Ошибка get_user_profile: {e}")
        return None

def save_user_profile(user_id, username, full_name, phone):
    supabase.table("users").upsert({
        "user_id": user_id,
        "username": username,
        "full_name": full_name,
        "phone": phone,
        "created_at": get_msk_time().isoformat()
    }).execute()

def add_track(user_id, track, product, price_cny, price_usd, price_byn, qty_type, quantity):
    supabase.table("tracks").insert({
        "user_id": user_id,
        "track_number": track,
        "product_name": product,
        "price_cny": price_cny,
        "price_usd": price_usd,
        "price_byn": price_byn,
        "quantity": quantity,
        "quantity_type": qty_type,
        "created_at": get_msk_time().isoformat()
    }).execute()

def get_user_tracks(user_id):
    try:
        res = supabase.table("tracks").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
        return res.data
    except Exception as e:
        print(f"Ошибка get_user_tracks: {e}")
        return []

def delete_track(track_id, user_id):
    supabase.table("tracks").delete().eq("id", track_id).eq("user_id", user_id).execute()

def delete_all_tracks(user_id):
    supabase.table("tracks").delete().eq("user_id", user_id).execute()

def get_total_sum_cny(user_id):
    tracks = get_user_tracks(user_id)
    return round(sum(t["price_cny"] * t["quantity"] for t in tracks), 2)

def get_total_sum_usd(user_id):
    tracks = get_user_tracks(user_id)
    return round(sum(t["price_usd"] * t["quantity"] for t in tracks), 2)

def get_total_sum_byn(user_id):
    tracks = get_user_tracks(user_id)
    return round(sum(t["price_byn"] * t["quantity"] for t in tracks), 2)

# === ПОЛУЧЕНИЕ КУРСОВ ===
async def get_exchange_rates_from_cny():
    # Пробуем получить прямые курсы CNY -> USD и CNY -> BYN
    apis = [
        "https://api.exchangerate.host/latest?base=CNY&symbols=USD,BYN",
        "https://api.exchangerate-api.com/v4/latest/CNY"
    ]
    for url in apis:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        rates = data.get("rates", {})
                        usd = rates.get("USD")
                        byn = rates.get("BYN")
                        if usd and byn:
                            return usd, byn
        except:
            continue
    return None, None

async def get_cny_to_usd_rate():
    usd, _ = await get_exchange_rates_from_cny()
    return usd

async def get_cny_to_byn_rate():
    _, byn = await get_exchange_rates_from_cny()
    return byn

# === СОЗДАНИЕ EXCEL-ФАЙЛА ===
def create_user_excel(user_id, full_name, phone, tracks):
    wb = Workbook()
    ws = wb.active
    ws.title = "Треки"

    # Шапка с данными пользователя
    ws.merge_cells('A1:I1')
    ws['A1'] = f"ФИО: {full_name}"
    ws.merge_cells('A2:I2')
    ws['A2'] = f"Телефон: {phone}"
    ws.merge_cells('A3:I3')
    ws['A3'] = f"ID пользователя: {user_id}"
    ws.merge_cells('A4:I4')
    ws['A4'] = "Курсы на момент добавления: 1 CNY = X USD / X BYN (фиксировано в каждой позиции)"
    
    # Заголовки таблицы
    headers = ["Трек-номер", "Товар", "Цена (CNY)", "Цена (USD)", "Цена (BYN)", "Количество", "Ед. изм.", "Дата", "Общая сумма (BYN)"]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=5, column=col, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
        cell.fill = PatternFill(start_color="DDDDDD", end_color="DDDDDD", fill_type="solid")

    row = 6
    for track in tracks:
        ws[f'A{row}'] = track["track_number"]
        ws[f'B{row}'] = track["product_name"]
        ws[f'C{row}'] = float(track["price_cny"])
        ws[f'D{row}'] = float(track["price_usd"])
        ws[f'E{row}'] = float(track["price_byn"])
        ws[f'F{row}'] = int(track["quantity"])
        ws[f'G{row}'] = track["quantity_type"]
        dt = datetime.fromisoformat(track['created_at'])
        ws[f'H{row}'] = dt.strftime("%Y-%m-%d %H:%M:%S")
        ws[f'I{row}'] = float(track["price_byn"]) * int(track["quantity"])
        row += 1

    # Итоговые суммы
    total_cny = get_total_sum_cny(user_id)
    total_usd = get_total_sum_usd(user_id)
    total_byn = get_total_sum_byn(user_id)
    ws[f'F{row}'] = "ИТОГО:"
    ws[f'F{row}'].font = Font(bold=True)
    ws[f'G{row}'] = f"{total_cny:.2f} CNY / {total_usd:.2f} USD / {total_byn:.2f} BYN"
    ws[f'G{row}'].font = Font(bold=True)
    
    # Автоширина колонок
    for col in ws.columns:
        max_len = 0
        col_letter = col[0].column_letter
        for cell in col:
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = min(max_len + 2, 30)

    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output

# === ХЕНДЛЕРЫ ===
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
    full_name = data.get("fullname")
    if not full_name:
        await message.answer("Ошибка. Начните заново с /start")
        await state.clear()
        return
    phone = message.text.strip()
    user_id = message.from_user.id
    username = message.from_user.username or "нет"
    try:
        save_user_profile(user_id, username, full_name, phone)
    except Exception as e:
        await message.answer(f"❌ Ошибка сохранения: {e}")
        await state.clear()
        return
    await state.clear()
    keyboard = owner_keyboard if user_id == OWNER_ID else main_keyboard
    await message.answer("✅ Профиль сохранён! Теперь можно добавлять треки.", reply_markup=keyboard)

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
    await state.set_state(TrackForm.waiting_for_price_cny)
    await message.answer("Введи цену в **китайских юанях (CNY)**:\n(бот пересчитает в USD и BYN по текущему курсу)", reply_markup=cancel_keyboard, parse_mode="Markdown")

@dp.message(TrackForm.waiting_for_price_cny)
async def process_price_cny(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await cancel_action(message, state)
        return
    match = re.search(r"(\d+(?:[.,]\d+)?)", message.text)
    if not match:
        await message.answer("Введи число!", reply_markup=cancel_keyboard)
        return
    price_cny = float(match.group(1).replace(",", "."))
    # Получаем курсы
    usd_rate, byn_rate = await get_exchange_rates_from_cny()
    if usd_rate is None or byn_rate is None:
        await message.answer("❌ Не удалось получить курсы CNY/USD и CNY/BYN. Попробуй позже.\nИспользую приблизительные курсы (1 CNY = 0.14 USD, 1 CNY = 0.35 BYN)", reply_markup=cancel_keyboard)
        usd_rate = 0.14
        byn_rate = 0.35
    price_usd = round(price_cny * usd_rate, 2)
    price_byn = round(price_cny * byn_rate, 2)
    await state.update_data(price_cny=price_cny, price_usd=price_usd, price_byn=price_byn)
    await state.set_state(TrackForm.waiting_for_quantity_type)
    await message.answer(f"💰 Цена: {price_cny:.2f} CNY = {price_usd:.2f} USD = {price_byn:.2f} BYN\n\nВыбери единицу измерения:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="шт"), KeyboardButton(text="пара")]], resize_keyboard=True))

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
    add_track(
        user_id=message.from_user.id,
        track=data["track"],
        product=data["product"],
        price_cny=data["price_cny"],
        price_usd=data["price_usd"],
        price_byn=data["price_byn"],
        qty_type=data["qtype"],
        quantity=quantity
    )
    await state.clear()
    keyboard = owner_keyboard if message.from_user.id == OWNER_ID else main_keyboard
    await message.answer("✅ Трек добавлен! Цены в USD и BYN рассчитаны по текущим курсам.", reply_markup=keyboard)

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
        text += f"{i}. Трек: {t['track_number']}\n   Товар: {t['product_name']}\n"
        text += f"   Цена: {t['price_cny']:.2f} CNY = {t['price_usd']:.2f} USD = {t['price_byn']:.2f} BYN\n"
        text += f"   Кол-во: {t['quantity']} {t['quantity_type']}\n   Дата: {date_str}\n\n"
    total_cny = get_total_sum_cny(message.from_user.id)
    total_usd = get_total_sum_usd(message.from_user.id)
    total_byn = get_total_sum_byn(message.from_user.id)
    text += f"💰 **Общая сумма: {total_cny:.2f} CNY ≈ {total_usd:.2f} USD ≈ {total_byn:.2f} BYN**"
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

@dp.message(DeleteTrackForm.waiting_for_track_id)
async def process_delete_track(message: types.Message, state: FSMContext):
    data = await state.get_data()
    tracks = data["tracks"]
    try:
        idx = int(message.text) - 1
        if idx < 0 or idx >= len(tracks):
            raise ValueError
        delete_track(tracks[idx]["id"], message.from_user.id)
        keyboard = owner_keyboard if message.from_user.id == OWNER_ID else main_keyboard
        await message.answer("✅ Трек удалён!", reply_markup=keyboard)
    except:
        await message.answer("Введи правильный номер!", reply_markup=cancel_keyboard)
    await state.clear()

@dp.message(F.text == "🗑 Удалить все треки")
async def delete_all_tracks_start(message: types.Message, state: FSMContext):
    tracks = get_user_tracks(message.from_user.id)
    if not tracks:
        await message.answer("У тебя и так нет треков.", reply_markup=main_keyboard)
        return
    await state.set_state(ConfirmDeleteAllForm.waiting_for_confirmation)
    await message.answer("⚠️ **ВНИМАНИЕ!** Вы уверены, что хотите удалить **ВСЕ** свои треки? Это действие необратимо.\n\nНапишите `ДА` (заглавными буквами), чтобы подтвердить.", reply_markup=cancel_keyboard, parse_mode="Markdown")

@dp.message(ConfirmDeleteAllForm.waiting_for_confirmation)
async def confirm_delete_all(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await cancel_action(message, state)
        return
    if message.text.strip() == "ДА":
        delete_all_tracks(message.from_user.id)
        keyboard = owner_keyboard if message.from_user.id == OWNER_ID else main_keyboard
        await message.answer("✅ Все ваши треки удалены.", reply_markup=keyboard)
    else:
        await message.answer("❌ Подтверждение не получено. Удаление отменено.", reply_markup=main_keyboard)
    await state.clear()

@dp.message(F.text == "📊 Мои треки (Excel)")
async def export_to_excel(message: types.Message):
    profile = get_user_profile(message.from_user.id)
    if not profile:
        await message.answer("Сначала заполните профиль (команда /start).")
        return
    tracks = get_user_tracks(message.from_user.id)
    if not tracks:
        await message.answer("Нет треков для выгрузки.")
        return
    output = create_user_excel(message.from_user.id, profile[0], profile[1], tracks)
    await message.answer_document(
        BufferedInputFile(output.getvalue(), filename=f"my_tracks_{message.from_user.id}.xlsx"),
        caption="📊 Ваши треки в Excel (цены в CNY, USD, BYN)"
    )

@dp.message(F.text == "📦 Калькулятор доставки")
async def calc_button(message: types.Message, state: FSMContext):
    keyboard = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Минск"), KeyboardButton(text="Лида")]], resize_keyboard=True)
    await state.set_state(CalcForm.waiting_for_city)
    await message.answer("Выберите город:", reply_markup=keyboard)

@dp.message(CalcForm.waiting_for_city)
async def calc_weight(message: types.Message, state: FSMContext):
    city = message.text
    if city not in ["Минск", "Лида"]:
        await message.answer("Выберите город из кнопок.")
        return
    await state.update_data(city=city)
    await state.set_state(CalcForm.waiting_for_weight)
    await message.answer("Введите вес (кг):", reply_markup=cancel_keyboard)

@dp.message(CalcForm.waiting_for_weight)
async def calc_result(message: types.Message, state: FSMContext):
    try:
        weight = float(message.text.replace(',', '.'))
    except:
        await message.answer("Введите число!")
        return
    data = await state.get_data()
    city = data.get("city")
    cost = weight * CARGO_RATE
    cost += weight * DELIVERY_MOSCOW_MINSK
    if city == "Лида":
        cost += weight * DELIVERY_MINSK_LIDA
    cost += TRANSFER_FEE
    await message.answer(f"📦 Примерная стоимость доставки до {city} для веса {weight:.2f} кг: {cost:.2f} руб.", reply_markup=main_keyboard)
    await state.clear()

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
    except:
        await message.answer("Введите число!")
        return
    data = await state.get_data()
    action = data.get('currency_action')
    # Для конвертера нам нужны курсы USD -> BYN и CNY -> USD, но проще переиспользовать get_exchange_rates_from_cny
    cny_to_usd, cny_to_byn = await get_exchange_rates_from_cny()
    if cny_to_usd is None or cny_to_byn is None:
        await message.answer("❌ Не удалось получить курсы. Попробуйте позже.")
        await state.clear()
        return
    usd_to_byn = cny_to_byn / cny_to_usd if cny_to_usd else None
    if action == "convert_cny_to_all":
        usd = amount * cny_to_usd
        byn = amount * cny_to_byn
        await message.answer(f"{amount:.2f} CNY = {usd:.2f} USD = {byn:.2f} BYN")
    elif action == "convert_usd_to_byn":
        if usd_to_byn:
            byn = amount * usd_to_byn
            await message.answer(f"{amount:.2f} USD = {byn:.2f} BYN")
        else:
            await message.answer("Не удалось рассчитать USD -> BYN")
    elif action == "convert_byn_to_usd":
        if usd_to_byn:
            usd = amount / usd_to_byn
            await message.answer(f"{amount:.2f} BYN = {usd:.2f} USD")
        else:
            await message.answer("Не удалось рассчитать BYN -> USD")
    else:
        await message.answer("Неизвестная операция.")
    await state.clear()

@dp.message(F.text == "✅ Завершить и отправить")
async def finish_and_send(message: types.Message):
    profile = get_user_profile(message.from_user.id)
    if not profile:
        await message.answer("Сначала заполните профиль.")
        return
    tracks = get_user_tracks(message.from_user.id)
    if not tracks:
        await message.answer("Нет треков для отправки.")
        return
    full_name, phone = profile
    text = f"📦 ТРЕКИ ПОЛЬЗОВАТЕЛЯ\n👤 {message.from_user.full_name} (@{message.from_user.username or 'нет'})\n📞 {full_name}\n📞 {phone}\n\n"
    for i, t in enumerate(tracks, 1):
        dt = datetime.fromisoformat(t['created_at'])
        date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        text += f"{i}. {t['track_number']} – {t['product_name']}\n   Цена: {t['price_cny']:.2f} CNY = {t['price_usd']:.2f} USD = {t['price_byn']:.2f} BYN\n   Кол-во: {t['quantity']} {t['quantity_type']} ({date_str})\n\n"
    total_cny = get_total_sum_cny(message.from_user.id)
    total_usd = get_total_sum_usd(message.from_user.id)
    total_byn = get_total_sum_byn(message.from_user.id)
    text += f"💰 Итого: {total_cny:.2f} CNY ≈ {total_usd:.2f} USD ≈ {total_byn:.2f} BYN"
    excel = create_user_excel(message.from_user.id, full_name, phone, tracks)
    try:
        await bot.send_message(OWNER_ID, text)
        await bot.send_document(OWNER_ID, BufferedInputFile(excel.getvalue(), filename=f"tracks_{message.from_user.id}.xlsx"), caption=f"Excel-файл для {full_name}")
        await message.answer("✅ Треки отправлены владельцу!")
    except Exception as e:
        await message.answer("⚠️ Не удалось отправить треки. Попробуйте позже.")
        print(f"Ошибка отправки владельцу: {e}")

@dp.message(F.text == "📢 Сделать рассылку")
async def broadcast_start(message: types.Message, state: FSMContext):
    if message.from_user.id != OWNER_ID:
        await message.answer("⛔ Только для владельца.")
        return
    await state.set_state(BroadcastForm.waiting_for_start_date)
    await message.answer("Введите начальную дату (ГГГГ-ММ-ДД):", reply_markup=cancel_keyboard)

@dp.message(BroadcastForm.waiting_for_start_date)
async def broadcast_start_date(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await cancel_action(message, state)
        return
    try:
        start_date = datetime.strptime(message.text.strip(), "%Y-%m-%d")
        await state.update_data(start_date=start_date)
        await state.set_state(BroadcastForm.waiting_for_end_date)
        await message.answer("Введите конечную дату (ГГГГ-ММ-ДД):")
    except:
        await message.answer("Неверный формат. Используйте ГГГГ-ММ-ДД")

@dp.message(BroadcastForm.waiting_for_end_date)
async def broadcast_end_date(message: types.Message, state: FSMContext):
    if message.text == "Отмена":
        await cancel_action(message, state)
        return
    try:
        end_date = datetime.strptime(message.text.strip(), "%Y-%m-%d")
        await state.update_data(end_date=end_date)
        await state.set_state(BroadcastForm.waiting_for_text)
        await message.answer("Введите текст рассылки:")
    except:
        await message.answer("Неверный формат даты.")

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
        await message.answer("Нет пользователей.")
        await state.clear()
        return
    await message.answer(f"👥 Найдено {len(user_ids)} пользователей. Рассылаю...")
    sent = 0
    for uid in user_ids:
        try:
            await bot.send_message(uid, f"📢 {text}")
            sent += 1
            await asyncio.sleep(0.05)
        except:
            pass
    await message.answer(f"✅ Отправлено {sent} сообщений.")
    await state.clear()

# === ВЕБ-СЕРВЕР И САМОПИНГ ===
async def handle_web(request):
    return web.Response(text="Bot is running")

async def start_web():
    app = web.Application()
    app.router.add_get("/", handle_web)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8000)
    await site.start()
    print("Веб-сервер на порту 8000")

async def keep_alive():
    url = os.getenv("RENDER_EXTERNAL_URL", "https://track-bot-fresh.onrender.com")
    while True:
        await asyncio.sleep(600)
        try:
            async with aiohttp.ClientSession() as session:
                await session.get(url, timeout=5)
                print("Ping")
        except Exception:
            pass

async def run_bot():
    while True:
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            await dp.start_polling(bot)
        except Exception as e:
            print(f"Бот упал: {e}. Перезапуск через 5с.")
            await asyncio.sleep(5)

async def main():
    print("Бот запущен...")
    await asyncio.gather(keep_alive(), run_bot(), start_web())

if __name__ == "__main__":
    asyncio.run(main())
