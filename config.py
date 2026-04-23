import os
from aiogram.enums import ParseMode

BOT_TOKEN = os.getenv("BOT_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x]

BOT_USERNAME = os.getenv("BOT_USERNAME")  # without @

START_IMAGE = "https://files.catbox.moe/kd21dg.jpg"
PARTICIPANT_IMAGE = "https://files.catbox.moe/xj0ci0.jpg"

PARSE_MODE = ParseMode.HTML
