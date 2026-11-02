# core/methodology_loader.py
"""
Динамическая загрузка и парсинг файлов методик расследования.
Поддерживает форматы: PDF, DOCX, XLSX, TXT.

СОГЛАШЕНИЕ ОБ ИМЕНОВАНИИ ФАЙЛОВ:
  - По статье:     216_методика.docx, 218_алгоритм.docx, ...
  - Общая:         методика_общий.docx, алгоритм_общий.docx, ...

Если специфический файл не найден — используются общие.
"""

import os
import re

# ---------------------------------------------------------------------------
# Низкоуровневые загрузчики файлов
# ---------------------------------------------------------------------------

def load_text_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        try:
            with open(file_path, 'r', encoding='cp1251') as f:
                return f.read()
        except Exception as e:
            print(f"  Ошибка чтения TXT {file_path}: {e}")
            return None


import sys
import os
import base64
import requests

def ocr_pdf_with_vllm(file_path: str) -> str:
    """
    Разбивает отсканированный PDF на картинки и отправляет их в OCR-сервер (olmOCR).
    Работает кроссплатформенно на любом сервере.
    """
    return _ocr_pdf(file_path)[0]


def _ocr_pdf(file_path: str):
    """OCR по страницам: (текст, число страниц, не распознанных сервером)."""
    api_url = os.environ.get("OCR_API_URL", "http://192.168.165.2:8807/v1")
    model = os.environ.get("OCR_MODEL", "olmOCR-2-7B-1025")

    try:
        import fitz
    except ImportError:
        print("  ⚠ OCR недоступно: требуется библиотека PyMuPDF (установите: pip install PyMuPDF)")
        return "", 1

    full_text = ""
    failed_pages = 0
    try:
        print(f"  👁 Запуск OCR ({model}) для файла: {os.path.basename(file_path)}")
        doc = fitz.open(file_path)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(dpi=150)
            img_bytes = pix.tobytes("jpeg")
            img_b64 = base64.b64encode(img_bytes).decode('utf-8')

            payload = {
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "Extract text from this document image exactly as it appears. Maintain the structure and layout."},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{img_b64}"
                                }
                            }
                        ]
                    }
                ],
                "max_tokens": 4096,
                "temperature": 0.1
            }
            
            headers = {"Content-Type": "application/json"}
            url = f"{api_url.rstrip('/')}/chat/completions"
            
            print(f"    ➡ Отправка страницы {i+1}/{len(doc)} на OCR-сервер...")
            try:
                session = requests.Session()
                session.trust_env = False
                response = session.post(url, json=payload, headers=headers, timeout=180)
                response.raise_for_status()
                res_data = response.json()
                text = res_data["choices"][0]["message"]["content"]
                
                if text:
                    full_text += text + "\n\n"
            except requests.exceptions.RequestException as req_e:
                failed_pages += 1
                print(f"    ⚠ Ошибка ответа от OCR-сервера: {req_e}")

    except Exception as e:
        print(f"  ⚠ Критическая ошибка при OCR PDF: {e}")
        return "", 1

    return full_text.strip(), failed_pages


def _text_layer_quality(text: str) -> float:
    """
    Доля символов, входящих в настоящие слова (3+ букв подряд). У скана с «мусорным» текстовым слоем
    («п .. Н ИЧ· КИ а п щ ни а_ ... и и р • •») она низкая, у нормального протокола — около 0.7–0.8.
    """
    chars = re.sub(r'\s+', '', text or '')
    if not chars:
        return 0.0
    words = re.findall(r'[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]{3,}', text)
    return sum(map(len, words)) / len(chars)


def _ocr_cache_path(file_path: str) -> str:
    """Кэш OCR по содержимому файла (sha256): переименованный файл не распознаётся заново, изменённый — распознаётся."""
    import hashlib
    h = hashlib.sha256()
    with open(file_path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            h.update(block)
    cache_dir = os.environ.get("OCR_CACHE_DIR", os.path.join("data", "ocr_cache"))
    return os.path.join(cache_dir, f"{h.hexdigest()}.txt")


def _ocr_with_cache(file_path: str) -> str:
    try:
        cache_path = _ocr_cache_path(file_path)
    except OSError:
        cache_path = None
    if cache_path and os.path.exists(cache_path):
        with open(cache_path, encoding='utf-8') as f:
            text = f.read()
        print(f"    ♻ OCR-текст взят из кэша ({len(text)} симв.)")
        return text
    text, failed_pages = _ocr_pdf(file_path)
    # Частично распознанный документ не кэшируем: при следующем запуске OCR повторится
    if text and not failed_pages and cache_path:
        try:
            os.makedirs(os.path.dirname(cache_path), exist_ok=True)
            with open(cache_path, 'w', encoding='utf-8') as f:
                f.write(text)
        except OSError as e:
            print(f"    ⚠ Не удалось сохранить OCR-кэш: {e}")
    elif failed_pages:
        print(f"    ⚠ OCR: не распознано страниц — {failed_pages}; текст в кэш не сохранён")
    return text


def _substantive_len(text: str) -> int:
    """Длина текста без строк, повторяющихся 2+ раза (штамп ЭЦП, колонтитул на каждой странице)."""
    lines = [l.strip() for l in (text or '').splitlines() if l.strip()]
    counts = {}
    for l in lines:
        counts[l] = counts.get(l, 0) + 1
    return sum(len(l) for l in lines if counts[l] == 1)


def load_pdf_file(file_path):
    result = None
    # Пытаемся использовать PyMuPDF (fitz) так как он лучше читает сложные/сканированные PDF
    try:
        import fitz
        text_parts = []
        with fitz.open(file_path) as doc:
            for page in doc:
                text = page.get_text()
                if text:
                    text_parts.append(text)
        result = "\n".join(text_parts).strip()
    except ImportError:
        pass  # Fallback to pypdf if fitz not installed
    except Exception as e:
        print(f"  Ошибка чтения PDF через fitz {file_path}: {e}")

    # Fallback на pypdf если fitz не отработал или вернул пустой результат
    if not result:
        try:
            from pypdf import PdfReader
            reader = PdfReader(file_path)
            result = "\n".join(p.extract_text() for p in reader.pages if p.extract_text()).strip()
        except Exception as e:
            pass

    # Если текст пустой (скан), делаем OCR через локальный сервер (olmOCR)
    quality = _text_layer_quality(result) if result else 0.0
    # Скан со штампом ЭЦП: текстовый слой — только повторяющаяся на каждой странице строка подписи
    substantive = _substantive_len(result) if result else 0
    if not result or len(result) < 50 or quality < 0.6 or substantive < 100:
        if not result or len(result) < 50:
            reason = f"текст: {len(result) if result else 0} симв."
        elif quality < 0.6:
            reason = f"текстовый слой нечитаем — доля слов {quality:.0%}"
        else:
            reason = f"в текстовом слое только штампы/подписи ({substantive} симв. по существу)"
        print(f"  👁 Обнаружен отсканированный PDF '{os.path.basename(file_path)}' ({reason}). Запуск OCR-сервера...")
        ocr_text = _ocr_with_cache(file_path)
        if ocr_text:
            print(f"    ✅ OCR успешно извлек {len(ocr_text)} символов")
            return ocr_text
        else:
            print("    ⚠ OCR-сервер не смог извлечь текст.")

    return result if result else None


def load_docx_file(file_path):
    try:
        from docx import Document
        doc = Document(file_path)
        parts = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(c.text.strip() for c in row.cells if c.text.strip())
                if row_text:
                    parts.append(row_text)
        return "\n".join(parts) or None
    except Exception as e:
        print(f"  Ошибка чтения DOCX {file_path}: {e}")
        return None


def load_xlsx_file(file_path):
    try:
        import pandas as pd
        xls = pd.ExcelFile(file_path)
        parts = []
        for sheet in xls.sheet_names:
            df = pd.read_excel(xls, sheet_name=sheet)
            parts.append(f"--- Лист: {sheet} ---")
            for _, row in df.iterrows():
                vals = [f"{c}: {row[c]}" for c in df.columns if pd.notna(row[c]) and str(row[c]).strip()]
                if vals:
                    parts.append(" | ".join(vals))
        return "\n".join(parts) or None
    except Exception as e:
        print(f"  Ошибка чтения XLSX {file_path}: {e}")
        return None


FILE_LOADERS = {
    '.txt':  load_text_file,
    '.pdf':  load_pdf_file,
    '.docx': load_docx_file,
    '.xlsx': load_xlsx_file,
}

# ---------------------------------------------------------------------------
# Старый API (обратная совместимость)
# ---------------------------------------------------------------------------

def load_all_methodologies(directory):
    """Загружает все файлы методик. Возвращает list[{"filename", "text"}]."""
    methodologies = []
    if not os.path.exists(directory):
        print(f"Папка методик не найдена: {directory}")
        return methodologies
    print(f"📚 Загрузка методик из: {directory}")
    for filename in sorted(os.listdir(directory)):
        if filename.startswith('.') or filename.startswith('~$'):
            continue
        fp = os.path.join(directory, filename)
        if not os.path.isfile(fp):
            continue
        ext = os.path.splitext(filename)[1].lower()
        loader = FILE_LOADERS.get(ext)
        if not loader:
            continue
        print(f"  📄 Загружаю: {filename}")
        text = loader(fp)
        if text and text.strip():
            methodologies.append({"filename": filename, "text": text.strip()})
            print(f"     ✅ {len(text)} символов")
    print(f"📚 Загружено {len(methodologies)} файлов методик")
    return methodologies


def build_methodology_context(methodologies, article=None, max_chars=30000):
    """Собирает контекст методик для LLM, приоритизируя по статье."""
    if not methodologies:
        return ""
    if article:
        m = re.match(r'(\d+)', str(article))
        num = m.group(1) if m else str(article)
        def score(x):
            s = (-2 if num in x["text"].lower() else 0) + (-3 if num in x["filename"].lower() else 0)
            return s
        methodologies = sorted(methodologies, key=score)
    parts, total = [], 0
    for m in methodologies:
        header = f"\n=== Методика: {m['filename']} ===\n"
        text = m["text"]
        rem = max_chars - total - len(header)
        if rem <= 0:
            break
        if len(text) > rem:
            text = text[:rem] + "\n[... обрезан ...]"
        parts.append(header + text)
        total += len(header) + len(text)
    result = "\n".join(parts)
    print(f"📑 Контекст методик: {len(result)} символов из {max_chars} макс.")
    return result


# ---------------------------------------------------------------------------
# Группы ключевых слов для сопоставления действий со сроками
# ---------------------------------------------------------------------------
# Группы ключевых слов — порядок важен (более специфичные — первые)
ACTION_KEYWORD_GROUPS = [
    ("арест_имущества",     ["арест на имущество", "наложить арест", "арест имущества",
                             "обеспечения возмещения ущерба"]),
    ("осмотр_изъятых",      ["осмотр и анализ изъятых", "осмотр изъятых", "вещественных доказательств",
                             "приобщить к материалам", "осмотр предметов и документов"]),
    ("экспертиза",          ["судебно-экономическую экспертизу", "экономическую экспертизу",
                             "назначить экспертизу", "назначить судебно", "аудит",
                             "судебную экспертизу", "экспертизы"]),
    ("допрос",              ["допрос фигурантов", "произвести допрос", "допросить",
                             "провести допрос", "допрос свидетел", "допрос подозреваемых",
                             "допрос потерпевш", "допроса"]),
    ("обыск",               ["личный обыск", "произвести обыск", "обыска по месту",
                             "обыск по месту", "провести обыск", "санкционировать обыск",
                             "обыски", "провести обыска"]),
    ("анализ_ддс",          ["анализ по движению денежных средств", "движению денежных средств",
                             "анализ движения денежных средств", "финансовый анализ",
                             "анализ финансовых операций", "анализ платежей",
                             "переводов посредством платежных систем",
                             "western union", "золотая корона", "qiwi"]),
    ("крипто",              ["binance", "bybit", "kraken", "криптовалютн", "крипто",
                             "цифровых активов", "криптокошельков", "криптобирж"]),
    ("нао_рк",              ["нао рк", "некоммерческое акционерное",
                             "регистрационные данные на компании",
                             "регистрационные данные по юридическим"]),
    ("кгд",                 ["комитет государственных доходов", "эсф",
                             "электронным счетам-фактурам", "электронных счет-фактур",
                             "запросить сведения с кгд", "запросить с кгд",
                             "уведомлениям", "извещениям", "кгд рк", "кгд по"]),
    ("выемка_банк",         ["выемку в банк", "выемка в банк", "банк-клиент",
                             "банках второго уровня", "банковских выписок",
                             "санкционировать и произвести выемку",
                             "народный банк", "kaspi bank", "fortebank", "jusan",
                             "bereke bank", "jysan", "bank rbk", "нурбанк",
                             "евразийский банк", "bank vtb", "отбасы банк",
                             "банк центркредит", "фридом финанс",
                             "движение по счету", "банковских счетах"]),
    ("имущество_запрос",    ["сиопсо", "транспортных средств", "недвижимост",
                             "доли в уставном капитале", "доля в уставном",
                             "транспортное средство", "автотранспорт",
                             "запросить сведения о транспортных",
                             "запросить сведения об имуществ",
                             "имуществу фигурантов", "имущество фигурантов",
                             "сведения о транспортных", "правительство для граждан",
                             "автомобил", "lexus", "kia"]),
    ("нсд_поручение",       ["негласных следственных действий", "поручение в оперативное",
                             "поручение оперативному", "поручение оу",
                             "поручение в оу", "нсд", "снятие информации с каналов",
                             "контроль переговоров"]),
    ("сорм",                ["изучить материалы сорм", "материалы сорм",
                             "сорм", "специальных оперативно-розыскных",
                             "оперативные материалы"]),
]

# Дефолтные сроки (дни) — если в методике не найден паттерн
DEFAULT_DAYS = {
    "сорм":             3,
    "нсд_поручение":    3,
    "имущество_запрос": 1,
    "выемка_банк":      3,
    "кгд":              9,
    "нао_рк":           9,
    "крипто":          10,
    "анализ_ддс":      21,
    "обыск":            5,
    "допрос":           7,
    "экспертиза":      45,
    "осмотр_изъятых":  60,
    "арест_имущества": 90,
}

# ---------------------------------------------------------------------------
# Парсинг сроков из текста методики
# ---------------------------------------------------------------------------

def _extract_days_value(text: str):
    """Извлекает число дней/суток из строки текста."""
    # "N–M дней/суток" → берём максимум
    m = re.search(r"(\d+)\s*[–\-]\s*(\d+)\s*(?:дней|суток|дня|день)", text, re.IGNORECASE)
    if m:
        return int(m.group(2))
    # "N дней" / "до N суток" / "срок N дней"
    m = re.search(r"(?:до|в срок|срок)?\s*(\d+)\s*(?:дней|суток|дня|день)", text, re.IGNORECASE)
    if m:
        return int(m.group(1))
    # "до 1 месяца"
    m = re.search(r"до\s*(\d+)\s*месяц", text, re.IGNORECASE)
    if m:
        return int(m.group(1)) * 30
    # "2–3 месяца"
    m = re.search(r"(\d+)\s*[–\-]\s*(\d+)\s*месяц", text, re.IGNORECASE)
    if m:
        return int(m.group(2)) * 30
    return None


def parse_deadline_rules(methodology_text: str) -> dict:
    """
    Парсит текст методики и строит словарь {тип_действия: кол-во_дней}.

    Алгоритм:
    1. Разбить текст на абзацы
    2. Для каждого типа действия найти абзацы с ключевыми словами
    3. В том же / следующих 2 абзацах найти число дней
    4. Заполнить дефолтами для ненайденных типов

    Returns:
        dict: {тип_действия: дни, ...}
    """
    if not methodology_text:
        return DEFAULT_DAYS.copy()

    days_map: dict = {}
    paragraphs = [p.strip() for p in re.split(r"\n+", methodology_text) if p.strip()]

    for action_type, keywords in ACTION_KEYWORD_GROUPS:
        if action_type in days_map:
            continue
        for i, para in enumerate(paragraphs):
            para_lower = para.lower()
            if not any(kw.lower() in para_lower for kw in keywords):
                continue
            # Ищем срок в том же абзаце
            days = _extract_days_value(para)
            if days is not None:
                days_map[action_type] = days
                break
            # Иначе — в следующих 2 абзацах
            for next_para in paragraphs[i+1:i+3]:
                days = _extract_days_value(next_para)
                if days is not None:
                    days_map[action_type] = days
                    break
            if action_type in days_map:
                break

    # Дефолты для типов без правил
    for k, v in DEFAULT_DAYS.items():
        days_map.setdefault(k, v)

    return days_map


def match_action_days(action_text: str, days_map: dict) -> int:
    """
    По тексту следственного действия возвращает кол-во дней из days_map.

    Args:
        action_text: Текст действия (любой регистр)
        days_map:    Словарь из parse_deadline_rules()

    Returns:
        int: количество дней
    """
    lower = action_text.lower()
    for action_type, keywords in ACTION_KEYWORD_GROUPS:
        if any(kw.lower() in lower for kw in keywords):
            days = days_map.get(action_type, DEFAULT_DAYS.get(action_type, 30))
            print(f"  [срок] '{action_text[:50]}...' → {action_type} = {days} дн.")
            return days
    print(f"  [срок⚠] Не совпало ни одно ключевое слово для: '{action_text[:60]}' → 30 дн. по умолчанию")
    return days_map.get("_default", 30)


# ---------------------------------------------------------------------------
# Главный публичный API
# ---------------------------------------------------------------------------

def _article_num(article) -> str:
    """«217 ч.3» -> «217», «301-1 ч.2» -> «301-1», 'Н/Д' -> ''."""
    m = re.search(r"\d+(?:-\d+)?", str(article or ""))
    return m.group(0) if m else ""


def _is_article_file(filename: str, article_num: str) -> bool:
    """
    Файл методики относится к статье, только если номер в начале имени совпадает ЦЕЛИКОМ:
    для «21» не подходит «214_методика.docx», для «301» не подходит «301-1_методика.docx».
    """
    if not article_num:
        return False
    m = re.match(r"(\d+(?:-\d+)?)(?=[_\s.\-]|$)", filename)
    return bool(m) and m.group(1) == article_num


_SUBTYPE_RE = re.compile(r"(?m)^[ \t]*ВИД\s*:")
_SUBTYPE_KW_RE = re.compile(r"(?im)^\s*ПРИЗНАКИ\s+ВИДА\s*:\s*(.+)$")


def _subtype_keywords(text: str) -> list:
    """Строка раздела вида «ПРИЗНАКИ ВИДА: табач; сигарет; подакциз» -> список regex-основ."""
    m = _SUBTYPE_KW_RE.search(text or "")
    return [k.strip().lower() for k in m.group(1).split(";") if k.strip()] if m else []


def _subtype_score(keywords: list, fabula: str) -> int:
    """Сколько разных признаков вида встречается в фабуле."""
    low = (fabula or "").lower()
    score = 0
    for kw in keywords:
        try:
            score += bool(re.search(kw, low))
        except re.error:
            score += kw in low
    return score


def select_subtype_section(text: str, fabula: str) -> str:
    """
    Методика статьи может делиться на виды преступления: разделы, начинающиеся строкой «ВИД: …», со строкой
    «ПРИЗНАКИ ВИДА: …». В план уходит общая часть (всё до первого «ВИД:») и ОДИН раздел — с наибольшим числом
    признаков в фабуле. Без совпадений — только общая часть. Методика без разделов возвращается как есть.
    """
    starts = [m.start() for m in _SUBTYPE_RE.finditer(text or "")]
    if not starts:
        return text
    base = text[:starts[0]].rstrip()
    best = None
    for i, st in enumerate(starts):
        section = text[st: starts[i + 1] if i + 1 < len(starts) else len(text)].strip()
        title = section.split("\n", 1)[0].strip()
        score = _subtype_score(_subtype_keywords(section), fabula)
        print(f"  🔎 {title[:70]}: совпадений признаков в фабуле — {score}")
        if score and (best is None or score > best[0]):
            best = (score, title, _SUBTYPE_KW_RE.sub("", section, count=1).strip())
    if not best:
        print("📋 Признаки видов преступления в фабуле не найдены — только общая часть методики")
        return base
    print(f"📋 Выбран раздел методики: {best[1]}")
    return f"{base}\n{best[2]}"


def load_specific_or_general(directory: str, article: str, fabula: str = "") -> dict:
    """
    ВАРИАНТ A: Загружает методику с жёстким приоритетом специфичной.

    Логика:
    1. Если есть файл NNN_*.docx для данной статьи → используем ТОЛЬКО ЕГО.
       Если методика делится на виды («ВИД: …»), в план уходит общая часть и один раздел вида по фабуле.
    2. Если специфичной нет → используем только общую методику.
    3. Никогда не объединяем обе — это устраняет дублирование и раздувание плана.

    Returns:
        {"text": str, "filename": str, "source": "specific"|"general"|"none"}
    """
    article_num = _article_num(article)

    if not os.path.exists(directory):
        print(f"⚠ Папка методик не найдена: {directory}")
        return {"text": "", "filename": "", "source": "none"}

    files = [
        f for f in sorted(os.listdir(directory))
        if os.path.isfile(os.path.join(directory, f))
        and not f.startswith(".")
        and not f.startswith("~$")
        and os.path.splitext(f)[1].lower() in FILE_LOADERS
    ]

    # 1. Ищем специфичную методику для данной статьи (файл начинается с номера)
    spec_files = [f for f in files if _is_article_file(f, article_num)]
    if spec_files:
        selected = spec_files
        source = "specific"
        print(f"📋 [Вариант A] СПЕЦИФИЧНАЯ методика ст. {article_num}: {selected}")
    else:
        # 2. Fallback: только общая методика
        general_kw = ("общий", "общая", "алгоритм", "general", "default", "фабула")
        general_files = [
            f for f in files
            if not re.match(r"^\d+", f)  # не начинается с цифры
            and any(kw in f.lower() for kw in general_kw)
        ]
        # Методики по ДРУГИМ статьям не подмешиваем: план по ст.214 не должен строиться
        # по методике ст.216/190 (иначе в плане появляются «фиктивные счета-фактуры» и т.п.)
        selected = general_files
        source = "general" if general_files else "none"
        print(f"📋 [Вариант A] Специфичной нет → ОБЩАЯ методика: {selected}")

    texts = []
    for fname in selected:
        ext = os.path.splitext(fname)[1].lower()
        loader = FILE_LOADERS.get(ext)
        if loader:
            t = loader(os.path.join(directory, fname))
            if t:
                texts.append(f"=== {fname} ===\n{select_subtype_section(t, fabula)}")

    combined = "\n\n".join(texts)
    print(f"📑 Методика ({source}) загружена: {len(combined)} символов")
    return {"text": combined, "filename": ", ".join(selected), "source": source}


def load_methodology_for_article(directory: str, article: str) -> dict:
    """
    Загружает методику для указанной статьи УК РК.
    Устаревший метод — объединяет общую и специфичную.
    Для нового поведения (Вариант A) используйте load_specific_or_general().

    Порядок поиска:
    1. Файл начинается с номера статьи: '216_*.docx'
    2. Файл содержит 'общий'/'алгоритм'/'методика' в имени
    3. Все файлы в папке

    Returns:
        {"text": str, "days_map": dict, "filename": str}
    """
    article_num = _article_num(article)

    if not os.path.exists(directory):
        print(f"⚠ Папка методик не найдена: {directory}")
        return {"text": "", "days_map": DEFAULT_DAYS.copy(), "filename": ""}

    files = [
        f for f in sorted(os.listdir(directory))
        if os.path.isfile(os.path.join(directory, f))
        and not f.startswith(".")
        and not f.startswith("~$")
        and os.path.splitext(f)[1].lower() in FILE_LOADERS
    ]

    general = []
    spec = []

    # 1. Ищем общую методику
    general_kw = ("общий", "общая", "алгоритм", "general", "default", "фабула")
    for f in files:
        # Исключаем файлы, которые начинаются с цифры (это номера статей)
        if not re.match(r"^\d+", f):
            if any(kw in f.lower() for kw in general_kw):
                general.append(f)

    # 2. Ищем специфичную методику по статье
    if article_num:
        spec = [f for f in files if _is_article_file(f, article_num)]

    # 3. Объединяем: сначала ОБЩАЯ, потом СПЕЦИФИЧНАЯ (чтобы специфичная дополняла)
    selected = general + spec

    # 4. Fallback: если ничего не найдено
    if not selected:
        # Методики по другим статьям не подмешиваем
        print(f"📋 Методика для ст. {article_num} и общая методика не найдены")
    else:
        if general:
            print(f"📋 Найдена ОБЩАЯ методика: {general}")
        if spec:
            print(f"📋 Найдена СПЕЦИФИЧНАЯ методика для ст. {article_num}: {spec}")

    # Читаем и объединяем
    texts = []
    for fname in selected:
        ext = os.path.splitext(fname)[1].lower()
        loader = FILE_LOADERS.get(ext)
        if loader:
            t = loader(os.path.join(directory, fname))
            if t:
                texts.append(f"=== {fname} ===\n{t}")

    combined = "\n\n".join(texts)
    print(f"📑 Методика загружена. Сроки будут заданы нейросетью на основе текста.")

    return {"text": combined, "filename": ", ".join(selected)}
