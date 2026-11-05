from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

VACANCY = "Создать вакансию"
INTERVIEW = "Создать интервью"
TRACK = "Отслеживать hh"
RESPONSES = "Отклики"
CONNECT = "Подключить hh"

MENU_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=VACANCY), KeyboardButton(text=INTERVIEW)],
        [KeyboardButton(text=TRACK), KeyboardButton(text=RESPONSES)],
        [KeyboardButton(text=CONNECT)],
    ],
    resize_keyboard=True,
    is_persistent=True,
)
