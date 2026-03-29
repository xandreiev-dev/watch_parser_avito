import re


WATCH_BRANDS = [
    ("apple", "Apple"),
    ("samsung", "Samsung"),
    ("huawei", "Huawei"),
    ("honor", "Honor"),
    ("garmin", "Garmin"),
    ("amazfit", "Amazfit"),
    ("xiaomi", "Xiaomi"),
    ("redmi", "Xiaomi"),
    ("google", "Google"),
    ("pixel", "Google"),
    ("oneplus", "OnePlus"),
]


COLOR_KEYWORDS = [
    "black", "white", "silver", "gold", "gray", "grey", "blue", "green", "red",
    "pink", "purple", "orange", "yellow", "beige", "brown", "titanium", "graphite",
    "midnight", "starlight", "slate", "cream", "ivory",
    "черный", "чёрный", "белый", "серебристый", "серый", "синий", "зеленый",
    "зелёный", "красный", "розовый", "фиолетовый", "оранжевый", "желтый",
    "жёлтый", "бежевый", "коричневый", "титан", "титановый", "графит",
]

APPLE_COLOR_PATTERNS = [
    ("natural titanium", "Natural Titanium"),
    ("black titanium", "Black Titanium"),
    ("rose gold", "Rose Gold"),
    ("starlight", "Starlight"),
    ("midnight", "Midnight"),
    ("silver", "Silver"),
    ("gold", "Gold"),
    ("pink", "Pink"),
    ("blue", "Blue"),
    ("green", "Green"),
    ("red", "Red"),
    ("graphite", "Graphite"),
    ("черный титан", "Black Titanium"),
    ("черный", "Black"),
    ("чёрный", "Black"),
    ("серебристый", "Silver"),
    ("серый", "Gray"),
    ("золотой", "Gold"),
    ("розовое золото", "Rose Gold"),
    ("розовый", "Pink"),
    ("синий", "Blue"),
    ("зеленый", "Green"),
    ("зелёный", "Green"),
]


WARRANTY_PATTERNS = [
    r"гарант\w*\s*(\d+\s*(?:мес|месяц|месяцев|год|года|лет))",
    r"гарантия\s*(\d+\s*(?:мес|месяц|месяцев|год|года|лет))",
    r"гарантия\s*до\s*([0-9\.]+)",
    r"\b1\s*год\b",
    r"\b12\s*мес\b",
]

MODEL_GARBAGE_PARTS = [
    r"\bmetal loop\b",
    r"\bmetall loop\b",
    r"\bbluetooth\b",
    r"\blte\b",
    r"\bwi[- ]?fi\b",
    r"\b\d{2}\s*mm\b",
    r"\bleather band\b",
    r"\bsilicone band\b",
    r"\bblack\b", r"\bwhite\b", r"\bsilver\b", r"\bgold\b",
    r"\bgray\b", r"\bgrey\b", r"\bblue\b", r"\bgreen\b",
    r"\bred\b", r"\bpink\b", r"\bpurple\b", r"\borange\b",
    r"\byellow\b", r"\bbeige\b", r"\bbrown\b",
    r"\btitanium\b", r"\bgraphite\b", r"\bmidnight\b",
    r"\bstarlight\b", r"\bslate\b", r"\bcream\b", r"\bivory\b",
    r"\bчерный\b", r"\bчёрный\b", r"\bбелый\b", r"\bсеребристый\b",
    r"\bсерый\b", r"\bсиний\b", r"\bзеленый\b", r"\bзелёный\b",
    r"\bкрасный\b", r"\bрозовый\b", r"\bфиолетовый\b", r"\bоранжевый\b",
    r"\bжелтый\b", r"\bжёлтый\b", r"\bбежевый\b", r"\bкоричневый\b",
    r"\bтитан\b", r"\bтитановый\b", r"\bграфит\b",
    r"\bновые\b", r"\bновый\b", r"\bновая\b", r"\bnew\b",
    r"\bбу\b", r"\bб/у\b", r"\bоригинал\b", r"\bв наличии\b",
    r"\bвсе цвета\b", r"\bдоставка\b"
]

# Валидные серии Apple Watch.
# Держим отдельным списком, чтобы потом легко обновлять.
VALID_APPLE_SERIES = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "11"]


def _clean_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def _cleanup_model_value(value: str) -> str:
    value = _clean_text(value or "")
    for g in MODEL_GARBAGE_PARTS:
        value = re.sub(g, "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s+", " ", value).strip(" -_,/")
    return value

def normalize_url(url: str) -> str:
    if not url:
        return ""

    url = url.strip()

    if url.startswith("/"):
        url = f"https://www.avito.ru{url}"
    elif url.startswith("www.avito.ru"):
        url = f"https://{url}"

    url = re.sub(r"^(https?:)/+", r"\1//", url)
    url = re.sub(r"(?<!:)/{2,}", "/", url)

    if url.startswith("https:/") and not url.startswith("https://"):
        url = url.replace("https:/", "https://", 1)
    if url.startswith("http:/") and not url.startswith("http://"):
        url = url.replace("http:/", "http://", 1)

    return url

def extract_brand(title: str, description: str = "") -> str:
    full = f"{title or ''} {description or ''}".lower()
    for key, brand in WATCH_BRANDS:
        if key in full:
            return brand
    return ""


def extract_condition(title: str, description: str = "") -> str:
    full = _clean_text(f"{title or ''} {description or ''}").lower()

    used_patterns = [
        r"\bб/у\b",
        r"\bбу\b",
        r"\bused\b",
        r"\bуцен[а-я]*\b",
        r"\bс пробегом\b",
        r"\bношен[а-я]*\b",
        r"\bносились\b",
        r"\bпосле использования\b",
    ]

    new_patterns = [
        r"\bновые\b",
        r"\bновый\b",
        r"\bновая\b",
        r"\bновое\b",
        r"\bnew\b",
        r"\bзапечатан[а-я]*\b",
        r"\bне активирован[а-я]*\b",
        r"\bгарантия 12 мес\b",
        r"\bгарантия 12 месяцев\b",
        r"\bс завода\b",
        r"\bзапечатанные\b",
        r"\bнеактивированные\b",
        r"\bне активированные\b",
        r"\bне вскрывались\b",
        r"\bс пломбами\b",
    ]

    for pattern in used_patterns:
        if re.search(pattern, full, flags=re.IGNORECASE):
            return "used"

    for pattern in new_patterns:
        if re.search(pattern, full, flags=re.IGNORECASE):
            return "new"

    return ""


def extract_size(title: str, description: str = "") -> str:
    full = _clean_text(f"{title or ''} {description or ''}")

    patterns = [
        r"\b(\d{2})\s*мм\b",
        r"\b(\d{2})mm\b",
        r"\b(4[0-9])\s*mm\b",
        r"\b(4[0-9])\s*мм\b",
        r"\b(5[0-9])\s*mm\b",
        r"\b(5[0-9])\s*мм\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, full, flags=re.IGNORECASE)
        if match:
            return f"{match.group(1)} мм"

    return ""

    # Отдельная логика цветов для Apple Watch.
    # Нужна, чтобы не смешивать фирменные Apple-цвета с общей логикой.

def extract_apple_color(title: str, description: str = "") -> str:
    full = _clean_text(f"{title or ''} {description or ''}").lower()

    found = []
    for raw, normalized in APPLE_COLOR_PATTERNS:
        if re.search(rf"\b{re.escape(raw)}\b", full, flags=re.IGNORECASE):
            found.append(normalized)

    unique = []
    for item in found:
        if item not in unique:
            unique.append(item)

    return ", ".join(unique[:2])

    # Общий dispatcher по цветам.
    # Для брендов со своей палитрой можно подключать отдельные функции.

def extract_color(title: str, description: str = "") -> str:
    full_text = _clean_text(f"{title or ''} {description or ''}")
    lower = full_text.lower()

    # Apple — отдельная цветовая логика
    if "apple watch" in lower or re.search(r"\bs(?:2|3|4|5|6|7|8|9|10|11)\b", lower):
        return extract_apple_color(title, description)

    # Общая базовая логика для остальных брендов
    found = []
    for color in COLOR_KEYWORDS:
        if re.search(rf"\b{re.escape(color)}\b", lower, flags=re.IGNORECASE):
            found.append(color)

    unique = []
    for item in found:
        if item not in unique:
            unique.append(item)

    return ", ".join(unique[:2])


def extract_warranty(title: str, description: str = "") -> str:
    full = _clean_text(f"{title or ''} {description or ''}".lower())

    for pattern in WARRANTY_PATTERNS:
        match = re.search(pattern, full, flags=re.IGNORECASE)
        if match:
            if match.groups():
                return match.group(1)
            return match.group(0)

    return ""

# Garmin: отдельная логика извлечения модели.
# Нужна, чтобы правки под Apple и другие бренды не ломали Garmin.

def extract_garmin_model(title: str, description: str = "") -> str:
    text = _clean_text(f"{title or ''} {description or ''}")
    lower = text.lower()

    garmin_patterns = [
        r"(garmin fenix\s*[0-9a-zx\-\+ ]*)",
        r"(garmin forerunner\s*[0-9a-zx\-\+ ]*)",
        r"(garmin venu\s*[0-9a-zx\-\+ ]*)",
        r"(garmin epix\s*[0-9a-zx\-\+ ]*)",
        r"(garmin instinct\s*[0-9a-zx\-\+ ]*)",
        r"(garmin lily\s*[0-9a-zx\-\+ ]*)",
        r"(garmin tactix\s*[0-9a-zx\-\+ ]*)",
        r"(garmin marq\s*[0-9a-zx\-\+ ]*)",
        r"(garmin vivomove\s*[0-9a-zx\-\+ ]*)",
        r"(garmin vivoactive\s*[0-9a-zx\-\+ ]*)",
        r"(garmin approach\s*[0-9a-zx\-\+ ]*)",
        r"(garmin enduro\s*[0-9a-zx\-\+ ]*)",
        r"(garmin quatix\s*[0-9a-zx\-\+ ]*)",
        r"(garmin descent\s*[0-9a-zx\-\+ ]*)",
    ]

    for pattern in garmin_patterns:
        match = re.search(pattern, lower, flags=re.IGNORECASE)
        if match:
            value = _cleanup_model_value(match.group(1))

            # Нормализуем регистр названия бренда/линейки
            value = re.sub(r"\bgarmin\b", "Garmin", value, flags=re.IGNORECASE)
            value = re.sub(r"\bfenix\b", "Fenix", value, flags=re.IGNORECASE)
            value = re.sub(r"\bforerunner\b", "Forerunner", value, flags=re.IGNORECASE)
            value = re.sub(r"\bvenu\b", "Venu", value, flags=re.IGNORECASE)
            value = re.sub(r"\bepix\b", "Epix", value, flags=re.IGNORECASE)
            value = re.sub(r"\binstinct\b", "Instinct", value, flags=re.IGNORECASE)
            value = re.sub(r"\blily\b", "Lily", value, flags=re.IGNORECASE)
            value = re.sub(r"\btactix\b", "Tactix", value, flags=re.IGNORECASE)
            value = re.sub(r"\bmarq\b", "MARQ", value, flags=re.IGNORECASE)
            value = re.sub(r"\bvivomove\b", "Vivomove", value, flags=re.IGNORECASE)
            value = re.sub(r"\bvivoactive\b", "Vivoactive", value, flags=re.IGNORECASE)
            value = re.sub(r"\bapproach\b", "Approach", value, flags=re.IGNORECASE)
            value = re.sub(r"\benduro\b", "Enduro", value, flags=re.IGNORECASE)
            value = re.sub(r"\bquatix\b", "Quatix", value, flags=re.IGNORECASE)
            value = re.sub(r"\bdescent\b", "Descent", value, flags=re.IGNORECASE)

            return value

    return ""

# Apple: отдельная логика для валидных моделей Apple Watch.
# Поддерживаем реальные линейки Series, SE и Ultra.
# Фейки/копии должны отсеиваться не здесь, а отдельным фильтром объявлений.

def extract_apple_model(title: str, description: str = "") -> str:
    text = _clean_text(f"{title or ''} {description or ''}")
    lower = text.lower()

    series_group = "|".join(VALID_APPLE_SERIES)

    apple_patterns = [
        rf"(apple watch ultra\s*(?:2|3)?)\b",
        rf"(apple watch se(?:\s*(?:2|3|gen\s*2|gen\s*3|2022|2024))?)\b",
        rf"(apple watch series\s*(?:{series_group}))\b",
        rf"(apple watch\s*(?:series\s*)?(?:{series_group}))\b",
        rf"\b(s(?:{series_group}))\b",
    ]

    for pattern in apple_patterns:
        match = re.search(pattern, lower, flags=re.IGNORECASE)
        if match:
            value = _clean_text(match.group(1))

            # Нормализация SE Gen2 / SE 2022 → SE 2
            se_match = re.search(r"apple watch se\s*(gen\s*2|2022|2)", value.lower())
            if se_match:
                return "Apple Watch SE 2"
            se_match_3 = re.search(r"apple watch se\s*(gen\s*3|2024|3)", value.lower())
            if se_match_3:
                return "Apple Watch SE 3"
            # Если нашли короткий формат вроде "S8", нормализуем в Apple Watch Series 8
            short_series_match = re.fullmatch(r"s(2|3|4|5|6|7|8|9|10|11)", value.lower())
            if short_series_match:
                return f"Apple Watch Series {short_series_match.group(1)}"

            value = _cleanup_model_value(value)

            # Нормализуем короткий формат типа "Apple Watch 10" на "Apple Watch Series 10"
            direct_series_match = re.fullmatch(r"apple watch\s*(2|3|4|5|6|7|8|9|10|11)", value.lower())
            if direct_series_match:
                return f"Apple Watch Series {direct_series_match.group(1)}"

            # Нормализуем регистр
            value = re.sub(r"\bapple\b", "Apple", value, flags=re.IGNORECASE)
            value = re.sub(r"\bwatch\b", "Watch", value, flags=re.IGNORECASE)
            value = re.sub(r"\bseries\b", "Series", value, flags=re.IGNORECASE)
            value = re.sub(r"\bse\b", "SE", value, flags=re.IGNORECASE)
            value = re.sub(r"\bultra\b", "Ultra", value, flags=re.IGNORECASE)

            return value

    return ""

    # Общий dispatcher по брендам.
    # Для каждого бренда используем свою функцию извлечения модели.
    # Это позволяет дорабатывать один бренд, не ломая остальные.

def extract_model(title: str, description: str = "") -> str:
    brand = extract_brand(title, description)

    if brand == "Apple":
        return extract_apple_model(title, description)

    if brand == "Garmin":
        return extract_garmin_model(title, description)

    # Общая временная логика для остальных брендов.
    # Когда будем разбирать следующий бренд, вынесем его в отдельную функцию.
    full_text = _clean_text(f"{title or ''} {description or ''}")
    lower = full_text.lower()

    patterns = [
        r"(samsung galaxy watch\s*\d+(?: classic)?(?: pro)?)",
        r"(huawei watch(?: gt)?(?: fit)?(?: d)?(?: ultimate)?\s*[a-z0-9\- ]*)",
        r"(honor watch\s*[a-z0-9\- ]*)",
        r"(amazfit [a-z0-9\- ]+)",
        r"((?:xiaomi|redmi) watch[a-z0-9\- ]*)",
        r"(google pixel watch\s*\d*)",
        r"(oneplus watch\s*\d*)",
    ]

    for pattern in patterns:
        match = re.search(pattern, lower, flags=re.IGNORECASE)
        if match:
            return _cleanup_model_value(match.group(1))

    return ""