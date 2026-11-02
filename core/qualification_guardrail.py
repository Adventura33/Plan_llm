# core/qualification_guardrail.py
"""
Модуль защиты квалификации (Qualification Guardrail).
Обеспечивает иммутабельность статьи УК РК, предотвращает галлюцинацию квалификации (переквалификацию)
и исключает использование статей Общей части УК РК (напр. ст. 63 УК РК - условное осуждение)
и статей других кодексов (УПК РК, КоАП РК).
"""

import re
from typing import Optional

# Общая часть УК РК — статьи 1–98, они не могут являться составом преступления
GENERAL_PART_ARTICLES = {str(n) for n in range(1, 99)}

UNKNOWN_ARTICLE = 'Н/Д'

# Номер статьи: 214, 301-1, 190-1 (в казахских текстах бывает «301-1-бабы»)
_ART_NUM = r'(\d{2,3}(?:-\d{1,2})?)'
_PART = r'(?:ч\.?|часть|части|частью)\s*(\d{1,2})'
_POINT = r'(?:п\.?|пункт|пункта)\s*(\d{1,2})'

# Статья, за которой следует другой кодекс — не квалификация по УК
_OTHER_CODE = re.compile(r'^\s*(?:ч\.?\s*\d+\s*)?(?:п\.?\s*\d+\s*)?(?:УПК|КоАП|ГК|НК|ГПК|АППК|Кодекс)', re.IGNORECASE)


def _is_general_part(article_num: Optional[str]) -> bool:
    if not article_num:
        return False
    return article_num.split('-')[0] in GENERAL_PART_ARTICLES


def _format(art: str, part: Optional[str] = None, point: Optional[str] = None) -> str:
    res = art
    if part:
        res += f" ч.{part}"
    if point:
        res += f" п.{point}"
    return res


def _find_articles(text: str) -> list:
    """
    Находит все упоминания статей в тексте. Возвращает список (article, part, point, pos, code_hint),
    где code_hint = 'УК' | 'другой' | '' (кодекс не указан).
    Поддерживаемые формы:
      «ч.3 ст.217 УК РК», «п.1 ч.2 ст.190», «ст.214 ч.2», «статьи 301-1 части 2»,
      «214-бабы, 2-бөлігі», «217-бабының 3-бөлігі», «214 ч.2» (голая строка из LLM).
    """
    if not text:
        return []
    found = []

    # 1. «[п.N] [ч.N] ст.NNN» — часть ПЕРЕД статьёй (самая частая юридическая форма)
    pat_prefix = re.compile(
        rf'(?:(?<![А-Яа-яЁё]){_POINT}\s*,?\s*)?(?:(?<![А-Яа-яЁё]){_PART}\s*,?\s*)?'
        rf'(?<![А-Яа-яЁё])(?:ст\.?|статья|статьи|статьей|статье)\s*{_ART_NUM}'
        rf'(?:\s*,?\s*{_PART})?(?:\s*,?\s*{_POINT})?',
        re.IGNORECASE)
    for m in pat_prefix.finditer(text):
        point = m.group(1) or m.group(5)
        part = m.group(2) or m.group(4)
        art = m.group(3)
        tail = text[m.end():m.end() + 25]
        found.append((art, part, point, m.start(), _code_hint(tail)))

    # 2. Казахская форма: «217-бабының 3-бөлігі», «214-бабы, 2-бөлігі»
    pat_kaz = re.compile(rf'{_ART_NUM}\s*-\s*баб\w*\s*,?\s*(?:(\d{{1,2}})\s*-\s*бөлі\w*)?', re.IGNORECASE)
    for m in pat_kaz.finditer(text):
        tail = text[m.end():m.end() + 25]
        found.append((m.group(1), m.group(2), None, m.start(), _code_hint(tail)))

    found.sort(key=lambda x: x[3])
    return found


def _code_hint(tail: str) -> str:
    if _OTHER_CODE.match(tail):
        return 'другой'
    if re.match(r'^\s*(?:ч\.?\s*\d+\s*)?(?:п\.?\s*\d+\s*)?(?:УК|ҚК|Уголовного кодекса|Қылмыстық)', tail, re.IGNORECASE):
        return 'УК'
    return ''


def normalize_article_string(article_str: str) -> str:
    """
    Нормализует строку статьи УК РК к виду «NNN[-N] ч.N [п.N]».
    Примеры: «ч.3 ст.217 УК РК» -> «217 ч.3», «214-бабы, 2-бөлігі» -> «214 ч.2», «301-1 ч.2» -> «301-1 ч.2».
    Возвращает 'Н/Д', если статью распознать не удалось (без подстановки «214» по умолчанию).
    """
    if not article_str or str(article_str).strip() in ('Н/Д', 'н/д', '-', ''):
        return UNKNOWN_ARTICLE

    res = str(article_str).strip()

    candidates = [c for c in _find_articles(res) if c[4] != 'другой']
    if candidates:
        # Предпочитаем явно помеченную УК и не из Общей части
        special = [c for c in candidates if not _is_general_part(c[0])]
        best = next((c for c in special if c[4] == 'УК'), None) or (special[0] if special else candidates[0])
        return _format(best[0], best[1], best[2])

    # Голая строка от LLM: «214 ч.2», «301-1», «217, ч.3»
    m = re.search(rf'{_ART_NUM}(?:\s*,?\s*{_PART})?(?:\s*,?\s*{_POINT})?', res, re.IGNORECASE)
    if m:
        return _format(m.group(1), m.group(2), m.group(3))

    return UNKNOWN_ARTICLE


def extract_article_number(article_str: str) -> Optional[str]:
    """
    Извлекает номер основной статьи (напр., «214 ч.2» -> «214», «301-1 ч.2» -> «301-1»).
    """
    if not article_str:
        return None
    match = re.search(_ART_NUM, str(article_str))
    if match:
        return match.group(1)
    return None


def extract_uk_article_from_text(text: str) -> Optional[str]:
    """
    Детерминированно находит квалификацию по УК РК в тексте документа (рапорта / постановления).
    Игнорирует ссылки на УПК/КоАП и статьи Общей части.
    Приоритет: статьи с явным указанием «УК» → первые по тексту.
    """
    candidates = [c for c in _find_articles(text or '')
                  if c[4] != 'другой' and not _is_general_part(c[0])]
    if not candidates:
        return None
    best = next((c for c in candidates if c[4] == 'УК'), candidates[0])
    return _format(best[0], best[1], best[2])


def sanitize_qualification(extracted_article: str, erdr_article: Optional[str] = None,
                           source_text: Optional[str] = None) -> str:
    """
    Валидирует и санирует квалификацию УК РК.
    1. Если передан erdr_article (из официального источника/базы), он является ИММУТАБЕЛЬНЫМ.
    2. Статьи Общей части (напр. ст. 63) и статьи УПК отклоняются.
    3. Если передан source_text, статья от LLM сверяется с текстом документа: если её номер
       не упоминается в тексте как статья УК, берётся статья, найденная в тексте детерминированно.
    Никогда не подставляет «214» по умолчанию — при отсутствии данных возвращает 'Н/Д'.
    """
    normalized_erdr = normalize_article_string(erdr_article) if erdr_article else UNKNOWN_ARTICLE
    erdr_num = extract_article_number(normalized_erdr)
    if erdr_num and not _is_general_part(erdr_num):
        return normalized_erdr

    text_article = extract_uk_article_from_text(source_text) if source_text else None
    text_nums = {c[0] for c in _find_articles(source_text or '') if c[4] != 'другой'}

    normalized_ext = normalize_article_string(extracted_article) if extracted_article else UNKNOWN_ARTICLE
    ext_num = extract_article_number(normalized_ext)

    if ext_num and _is_general_part(ext_num):
        print(f"  🛡 [QualificationGuardrail] Отклонена статья Общей части УК РК: ст.{ext_num}")
        ext_num = None
    elif ext_num and source_text and text_nums and ext_num not in text_nums:
        print(f"  🛡 [QualificationGuardrail] Статья ст.{ext_num} от LLM не встречается в тексте документа "
              f"как статья УК — используется статья из текста: {text_article}")
        ext_num = None

    if ext_num:
        # Если LLM потерял часть статьи, а в тексте она есть — дополняем
        if text_article and extract_article_number(text_article) == ext_num and 'ч.' not in normalized_ext:
            return text_article
        return normalized_ext

    return text_article or UNKNOWN_ARTICLE
