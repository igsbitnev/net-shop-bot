import os
import asyncio
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import Message, KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
import aiosqlite

load_dotenv()
BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_ID = int(os.getenv('ADMIN_ID', '0')) if os.getenv('ADMIN_ID') else None
if not BOT_TOKEN:
    raise SystemExit('Set BOT_TOKEN in .env (see config.example.env)')

DB_PATH = 'db.sqlite'

class OrderStates(StatesGroup):
    browsing = State()
    awaiting_cart = State()
    reserving = State()
    waiting_reservation_date = State()
    waiting_reservation_time = State()
    waiting_reservation_people = State()

MENU = {
    'Закуски': [
        ('Брускетта', 320),
        ('Салат Цезарь', 450),
    ],
    'Основные': [
        ('Стейк рибай', 1200),
        ('Лосось гриль', 980),
    ],
    'Десерты': [
        ('Тирамису', 380),
        ('Панна котта', 340),
    ],
}

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            tg_id INTEGER UNIQUE,
            username TEXT,
            points INTEGER DEFAULT 0
        );''')
        await db.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            item TEXT,
            quantity INTEGER DEFAULT 1,
            total INTEGER DEFAULT 0,
            status TEXT DEFAULT 'new',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            latitude REAL,
            longitude REAL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );''')
        await db.execute('''
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT,
            time TEXT,
            people INTEGER,
            status TEXT DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );''')
        await db.commit()

async def get_or_create_user(conn, tg_id, username):
    cur = await conn.execute('SELECT id, points FROM users WHERE tg_id = ?', (tg_id,))
    row = await cur.fetchone()
    if row:
        return row[0], row[1]
    await conn.execute('INSERT INTO users (tg_id, username) VALUES (?, ?)', (tg_id, username))
    await conn.commit()
    cur = await conn.execute('SELECT id, points FROM users WHERE tg_id = ?', (tg_id,))
    row = await cur.fetchone()
    return row[0], row[1]

async def create_order(conn, user_id, item, quantity=1, total=0, lat=None, lon=None):
    await conn.execute('INSERT INTO orders (user_id, item, quantity, total, latitude, longitude) VALUES (?, ?, ?, ?, ?, ?)', (user_id, item, quantity, total, lat, lon))
    await conn.commit()
    cur = await conn.execute('SELECT last_insert_rowid()')
    r = await cur.fetchone()
    return r[0][0] if r else None

async def create_reservation(conn, user_id, date, time, people):
    await conn.execute('INSERT INTO reservations (user_id, date, time, people) VALUES (?, ?, ?, ?)', (user_id, date, time, people))
    await conn.commit()
    cur = await conn.execute('SELECT last_insert_rowid()')
    r = await cur.fetchone()
    return r[0][0] if r else None

async def add_points(conn, user_id, points=10):
    await conn.execute('UPDATE users SET points = points + ? WHERE id = ?', (points, user_id))
    await conn.commit()

async def list_orders(conn, limit=50):
    cur = await conn.execute('SELECT orders.id, users.tg_id, users.username, orders.item, orders.quantity, orders.status, orders.created_at FROM orders JOIN users ON users.id = orders.user_id ORDER BY orders.created_at DESC LIMIT ?', (limit,))
    return await cur.fetchall()

async def list_reservations(conn, limit=50):
    cur = await conn.execute('SELECT reservations.id, users.tg_id, users.username, reservations.date, reservations.time, reservations.people, reservations.status FROM reservations JOIN users ON users.id = reservations.user_id ORDER BY reservations.created_at DESC LIMIT ?', (limit,))
    return await cur.fetchall()

async def main():
    await init_db()
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()

    main_kb = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text='🍽 Меню')],
            [KeyboardButton(text='🪑 Забронировать')],
            [KeyboardButton(text='🧾 Мои баллы')],
        ],
        resize_keyboard=True
    )

    @dp.message(Command(commands=['start']))
    async def cmd_start(message: Message, state: FSMContext):
        async with aiosqlite.connect(DB_PATH) as conn:
            await get_or_create_user(conn, message.from_user.id, message.from_user.username)
        await message.answer('Привет! Я помогу оформить заказ или забронировать столик в вашем ресторане.', reply_markup=main_kb)
        await state.clear()

    @dp.message(lambda msg: msg.text == '🍽 Меню')
    async def show_categories(message: Message, state: FSMContext):
        kb = InlineKeyboardBuilder()
        for cat in MENU.keys():
            kb.button(text=cat, callback_data=f'cat:{cat}')
        kb.adjust(1)
        await message.answer('Выберите категорию:', reply_markup=kb.as_markup())

    @dp.callback_query(lambda cb: cb.data and cb.data.startswith('cat:'))
    async def show_items(cb: types.CallbackQuery):
        cat = cb.data.split(':',1)[1]
        kb = InlineKeyboardBuilder()
        for name, price in MENU.get(cat, []):
            kb.button(text=f'{name} — {price}₽', callback_data=f'item:{name}:{price}')
        kb.adjust(1)
        await cb.message.answer(f'Категория: {cat}', reply_markup=kb.as_markup())
        await cb.answer()

    @dp.callback_query(lambda cb: cb.data and cb.data.startswith('item:'))
    async def choose_item(cb: types.CallbackQuery, state: FSMContext):
        _, name, price = cb.data.split(':',2)
        await state.update_data(chosen_item=name, chosen_price=int(price))
        await state.set_state(OrderStates.awaiting_cart)
        await cb.message.answer(f'Добавлено в корзину: {name} — {price}₽. Нажмите ещё раз на «🍽 Меню» чтобы добавить новое или /checkout для оформления.')
        await cb.answer()

    @dp.message(Command(commands=['checkout']))
    async def checkout(message: Message, state: FSMContext):
        data = await state.get_data()
        item = data.get('chosen_item')
        price = data.get('chosen_price', 0)
        if not item:
            await message.answer('Корзина пуста. Добавьте блюда через меню.')
            return
        async with aiosqlite.connect(DB_PATH) as conn:
            uid, _ = await get_or_create_user(conn, message.from_user.id, message.from_user.username)
            order_id = await create_order(conn, uid, item, quantity=1, total=price)
            await add_points(conn, uid, 10)
        await message.answer(f'Заказ #{order_id} создан. Спасибо! Вы получили +10 баллов.')
        await state.clear()

    @dp.message(lambda msg: msg.text == '🪑 Забронировать')
    async def start_reservation(message: Message, state: FSMContext):
        await state.set_state(OrderStates.waiting_reservation_date)
        await message.answer('Введите дату бронирования в формате YYYY-MM-DD (пример: 2025-12-31):', reply_markup=ReplyKeyboardRemove())

    @dp.message(lambda msg: True)
    async def reservation_flow_and_fallback(message: Message, state: FSMContext):
        state_name = await state.get_state()
        if state_name == OrderStates.waiting_reservation_date.state:
            await state.update_data(res_date=message.text)
            await state.set_state(OrderStates.waiting_reservation_time)
            await message.answer('Введите время бронирования (HH:MM):')
            return
        if state_name == OrderStates.waiting_reservation_time.state:
            await state.update_data(res_time=message.text)
            await state.set_state(OrderStates.waiting_reservation_people)
            await message.answer('Введите количество человек:')
            return
        if state_name == OrderStates.waiting_reservation_people.state:
            data = await state.get_data()
            date = data.get('res_date')
            time = data.get('res_time')
            people = int(message.text or 1)
            async with aiosqlite.connect(DB_PATH) as conn:
                uid, _ = await get_or_create_user(conn, message.from_user.id, message.from_user.username)
                res_id = await create_reservation(conn, uid, date, time, people)
                await add_points(conn, uid, 5)
            await message.answer(f'Бронирование #{res_id} создано: {date} {time}, {people} чел. Спасибо! +5 баллов.', reply_markup=ReplyKeyboardRemove())
            await state.clear()
            return
        # Fallback for other messages
        await message.answer('Используйте меню: /start или кнопки. Для оформления заказа используйте /checkout.', reply_markup=main_kb)

    @dp.message(lambda msg: msg.text == '🧾 Мои баллы')
    async def my_points(message: Message):
        async with aiosqlite.connect(DB_PATH) as conn:
            cur = await conn.execute('SELECT points FROM users WHERE tg_id = ?', (message.from_user.id,))
            row = await cur.fetchone()
            points = row[0] if row else 0
        await message.answer(f'У вас {points} баллов.')

    @dp.message(Command(commands=['admin_orders']))
    async def admin_orders(message: Message):
        if ADMIN_ID and message.from_user.id != ADMIN_ID:
            await message.answer('Недостаточно прав.')
            return
        async with aiosqlite.connect(DB_PATH) as conn:
            orders = await list_orders(conn, limit=50)
            reservations = await list_reservations(conn, limit=50)
        text = ''
        if orders:
            text += 'Последние заказы:\n'
            for r in orders:
                text += f'#{r[0]} — @{r[2]}({r[1]}): {r[3]} x{r[4]} — {r[5]} — {r[6]}\n'
        else:
            text += 'Заказов нет.\n'
        if reservations:
            text += '\nПоследние бронирования:\n'
            for r in reservations:
                text += f'#{r[0]} — @{r[2]}({r[1]}): {r[3]} {r[4]} — {r[5]} чел — {r[6]}\n'
        await message.answer(text or 'Данных нет.')

    @dp.message(Command(commands=['help']))
    async def cmd_help(message: Message):
        await message.answer('/start — запустить бота\n/checkout — оформить текущую позицию в корзине\n/admin_orders — (админ) список заказов и бронирований.')

    try:
        print('Bot polling started...')
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == '__main__':
    asyncio.run(main())
