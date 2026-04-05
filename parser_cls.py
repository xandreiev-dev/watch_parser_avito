import json
import random
import re
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from bs4 import BeautifulSoup
from loguru import logger
from pydantic import ValidationError

from common_data import HEADERS
from db_service import SQLiteDBHandler
from dto import Proxy, AvitoConfig
from filters.ads_filter import AdsFilter
from hide_private_data import log_config
from integrations.notifications.factory import build_notifier
from load_config import load_avito_config
from models import ItemsResponse, Item
from parser.cookies.factory import build_cookies_provider
from parser.export.factory import build_result_storage
from parser.http.client import HttpClient
from parser.proxies.proxy_factory import build_proxy
from utils.parse_phone import ParsePhone
from version import VERSION

DEBUG_MODE = False

logger.add("logs/app.log", rotation="5 MB", retention="5 days", level="DEBUG")


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
        self.db_handler = SQLiteDBHandler()
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
        self.ads_filter = AdsFilter(config=config, is_viewed_fn=self.is_viewed)
        log_config(config=self.config, version=VERSION)


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
        

    def parse_urls(self, urls, batch_name: str):
        """Парсит список ссылок и сохраняет результат в отдельный итоговый файл."""
        all_ads = []

        for url in urls:
            self.reset_http_client()
            logger.info(f"Начинаю парсинг ссылки ({batch_name}): {url}")

            ads_in_link = []
            seen_page_hashes = set()
            seen_ad_ids = set()
            failed_html_attempts = 0
            page_num = 1
            current_url = url

            while True:
                logger.info(f"[{batch_name}] page={page_num} url={current_url}")

                if self.stop_event and self.stop_event.is_set():
                    return

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
                catalog = data_from_page.get("catalog") or {}

                if not catalog or "items" not in catalog:
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

                unique_ads = []
                for ad in filter_ads:
                    if ad.id not in seen_ad_ids:
                        seen_ad_ids.add(ad.id)
                        unique_ads.append(ad)

                logger.info(f"Новых объявлений на странице: {len(unique_ads)}")

                if not unique_ads:
                    logger.info("Новых объявлений больше нет, заканчиваю работу с данной ссылкой")
                    break

                ads_in_link.extend(unique_ads)

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

            if self.config.one_file_for_link and ads_in_link:
                try:
                    result_storage = build_result_storage(config=self.config, url=url)
                    result_storage.save(ads_in_link)
                    logger.info(f"Сохранил файл по ссылке: {url}")
                except Exception as err:
                    logger.error(f"Ошибка при сохранении результата по ссылке {url}: {err}")

            all_ads.extend(ads_in_link)

        logger.info(f"Всего собрано объявлений в batch '{batch_name}': {len(all_ads)}")

        if not self.config.one_file_for_link and all_ads:
            try:
                result_storage = build_result_storage(config=self.config, batch_name=batch_name)

                if hasattr(result_storage, "file_path"):
                    original_path = result_storage.file_path
                    parent = original_path.parent
                    batch_file_path = parent / f"Avito_ru_{batch_name}.xlsx"

                    result_storage.file_path = batch_file_path

                    if result_storage.file_path.exists():
                        result_storage.file_path.unlink()

                    result_storage._create_file()

                result_storage.save(all_ads)
                logger.info(f"Сохранил итоговый файл: Avito_ru_{batch_name}.xlsx")
            except Exception as err:
                logger.error(f"Ошибка при сохранении общего результата ({batch_name}): {err}")


    def parse(self):
        """Запускает два отдельных прогона: для новых и для б/у устройств."""
        logger.info("=== Начинаю batch: NEW ===")
        self.parse_urls(self.config.new_urls, "new")

        logger.info("=== Начинаю batch: USED ===")
        self.parse_urls(self.config.used_urls, "used")

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
        if not self.config.parse_phone or self.config.parse_phone:
            # future feat
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

    def is_viewed(self, ad: Item) -> bool:
        """Проверяет, есть ли уже такое объявление в локальной БД."""
        return self.db_handler.record_exists(record_id=ad.id, price=ad.priceDetailed.value)

    @staticmethod
    def _is_recent(timestamp_ms: int, max_age_seconds: int) -> bool:
        now = datetime.utcnow()
        published_time = datetime.utcfromtimestamp(timestamp_ms / 1000)
        return (now - published_time) <= timedelta(seconds=max_age_seconds)

    def __save_viewed(self, ads: list[Item]) -> None:
        """Сохраняет просмотренные объявления"""
        try:
            self.db_handler.add_record_from_page(ads=ads)
        except Exception as err:
            logger.info(f"При сохранении в БД ошибка {err}")

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


if __name__ == "__main__":
    try:
        config = load_avito_config("config.toml")
    except Exception as err:
        logger.error(f"Ошибка загрузки конфига: {err}")
        exit(1)

    while True:
        try:
            parser = AvitoParse(config)
            parser.parse()
            if config.one_time_start:
                logger.info("Парсинг завершен т.к. включён one_time_start в настройках")
                break
            logger.info(f"Парсинг завершен. Пауза {config.pause_general} сек")
            time.sleep(config.pause_general)
        except Exception as err:
            logger.exception(err)
            logger.error(f"Произошла ошибка {err}. Будет повторный запуск через 30 сек.")
            time.sleep(30)
