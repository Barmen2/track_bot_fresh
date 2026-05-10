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
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from supabase import create_client, Client
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
import aiohttp
from aiohttp import web

# === НАСТРОЙКИ ===
BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 6810564564))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# === КЛАВИАТУРЫ ===
main_kb = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="Новый трек")],
        [KeyboardButton(text="📋 Мои треки"), KeyboardButton(text="📊 Excel")],
        [KeyboardButton(text="💰 Конвертер"), KeyboardButton(text="🚚 Доставка")],
        [KeyboardButton(text="✅ Отправить"), KeyboardButton(text="🗑 Удалить всё")]
    ],
    resize_keyboard=True
)

cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)

# === FSM ===
class Profile(StatesGroup):
    name = State()
    phone = State()

class Track(StatesGroup):
    number = State()
    product = State()
    price = State()
    qtype = State()
    qty = State()

class Currency(StatesGroup):
    amount = State()

class Delete(StatesGroup):
    id = State()

# === БАЗА ДАННЫХ ===
def now_msk():
    return datetime.now(timezone.utc) + timedelta(hours=3)

def get_profile(user_id):
    res = supabase.table("users").select("full_name, phone").eq("user_id", user_id).execute()
    return res.data[0] if res.data else None

def save_profile(user_id, username, name, phone):
    supabase.table("users").upsert({"user_id": user_id, "username": username, "full_name": name, "phone": phone, "created_at": now_msk().isoformat()}).execute()

def add_track(user_id, track, product, price_cny, price_usd, price_byn, qtype, qty):
    supabase.table("tracks").insert({
        "user_id": user_id, "track_number": track, "product_name": product,
        "price_cny": price_cny, "price_usd": price_usd, "price_byn": price_byn,
        "quantity": qty, "quantity_type": qtype, "created_at": now_msk().isoformat()
    }).execute()

def get_tracks(user_id):
    res = supabase.table("tracks").select("*").eq("user_id", user_id).order("created_at", desc=True).execute()
    return res.data

def delete_track(track_id, user_id):
    supabase.table("tracks").delete().eq("id", track_id).eq("user_id", user_id).execute()

def delete_all(user_id):
    supabase.table("tracks").delete().eq("user_id", user_id).execute()

def total_cny(user_id):
    tracks = get_tracks(user_id)
    return sum(t["price_cny"] * t["quantity"] for t in tracks) if tracks else 0

def total_usd(user_id):
    tracks = get_tracks(user_id)
    return sum((t.get("price_usd") or 0) * t["quantity"] for t in tracks) if tracks else 0

def total_byn(user_id):
    tracks = get_tracks(user_id)
    return sum((t.get("price_byn") or 0) * t["quantity"] for t in tracks) if tracks else 0

# === КУРСЫ ===
async def get_rates():
    cny_usd, usd_byn = 0.14, 3.2
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get("https://api.exchangerate.host/latest?base=CNY&symbols=USD", timeout=10) as r:
                if r.status == 200:
                    cny_usd = (await r.json()).get("rates", {}).get("USD") or cny_usd
            async with session.get("https://api.exchangerate.host/latest?base=USD&symbols=BYN", timeout=10) as r:
                if r.status == 200:
                    usd_byn = (await r.json()).get("rates", {}).get("BYN") or usd_byn
    except:
        pass
    return cny_usd, usd_byn

# === EXCEL ===
def make_excel(tracks, name, phone, uid):
    wb = Workbook()
    ws = wb.active
    ws.title = "Треки"
    headers = ["№", "Трек", "Товар", "CNY", "USD", "BYN", "Кол-во", "Ед.", "Дата"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=h)
        cell.font = Font(bold=True)
    for i, t in enumerate(tracks, 2):
        ws.cell(row=i, column=1, value=i-1)
        ws.cell(row=i, column=2, value=t["track_number"])
        ws.cell(row=i, column=3, value=t["product_name"])
        ws.cell(row=i, column=4, value=float(t["price_cny"]))
        ws.cell(row=i, column=5, value=float(t.get("price_usd") or 0))
        ws.cell(row=i, column=6, value=float(t.get("price_byn") or 0))
        ws.cell(row=i, column=7, value=int(t["quantity"]))
        ws.cell(row=i, column=8, value=t["quantity_type"])
        ws.cell(row=i, column=9, value=t["created_at"][:19])
    row = len(tracks) + 2
    ws.cell(row=row, column=1, value="Всего треков:"); ws.cell(row=row, column=2, value=len(tracks))
    ws.cell(row=row+1, column=1, value="Общее количество:"); ws.cell(row=row+1, column=2, value=sum(t["quantity"] for t in tracks))
    ws.cell(row=row+2, column=7, value="ИТОГО CNY:"); ws.cell(row=row+2, column=8, value=f"{total_cny(uid):.2f}")
    ws.cell(row=row+3, column=7, value="ИТОГО USD:"); ws.cell(row=row+3, column=8, value=f"{total_usd(uid):.2f}")
    ws.cell(row=row+4, column=7, value="ИТОГО BYN:"); ws.cell(row=row+4, column=8, value=f"{total_byn(uid):.2f}")
    ws.cell(row=row+6, column=1, value=f"ФИО: {name}")
    ws.cell(row=row+7, column=1, value=f"Телефон: {phone}")
    out = BytesIO()
    wb.save(out)
    out.seek(0)
    return out

# === ХЕНДЛЕРЫ ===
@dp.message(Command("start"))
async def start(msg: types.Message, state: FSMContext):
    if msg.chat.type in ["group", "supergroup"]:
        await msg.answer("📦 Загрузка треков", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Бот", url="https://t.me/little_Bro_track_bot")]]))
        return
    prof = get_profile(msg.from_user.id)
    if prof:
        await msg.answer(f"✅ С возвращением!\n{prof['full_name']}\n{prof['phone']}", reply_markup=main_kb)
    else:
        await state.set_state(Profile.name)
        await msg.answer("Введи ФИО:", reply_markup=cancel_kb)

@dp.message(Profile.name)
async def p_name(msg: types.Message, state: FSMContext):
    if msg.text == "Отмена": await cancel(msg, state); return
    await state.update_data(name=msg.text)
    await state.set_state(Profile.phone)
    await msg.answer("Введи телефон:", reply_markup=cancel_kb)

@dp.message(Profile.phone)
async def p_phone(msg: types.Message, state: FSMContext):
    if msg.text == "Отмена": await cancel(msg, state); return
    data = await state.get_data()
    save_profile(msg.from_user.id, msg.from_user.username or "", data["name"], msg.text.strip())
    await state.clear()
    await msg.answer("✅ Профиль сохранён!", reply_markup=main_kb)

@dp.message(F.text == "Отмена")
async def cancel(msg: types.Message, state: FSMContext):
    await state.clear()
    await msg.answer("Отменено", reply_markup=main_kb)

@dp.message(F.text == "Новый трек")
async def new_track(msg: types.Message, state: FSMContext):
    if not get_profile(msg.from_user.id):
        await msg.answer("Сначала заполни профиль через /start")
        return
    await state.set_state(Track.number)
    await msg.answer("Введи трек-номер:", reply_markup=cancel_kb)

@dp.message(Track.number)
async def t_num(msg: types.Message, state: FSMContext):
    if msg.text == "Отмена": await cancel(msg, state); return
    await state.update_data(track=msg.text)
    await state.set_state(Track.product)
    await msg.answer("Введи товар:", reply_markup=cancel_kb)

@dp.message(Track.product)
async def t_prod(msg: types.Message, state: FSMContext):
    if msg.text == "Отмена": await cancel(msg, state); return
    await state.update_data(product=msg.text)
    await state.set_state(Track.price)
    await msg.answer("Цена в CNY:", reply_markup=cancel_kb)

@dp.message(Track.price)
async def t_price(msg: types.Message, state: FSMContext):
    if msg.text == "Отмена": await cancel(msg, state); return
    match = re.search(r"[\d,.]+", msg.text)
    if not match:
        await msg.answer("Введи число!")
        return
    price_cny = float(match.group().replace(",", "."))
    cny_usd, usd_byn = await get_rates()
    price_usd = round(price_cny * cny_usd, 2)
    price_byn = round(price_usd * usd_byn, 2)
    await state.update_data(price_cny=price_cny, price_usd=price_usd, price_byn=price_byn)
    await state.set_state(Track.qtype)
    await msg.answer(f"Цена: {price_cny} CNY = {price_usd} USD = {price_byn} BYN\nВыбери единицу:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="шт"), KeyboardButton(text="пара")]], resize_keyboard=True))

@dp.message(Track.qtype)
async def t_qtype(msg: types.Message, state: FSMContext):
    if msg.text not in ["шт", "пара"]:
        await msg.answer("Выбери кнопку!")
        return
    await state.update_data(qtype=msg.text)
    await state.set_state(Track.qty)
    await msg.answer(f"Количество в {msg.text}:", reply_markup=cancel_kb)

@dp.message(Track.qty)
async def t_qty(msg: types.Message, state: FSMContext):
    if msg.text == "Отмена": await cancel(msg, state); return
    try:
        qty = int(msg.text)
    except:
        await msg.answer("Целое число!")
        return
    data = await state.get_data()
    add_track(msg.from_user.id, data["track"], data["product"], data["price_cny"], data["price_usd"], data["price_byn"], data["qtype"], qty)
    await state.clear()
    await msg.answer("✅ Трек добавлен!", reply_markup=main_kb)

@dp.message(F.text == "📋 Мои треки")
async def my_tracks(msg: types.Message):
    tracks = get_tracks(msg.from_user.id)
    if not tracks:
        await msg.answer("Нет треков.")
        return
    text = "📦 ТВОИ ТРЕКИ:\n\n"
    for i, t in enumerate(tracks, 1):
        text += f"{i}. {t['track_number']}\n   {t['product_name']}\n   {t['price_cny']} CNY / {t.get('price_usd',0)} USD / {t.get('price_byn',0)} BYN\n   {t['quantity']} {t['quantity_type']}\n   {t['created_at'][:19]}\n\n"
    text += f"💰 Итого: {total_cny(msg.from_user.id):.2f} CNY ≈ {total_usd(msg.from_user.id):.2f} USD ≈ {total_byn(msg.from_user.id):.2f} BYN"
    await msg.answer(text)

@dp.message(F.text == "📊 Excel")
async def excel(msg: types.Message):
    tracks = get_tracks(msg.from_user.id)
    if not tracks:
        await msg.answer("Нет треков.")
        return
    prof = get_profile(msg.from_user.id)
    if not prof:
        await msg.answer("Профиль не найден. /start")
        return
    file = make_excel(tracks, prof["full_name"], prof["phone"], msg.from_user.id)
    await msg.answer_document(BufferedInputFile(file.getvalue(), filename="tracks.xlsx"), caption="Excel")

@dp.message(F.text == "💰 Конвертер")
async def conv(msg: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="CNY→USD→BYN", callback_data="cny")],
        [InlineKeyboardButton(text="USD→BYN", callback_data="usd2byn")],
        [InlineKeyboardButton(text="BYN→USD", callback_data="byn2usd")],
        [InlineKeyboardButton(text="❌", callback_data="cancel")]
    ])
    await msg.answer("Выбери операцию:", reply_markup=kb)

@dp.callback_query(lambda c: c.data in ["cny", "usd2byn", "byn2usd", "cancel"])
async def conv_cb(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if call.data == "cancel":
        await call.message.edit_text("Отменено")
        return
    await state.update_data(conv_type=call.data)
    await state.set_state(Currency.amount)
    await call.message.edit_text("Введи сумму:")

@dp.message(Currency.amount)
async def conv_amount(msg: types.Message, state: FSMContext):
    try:
        amount = float(msg.text.replace(",", "."))
    except:
        await msg.answer("Введи число!")
        return
    data = await state.get_data()
    conv_type = data.get("conv_type")
    cny_usd, usd_byn = await get_rates()
    if conv_type == "cny":
        usd = amount * cny_usd
        byn = usd * usd_byn
        await msg.answer(f"{amount} CNY = {usd:.2f} USD = {byn:.2f} BYN")
    elif conv_type == "usd2byn":
        await msg.answer(f"{amount} USD = {amount * usd_byn:.2f} BYN")
    elif conv_type == "byn2usd":
        await msg.answer(f"{amount} BYN = {amount / usd_byn:.2f} USD")
    await state.clear()

@dp.message(F.text == "🚚 Доставка")
async def delivery(msg: types.Message, state: FSMContext):
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Минск"), KeyboardButton(text="Лида")]], resize_keyboard=True)
    await state.set_state(CalcForm.waiting_for_city)
    await msg.answer("Город:", reply_markup=kb)

# Для простоты добавим класс CalcForm (если нет)
class CalcForm(StatesGroup):
    waiting_for_city = State()
    waiting_for_weight = State()

@dp.message(CalcForm.waiting_for_city)
async def del_city(msg: types.Message, state: FSMContext):
    if msg.text not in ["Минск", "Лида"]:
        await msg.answer("Кнопка!")
        return
    await state.update_data(city=msg.text)
    await state.set_state(CalcForm.waiting_for_weight)
    await msg.answer("Вес в кг:", reply_markup=cancel_kb)

@dp.message(CalcForm.waiting_for_weight)
async def del_weight(msg: types.Message, state: FSMContext):
    try:
        weight = float(msg.text.replace(",", "."))
    except:
        await msg.answer("Число!")
        return
    data = await state.get_data()
    city = data["city"]
    cost = weight * 12 + weight * 1.6 + (weight * 0.8 if city == "Лида" else 0) + 10
    await msg.answer(f"Доставка до {city}: {cost:.2f} руб.", reply_markup=main_kb)
    await state.clear()

@dp.message(F.text == "✅ Отправить")
async def send_to_owner(msg: types.Message):
    tracks = get_tracks(msg.from_user.id)
    if not tracks:
        await msg.answer("Нет треков")
        return
    prof = get_profile(msg.from_user.id)
    if not prof:
        await msg.answer("Профиль не найден")
        return
    text = f"📦 Треки от {prof['full_name']} (@{msg.from_user.username or '-'})\n{prof['phone']}\n\n"
    for i, t in enumerate(tracks, 1):
        text += f"{i}. {t['track_number']} – {t['product_name']}\n   {t['price_cny']} CNY ≈ {t.get('price_usd',0)} USD ≈ {t.get('price_byn',0)} BYN, {t['quantity']} {t['quantity_type']}\n"
    text += f"\n💰 Итого: {total_cny(msg.from_user.id):.2f} CNY ≈ {total_usd(msg.from_user.id):.2f} USD ≈ {total_byn(msg.from_user.id):.2f} BYN"
    excel_file = make_excel(tracks, prof["full_name"], prof["phone"], msg.from_user.id)
    await bot.send_message(OWNER_ID, text)
    await bot.send_document(OWNER_ID, BufferedInputFile(excel_file.getvalue(), filename=f"{msg.from_user.id}.xlsx"), caption=f"От {prof['full_name']}")
    await msg.answer("Отправлено владельцу!")

@dp.message(F.text == "🗑 Удалить всё")
async def delete_all_confirm(msg: types.Message, state: FSMContext):
    await state.set_state(ConfirmDeleteAllForm.waiting_for_confirmation)
    await msg.answer("Удалить ВСЕ треки? Напиши ДА (заглавными)", reply_markup=cancel_kb)

class ConfirmDeleteAllForm(StatesGroup):
    waiting_for_confirmation = State()

@dp.message(ConfirmDeleteAllForm.waiting_for_confirmation)
async def confirm_delete_all(msg: types.Message, state: FSMContext):
    if msg.text == "Отмена":
        await cancel(msg, state)
        return
    if msg.text.strip() == "ДА":
        delete_all(msg.from_user.id)
        await msg.answer("Все треки удалены.", reply_markup=main_kb)
    else:
        await msg.answer("Не подтверждено.", reply_markup=main_kb)
    await state.clear()

@dp.message(F.text == "📢 Сделать рассылку")
async def broadcast(msg: types.Message):
    if msg.from_user.id != OWNER_ID:
        await msg.answer("Нет прав")
        return
    # упрощённо
    await msg.answer("Функция рассылки отключена для простоты")

# === ВЕБ-СЕРВЕР (обязателен для Render) ===
async def handle_web(request):
    return web.Response(text="OK")

async def start_web():
    app = web.Application()
    app.router.add_get("/", handle_web)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8000)
    await site.start()

async def run_bot():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

async def main():
    await asyncio.gather(start_web(), run_bot())

if __name__ == "__main__":
    asyncio.run(main())
