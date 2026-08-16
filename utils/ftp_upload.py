"""Загрузка на FTP: open + storbinary; вложенные каталоги — через cwd."""

from __future__ import annotations

import ftplib
import os
from pathlib import Path

from loguru import logger

from integrations.notifications.utils import escape_markdown_v2


def _ftp_ensure_remote_dirs(ftp: ftplib.FTP, remote_path: str) -> None:
    """
    Создает подкаталоги через mkd по накопленному пути, затем cwd по сегментам.
    Дальше STOR вызывается только с basename — так стабильнее на FTP-серверах.
    """
    parts = remote_path.replace("\\", "/").split("/")
    acc: list[str] = []

    for part in parts[:-1]:
        if not part:
            continue
        acc.append(part)
        remote_dir = "/".join(acc)
        try:
            ftp.mkd(remote_dir)
        except ftplib.error_perm:
            pass

    for part in parts[:-1]:
        if not part:
            continue
        ftp.cwd(part)


def send_file_to_ftp(out_file: str | Path, notifier=None) -> bool:
    """
    Загружает итоговый XLSX на FTP.

    Переменные окружения:
    FTP_HOST, FTP_USER, FTP_PASSWORD, FTP_PORT, FTP_REMOTE_DIR.

    Если FTP_HOST / FTP_USER / FTP_PASSWORD не заданы, загрузка пропускается.
    """
    path = Path(out_file)
    if not path.is_file():
        logger.error(f"FTP: файл не найден: {path}")
        return False

    host = os.environ.get("FTP_HOST")
    user = os.environ.get("FTP_USER")
    password = os.environ.get("FTP_PASSWORD")
    port_str = os.environ.get("FTP_PORT", "21")
    remote_prefix = (os.environ.get("FTP_REMOTE_DIR") or "avito").strip().strip("/")

    if not all([host, user, password]):
        logger.warning("FTP: не заданы FTP_HOST / FTP_USER / FTP_PASSWORD — загрузка пропущена")
        return False

    try:
        port = int(port_str)
    except ValueError:
        logger.warning(f"FTP: FTP_PORT={port_str!r} не является числом, используется порт 21")
        port = 21

    basename = path.name
    remote_path = f"{remote_prefix}/{basename}".replace("\\", "/")
    logger.info(f"FTP: загрузка «{basename}» на {host}:{port}, удаленный путь: {remote_path}")

    try:
        with ftplib.FTP() as ftp:
            ftp.connect(host, port, timeout=20)
            ftp.login(user, password)
            logger.debug("FTP: подключение установлено, вход выполнен")

            _ftp_ensure_remote_dirs(ftp, remote_path)
            with path.open("rb") as file_obj:
                ftp.storbinary(f"STOR {basename}", file_obj)

            logger.info(f"FTP: файл «{basename}» успешно загружен ({remote_path})")
        return True
    except Exception as err:
        logger.exception(f"FTP: ошибка загрузки {path}: {err}")
        if notifier:
            try:
                msg = f"Ошибка FTP загрузки {basename}: {err}"
                notifier.notify(message=escape_markdown_v2(msg))
            except Exception as notify_err:
                logger.warning(f"Не удалось отправить уведомление об ошибке FTP: {notify_err}")
        return False
