from __future__ import annotations

import os
from pathlib import Path
from typing import Final
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)



def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    stripped = raw_value.strip()
    if not stripped:
        return default

    try:
        return int(stripped)
    except ValueError:
        return default


def _env_int_set(name: str) -> frozenset[int]:
    raw_value = os.getenv(name, "")
    if not raw_value:
        return frozenset()

    values: set[int] = set()
    for item in raw_value.split(","):
        text = item.strip()
        if not text:
            continue
        try:
            values.add(int(text))
        except ValueError:
            continue
    return frozenset(values)


def _env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default

    normalized = raw_value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    return default


BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("Переменная окружения BOT_TOKEN обязательна для запуска бота.")

REMINDERS_FILE_PATH = DATA_DIR / "reminders.json"
DB_PATH = REMINDERS_FILE_PATH

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").strip().upper()
if not LOG_LEVEL:
    LOG_LEVEL = "INFO"

MOSCOW_TZ = ZoneInfo("Europe/Moscow")
UTC_TZ = ZoneInfo("UTC")

TIME_INPUT_FORMAT = "%H:%M"
DATETIME_INPUT_FORMAT = "%d.%m.%Y %H:%M"

MAX_REMINDER_TEXT_LENGTH: Final[int] = _env_int("MAX_REMINDER_TEXT_LENGTH", 1000)
MAX_ACTIVE_REMINDERS_PER_CHAT: Final[int] = _env_int("MAX_ACTIVE_REMINDERS_PER_CHAT", 100)
SCHEDULER_MISFIRE_GRACE_SECONDS: Final[int] = _env_int("SCHEDULER_MISFIRE_GRACE_SECONDS", 300)
DELIVERY_RETRY_ATTEMPTS: Final[int] = _env_int("DELIVERY_RETRY_ATTEMPTS", 2)
DELIVERY_RETRY_DELAY_SECONDS: Final[float] = 1.25
DELIVERY_COOLDOWN_SECONDS: Final[int] = _env_int("DELIVERY_COOLDOWN_SECONDS", 1)
TELEGRAM_INIT_DATA_TTL_SECONDS: Final[int] = _env_int("TELEGRAM_INIT_DATA_TTL_SECONDS", 3600)

ALLOWED_USER_IDS: Final[frozenset[int]] = _env_int_set("ALLOWED_USER_IDS")
ALLOWED_CHAT_IDS: Final[frozenset[int]] = _env_int_set("ALLOWED_CHAT_IDS")

WEBAPP_URL = os.getenv("WEBAPP_URL", "").strip()
WEBAPP_LISTEN_HOST = os.getenv("WEBAPP_LISTEN_HOST", "127.0.0.1").strip()
WEBAPP_LISTEN_PORT = _env_int("WEBAPP_LISTEN_PORT", 8080)
WEBAPP_ENABLED = _env_bool("WEBAPP_ENABLED", bool(WEBAPP_URL))
USER_PROFILES_PATH = BASE_DIR / "profiles.json"

ROMANTIC_OPENERS: tuple[str, ...] = (
    "Доброе утро, я рядом с тобой и мягко напоминаю о хорошем.",
    "Привет, я рядом: давай сделаем это спокойно и без спешки.",
    "С любовью и заботой напоминаю: сейчас важно",
    "Я с тобой, и это маленькое напоминание для тебя",
    "Тебе не нужно быть идеальным — достаточно сделать этот шаг.",
    "Я вежливо подзвонил во внутренний будильник заботы.",
    "Дорогой друг, это напоминание пришло с заботой о тебе.",
    "Я знаю, что ты можешь — напоминание для поддержки и фокуса.",
    "Мягко напоминаю о важном, чтобы день был легче.",
    "Дела не сдаются — ты просто держи курс.",
    "Ты ценен(на), и твое время тоже. Время сделать это сейчас.",
    "Не надо торопиться, но лучше не забыть это маленькое важное дело.",
    "Ты проделала(и) огромную работу, теперь бережно возвращаюсь к этому пункту.",
    "Я здесь, чтобы помочь не терять ритм и мягко поддержать тебя.",
    "Дела важны, а ты — приоритет. Напоминаю.",
    "Позаботаюсь о том, чтобы это не ушло из поля внимания.",
    "Это напоминание для твоего спокойствия и внутреннего порядка.",
    "Я рядом — делай шаг, даже если он небольшой.",
    "Мягкий сигнал: сейчас подходящий момент закрыть этот пункт.",
    "Заботься о себе — и не забывай обещанное себе дело.",
    "Я здесь, чтобы держать мягкий якорь: это важно сделать сегодня.",
    "Дыши спокойно, осталось это несложное действие.",
    "Напоминаю с теплотой: у тебя всё получится, даже если сложно.",
    "Ты всё ещё на правильном пути, и это напоминание поможет его продолжить.",
    "Без давления, только аккуратный сигнал: время выполнить задачу.",
    "Спасибо, что возвращаешься к своим делам с такой бережностью к себе.",
    "Маленький шаг — и ты снова в ритме. Пора его сделать.",
    "Я поддерживаю тебя — не откладывай приятный порядок в долгий ящик.",
    "Нежный чек-ин: не забыть о самом важном в расписании.",
    "Ты в безопасности в своём ритме. Просто аккуратно напоминание.",
    "Я здесь, чтобы помочь тебе держать фокус без перегруза.",
    "Коротко и по-доброму: пора выполнить это на самом деле нужное.",
    "Сейчас подходящий момент сделать это и сохранить спокойствие.",
    "Помню про твои обещания — напоминание пришло вовремя.",
    "Ты не одна в своих задачах: я мягко держу фокус.",
    "Напоминаю с теплотой: это важно для твоего комфорта и порядка.",
    "Я рядом, пока ты закрываешь этот пункт, всё получится.",
    "Сделай паузу и этот шаг — небольшая, но очень полезная забота о себе.",
    "Не все должно быть идеально: достаточно просто сделать это сейчас.",
    "Твое благополучие важнее скорости. Напоминание в мягком темпе.",
    "Я рядом, чтобы поддержать и не потерять важное.",
    "Не теряй связь с тем, что действительно ценно сегодня.",
    "Нежно напоминаю: ты достойна спокойствия и завершённых дел.",
    "Ты делаешь многое. Это сообщение — мини-поддержка, чтобы дойти до конца.",
    "Береги себя: маленький шаг в нужный момент — уже успех.",
    "Я с тобой, и это напоминание отправлено с добротой.",
)
