import asyncio
import json
import os
import random
import sys
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse

from bs4 import BeautifulSoup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from loguru import logger
from pydantic import ValidationError

from common_data import HEADERS
from dto import Proxy, AvitoConfig
from filters.ads_filter import AdsFilter
from hide_private_data import log_config
from integrations.notifications.factory import build_notifier
from integrations.run_notifications import TelegramRunNotifier, format_run_error, format_run_report
from load_config import load_avito_config
from models import ItemsResponse, Item
from parser.cookies.factory import build_cookies_provider
from parser.export.excel import ExcelStorage
from parser.http.client import HttpClient
from parser.proxies.proxy_factory import build_proxy
from utils.ftp_upload import send_file_to_ftp
from utils.parse_phone import ParsePhone


DEBUG_MODE = False

Path("logs").mkdir(parents=True, exist_ok=True)
logger.add("logs/app.log", rotation="5 MB", retention="5 days", level="DEBUG")


@dataclass
class BatchRunResult:
    batch_name: str
    ads_count: int
    files: list[Path]
    pages_parsed: int = 0
    redirects_count: int = 0
    empty_catalog_pages: int = 0
    links_total: int = 0


class AvitoParse:
    """Основной класс парсера объявлений Avito для умных часов."""
    def __init__(
            self,
            config: AvitoConfig,
            stop_event=None
    ):
        self.config = config
        self.proxy = build_proxy(self.config)
        self.cookies_provider = build_cookies_provider(config=config)
        self.notifier = build_notifier(config=config)
        self.result_storage = None
        self.stop_event = stop_event
        self.headers = HEADERS
        self.good_request_count = 0
        self.bad_request_count = 0
        self.proxy_rotations_used = 0
        self.http = HttpClient(
            proxy=self.proxy,
            cookies=self.cookies_provider,
            timeout=20,
            max_retries=self.config.max_count_of_retry,
        )
        self.ads_filter = AdsFilter(config=config)
        log_config(self.config, version=os.environ.get("APP_VERSION") or None)


    def get_proxy_obj(self) -> Proxy | None:
        """Возвращает объект прокси, если прокси включены в конфиге."""
        if all([self.config.proxy_string, self.config.proxy_change_url]):
            return Proxy(
                proxy_string=self.config.proxy_string,
                change_ip_link=self.config.proxy_change_url
            )
        logger.info("Работаем без прокси")
        return None

    def fetch_data(self, url: str) -> str | None:
        """Выполняет HTTP-запрос и возвращает HTML страницы."""
        if self.stop_event and self.stop_event.is_set():
            return None

        try:
            response = self.http.request("GET", url)
            self.good_request_count += 1
            return response.text

        except Exception as err:
            self.bad_request_count += 1
            logger.warning(f"Ошибка при запросе {url}: {err}")
            return None

    def reset_http_client(self):
        """Пересоздаёт HTTP-клиент, например после смены IP."""
        self.http = HttpClient(
            proxy=self.proxy,
            cookies=self.cookies_provider,
            timeout=20,
            max_retries=self.config.max_count_of_retry,
        )
        logger.info("HTTP клиент пересоздан")

    def rotate_proxy_if_allowed(self, reason: str = "") -> bool:
        """Пытается сменить IP прокси, если не превышен лимит ротаций."""
        if self.proxy_rotations_used >= self.config.proxy_rotation_limit:
            logger.warning(
                f"Лимит смен IP достигнут: {self.proxy_rotations_used}/{self.config.proxy_rotation_limit}"
            )
            return False

        try:
            self.proxy.handle_block()
            self.proxy_rotations_used += 1
            logger.info(
                f"IP прокси обновлён ({self.proxy_rotations_used}/{self.config.proxy_rotation_limit}). Причина: {reason}"
            )
            time.sleep(self.config.proxy_rotation_cooldown)
            return True
        except Exception as err:
            logger.warning(f"Не удалось обновить IP: {err}")
            return False
        

    @staticmethod
    def _watch_xlsx_suffix(batch_name: str) -> str:
        """Возвращает стабильный суффикс итогового XLSX."""
        return "new" if batch_name == "new" else "old"

    def parse_urls(self, urls, batch_name: str) -> BatchRunResult:
        """Парсит batch ссылок и сохраняет объявления в итоговый XLSX."""
        run_date = datetime.now().strftime("%Y-%m-%d")
        file_suffix = self._watch_xlsx_suffix(batch_name)
        out_dir = Path(self.config.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        all_ads = []
        output_files: list[Path] = []
        batch_storage: ExcelStorage | None = None
        batch_file_path: Path | None = None
        batch_ads_collected = 0
        pages_parsed = 0
        redirects_count = 0
        empty_catalog_pages = 0

        if self.config.save_xlsx and not self.config.one_file_for_link:
            batch_file_path = out_dir / f"avito_watch_{run_date}_{file_suffix}.xlsx"
            if batch_file_path.exists():
                batch_file_path.unlink()
            batch_storage = ExcelStorage(batch_file_path)
            output_files.append(batch_file_path)
            logger.info(f"Итоговый файл batch (с промежуточными сохранениями): {batch_file_path.name}")

        for link_index, url in enumerate(urls):
            limit_reached = False
            self.reset_http_client()
            logger.info(f"Начинаю парсинг ссылки ({batch_name}): {url}")

            ads_in_link = []
            seen_page_hashes = set()
            seen_ad_ids = set()
            failed_html_attempts = 0
            page_num = 1
            current_url = url

            link_storage: ExcelStorage | None = None
            link_file_path: Path | None = None
            if self.config.save_xlsx and self.config.one_file_for_link:
                link_file_path = out_dir / f"avito_watch_{run_date}_{file_suffix}_link{link_index + 1}.xlsx"
                if link_file_path.exists():
                    link_file_path.unlink()
                link_storage = ExcelStorage(link_file_path)
                output_files.append(link_file_path)
                logger.info(f"Файл по ссылке {link_index + 1}: {link_file_path.name}")

            while True:
                logger.info(f"[{batch_name}] page={page_num} url={current_url}")

                if self.stop_event and self.stop_event.is_set():
                    return BatchRunResult(
                        batch_name=batch_name,
                        ads_count=len(all_ads) + len(ads_in_link),
                        files=output_files,
                        pages_parsed=pages_parsed,
                        redirects_count=redirects_count,
                        empty_catalog_pages=empty_catalog_pages,
                        links_total=len(urls),
                    )

                if DEBUG_MODE:
                    html_code = open("may.txt", "r", encoding="utf-8").read()
                else:
                    html_code = self.fetch_data(url=current_url)

                if not html_code:
                    failed_html_attempts += 1

                    if failed_html_attempts >= 2:
                        cooldown = random.uniform(15, 25)
                        logger.info(f"Cooldown перед сменой IP: {cooldown:.2f} сек.")
                        time.sleep(cooldown)

                        rotated = self.rotate_proxy_if_allowed(reason=f"no html on page {page_num}")
                        if rotated:
                            self.reset_http_client()
                            time.sleep(random.uniform(7, 15))
                            failed_html_attempts = 0
                            continue

                        logger.warning("Слишком много неудачных попыток по странице, заканчиваю ссылку")
                        break

                    sleep_time = random.uniform(8, 15)
                    logger.info(f"Пауза после ошибки {sleep_time:.2f} сек.")
                    time.sleep(sleep_time)
                    continue

                failed_html_attempts = 0

                data_from_page = self.find_json_on_page(html_code=html_code)
                redirect_url = self._get_redirect_url(data_from_page)
                if redirect_url:
                    next_url = self._resolve_avito_url(redirect_url)
                    if next_url and next_url != current_url:
                        redirects_count += 1
                        logger.info(f"Avito перенаправил выдачу, перехожу на: {next_url}")
                        current_url = next_url
                        continue

                catalog = data_from_page.get("catalog") or {}

                if not catalog or "items" not in catalog:
                    empty_catalog_pages += 1
                    logger.warning(
                        f"На странице {current_url} отсутствует catalog.items, заканчиваю работу с данной ссылкой"
                    )
                    break

                try:
                    ads_models = ItemsResponse(**catalog)
                except ValidationError as err:
                    logger.warning(
                        f"Не удалось провалидировать catalog на странице {current_url}: {err}"
                    )
                    break

                pages_parsed += 1
                ads = self._clean_null_ads(ads=ads_models.items)
                logger.info(f"Объявлений до фильтров: {len(ads)}")

                if not ads:
                    logger.info("Объявления закончились, заканчиваю работу с данной ссылкой")
                    break

                page_hash = self._make_page_hash(ads)
                if page_hash in seen_page_hashes:
                    logger.info("Получили повтор страницы, заканчиваю работу с данной ссылкой")
                    break
                seen_page_hashes.add(page_hash)

                ads = self._add_seller_to_ads(ads=ads)
                for ad in ads:
                    ad.delivery = self._extract_delivery_text(ad)

                filter_ads = self.filter_ads(ads=ads)
                logger.info(f"После фильтров: {len(filter_ads)}")

                candidates = [ad for ad in filter_ads if ad.id not in seen_ad_ids]
                lim = self.config.max_ads_per_batch
                if lim > 0:
                    room = lim - batch_ads_collected
                    if room <= 0:
                        logger.info(f"Достигнут лимит объявлений в batch ({lim}), завершаю ссылку")
                        limit_reached = True
                        break
                    candidates = candidates[:room]

                for ad in candidates:
                    seen_ad_ids.add(ad.id)
                unique_ads = candidates

                logger.info(f"Новых объявлений на странице: {len(unique_ads)}")

                if not unique_ads:
                    logger.info("Новых объявлений больше нет, заканчиваю работу с данной ссылкой")
                    break

                ads_in_link.extend(unique_ads)
                batch_ads_collected += len(unique_ads)

                if self.config.save_xlsx and unique_ads:
                    try:
                        if self.config.one_file_for_link and link_storage:
                            link_storage.save(unique_ads)
                        elif batch_storage:
                            batch_storage.save(unique_ads)
                        logger.info(
                            f"Сохранено в xlsx объявлений с страницы: {len(unique_ads)}"
                        )
                    except Exception as err:
                        logger.error(f"Ошибка промежуточного сохранения в xlsx: {err}")

                if limit_reached or (lim > 0 and batch_ads_collected >= lim):
                    logger.info(f"Лимит batch ({lim}) набран, дальше не иду")
                    limit_reached = True
                    break

                page_limit = self._get_page_limit()
                if page_limit and page_num >= page_limit:
                    logger.info(f"Достигнут лимит страниц для ссылки ({page_limit}), заканчиваю работу с данной ссылкой")
                    break

                if not self.has_next_page(html_code, page_num):
                    logger.info("Следующей страницы в пагинации нет, заканчиваю работу с данной ссылкой")
                    break

                next_url = self.get_next_page_url(url=current_url)
                if not next_url or next_url == current_url:
                    logger.info("Не удалось получить следующую страницу")
                    break

                current_url = next_url
                page_num += 1

                pause = random.uniform(10, 20)
                logger.info(f"Пауза {pause:.2f} сек.")
                time.sleep(pause)

            logger.info(f"По ссылке собрано объявлений: {len(ads_in_link)}")
            logger.info("Пауза перед следующей ссылкой 8 сек.")
            time.sleep(8)

            if self.config.save_xlsx and self.config.one_file_for_link and link_file_path and link_file_path.exists():
                send_file_to_ftp(link_file_path, self.notifier)

            all_ads.extend(ads_in_link)

            if limit_reached:
                break

        logger.info(f"Всего собрано объявлений в batch '{batch_name}': {len(all_ads)}")
        logger.info(
            f"Диагностика batch '{batch_name}': pages={pages_parsed}, "
            f"redirects={redirects_count}, empty_catalog_pages={empty_catalog_pages}"
        )

        if self.config.save_xlsx and not self.config.one_file_for_link and batch_file_path and batch_file_path.exists():
            send_file_to_ftp(batch_file_path, self.notifier)

        return BatchRunResult(
            batch_name=batch_name,
            ads_count=len(all_ads),
            files=output_files,
            pages_parsed=pages_parsed,
            redirects_count=redirects_count,
            empty_catalog_pages=empty_catalog_pages,
            links_total=len(urls),
        )

    def parse(self) -> list[BatchRunResult]:
        """Запускает два отдельных прогона: для новых и для б/у устройств."""
        started_at = datetime.now()
        run_notifier = TelegramRunNotifier.from_env()
        results: list[BatchRunResult] = []

        try:
            logger.info("=== Начинаю batch: NEW ===")
            results.append(self.parse_urls(self.config.new_urls, "new"))

            logger.info("=== Начинаю batch: USED ===")
            results.append(self.parse_urls(self.config.used_urls, "used"))
        except Exception as err:
            run_notifier.send_message(
                format_run_error(
                    started_at=started_at,
                    finished_at=datetime.now(),
                    error=err,
                )
            )
            raise

        files = [file_path for result in results for file_path in result.files]
        category_counts = {result.batch_name: result.ads_count for result in results}
        rows = sum(result.ads_count for result in results)
        diagnostics = {
            "pages": sum(result.pages_parsed for result in results),
            "redirects": sum(result.redirects_count for result in results),
            "empty_catalog_pages": sum(result.empty_catalog_pages for result in results),
        }
        warnings = []
        if rows == 0:
            warnings.append("объявления не собраны")
        for result in results:
            if result.links_total > 0 and result.ads_count == 0:
                warnings.append(f"batch {result.batch_name}: 0 объявлений")
        if diagnostics["empty_catalog_pages"] > 0:
            warnings.append(
                f"Avito отдал страниц без catalog.items: {diagnostics['empty_catalog_pages']}"
            )

        run_notifier.send_message(
            format_run_report(
                started_at=started_at,
                finished_at=datetime.now(),
                rows=rows,
                files=files,
                category_counts=category_counts,
                diagnostics=diagnostics,
                warnings=warnings,
            )
        )
        return results

    @staticmethod
    def _extract_delivery_text(ad: Item) -> str | None:
        """Извлекает текст доставки из блока iva -> GeoStep."""
        try:
            iva = ad.iva or {}
            geo_steps = iva.get("GeoStep", [])

            for step in geo_steps:
                component_data = getattr(step, "componentData", None)
                payload = getattr(step, "payload", None)

                component = getattr(component_data, "component", None)

                if component == "delivery" and payload:
                    text = payload.get("text") if isinstance(payload, dict) else getattr(payload, "text", None)
                    if text:
                        return text

            return None
        except Exception as err:
            logger.warning(f"Ошибка при извлечении доставки для ad.id={getattr(ad, 'id', None)}: {err}")
            return None
        
    @staticmethod
    def has_next_page(html_code: str, current_page: int) -> bool:
        """Проверяет, есть ли ссылка на следующую страницу в пагинации."""
        soup = BeautifulSoup(html_code, "html.parser")

        next_page = current_page + 1

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if re.search(rf"[?&]p={next_page}([&#]|$)", href):
                return True

        return False
    
    @staticmethod
    def _make_page_hash(ads: list[Item]) -> str:
        """Строит hash страницы по id объявлений для защиты от повторов."""
        ids = sorted(str(ad.id) for ad in ads if ad.id)
        return "|".join(ids)
    
    @staticmethod
    def _clean_null_ads(ads: list[Item]) -> list[Item]:
        """Удаляет объявления без id."""
        return [ad for ad in ads if ad.id]

    @staticmethod
    def _get_redirect_url(data_from_page: dict) -> str | None:
        """Возвращает URL фронтенд-редиректа Avito, если страница просит перейти."""
        if data_from_page.get("redirected") is True and data_from_page.get("url"):
            return str(data_from_page["url"])
        return None

    @staticmethod
    def _resolve_avito_url(url: str) -> str:
        """Приводит относительный Avito URL к абсолютному."""
        return urljoin("https://www.avito.ru", url)

    def _get_page_limit(self) -> int:
        """Возвращает лимит страниц на ссылку из настроек."""
        return int(self.config.max_pages or self.config.count or 0)

    @staticmethod
    def find_json_on_page(html_code, data_type: str = "mime") -> dict:
        """Извлекает JSON с данными объявлений из HTML страницы."""
        import html as html_lib
        html_code = BeautifulSoup(html_code, "html.parser")
        try:
            for _script in html_code.select('script'):

                script_type = _script.get('type')


                if data_type == 'mime':
                    for script in html_code.select('script'):
                        if script.get('type') == 'mime/invalid' and script.get('data-mfe-state') == 'true' and 'sandbox' not in script.text:
                            data = json.loads(html_lib.unescape(script.text))
                            if data.get('i18n', {}).get('hasMessages', {}):
                                return data.get('state', {}).get('data', {})

        except Exception as err:
            logger.error(f"Ошибка при поиске информации на странице: {err}")
        logger.warning("not found json")
        return {}


    def filter_ads(self, ads: list[Item]) -> list[Item]:
        """Применяет все фильтры к списку объявлений."""
        return self.ads_filter.apply(ads)

    def _add_seller_to_ads(self, ads: list[Item]) -> list[Item]:
        """Добавляет sellerId в объявления на основе url/данных продавца."""
        for ad in ads:
            if seller_id := self._extract_seller_slug(data=ad):
                ad.sellerId = seller_id
        return ads

    @staticmethod
    def _add_promotion_to_ads(ads: list[Item]) -> list[Item]:
        """Определяет, является ли объявление продвинутым."""
        for ad in ads:
            ad.isPromotion = any(
                v.get("title") == "Продвинуто"
                for step in (ad.iva or {}).get("DateInfoStep", [])
                for v in step.payload.get("vas", [])
            )
        return ads

    def parse_views(self, ads: list[Item]) -> list[Item]:
        """Дополнительно парсит просмотры с полной страницы объявления."""
        if not self.config.parse_views:
            return ads

        logger.info("Начинаю парсинг просмотров")

        for ad in ads:
            try:
                html_code_full_page = self.fetch_data(url=f"https://www.avito.ru{ad.urlPath}")
                if not html_code_full_page:
                    continue
                ad.total_views, ad.today_views = self._extract_views(html=html_code_full_page)
                delay = random.uniform(0.1, 0.9)
                time.sleep(delay)
            except Exception as err:
                logger.warning(f"Ошибка при парсинге {ad.urlPath}: {err}")
                continue

        return ads

    def parse_phone(self, ads: list[Item]) -> list[Item]:
        if not self.config.parse_phone:
            return ads

        try:
            return ParsePhone(ads=ads, config=self.config).parse_phones()
        except Exception as err:
            logger.warning(f"Ошибка при парсинге телефонов: {err}")
            return ads

    @staticmethod
    def _extract_views(html: str) -> tuple:
        """Извлекает общее и дневное количество просмотров."""
        soup = BeautifulSoup(html, "html.parser")

        def extract_digits(element):
            return int(''.join(filter(str.isdigit, element.get_text()))) if element else None

        total = extract_digits(soup.select_one('[data-marker="item-view/total-views"]'))
        today = extract_digits(soup.select_one('[data-marker="item-view/today-views"]'))

        return total, today

    @staticmethod
    def _extract_seller_slug(data):
        match = re.search(r"/brands/([^/?#]+)", str(data))
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _is_recent(timestamp_ms: int, max_age_seconds: int) -> bool:
        now = datetime.utcnow()
        published_time = datetime.utcfromtimestamp(timestamp_ms / 1000)
        return (now - published_time) <= timedelta(seconds=max_age_seconds)

    def get_next_page_url(self, url: str):
        """Получает следующую страницу"""
        try:
            url_parts = urlparse(url)
            query_params = parse_qs(url_parts.query)
            current_page = int(query_params.get('p', [1])[0])
            query_params['p'] = [current_page + 1]
            if self.config.one_time_start:
                logger.debug(f"Страница {current_page}")

            new_query = urlencode(query_params, doseq=True)
            next_url = urlunparse((url_parts.scheme, url_parts.netloc, url_parts.path, url_parts.params, new_query,
                                   url_parts.fragment))
            return next_url
        except Exception as err:
            logger.error(f"Не смог сформировать ссылку на следующую страницу для {url}. Ошибка: {err}")


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def resume_unfinished_parsing() -> None:
    """Сохранение промежуточного состояния парсинга в проекте не ведётся — точка расширения."""
    logger.info("resume_unfinished_parsing: незавершённых прогонов для возобновления нет")


def show_status() -> None:
    logger.info("Планировщик: работает (тик каждые 5 мин)")


def main() -> None:
    load_dotenv()

    try:
        config = load_avito_config("config.toml")
    except Exception as err:
        logger.error(f"Ошибка загрузки конфига: {err}")
        return

    logger.info("Запуск планировщика Авито парсера (каждый день в 9:00)")
    logger.info("Проверка незавершенных парсингов при запуске")
    resume_unfinished_parsing()

    try:
        parser = AvitoParse(config)
        parser.parse()
        if config.one_time_start:
            logger.info("Парсинг завершен т.к. включён one_time_start в настройках")
            return
        logger.info("Парсинг завершен.")
    except Exception as err:
        logger.exception(err)


async def run_scheduler() -> None:
    scheduler = AsyncIOScheduler()
    scheduler.add_job(main, "interval", hours=24, start_date=datetime.now().replace(hour=9, minute=0, second=0, microsecond=0), misfire_grace_time=None)
    scheduler.add_job(show_status, "interval", minutes=5, misfire_grace_time=None)
    scheduler.start()
    await asyncio.Event().wait()


if __name__ == "__main__":
    load_dotenv()

    if _env_flag("AVITO_TELEGRAM_TEST"):
        sent = TelegramRunNotifier.from_env().send_message(
            "Avito Watch Parser\nТестовое Telegram-уведомление успешно отправлено."
        )
        if sent:
            logger.info("Тестовое Telegram-уведомление отправлено")
        else:
            logger.warning("Тестовое Telegram-уведомление не отправлено")
        sys.exit(0)

    # Короткий локальный прогон: первая ссылка new_urls, лимит объявлений и выход.
    if _env_flag("AVITO_TEST_FIRST_LINK"):
        config = load_avito_config("config.toml")
        if not config.new_urls:
            logger.error("В config.toml нет new_urls — тест невозможен")
            sys.exit(1)
        config.new_urls = [config.new_urls[0]]
        config.used_urls = []
        config.max_ads_per_batch = int(os.environ.get("AVITO_MAX_ADS", "50"))
        config.one_time_start = True
        if output_dir := os.environ.get("AVITO_OUTPUT_DIR"):
            config.output_dir = Path(output_dir)
        logger.info(
            f"Режим теста: первая ссылка из new_urls, лимит {config.max_ads_per_batch} объявлений"
        )
        try:
            AvitoParse(config).parse()
        except Exception as err:
            logger.exception(err)
            sys.exit(1)
        logger.info("Тестовый прогон завершён.")
        sys.exit(0)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(run_scheduler())
    loop.run_forever()
