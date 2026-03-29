import re
from typing import List

from loguru import logger

from dto import AvitoConfig
from models import Item


WATCH_BLACKLIST = [
    "ремеш",
    "браслет",
    "strap",
    "band",
    "кабель",
    "зарядк",
    "charger",
    "cable",
    "dock",
    "док-станц",
    "чехол",
    "case",
    "glass",
    "стекл",
    "защитное стекло",
    "пленк",
    "protect",
    "protector",
    "sensor",
    "датчик",
    "пульсометр",
    "hrm",
    "нагрудный",
    "гармин hrm",
    "запчаст",
    "дисплей",
    "экран",
    "корпус",
    "без часов",
    "только ремешок",
    "только браслет",
    "коробка",
]

# Явные маркеры фейков, копий, бандлов и аксессуаров под Apple Watch
APPLE_WATCH_BLACKLIST = [
    "hk 11",
    "hk11",
    "pro max",
    "watch x",
    "copy",
    "копия",
    "реплика",
    "1:1",
    "airpods",
    "pods",
    "наушники",
    "зарядка",
    "charger",
    "loop",
    "metal loop",
    "metall loop",
]


WATCH_ALLOWLIST_EXCEPTIONS = [
    "apple watch",
    "samsung galaxy watch",
    "huawei watch",
    "honor watch",
    "garmin",
    "amazfit",
    "xiaomi watch",
    "redmi watch",
    "pixel watch",
    "oneplus watch",
    "forerunner",
    "fenix",
    "venu",
    "epix",
    "instinct",
    "lily",
    "tactix",
    "marq",
    "vivomove",
    "vivoactive",
    "approach",
    "enduro",
    "quatix",
    "descent",
]


class AdsFilter:
    def __init__(self, config: AvitoConfig, is_viewed_fn=None):
        self.config = config
        self.is_viewed_fn = is_viewed_fn

    def apply(self, ads: List[Item]) -> List[Item]:
        """Применяет все фильтры по порядку"""
        filters = [
            # self._filter_viewed,              # временно отключаем
            self._filter_by_price_range,
            self._filter_watch_accessories,
            # self._filter_by_black_keywords,   # временно отключаем
            # self._filter_by_white_keyword,    # временно отключаем
            # self._filter_by_address,          # временно отключаем
            # self._filter_by_seller,           # временно отключаем
            # self._filter_by_recent_time,      # временно отключаем
            # self._filter_by_reserve,          # временно отключаем
            # self._filter_by_promotion,        # временно отключаем
        ]

        for filter_fn in filters:
            ads = filter_fn(ads)
            logger.info(f"После фильтрации {filter_fn.__name__} осталось {len(ads)}")
            if not ads:
                return ads
        return ads

    def _filter_viewed(self, ads: List[Item]) -> List[Item]:
        if self.is_viewed_fn:
            return [ad for ad in ads if not self.is_viewed_fn(ad)]
        return ads

    def _filter_by_price_range(self, ads: List[Item]) -> List[Item]:
        if not self.config.min_price and not self.config.max_price:
            return ads
        try:
            return [ad for ad in ads if self.config.min_price <= ad.priceDetailed.value <= self.config.max_price]
        except Exception:
            return ads

    def _filter_watch_accessories(self, ads: List[Item]) -> List[Item]:
        """
        Убирает аксессуары и не-часы из выдачи часов:
        ремешки, зарядки, стекла, датчики и т.п.
        """
        filtered = []

        for ad in ads:
            title = (ad.title or "").lower()
            description = (ad.description or "").lower()
            full_text = f"{title} {description}"

            has_black_word = any(word in full_text for word in WATCH_BLACKLIST)
            has_watch_signal = any(word in full_text for word in WATCH_ALLOWLIST_EXCEPTIONS)

            # Если нашли явный аксессуар/датчик и при этом нет нормального сигнала, что это именно модель часов — выкидываем.
            if has_black_word and not has_watch_signal:
                continue

            # Отдельный жесткий кейс: Garmin HRM и похожие датчики — это точно не часы.
            if "hrm" in full_text or "пульсометр" in full_text or "нагрудный датчик" in full_text:
                continue

            # Отдельный фильтр под Apple:
            # режем фейки, копии, бандлы и аксессуарные Apple-объявления.
            if self._is_apple_watch_fake_or_bundle(ad):
                continue

            filtered.append(ad)

        return filtered
    
    def _is_apple_watch_fake_or_bundle(self, ad: Item) -> bool:
        """
        Отсекает Apple Watch-фейки, копии, бандлы и аксессуарные объявления.
        """
        title = (ad.title or "").lower()
        description = (ad.description or "").lower()
        full_text = f"{title} {description}"

        # Если вообще не похоже на Apple Watch, выходим
        if "apple watch" not in full_text and not re.search(r"\bs(?:2|3|4|5|6|7|8|9|10|11)\b", full_text):
            return False

        return any(word in full_text for word in APPLE_WATCH_BLACKLIST)

    def _filter_by_black_keywords(self, ads: List[Item]) -> List[Item]:
        if not self.config.keys_word_black_list:
            return ads
        return [ad for ad in ads if not self._is_phrase_in_ads(ad, self.config.keys_word_black_list)]

    def _filter_by_white_keyword(self, ads: List[Item]) -> List[Item]:
        if not self.config.keys_word_white_list:
            return ads
        return [ad for ad in ads if self._is_phrase_in_ads(ad, self.config.keys_word_white_list)]

    def _filter_by_address(self, ads: List[Item]) -> List[Item]:
        if not self.config.geo:
            return ads
        return [ad for ad in ads if self.config.geo in getattr(ad, "geo", {}).get("formattedAddress", "")]

    def _filter_by_seller(self, ads: List[Item]) -> List[Item]:
        if not self.config.seller_black_list:
            return ads
        return [ad for ad in ads if not getattr(ad, "sellerId", None) or ad.sellerId not in self.config.seller_black_list]

    def _filter_by_recent_time(self, ads: List[Item]) -> List[Item]:
        if not self.config.max_age:
            return ads
        from datetime import datetime, timedelta
        now = datetime.utcnow()
        filtered = []
        for ad in ads:
            published = datetime.utcfromtimestamp(ad.sortTimeStamp / 1000)
            if (now - published) <= timedelta(seconds=self.config.max_age):
                filtered.append(ad)
        return filtered

    def _filter_by_reserve(self, ads: List[Item]) -> List[Item]:
        if not self.config.ignore_reserv:
            return ads
        return [ad for ad in ads if not getattr(ad, "isReserved", False)]

    def _filter_by_promotion(self, ads: List[Item]) -> List[Item]:
        if not self.config.ignore_promotion:
            return ads
        for ad in ads:
            ad.isPromotion = any(
                v.get("title") == "Продвинуто"
                for step in (ad.iva or {}).get("DateInfoStep", [])
                for v in step.payload.get("vas", [])
            )
        return [ad for ad in ads if not ad.isPromotion]

    @staticmethod
    def _is_phrase_in_ads(ad: Item, phrases: list) -> bool:
        full_text = ((ad.title or "") + (ad.description or "")).lower()
        return any(phrase.lower() in full_text for phrase in phrases)