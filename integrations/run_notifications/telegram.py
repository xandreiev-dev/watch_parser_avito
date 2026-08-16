from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
from loguru import logger


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"{name}={raw!r} не является числом, используется {default}")
        return default


class TelegramRunSettings:
    def __init__(
        self,
        enabled: bool = False,
        bot_token: str = "",
        chat_id: str = "",
        timeout: int = 10,
    ):
        self.enabled = enabled
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "TelegramRunSettings":
        load_dotenv()
        return cls(
            enabled=_env_bool("TELEGRAM_NOTIFICATIONS_ENABLED", False),
            bot_token=(os.getenv("TELEGRAM_BOT_TOKEN") or "").strip(),
            chat_id=(os.getenv("TELEGRAM_CHAT_ID") or "").strip(),
            timeout=_env_int("TELEGRAM_TIMEOUT", 10),
        )


class TelegramRunNotifier:
    def __init__(self, settings: TelegramRunSettings):
        self.settings = settings

    @classmethod
    def from_env(cls) -> "TelegramRunNotifier":
        return cls(TelegramRunSettings.from_env())

    def send_message(self, text: str) -> bool:
        if not self.settings.enabled:
            logger.debug("Telegram run-уведомления выключены")
            return False
        if not self.settings.bot_token or not self.settings.chat_id:
            logger.warning("Telegram run-уведомления включены, но TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID не заполнены")
            return False

        url = f"https://api.telegram.org/bot{self.settings.bot_token}/sendMessage"
        try:
            response = requests.post(
                url,
                data={
                    "chat_id": self.settings.chat_id,
                    "text": text,
                    "disable_web_page_preview": "true",
                },
                timeout=self.settings.timeout,
            )
            response.raise_for_status()
            payload = response.json()
            if not payload.get("ok"):
                logger.warning(f"Telegram вернул неуспешный ответ: {payload}")
                return False
            logger.info("Telegram run-уведомление отправлено")
            return True
        except Exception as err:
            error_text = str(err)
            if self.settings.bot_token:
                error_text = error_text.replace(self.settings.bot_token, "<hidden>")
            logger.warning(
                f"Не удалось отправить Telegram run-уведомление: {err.__class__.__name__}: {error_text}"
            )
            return False


def _format_duration(started_at: datetime, finished_at: datetime) -> str:
    seconds = max(0, int((finished_at - started_at).total_seconds()))
    minutes, tail_seconds = divmod(seconds, 60)
    return f"{minutes} мин {tail_seconds} сек"


def _format_files(files: list[Path]) -> str:
    if not files:
        return "не сформирован"
    return ", ".join(path.name for path in files)


def format_run_report(
    *,
    started_at: datetime,
    finished_at: datetime,
    rows: int,
    files: list[Path],
    category_counts: dict[str, int],
    diagnostics: dict[str, int] | None = None,
    warnings: list[str] | None = None,
) -> str:
    lines = [
        "Avito Watch Parser",
        "Статус: завершен",
        "Магазин: Avito",
        f"Старт: {started_at.strftime('%Y-%m-%d %H:%M:%S')}",
        f"Время работы: {_format_duration(started_at, finished_at)}",
        f"Объявлений: {rows}",
        f"Файл: {_format_files(files)}",
    ]

    if category_counts:
        lines.append("Категории:")
        for category, count in category_counts.items():
            lines.append(f"- {category}: {count}")

    if diagnostics:
        lines.append(
            "Проверка: "
            f"страниц {diagnostics.get('pages', 0)}, "
            f"редиректов {diagnostics.get('redirects', 0)}, "
            f"пустых catalog.items {diagnostics.get('empty_catalog_pages', 0)}"
        )

    if warnings:
        lines.append("Внимание:")
        for warning in warnings[:3]:
            lines.append(f"- {warning}")

    return "\n".join(lines)


def format_run_error(
    *,
    started_at: datetime,
    finished_at: datetime,
    error: Exception,
) -> str:
    error_text = str(error).strip() or error.__class__.__name__
    if len(error_text) > 500:
        error_text = error_text[:497] + "..."

    return "\n".join(
        [
            "Avito Watch Parser",
            "Статус: ошибка",
            "Магазин: Avito",
            f"Старт: {started_at.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Время работы: {_format_duration(started_at, finished_at)}",
            f"Ошибка: {error_text}",
        ]
    )
