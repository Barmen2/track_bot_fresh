import asyncio, os, re
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

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID = int(os.getenv("OWNER_ID", 6810564564))
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

main_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Новый трек")],[KeyboardButton(text="📋 Мои треки"), KeyboardButton(text="📊 Мои треки (Excel)")],[KeyboardButton(text="Редактировать профиль"), KeyboardButton(text="Удалить трек")],[KeyboardButton(text="💰 Конвертер валют"), KeyboardButton(text="🚚 Калькулятор доставки")],[KeyboardButton(text="✅ Завершить и отправить"), KeyboardButton(text="🗑 Удалить все треки")]], resize_keyboard=True)
owner_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Новый трек")],[KeyboardButton(text="📋 Мои треки"), KeyboardButton(text="📊 Мои треки (Excel)")],[KeyboardButton(text="Редактировать профиль"), KeyboardButton(text="Удалить трек")],[KeyboardButton(text="💰 Конвертер валют"), KeyboardButton(text="🚚 Калькулятор доставки")],[KeyboardButton(text="✅ Завершить и отправить"), KeyboardButton(text="🗑 Удалить все треки")],[KeyboardButton(text="📢 Сделать рассылку")]], resize_keyboard=True)
cancel_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Отмена")]], resize_keyboard=True)
group_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📦 Загрузить трек", url="https://t.me/little_Bro_track_bot")]])

class ProfileForm(StatesGroup):
    fullname = State(); phone = State(); address = State()
class TrackForm(StatesGroup):
    track = State(); product = State(); price = State(); qtype = State(); qty = State()
class DeleteTrackForm(StatesGroup):
    ids = State()
class CurrencyForm(StatesGroup):
    amount = State()
class CalcForm(StatesGroup):
    city = State(); weight = State()
class ConfirmDeleteAllForm(StatesGroup):
    confirm = State()

def now(): return datetime.now(timezone.utc) + timedelta(hours=3)
def get_profile(uid):
    try:
        res = supabase.table("users").select("full_name, phone, address").eq("user_id", uid).execute()
        return res.data[0] if res.data else None
    except: return None
def save_profile(uid, uname, name, phone, addr):
    supabase.table("users").upsert({"user_id": uid, "username": uname, "full_name": name, "phone": phone, "address": addr, "created_at": now().isoformat()}).execute()
def add_track(uid, track, prod, p_cny, p_usd, p_byn, qtype, qty):
    supabase.table("tracks").insert({"user_id": uid, "track_number": track, "product_name": prod, "price_cny": p_cny, "price_usd": p_usd, "price_byn": p_byn, "quantity": qty, "quantity_type": qtype, "created_at": now().isoformat()}).execute()
def get_tracks(uid):
    try:
        res = supabase.table("tracks").select("*").eq("user_id", uid).order("created_at", desc=True).execute()
        return res.data
    except: return []
def del_track(tid, uid): supabase.table("tracks").delete().eq("id", tid).eq("user_id", uid).execute()
def del_all(uid): supabase.table("tracks").delete().eq("user_id", uid).execute()
def sum_cny(uid): return round(sum(t["price_cny"]*t["quantity"] for t in get_tracks(uid)),2) if get_tracks(uid) else 0
def sum_usd(uid): return round(sum((t.get("price_usd") or 0)*t["quantity"] for t in get_tracks(uid)),2) if get_tracks(uid) else 0
def sum_byn(uid): return round(sum((t.get("price_byn") or 0)*t["quantity"] for t in get_tracks(uid)),2) if get_tracks(uid) else 0
def total_qty(uid): return sum(t["quantity"] for t in get_tracks(uid)) if get_tracks(uid) else 0

async def get_rates():
    usd, cny = 3.2, 0.45
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.nbrb.by/exrates/rates/USD?parammode=2", timeout=10) as r:
                if r.status == 200: usd = (await r.json()).get("Cur_OfficialRate") or usd
            async with s.get("https://api.nbrb.by/exrates/rates/CNY?parammode=2", timeout=10) as r:
                if r.status == 200:
                    d = await r.json()
                    cny = (d.get("Cur_OfficialRate") or 4.5) / d.get("Cur_Scale", 1)
    except: pass
    return usd, cny

def make_excel(tracks, name, phone, addr, uid):
    wb = Workbook(); ws = wb.active; ws.title = "Треки"
    hdrs = ["№", "Трек", "Товар", "CNY", "USD", "BYN", "Кол-во", "Ед.", "Дата"]
    for c, h in enumerate(hdrs, 1): ws.cell(row=1, column=c, value=h).font = Font(bold=True)
    for i, t in enumerate(tracks, 2):
        ws.cell(row=i, column=1, value=i-1); ws.cell(row=i, column=2, value=t["track_number"]); ws.cell(row=i, column=3, value=t["product_name"])
        ws.cell(row=i, column=4, value=float(t["price_cny"])); ws.cell(row=i, column=5, value=float(t.get("price_usd") or 0)); ws.cell(row=i, column=6, value=float(t.get("price_byn") or 0))
        ws.cell(row=i, column=7, value=int(t["quantity"])); ws.cell(row=i, column=8, value=t["quantity_type"]); ws.cell(row=i, column=9, value=t["created_at"][:19])
    last = len(tracks) + 2
    ws.cell(row=last, column=1, value="Всего:"); ws.cell(row=last, column=2, value=len(tracks))
    ws.cell(row=last+1, column=1, value="Кол-во:"); ws.cell(row=last+1, column=2, value=total_qty(uid))
    ws.cell(row=last+2, column=7, value="CNY:"); ws.cell(row=last+2, column=8, value=f"{sum_cny(uid):.2f}")
    ws.cell(row=last+3, column=7, value="USD:"); ws.cell(row=last+3, column=8, value=f"{sum_usd(uid):.2f}")
    ws.cell(row=last+4, column=7, value="BYN:"); ws.cell(row=last+4, column=8, value=f"{sum_byn(uid):.2f}")
    ws.cell(row=last+6, column=1, value=f"ФИО: {name}"); ws.cell(row=last+7, column=1, value=f"Тел: {phone}"); ws.cell(row=last+8, column=1, value=f"Адрес: {addr}")
    for c in range(1, 10): ws.column_dimensions[chr(64+c)].width = 18
    out = BytesIO(); wb.save(out); out.seek(0); return out

def make_packing(tracks):
    wb = Workbook(); ws = wb.active; ws.title = "Упаковочный лист"
    ws.cell(row=1, column=1, value="АРТИКУЛ").font = Font(bold=True); ws.cell(row=2, column=1, value="картинка").font = Font(bold=True)
    for c, h in enumerate(["Наименование", "Количество", "цена за единицу в долларах", "сумма в долларах"], 2):
        ws.cell(row=2, column=c, value=h).font = Font(bold=True)
    r = 3
    for t in tracks:
        ws.cell(row=r, column=2, value=t["product_name"]); ws.cell(row=r, column=3, value=int(t["quantity"]))
        ws.cell(row=r, column=4, value=float(t.get("price_usd") or 0)); ws.cell(row=r, column=5, value=f"=C{r}*D{r}"); r += 1
    ws.cell(row=r, column=4, value="ИТОГО:").font = Font(bold=True); ws.cell(row=r, column=5, value=f"=SUM(E3:E{r-1})").font = Font(bold=True)
    for c, w in zip("ABCDE", [12, 35, 12, 22, 18]): ws.column_dimensions[c].width = w
    out = BytesIO(); wb.save(out); out.seek(0); return out

def make_inventory(tracks, name, phone, addr):
    wb = Workbook(); ws = wb.active; ws.title = "Опись"
    ws.cell(row=1, column=1, value="СОПРОВОДИТЕЛЬНОЕ ПИСЬМО").font = Font(bold=True, size=14); ws.merge_cells('A1:E1')
    ws.cell(row=3, column=1, value=f"Я, {name}, к.т. {phone}"); ws.merge_cells('A3:E3')
    ws.cell(row=4, column=1, value="отправляю груз НОМЕР ГРУЗА, весом ВЕС кг., мест: МЕСТ"); ws.merge_cells('A4:E4')
    ws.cell(row=5, column=1, value="из: РФ, МКАД, 19-й километр, вл20с1, Москва"); ws.merge_cells('A5:E5')
    ws.cell(row=6, column=1, value=f"по адресу: {addr}"); ws.merge_cells('A6:E6')
    ws.cell(row=7, column=1, value=f"для получателя: {name}, к.т. {phone}"); ws.merge_cells('A7:E7')
    ws.cell(row=9, column=1, value="К данному письму прилагаю опись груза...").alignment = Alignment(wrap_text=True); ws.merge_cells('A9:E9'); ws.row_dimensions[9].height = 40
    ws.cell(row=11, column=1, value="Опись:").font = Font(bold=True)
    for c, h in enumerate(["№", "Наименование", "Кол-во", "Ед.", "Стоимость (BYN)"], 1): ws.cell(row=12, column=c, value=h).font = Font(bold=True)
    r = 13
    for i, t in enumerate(tracks, 1):
        ws.cell(row=r, column=1, value=i); ws.cell(row=r, column=2, value=t["product_name"]); ws.cell(row=r, column=3, value=int(t["quantity"]))
        ws.cell(row=r, column=4, value=t["quantity_type"]); ws.cell(row=r, column=5, value=float(t.get("price_byn") or 0)); r += 1
    ws.cell(row=r, column=4, value="Итого:").font = Font(bold=True); ws.cell(row=r, column=5, value=f"=SUM(E13:E{r-1})").font = Font(bold=True)
    for c, w in zip("ABCDE", [6, 35, 15, 18, 20]): ws.column_dimensions[c].width = w
    out = BytesIO(); wb.save(out); out.seek(0); return out

@dp.message(Command("start"))
async def start(msg: types.Message, state: FSMContext):
    if msg.chat.type in ["group", "supergroup"]:
        await msg.answer("📦 Загрузка треков", reply_markup=group_kb); return
    p = get_profile(msg.from_user.id)
    if p: await msg.answer(f"✅ Здравствуйте!\n{p['full_name']}\n{p['phone']}", reply_markup=owner_kb if msg.from_user.id == OWNER_ID else main_kb)
    else:
        await state.set_state(ProfileForm.fullname); await msg.answer("Введи ФИО:", reply_markup=cancel_kb)

@dp.message(ProfileForm.fullname)
async def p_name(msg: types.Message, state: FSMContext):
    if msg.text == "Отмена": await state.clear(); await msg.answer("Отменено", reply_markup=main_kb); return
    await state.update_data(name=msg.text); await state.set_state(ProfileForm.phone); await msg.answer("Введи телефон:", reply_markup=cancel_kb)

@dp.message(ProfileForm.phone)
async def p_phone(msg: types.Message, state: FSMContext):
    if msg.text == "Отмена": await state.clear(); await msg.answer("Отменено", reply_markup=main_kb); return
    await state.update_data(phone=msg.text); await state.set_state(ProfileForm.address); await msg.answer("Введи адрес:", reply_markup=cancel_kb)

@dp.message(ProfileForm.address)
async def p_addr(msg: types.Message, state: FSMContext):
    if msg.text == "Отмена": await state.clear(); await msg.answer("Отменено", reply_markup=main_kb); return
    d = await state.get_data(); save_profile(msg.from_user.id, msg.from_user.username or "", d["name"], d["phone"], msg.text)
    await state.clear(); await msg.answer("✅ Профиль сохранен!", reply_markup=owner_kb if msg.from_user.id == OWNER_ID else main_kb)

@dp.message(F.text == "Редактировать профиль")
async def edit_p(msg: types.Message, state: FSMContext):
    await state.set_state(ProfileForm.fullname); await msg.answer("Введи ФИО:", reply_markup=cancel_kb)

@dp.message(F.text == "Отмена")
async def cancel(msg: types.Message, state: FSMContext):
    await state.clear(); await msg.answer("Действие отменено", reply_markup=main_kb)

@dp.message(F.text == "Новый трек")
async def new_t(msg: types.Message, state: FSMContext):
    if not get_profile(msg.from_user.id): await msg.answer("Сначала /start"); return
    await state.set_state(TrackForm.track); await msg.answer("Введи трек-номер:", reply_markup=cancel_kb)

@dp.message(TrackForm.track)
async def t_track(msg: types.Message, state: FSMContext):
    if msg.text == "Отмена": await cancel(msg, state); return
    await state.update_data(track=msg.text); await state.set_state(TrackForm.product); await msg.answer("Введи товар:", reply_markup=cancel_kb)

@dp.message(TrackForm.product)
async def t_prod(msg: types.Message, state: FSMContext):
    if msg.text == "Отмена": await cancel(msg, state); return
    await state.update_data(prod=msg.text); await state.set_state(TrackForm.price); await msg.answer("Цена в юанях (CNY):", reply_markup=cancel_kb)

@dp.message(TrackForm.price)
async def t_price(msg: types.Message, state: FSMContext):
    if msg.text == "Отмена": await cancel(msg, state); return
    m = re.search(r"(\d+(?:[.,]\d+)?)", msg.text)
    if not m: await msg.answer("Введи число!"); return
    p_cny = float(m.group(1).replace(",", ".")); usd, cny = await get_rates()
    p_byn = round(p_cny * cny, 2); p_usd = round(p_byn / usd, 2)
    await state.update_data(cny=p_cny, usd=p_usd, byn=p_byn); await state.set_state(TrackForm.qtype)
    kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="шт"), KeyboardButton(text="пара"), KeyboardButton(text="упаковка")]], resize_keyboard=True)
    await msg.answer(f"Цена: {p_cny} CNY = {p_usd} USD = {p_byn} BYN\nЕдиница:", reply_markup=kb)

@dp.message(TrackForm.qtype)
async def t_qtype(msg: types.Message, state: FSMContext):
    if msg.text not in ["шт", "пара", "упаковка"]: await msg.answer("Выбери кнопку!"); return
    await state.update_data(qtype=msg.text); await state.set_state(TrackForm.qty); await msg.answer(f"Количество ({msg.text}):", reply_markup=cancel_kb)

@dp.message(TrackForm.qty)
async def t_qty(msg: types.Message, state: FSMContext):
    if msg.text == "Отмена": await cancel(msg, state); return
    try: q = int(msg.text)
    except: await msg.answer("Целое число!"); return
    d = await state.get_data(); add_track(msg.from_user.id, d["track"], d["prod"], d["cny"], d["usd"], d["byn"], d["qtype"], q)
    await state.clear(); await msg.answer("✅ Трек добавлен!", reply_markup=owner_kb if msg.from_user.id == OWNER_ID else main_kb)

@dp.message(F.text == "📋 Мои треки")
async def my_t(msg: types.Message):
    tracks = get_tracks(msg.from_user.id)
    if not tracks: await msg.answer("Нет треков."); return
    txt = "📦 Твои треки:\n\n"
    for i, t in enumerate(tracks, 1):
        txt += f"{i}. {t['track_number']}\n   {t['product_name']}\n   {t['price_cny']} CNY = {t.get('price_usd',0)} USD = {t.get('price_byn',0)} BYN\n   {t['quantity']} {t['quantity_type']}\n\n"
    txt += f"💰 Итого: {sum_cny(msg.from_user.id)} CNY ≈ {sum_usd(msg.from_user.id)} USD ≈ {sum_byn(msg.from_user.id)} BYN"
    await msg.answer(txt)

@dp.message(F.text == "Удалить трек")
async def del_t_start(msg: types.Message, state: FSMContext):
    tracks = get_tracks(msg.from_user.id)
    if not tracks: await msg.answer("Нет треков."); return
    txt = "Номера для удаления (через запятую):\n"
    for i, t in enumerate(tracks, 1): txt += f"{i}. {t['track_number']} - {t['product_name']}\n"
    await state.set_state(DeleteTrackForm.ids); await state.update_data(tracks=tracks); await msg.answer(txt, reply_markup=cancel_kb)

@dp.message(DeleteTrackForm.ids)
async def del_t(msg: types.Message, state: FSMContext):
    if msg.text == "Отмена": await cancel(msg, state); return
    d = await state.get_data(); tracks = d["tracks"]; deleted = 0
    for p in msg.text.replace(" ", "").split(","):
        try:
            idx = int(p) - 1
            if 0 <= idx < len(tracks): del_track(tracks[idx]["id"], msg.from_user.id); deleted += 1
        except: continue
    await msg.answer(f"Удалено: {deleted}", reply_markup=main_kb); await state.clear()

@dp.message(F.text == "🗑 Удалить все треки")
async def del_all_start(msg: types.Message, state: FSMContext):
    if not get_tracks(msg.from_user.id): await msg.answer("Нет треков."); return
    await state.set_state(ConfirmDeleteAllForm.confirm); await msg.answer("Удалить ВСЕ? Напиши ДА", reply_markup=cancel_kb)

@dp.message(ConfirmDeleteAllForm.confirm)
async def del_all_conf(msg: types.Message, state: FSMContext):
    if msg.text == "ДА": del_all(msg.from_user.id); await msg.answer("✅ Все удалены.", reply_markup=main_kb)
    else: await msg.answer("Отменено.", reply_markup=main_kb)
    await state.clear()

@dp.message(F.text == "📊 Мои треки (Excel)")
async def exp_excel(msg: types.Message):
    tracks = get_tracks(msg.from_user.id); p = get_profile(msg.from_user.id)
    if not tracks or not p: await msg.answer("Нет данных."); return
    f = make_excel(tracks, p["full_name"], p["phone"], p["address"], msg.from_user.id)
    await msg.answer_document(BufferedInputFile(f.getvalue(), filename="tracks.xlsx"), caption="Ваши треки")

@dp.message(F.text == "🚚 Калькулятор доставки")
async def calc_s(msg: types.Message, state: FSMContext):
    await state.set_state(CalcForm.city); await msg.answer("Город:", reply_markup=ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Минск"), KeyboardButton(text="Лида")]], resize_keyboard=True))

@dp.message(CalcForm.city)
async def calc_w(msg: types.Message, state: FSMContext):
    if msg.text not in ["Минск", "Лида"]: await msg.answer("Кнопкой!"); return
    await state.update_data(city=msg.text); await state.set_state(CalcForm.weight); await msg.answer("Вес (кг):", reply_markup=cancel_kb)

@dp.message(CalcForm.weight)
async def calc_r(msg: types.Message, state: FSMContext):
    try: w = float(msg.text.replace(',', '.'))
    except: await msg.answer("Число!"); return
    d = await state.get_data(); city = d["city"]
    cost = w * float(os.getenv("DELIVERY_RATE", "12.0")) + w * float(os.getenv("HANDLING_RATE", "1.6")) + (w * float(os.getenv("LIDA_EXTRA_RATE", "0.8")) if city == "Лида" else 0) + float(os.getenv("FIXED_FEE", "10.0"))
    await msg.answer(f"Доставка до {city}: {cost:.2f} руб.", reply_markup=main_kb); await state.clear()

@dp.message(F.text == "💰 Конвертер валют")
async def curr_s(msg: types.Message, state: FSMContext):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="CNY → USD → BYN", callback_data="cny_all")],[InlineKeyboardButton(text="USD → BYN", callback_data="usd2byn")],[InlineKeyboardButton(text="BYN → USD", callback_data="byn2usd")],[InlineKeyboardButton(text="❌ Отмена", callback_data="cancel_curr")]])
    await msg.answer("Выбери операцию:", reply_markup=kb)

@dp.callback_query(lambda c: c.data in ["cny_all", "usd2byn", "byn2usd", "cancel_curr"])
async def curr_cb(call: CallbackQuery, state: FSMContext):
    await call.answer()
    if call.data == "cancel_curr": await call.message.edit_text("Отменено"); return
    await state.update_data(act=call.data); await state.set_state(CurrencyForm.amount); await call.message.edit_text("Введи сумму:")

@dp.message(CurrencyForm.amount)
async def curr_amt(msg: types.Message, state: FSMContext):
    try: amt = float(msg.text.replace(',', '.'))
    except: await msg.answer("Число!"); return
    d = await state.get_data(); act = d["act"]; usd, cny = await get_rates()
    if act == "cny_all": await msg.answer(f"{amt} CNY = {amt * (cny/usd):.2f} USD = {amt * cny:.2f} BYN")
    elif act == "usd2byn": await msg.answer(f"{amt} USD = {amt * usd:.2f} BYN")
    elif act == "byn2usd": await msg.answer(f"{amt} BYN = {amt / usd:.2f} USD")
    await state.clear()

@dp.message(F.text == "✅ Завершить и отправить")
async def finish(msg: types.Message):
    uid = msg.from_user.id; tracks = get_tracks(uid); p = get_profile(uid)
    if not tracks or not p: await msg.answer("Нет данных."); return
    
    txt = f"📦 Новый заказ от {p['full_name']}\n👤 @{msg.from_user.username or 'нет'}\n📞 {p['phone']}\n🏠 {p['address']}\n\n"
    txt += f"📊 Всего треков: {len(tracks)}\n💰 Итого: {sum_cny(uid)} CNY ≈ {sum_usd(uid)} USD ≈ {sum_byn(uid)} BYN"
    
    f1, f2, f3 = make_excel(tracks, p['full_name'], p['phone'], p['address'], uid), make_packing(tracks), make_inventory(tracks, p['full_name'], p['phone'], p['address'])
    try:
        await bot.send_message(OWNER_ID, txt)
        await bot.send_document(OWNER_ID, BufferedInputFile(f1.getvalue(), filename=f"tracks_{uid}.xlsx"), caption=f"Excel от {p['full_name']}")
        await bot.send_document(OWNER_ID, BufferedInputFile(f2.getvalue(), filename=f"packing_{uid}.xlsx"), caption=f"📦 Упаковочный лист")
        await bot.send_document(OWNER_ID, BufferedInputFile(f3.getvalue(), filename=f"inventory_{uid}.xlsx"), caption=f"📋 Опись")
        await msg.answer("✅ Треки отправлены!")
    except Exception as e: await msg.answer(f"Ошибка: {e}")

@dp.message(F.text == "📢 Сделать рассылку")
async def broadcast(msg: types.Message): await msg.answer("Функция в разработке.")

async def handle_web(request): return web.Response(text="Bot is running")
async def start_web():
    app = web.Application(); app.router.add_get("/", handle_web)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8000); await site.start()

async def run_bot():
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

async def main(): await asyncio.gather(start_web(), run_bot())

if __name__ == "__main__": asyncio.run(main())
