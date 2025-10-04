from aiogram.fsm.state import State, StatesGroup

class SessionStates(StatesGroup):
    waiting_custom_name = State()
    waiting_custom_price = State()
    waiting_mixed_cash = State()
    waiting_exchange_cash = State()