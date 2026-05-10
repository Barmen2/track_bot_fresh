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
from openpyxl.styles import Font, Alignment
import aiohttp
from aiohttp import web

# === КОНФИГУРАЦИЯ ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 6810564564))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

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
        [KeyboardButton(text="💰 Конвертер валют"), KeyboardButton(text="🚚 Калькулятор доставки")],
        [KeyboardButton(text="✅ Завершить и отправить"), KeyboardButton(text="🗑 Удалить все треки")]
    ],
    resize_keyboard=True
)

owner_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Новый трек")],
        [KeyboardButton(text="📋 Мои треки"), KeyboardButton(text="📊 Мои треки (Excel)")],
        [KeyboardButton(text="Редактировать профиль"), KeyboardButton(text="Удалить трек")],
        [KeyboardButton(text="💰 Конвертер валют"), KeyboardButton(text="🚚 Калькулятор доставки")],
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

# === БАЗА ДАННЫХ ===
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
    return round(sum(t["price_cny"] * t["quantity"] for t in tracks), 2) if tracks else 0

def get_total_sum_usd(user_id):
    tracks = get_user_tracks(user_id)
    return round(sum((t.get("price_usd") or 0) * t["quantity"] for t in tracks), 2) if tracks else 0

def get_total_sum_byn(user_id):
    tracks = get_user_tracks(user_id)
    return round(sum((t.get("price_byn") or 0) * t["quantity"] for t in tracks), 2) if tracks else 0

def get_total_quantity(user_id):
    tracks = get_user_tracks(user_id)
    return sum(t["quantity"] for t in tracks) if tracks else 0

# === ПОЛУЧЕНИЕ КУРСОВ ===
async def get_exchange_rates():
    fallback_cny_to_usd = 0.14
    fallback_usd_to_byn = 3.2
    cny_to_usd = None
    usd_to_byn = None
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.exchangerate.host/latest?base=CNY&symbols=USD", timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    cny_to_usd = data.get("rates", {}).get("USD")
            async with session.get("https://api.exchangerate.host/latest?base=USD&symbols=BYN", timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    usd_to_byn = data.get("rates", {}).get("BYN")
            if usd_to_byn is None:
                async with session.get("https://api.exchangerate.host/latest?base=BYN&symbols=USD", timeout=10) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        usd_to_byn_inv = data.get("rates", {}).get("USD")
                        if usd_to_byn_inv:
                            usd_to_byn = 1 / usd_to_byn_inv
    except:
        pass
    if cny_to_usd is None:
        cny_to_usd = fallback_cny_to_usd
    if usd_to_byn is None:
        usd_to_byn = fallback_usd_to_byn
    return cny_to_usd, usd_to_byn

async def get_cny_to_usd_rate():
    cny_to_usd, _ = await get_exchange_rates()
    return cny_to_usd

async def get_usd_to_byn_rate():
    _, usd_to_byn = await get_exchange_rates()
    return usd_to_byn

# === EXCEL ===
def create_excel(tracks, full_name, phone, user_id):
    wb = Workbook()
    ws = wb.active
    ws.title = "Треки"
    headers = ["№", "Трек-номер", "Товар", "Цена (CNY)", "Цена (USD)", "Цена (BYN)", "Кол-во", "Ед. изм.", "Дата"]
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = Font(bold=True)
        cell.alignment = Alignment(horizontal="center")
    for row_idx, t in enumerate(tracks, start=2):
        ws.cell(row=row_idx, column=1, value=row_idx-1)
        ws.cell(row=row_idx, column=2, value=t["track_number"])
        ws.cell(row=row_idx, column=3, value=t["product_name"])
        ws.cell(row=row_idx, column=4, value=float(t["price_cny"]))
        ws.cell(row=row_idx, column=5, value=float(t.get("price_usd") or 0))
        ws.cell(row=row_idx, column=6, value=float(t.get("price_byn") or 0))
        ws.cell(row=row_idx, column=7, value=int(t["quantity"]))
        ws.cell(row=row_idx, column=8, value=t["quantity_type"])
        dt = datetime.fromisoformat(t['created_at'])
        ws.cell(row=row_idx, column=9, value=dt.strftime("%Y-%m-%d %H:%M:%S"))
    total_tracks = len(tracks)
    total_quantity = get_total_quantity(user_id)
    last_row = len(tracks) + 2
    ws.cell(row=last_row, column=1, value="Всего треков:")
    ws.cell(row=last_row, column=2, value=total_tracks)
    ws.cell(row=last_row+1, column=1, value="Общее количество единиц:")
    ws.cell(row=last_row+1, column=2, value=total_quantity)
    total_cny = get_total_sum_cny(user_id)
    total_usd = get_total_sum_usd(user_id)
    total_byn = get_total_sum_byn(user_id)
    ws.cell(row=last_row+2, column=7, value="ИТОГО (CNY):")
    ws.cell(row=last_row+2, column=8, value=f"{total_cny:.2f}")
    ws.cell(row=last_row+3, column=7, value="ИТОГО (USD):")
    ws.cell(row=last_row+3, column=8, value=f"{total_usd:.2f}")
    ws.cell(row=last_row+4, column=7, value="ИТОГО (BYN):")
    ws.cell(row=last_row+4, column=8, value=f"{total_byn:.2f}")
    ws.cell(row=last_row+6, column=1, value=f"ФИО: {full_name}")
    ws.cell(row=last_row+7, column=1, value=f"Телефон: {phone}")
    ws.cell(row=last_row+8, column=1, value=f"ID: {user_id}")
    for col in range(1, 10):
        ws.column_dimensions[chr(64+col)].width = 18
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
        await message.answer(f"✅ С возвращением!\n\nФИО: {profile[0]}\nТелефон: {profile[1]}", reply_markup=keyboard)
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
    save_user_profile(message.from_user.id, message.from_user.username or "нет", full_name, message.text.strip())
    await state.clear()
    keyboard = owner_keyboard if message.from_user.id == OWNER_ID else main_keyboard
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
    if not get_user_profile(message.from_user.id):
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
    await message.answer("Введи цену в **юанях (CNY)**:\n(бот пересчитает в USD и BYN по текущему курсу)", reply_markup=cancel_keyboard, parse_mode="Markdown")

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
    cny_to_usd = await get_cny_to_usd_rate()
    usd_to_byn = await get_usd_to_byn_rate()
    price_usd = round(price_cny * cny_to_usd, 2)
    price_byn = round(price_usd * usd_to_byn, 2)
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
        qty = int(message.text)
    except:
        await message.answer("Введи целое число!")
        return
    data = await state.get_data()
    add_track(
        message.from_user.id,
        data["track"],
        data["product"],
        data["price_cny"],
        data["price
