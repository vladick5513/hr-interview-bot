from aiogram import Bot
from aiogram.types import Document

MAX_TXT_BYTES = 1024 * 1024


class TxtFileError(Exception):
    pass


async def read_txt_document(bot: Bot, document: Document) -> str:
    if not (document.file_name or "").lower().endswith(".txt"):
        raise TxtFileError("Нужен файл в формате .txt.")
    if (document.file_size or 0) > MAX_TXT_BYTES:
        raise TxtFileError("Файл слишком большой, нужен до 1 МБ.")
    file = await bot.download(document)
    text = decode_text_file(file.read())
    if text is None:
        raise TxtFileError("Не получилось прочитать файл. Сохраните его в кодировке UTF-8.")
    return text


def decode_text_file(data: bytes) -> str | None:
    for encoding in ("utf-8-sig", "cp1251"):
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return None
