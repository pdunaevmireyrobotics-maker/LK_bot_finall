import asyncio
import logging
from aiogram import Bot, Dispatcher, types, F, Router
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
import datetime
import os
import json
import glob

# Импортируем из отдельных файлов
from config import Config
from categories import CATEGORIES_DATA, PEOPLE_ITEMS, ONLINE_COMBO_ITEMS, INVITATION_ITEMS
from models import SessionStates

# Создание необходимых папок
from config import Config
Config.create_folders()

# ====== ЛОГИРОВАНИЕ ======
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler('bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ====== ПРОВЕРКА КОНФИГУРАЦИИ ======
if not Config.BOT_TOKEN:
    logger.error("Токен бота не установлен!")
    exit(1)

logger.info(f"Бот инициализирован с токеном: {Config.BOT_TOKEN[:10]}...")

# ====== НАСТРОЙКА БОТА ======
bot = Bot(token=Config.BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)
router = Router()
dp.include_router(router)

# ====== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ======
def format_currency(amount):
    """Форматирование суммы с разделителями тысяч"""
    return f"{amount:,.0f}₸".replace(",", ".")

def validate_amount(text: str) -> tuple[bool, int | None]:
    """Валидация числового ввода (без проверки на положительное)"""
    try:
        amount = int(text)
        return True, amount
    except ValueError:
        return False, None

async def safe_edit_message(message, text: str, reply_markup=None):
    """Безопасное редактирование сообщения с fallback"""
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except Exception as e:
        logger.warning(f"Не удалось изменить сообщение: {e}")
        await message.answer(text, reply_markup=reply_markup)

def save_session_report(session_data: dict) -> str:
    """Сохранение отчета о смене в файл"""
    try:
        filename = f"{Config.CLOSED_SESSIONS_FOLDER}/смена_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        
        report_content = f"""Астана, «Космопарк 01»
Смена от: {session_data['open_time'].strftime('%d.%m.%Y %H:%M')}
Закрыта: {datetime.datetime.now().strftime('%d.%m.%Y %H:%M')}
Длительность: {str(session_data['close_time'] - session_data['open_time']).split('.')[0]}

{session_data['combined_report']}

{session_data['metrics_report']}

{session_data['receipts_report']}
"""
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(report_content)
        
        logger.info(f"Отчет сохранен в файл: {filename}")
        return filename
    except Exception as e:
        logger.error(f"Ошибка при сохранении отчета: {e}")
        return None

def get_closed_sessions():
    """Получение списка закрытых смен за последние 30 дней"""
    sessions = []
    try:
        # Получаем все txt файлы в папке закрытых смен
        pattern = f"{Config.CLOSED_SESSIONS_FOLDER}/смена_*.txt"
        files = glob.glob(pattern)
        
        # Фильтруем файлы за последние 30 дней
        thirty_days_ago = datetime.datetime.now() - datetime.timedelta(days=30)
        
        for filepath in files:
            try:
                filename = os.path.basename(filepath)
                # Извлекаем дату из имени файла
                date_str = filename.replace('смена_', '').replace('.txt', '')[:8]
                file_date = datetime.datetime.strptime(date_str, '%Y%m%d')
                
                if file_date >= thirty_days_ago:
                    sessions.append({
                        'filename': filename,
                        'filepath': filepath,
                        'date': file_date,
                        'display_date': file_date.strftime('%d.%m.%Y')
                    })
            except Exception as e:
                logger.warning(f"Ошибка обработки файла {filepath}: {e}")
        
        # Сортируем по дате (новые сверху)
        sessions.sort(key=lambda x: x['date'], reverse=True)
        return sessions
        
    except Exception as e:
        logger.error(f"Ошибка при получении закрытых смен: {e}")
        return []

# ====== ДАННЫЕ СМЕНЫ И БЭКАПЫ ======
class SessionManager:
    def __init__(self):
        self.is_open = False
        self.sales = []
        self.cart = []
        self.mixed_amount = None
        self.custom_item_temp = None
        self.item_mapping = {}
        self.open_time = None
        self.last_report_type = None
        self.exchange_cash = 0
        self.auto_save_task = None
    
    def reset(self):
        self.is_open = False
        self.sales = []
        self.cart = []
        self.mixed_amount = None
        self.custom_item_temp = None
        self.open_time = None
        self.last_report_type = None
        self.exchange_cash = 0
    
    def get_cart_total(self):
        """Возвращает общую сумму корзины"""
        return sum(item["price"] for item in self.cart)
    
    def add_sale(self, items, cash_amount=0, cashless_amount=0):
        """Добавление продажи"""
        sale = {
            "id": len(self.sales) + 1,
            "items": items.copy(),
            "cash_amount": cash_amount,
            "cashless_amount": cashless_amount,
            "time": datetime.datetime.now(),
            "total": cash_amount + cashless_amount
        }
        self.sales.append(sale)
    
    def save_backup(self):
        """Сохранение резервной копии открытой смены"""
        try:
            if self.is_open:
                backup_data = {
                    'is_open': self.is_open,
                    'sales': self.sales,
                    'exchange_cash': self.exchange_cash,
                    'open_time': self.open_time.isoformat() if self.open_time else None,
                    'last_backup': datetime.datetime.now().isoformat()
                }
                
                # Создаем папку для бэкапов если её нет
                os.makedirs(Config.BACKUP_FOLDER, exist_ok=True)
                
                backup_file = f"{Config.BACKUP_FOLDER}/session_backup.json"
                with open(backup_file, 'w', encoding='utf-8') as f:
                    json.dump(backup_data, f, ensure_ascii=False, indent=2, default=str)
                
                logger.info("✅ Бэкап смены сохранен")
                return True
                
        except Exception as e:
            logger.error(f"❌ Ошибка при сохранении бэкапа: {e}")
            return False
    
    def load_backup(self):
        """Загрузка последней резервной копии"""
        try:
            backup_file = f"{Config.BACKUP_FOLDER}/session_backup.json"
            if os.path.exists(backup_file):
                with open(backup_file, 'r', encoding='utf-8') as f:
                    backup_data = json.load(f)
                
                # Конвертируем время из строки обратно в datetime
                if backup_data.get('open_time'):
                    backup_data['open_time'] = datetime.datetime.fromisoformat(backup_data['open_time'])
                
                logger.info("✅ Бэкап смены загружен")
                return backup_data
                
        except Exception as e:
            logger.error(f"❌ Ошибка при загрузке бэкапа: {e}")
        
        return None
    
    def restore_session(self):
        """Восстановление сессии из бэкапа"""
        backup_data = self.load_backup()
        if backup_data and backup_data.get('is_open'):
            self.is_open = True
            self.sales = backup_data.get('sales', [])
            self.exchange_cash = backup_data.get('exchange_cash', 0)
            self.open_time = backup_data.get('open_time')
            
            last_backup = backup_data.get('last_backup', 'неизвестно')
            logger.info(f"🔄 Восстановлена открытая смена из бэкапа от {last_backup}")
            return True
        
        return False
    
    def delete_backup(self):
        """Удаление файла бэкапа (при корректном закрытии смены)"""
        try:
            backup_file = f"{Config.BACKUP_FOLDER}/session_backup.json"
            if os.path.exists(backup_file):
                os.remove(backup_file)
                logger.info("🗑️ Бэкап смены удален")
                return True
        except Exception as e:
            logger.error(f"❌ Ошибка при удалении бэкапа: {e}")
        
        return False
    
    async def start_auto_save(self, interval_seconds=120):
        """Запуск автоматического сохранения"""
        async def auto_save_loop():
            while True:
                await asyncio.sleep(interval_seconds)
                if self.is_open:
                    self.save_backup()
                    logger.debug("🔄 Автосохранение выполнено")
        
        self.auto_save_task = asyncio.create_task(auto_save_loop())
        logger.info(f"🔄 Автосохранение запущено (интервал: {interval_seconds}сек)")
    
    def stop_auto_save(self):
        """Остановка автоматического сохранения"""
        if self.auto_save_task:
            self.auto_save_task.cancel()
            logger.info("🛑 Автосохранение остановлено")

session = SessionManager()

# ====== ИНИЦИАЛИЗАЦИЯ КАТЕГОРИЙ ======
CATEGORIES_IDS = {}
ITEMS_MAPPING = {}

for i, (category_name, items) in enumerate(CATEGORIES_DATA.items()):
    cat_id = f"cat{i}"
    CATEGORIES_IDS[cat_id] = category_name
    
    for j, (item_name, price) in enumerate(items.items()):
        item_id = f"item{i}_{j}"
        ITEMS_MAPPING[item_id] = {
            "name": item_name,
            "price": price,
            "category": category_name
        }

session.item_mapping = ITEMS_MAPPING

# ====== ИНЛАЙН КЛАВИАТУРЫ ======
def get_main_kb():
    buttons = [
        [InlineKeyboardButton(text="🎬 Открыть смену", callback_data="open_shift")],
        [InlineKeyboardButton(text="➕ Продажа", callback_data="start_sale")],
        [InlineKeyboardButton(text="💵 Внести размен", callback_data="add_exchange")],
        [InlineKeyboardButton(text="📊 Отчёт", callback_data="show_report")],
        [InlineKeyboardButton(text="📋 Архив смен", callback_data="session_archive")],
    ]
    if Config.ADMIN_USERNAME:
        buttons.append([InlineKeyboardButton(text="↩️ Возврат", callback_data="refund_menu")])
    buttons.append([InlineKeyboardButton(text="✅ Закрыть смену", callback_data="close_shift")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_categories_kb():
    buttons = []
    for cat_id, cat_name in CATEGORIES_IDS.items():
        buttons.append([InlineKeyboardButton(text=cat_name, callback_data=f"cat_{cat_id}")])
    buttons.append([InlineKeyboardButton(text="🛒 Корзина", callback_data="show_cart")])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_items_kb(category_id: str):
    if category_id not in CATEGORIES_IDS:
        return get_categories_kb()
    
    category_name = CATEGORIES_IDS[category_id]
    buttons = []
    
    for item_id, item_data in ITEMS_MAPPING.items():
        if item_data["category"] == category_name:
            if item_data["price"] == "custom":
                price_display = "⚡ Задать название и цену"
            elif item_data["price"] == 0:
                price_display = "БЕСПЛАТНО"
            else:
                price_display = f"{format_currency(item_data['price'])}"
                
            buttons.append([InlineKeyboardButton(
                text=f"{item_data['name']} - {price_display}", 
                callback_data=f"item_{item_id}"
            )])
    
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_categories")])
    buttons.append([InlineKeyboardButton(text="🛒 Корзина", callback_data="show_cart")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_cart_kb():
    buttons = [
        [InlineKeyboardButton(text="💵 Оплата наличными", callback_data="payment_cash")],
        [InlineKeyboardButton(text="💳 Оплата картой", callback_data="payment_card")],
        [InlineKeyboardButton(text="💱 Смешанная оплата", callback_data="payment_mixed")],
        [InlineKeyboardButton(text="🗑 Удалить позиции", callback_data="remove_items")],
        [InlineKeyboardButton(text="🔄 Продолжить покупки", callback_data="back_to_categories")],
        [InlineKeyboardButton(text="🗑 Очистить корзину", callback_data="clear_cart")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_remove_items_kb():
    buttons = []
    for i, item in enumerate(session.cart, 1):
        price_display = "БЕСПЛАТНО" if item["price"] == 0 else f"{format_currency(item['price'])}"
        buttons.append([
            InlineKeyboardButton(
                text=f"❌ {i}. {item['item']} - {price_display}", 
                callback_data=f"remove_{i-1}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад к корзине", callback_data="show_cart")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_report_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🧾 Детализация по чекам", callback_data="report_receipts")],
        [InlineKeyboardButton(text="📈 Отчёт по показателям", callback_data="report_metrics")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])

def get_refund_kb():
    buttons = []
    for sale in session.sales[-20:]:
        time_str = sale["time"].strftime("%H:%M") if isinstance(sale["time"], datetime.datetime) else sale["time"][11:16]
        buttons.append([
            InlineKeyboardButton(
                text=f"🧾 Чек #{sale['id']} ({time_str}) - {format_currency(sale['total'])}",
                callback_data=f"refund_{sale['id']}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_session_archive_kb():
    """Клавиатура для архива смен"""
    sessions = get_closed_sessions()
    buttons = []
    for session_data in sessions[:10]:  # Показываем последние 10 смен
        buttons.append([
            InlineKeyboardButton(
                text=f"📅 {session_data['display_date']}",
                callback_data=f"archive_{session_data['filename']}"
            )
        ])
    buttons.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

# ====== ФУНКЦИИ ОТЧЕТОВ ======
def build_combined_report() -> str:
    """Объединенный отчет: общая статистика + категории"""
    total_cash_sales = sum(sale["cash_amount"] for sale in session.sales)
    total_cashless = sum(sale["cashless_amount"] for sale in session.sales)
    total_revenue = total_cash_sales + total_cashless
    total_items = sum(len(sale["items"]) for sale in session.sales)
    
    # Статистика по категориям
    category_stats = {}
    for sale in session.sales:
        for item in sale["items"]:
            category = item["category"]
            item_name = item["item"]
            price = item["price"]
            
            if category not in category_stats:
                category_stats[category] = {"items": {}, "total_count": 0, "total_revenue": 0}
            
            if item_name not in category_stats[category]["items"]:
                category_stats[category]["items"][item_name] = {"count": 0, "revenue": 0}
            
            category_stats[category]["items"][item_name]["count"] += 1
            category_stats[category]["items"][item_name]["revenue"] += price
            category_stats[category]["total_count"] += 1
            category_stats[category]["total_revenue"] += price
    
    report_text = f"""📊 ОБЩИЙ ОТЧЁТ С КАТЕГОРИЯМИ

Астана, «Космопарк 01»
{datetime.datetime.now().strftime('Сегодня %d.%m.%Y')}
С 10:00 до {datetime.datetime.now().strftime('%H:%M')}

💵 Наличные: {format_currency(total_cash_sales)}
💳 Безналичные: {format_currency(total_cashless)}
💰 Общая выручка: {format_currency(total_revenue)}
💵 Размен: {format_currency(session.exchange_cash)}
📊 Количество чеков: {len(session.sales)}
🛒 Всего позиций: {total_items} шт.

📦 ДЕТАЛИЗАЦИЯ ПО КАТЕГОРИЯМ:
"""
    
    for category, stats in sorted(category_stats.items()):
        report_text += f"\n▶ {category}:\n"
        report_text += f"   📊 Позиций: {stats['total_count']} шт.\n"
        report_text += f"   💰 Выручка: {format_currency(stats['total_revenue'])}\n"
        
        for item_name, item_data in sorted(stats["items"].items()):
            if item_data['revenue'] == 0:
                report_text += f"   • {item_name}: {item_data['count']} шт. (бесплатно)\n"
            else:
                avg_price = item_data['revenue'] / item_data['count']
                report_text += f"   • {item_name}: {item_data['count']} шт. × {format_currency(avg_price)} = {format_currency(item_data['revenue'])}\n"
    
    return report_text

def build_metrics_report() -> str:
    """Отчет по показателям текущей смены"""
    # Статистика по всем продажам (учитываем возвраты)
    total_people = 0
    total_online_combo = 0
    total_invitations = 0
    total_partners = 0
    total_bloggers = 0
    
    # Выручка по типам (учитываем возвраты)
    допы_revenue = 0
    магазин_revenue = 0
    
    # Для среднего чека магазина - считаем только людей, купивших что-то в магазине
    магазин_покупатели = 0
    
    for sale in session.sales:
        # Проверяем, есть ли в чеке товары магазина
        has_shop_items = any(item["category"] in ["📝 Другие позиции", "📝 Свободные позиции"] for item in sale["items"])
        
        for item in sale["items"]:
            item_name = item["item"]
            price = item["price"]  # Уже учитывает возвраты (отрицательные значения)
            category = item["category"]
            
            # Подсчет людей (только положительные продажи)
            if item_name in PEOPLE_ITEMS and price >= 0:
                total_people += 1
            
            # Подсчет онлайн комбо (только положительные)
            if item_name in ONLINE_COMBO_ITEMS and price >= 0:
                total_online_combo += 1
            
            # Подсчет пригласительных (только положительные)
            if item_name in INVITATION_ITEMS and price >= 0:
                total_invitations += 1
            
            # Подсчет партнеров (только положительные)
            if item_name == "Партнёр" and price >= 0:
                total_partners += 1
            
            # Подсчет блогеров (только положительные)
            if item_name == "Блогер" and price >= 0:
                total_bloggers += 1
            
            # РАСПРЕДЕЛЕНИЕ ВЫРУЧКИ (учитываем все, включая возвраты)
            if category in ["📍 Локации", "🍿 Комбо"]:
                допы_revenue += price
            elif category in ["📝 Другие позиции", "📝 Свободные позиции"]:
                магазин_revenue += price
                # Если это положительная продажа магазина, считаем покупателя
                if price > 0 and has_shop_items:
                    магазин_покупатели += 1
    
    # Расчет выручки (уже учитывает возвраты)
    total_revenue = sum(sale['total'] for sale in session.sales)
    
    # Расчет средних чеков
    total_dops_magazin = допы_revenue + магазин_revenue
    avg_check_total = total_dops_magazin / total_people if total_people > 0 else 0
    avg_check_shop = магазин_revenue / магазин_покупатели if магазин_покупатели > 0 else 0
    
    report_text = f"""📈 ОТЧЁТ ПО ПОКАЗАТЕЛЯМ

Астана, «Космопарк 01»
{datetime.datetime.now().strftime('Сегодня %d.%m.%Y')}
С 10:00 до {datetime.datetime.now().strftime('%H:%M')}

👥 Всего людей: {total_people} чел.
💰 Общая выручка: {format_currency(total_revenue)}
🎯 Выручка допов + магазин: {format_currency(total_dops_magazin)}
🛍️ Выручка магазина: {format_currency(магазин_revenue)}
📊 Средний чек: {format_currency(avg_check_total)}
🛒 Средний чек магазина: {format_currency(avg_check_shop)}

📱 Онлайн комбо: {total_online_combo} шт.
🎫 Пригласительные: {total_invitations} шт.
🤝 Партнеры: {total_partners} шт.
📸 Блогеры: {total_bloggers} шт.
"""
    
    return report_text

def build_receipts_report() -> str:
    """Детализация по чекам текущей смены"""
    if not session.sales:
        return "📋 Детализация по чекам\n\n📭 Чеков пока нет"
    
    report_text = "📋 Детализация по чекам\n\n"
    for i, sale in enumerate(session.sales, 1):
        time_str = sale["time"].strftime("%H:%M:%S")
        payment_type = ""
        if sale["cash_amount"] > 0 and sale["cashless_amount"] > 0:
            payment_type = f"💱 Смешанная ({format_currency(sale['cash_amount'])} нал + {format_currency(sale['cashless_amount'])} безнал)"
        elif sale["cash_amount"] > 0:
            payment_type = "💵 Наличные"
        elif sale["cashless_amount"] > 0:
            payment_type = "💳 Карта"
        else:
            payment_type = "🎁 Бесплатно"
        
        report_text += f"🧾 Чек #{i} ({time_str})\n"
        report_text += f"   {payment_type}\n"
        report_text += f"   💰 Сумма: {format_currency(sale['total'])}\n"
        report_text += f"   📦 Позиций: {len(sale['items'])} шт.\n"
        
        for j, item in enumerate(sale["items"], 1):
            price_display = "БЕСПЛАТНО" if item["price"] == 0 else f"{format_currency(item['price'])}"
            report_text += f"      {j}. {item['item']} - {price_display}\n"
        report_text += "\n"
    
    return report_text

# ====== ОСНОВНЫЕ ОБРАБОТЧИКИ ======
@dp.message(Command("start"))
async def start_command(message: types.Message):
    await message.answer(
        "🎭 Добро пожаловать в бот для учёта продажи билетов!\n\nВыберите действие:",
        reply_markup=get_main_kb()
    )

# ====== ОБРАБОТЧИКИ CALLBACK ======
@dp.callback_query(F.data == "main_menu")
async def main_menu_handler(callback: CallbackQuery):
    await safe_edit_message(
        callback.message,
        "🎭 Главное меню\n\nВыберите действие:",
        get_main_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == "open_shift")
async def open_shift_handler(callback: CallbackQuery):
    if session.is_open:
        await callback.answer("❌ Смена уже открыта!", show_alert=True)
        return
    
    session.is_open = True
    session.sales = []
    session.cart = []
    session.open_time = datetime.datetime.now()
    session.exchange_cash = 0
    
    # Сохраняем бэкап при открытии смены
    session.save_backup()
    
    await safe_edit_message(
        callback.message,
        "✅ Смена открыта!\n\nВыберите действие:",
        get_main_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == "add_exchange")
async def add_exchange_handler(callback: CallbackQuery, state: FSMContext):
    if not session.is_open:
        await callback.answer("❌ Сначала откройте смену!", show_alert=True)
        return
    
    await callback.message.answer("💵 Введите сумму размена:")
    await state.set_state(SessionStates.waiting_exchange_cash)
    await callback.answer()

@dp.message(SessionStates.waiting_exchange_cash)
async def process_exchange_cash(message: types.Message, state: FSMContext):
    is_valid, exchange_amount = validate_amount(message.text)
    if not is_valid:
        await message.answer("❌ Пожалуйста, введите корректное число:")
        return
    
    session.exchange_cash = exchange_amount
    
    # Сохраняем бэкап после внесения размена
    session.save_backup()
    
    await message.answer(
        f"✅ Размен внесен!\n💵 Сумма: {format_currency(exchange_amount)}",
        reply_markup=get_main_kb()
    )
    await state.clear()

@dp.callback_query(F.data == "start_sale")
async def start_sale_handler(callback: CallbackQuery):
    if not session.is_open:
        await callback.answer("❌ Сначала откройте смену!", show_alert=True)
        return
    
    await safe_edit_message(
        callback.message,
        "🛍 Выберите категорию:",
        get_categories_kb()
    )
    await callback.answer()

@dp.callback_query(F.data == "back_to_categories")
async def back_to_categories_handler(callback: CallbackQuery):
    await safe_edit_message(
        callback.message,
        "🛍 Выберите категорию:",
        get_categories_kb()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("cat_"))
async def category_handler(callback: CallbackQuery):
    category_id = callback.data.replace("cat_", "")
    if category_id not in CATEGORIES_IDS:
        await callback.answer("❌ Категория не найдена!", show_alert=True)
        return
    
    category_name = CATEGORIES_IDS[category_id]
    await safe_edit_message(
        callback.message,
        f"📁 {category_name}\n\nВыберите товар:",
        get_items_kb(category_id)
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("item_"))
async def item_handler(callback: CallbackQuery, state: FSMContext):
    item_id = callback.data.replace("item_", "")
    if item_id not in ITEMS_MAPPING:
        await callback.answer("❌ Товар не найден!", show_alert=True)
        return
    
    item_data = ITEMS_MAPPING[item_id]
    
    if item_data["price"] == "custom":
        session.custom_item_temp = {"category": item_data["category"]}
        await callback.message.answer("📝 Введите название позиции:")
        await state.set_state(SessionStates.waiting_custom_name)
        await callback.answer()
        return
    
    session.cart.append({
        "item": item_data["name"],
        "price": item_data["price"],
        "category": item_data["category"],
        "item_id": item_id
    })
    
    cart_count = len(session.cart)
    cart_total = session.get_cart_total()
    
    price_display = "БЕСПЛАТНО" if item_data["price"] == 0 else f"{format_currency(item_data['price'])}"
    
    await safe_edit_message(
        callback.message,
        f"✅ Добавлено: {item_data['name']} - {price_display}\n\n"
        f"🛒 В корзине: {cart_count} позиций на сумму {format_currency(cart_total)}\n\n"
        f"Выберите следующую категорию:",
        get_categories_kb()
    )
    await callback.answer(f"✅ {item_data['name']} добавлен в корзину!")

# ====== ОБРАБОТЧИК КОРЗИНЫ ======
@dp.callback_query(F.data == "show_cart")
async def show_cart_handler(callback: CallbackQuery):
    if not session.cart:
        await safe_edit_message(
            callback.message,
            "🛒 Корзина пуста",
            InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛍 К покупкам", callback_data="back_to_categories")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
            ])
        )
        await callback.answer()
        return
    
    cart_text = "🛒 Ваша корзина:\n\n"
    total = 0
    
    for i, item in enumerate(session.cart, 1):
        price_display = "БЕСПЛАТНО" if item["price"] == 0 else f"{format_currency(item['price'])}"
        cart_text += f"{i}. {item['item']} - {price_display}\n"
        total += item["price"]
    
    cart_text += f"\n💵 Итого: {format_currency(total)}"
    
    await safe_edit_message(callback.message, cart_text, get_cart_kb())
    await callback.answer()

@dp.callback_query(F.data == "clear_cart")
async def clear_cart_handler(callback: CallbackQuery):
    session.cart.clear()
    await safe_edit_message(
        callback.message,
        "🗑 Корзина очищена!",
        get_categories_kb()
    )
    await callback.answer("Корзина очищена!")

@dp.callback_query(F.data == "remove_items")
async def remove_items_handler(callback: CallbackQuery):
    if not session.cart:
        await callback.answer("❌ Корзина пуста!", show_alert=True)
        return
    
    await safe_edit_message(
        callback.message,
        "🗑 Выберите позиции для удаления:",
        get_remove_items_kb()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("remove_"))
async def remove_single_item_handler(callback: CallbackQuery):
    try:
        index = int(callback.data.replace("remove_", ""))
        if 0 <= index < len(session.cart):
            removed_item = session.cart.pop(index)
            await callback.answer(f"❌ {removed_item['item']} удален из корзины")
            
            if session.cart:
                await safe_edit_message(
                    callback.message,
                    "🗑 Выберите позиции для удаления:",
                    get_remove_items_kb()
                )
            else:
                await safe_edit_message(
                    callback.message,
                    "🛒 Корзина пуста",
                    get_categories_kb()
                )
        else:
            await callback.answer("❌ Позиция не найдена!", show_alert=True)
    except ValueError:
        await callback.answer("❌ Ошибка удаления!", show_alert=True)

# ====== ОБРАБОТЧИК КАСТОМНЫХ ПОЗИЦИЙ ======
@dp.message(SessionStates.waiting_custom_name)
async def process_custom_name(message: types.Message, state: FSMContext):
    custom_name = message.text.strip()
    if not custom_name:
        await message.answer("❌ Название не может быть пустым. Введите название:")
        return
    
    session.custom_item_temp["name"] = custom_name
    await message.answer("💵 Введите цену позиции (в рублях):")
    await state.set_state(SessionStates.waiting_custom_price)

@dp.message(SessionStates.waiting_custom_price)
async def process_custom_price(message: types.Message, state: FSMContext):
    is_valid, price = validate_amount(message.text)
    if not is_valid:
        await message.answer("❌ Пожалуйста, введите корректное число:")
        return
    
    session.cart.append({
        "item": session.custom_item_temp["name"],
        "price": price,
        "category": "📝 Свободные позиции",
        "item_id": "custom"
    })
    
    cart_count = len(session.cart)
    cart_total = session.get_cart_total()
    
    await message.answer(
        f"✅ Свободная позиция добавлена!\n\n"
        f"📝 Название: {session.custom_item_temp['name']}\n"
        f"💵 Цена: {format_currency(price)}\n\n"
        f"🛒 В корзине: {cart_count} позиций на сумму {format_currency(cart_total)}\n\n"
        f"Выберите следующую категорию:",
        reply_markup=get_categories_kb()
    )
    
    session.custom_item_temp = None
    await state.clear()

# ====== ОБРАБОТЧИК ОПЛАТЫ ======
@dp.callback_query(F.data.in_(["payment_cash", "payment_card"]))
async def payment_handler(callback: CallbackQuery):
    if not session.cart:
        await callback.answer("❌ Корзина пуста!", show_alert=True)
        return
    
    total = session.get_cart_total()
    pay_type = "наличные" if callback.data == "payment_cash" else "карта"
    
    if pay_type == "наличные":
        session.add_sale(session.cart, cash_amount=total)
    else:
        session.add_sale(session.cart, cashless_amount=total)
    
    # Сохраняем бэкап после продажи
    session.save_backup()
    
    if total == 0:
        await safe_edit_message(
            callback.message,
            f"✅ Бесплатный заказ оформлен!\n📦 Позиций: {len(session.cart)}",
            get_main_kb()
        )
    else:
        await safe_edit_message(
            callback.message,
            f"✅ Продажа оформлена!\n💳 Способ: {pay_type}\n💰 Сумма: {format_currency(total)}\n📦 Позиций: {len(session.cart)}",
            get_main_kb()
        )
    
    session.cart.clear()
    await callback.answer()

@dp.callback_query(F.data == "payment_mixed")
async def payment_mixed_handler(callback: CallbackQuery, state: FSMContext):
    if not session.cart:
        await callback.answer("❌ Корзина пуста!", show_alert=True)
        return
    
    total = session.get_cart_total()
    if total == 0:
        await callback.answer("ℹ️ Бесплатные заказы не требуют оплаты!", show_alert=True)
        return
    
    session.mixed_amount = total
    await callback.message.answer(
        f"💱 Введите сумму наличными (из {format_currency(total)}):",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="show_cart")]
        ])
    )
    await state.set_state(SessionStates.waiting_mixed_cash)
    await callback.answer()

@dp.message(SessionStates.waiting_mixed_cash)
async def process_mixed_cash(message: types.Message, state: FSMContext):
    is_valid, cash_amount = validate_amount(message.text)
    if not is_valid:
        await message.answer("❌ Пожалуйста, введите корректное число:")
        return
    
    if cash_amount > session.mixed_amount:
        await message.answer(f"❌ Сумма не может превышать {format_currency(session.mixed_amount)}. Введите снова:")
        return
    
    cashless_amount = session.mixed_amount - cash_amount
    session.add_sale(session.cart, cash_amount, cashless_amount)
    
    # Сохраняем бэкап после продажи
    session.save_backup()
    
    await message.answer(
        f"✅ Продажа оформлена!\n💱 Смешанная оплата\n💵 Наличные: {format_currency(cash_amount)}\n💳 Карта: {format_currency(cashless_amount)}\n💰 Всего: {format_currency(session.mixed_amount)}",
        reply_markup=get_main_kb()
    )
    
    session.cart.clear()
    session.mixed_amount = None
    await state.clear()

# ====== ОБРАБОТЧИК ВОЗВРАТОВ ======
@dp.callback_query(F.data == "refund_menu")
async def refund_menu_handler(callback: CallbackQuery):
    if not session.is_open:
        await callback.answer("❌ Смена не открыта!", show_alert=True)
        return
    
    if not session.sales:
        await callback.answer("📭 Чеков для возврата нет", show_alert=True)
        return
    
    await safe_edit_message(
        callback.message,
        "↩️ Выберите чек для возврата:",
        get_refund_kb()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("refund_"))
async def refund_sale_handler(callback: CallbackQuery):
    try:
        sale_id = int(callback.data.replace("refund_", ""))
        # Поиск чека по ID
        sale_to_refund = None
        for sale in session.sales:
            if sale['id'] == sale_id:
                sale_to_refund = sale
                break
        
        if not sale_to_refund:
            await callback.answer("❌ Чек не найден!", show_alert=True)
            return
        
        # Создаем возврат (добавляем чек с отрицательными суммами)
        refund_items = []
        for item in sale_to_refund['items']:
            refund_items.append({
                'item': f"↩️ ВОЗВРАТ: {item['item']}",
                'price': -item['price'],
                'category': item['category'],
                'item_id': item.get('item_id', 'refund')
            })
        
        session.add_sale(
            refund_items, 
            cash_amount=-sale_to_refund['cash_amount'],
            cashless_amount=-sale_to_refund['cashless_amount']
        )
        
        # Сохраняем бэкап после возврата
        session.save_backup()
        
        await safe_edit_message(
            callback.message,
            f"✅ Возврат оформлен!\n🧾 Чек #{sale_id}\n💰 Сумма: {format_currency(sale_to_refund['total'])}",
            get_main_kb()
        )
        await callback.answer()
        
    except ValueError:
        await callback.answer("❌ Ошибка возврата!", show_alert=True)

# ====== ОБРАБОТЧИК ОТЧЕТОВ ======
@dp.callback_query(F.data == "show_report")
async def show_report_handler(callback: CallbackQuery):
    if not session.is_open:
        await callback.answer("❌ Смена не открыта!", show_alert=True)
        return
    
    # Показываем только отчет по показателям и чекам (без общего отчета)
    report_text = build_metrics_report()
    await safe_edit_message(callback.message, report_text, get_report_kb())
    session.last_report_type = "metrics"
    await callback.answer()

@dp.callback_query(F.data == "report_receipts")
async def report_receipts_handler(callback: CallbackQuery):
    if session.last_report_type == "receipts":
        await callback.answer("ℹ️ Уже показан этот отчёт", show_alert=True)
        return
    
    report_text = build_receipts_report()
    await safe_edit_message(callback.message, report_text, get_report_kb())
    session.last_report_type = "receipts"
    await callback.answer()

@dp.callback_query(F.data == "report_metrics")
async def report_metrics_handler(callback: CallbackQuery):
    if session.last_report_type == "metrics":
        await callback.answer("ℹ️ Уже показан этот отчёт", show_alert=True)
        return
    
    report_text = build_metrics_report()
    await safe_edit_message(callback.message, report_text, get_report_kb())
    session.last_report_type = "metrics"
    await callback.answer()

# ====== ОБРАБОТЧИК АРХИВА СМЕН ======
@dp.callback_query(F.data == "session_archive")
async def session_archive_handler(callback: CallbackQuery):
    sessions = get_closed_sessions()
    if not sessions:
        await callback.answer("📭 Архив смен пуст", show_alert=True)
        return
    
    await safe_edit_message(
        callback.message,
        "📋 Архив закрытых смен (последние 30 дней):\n\nВыберите смену для просмотра:",
        get_session_archive_kb()
    )
    await callback.answer()

@dp.callback_query(F.data.startswith("archive_"))
async def archive_session_handler(callback: CallbackQuery):
    filename = callback.data.replace("archive_", "")
    filepath = f"{Config.CLOSED_SESSIONS_FOLDER}/{filename}"
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Отправляем файл
        await callback.message.answer_document(
            document=types.FSInputFile(filepath),
            caption=f"📄 Отчет по смене: {filename}"
        )
        
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка при чтении файла смены: {e}")
        await callback.answer("❌ Ошибка при загрузке отчета", show_alert=True)

# ====== ОБРАБОТЧИК ЗАКРЫТИЯ СМЕНЫ ======
@dp.callback_query(F.data == "close_shift")
async def close_shift_handler(callback: CallbackQuery):
    if not session.is_open:
        await callback.answer("❌ Смена не открыта!", show_alert=True)
        return
    
    await callback.message.answer("📊 Формирую итоговые отчёты...")
    
    # Собираем все отчеты
    session_data = {
        'open_time': session.open_time,
        'close_time': datetime.datetime.now(),
        'combined_report': build_combined_report(),
        'metrics_report': build_metrics_report(),
        'receipts_report': build_receipts_report()
    }
    
    # Сохраняем в файл
    filename = save_session_report(session_data)
    
    if filename:
        # Удаляем бэкап при корректном закрытии смены
        session.delete_backup()
        
        # Отправляем файл пользователю
        await callback.message.answer_document(
            document=types.FSInputFile(filename),
            caption="📄 Полный отчет по смене"
        )
        
        await callback.message.answer("✅ Смена закрыта! Отчет сохранен в файл.", reply_markup=get_main_kb())
    else:
        await callback.message.answer("❌ Ошибка при сохранении отчета!", reply_markup=get_main_kb())
    
    # Закрываем смену
    session.reset()
    await callback.answer()

# ====== GRACEFUL SHUTDOWN ======
async def shutdown():
    """Корректное завершение работы бота"""
    logger.info("Завершение работы бота...")
    session.save_backup()
    session.stop_auto_save()
    await bot.session.close()
    logger.info("Бот корректно завершил работу")

# ====== ЗАПУСК БОТА ======
async def main():
    logger.info("Бот запускается...")
    
    try:
        # Восстановление сессии из бэкапа
        session.restore_session()
        
        # Запуск автосохранения
        await session.start_auto_save(interval_seconds=120)
        
        logger.info("Бот начал polling...")
        await dp.start_polling(bot)
        
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем")
        asyncio.run(shutdown())
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}")
        asyncio.run(shutdown())