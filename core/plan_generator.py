# core/plan_generator.py

import re
import os
import json

from core.templates import INVESTIGATION_PLAN_ACTIONS
from core.case_documents import load_case_documents, load_conclusion_documents

# Лимит символов контекста документов в запросе к модели (ограничение сервера; поднимается вместе с max-model-len)
APPEND_CONTEXT_CHARS = int(os.environ.get("APPEND_CONTEXT_CHARS", "26000"))
# methodology_loader is imported inline in generate_investigation_plan_with_llm

# Определяем маппинг сроков (Устарело - сроки генерирует LLM)
# PERIOD_MAP = {}

def extract_main_uk_article(uk_article_string):
    match = re.match(r'(\d+)', uk_article_string)
    if match:
        return match.group(1)
    return None


def _fix_ooo(text: str) -> str:
    """
    Принудительно заменяет российскую аббревиатуру ООО на казахстанскую ТОО.
    Казахстанское законодательство использует ТОО (товарищество с ограниченной ответственностью).
    """
    if not text:
        return text
    # Заменяем все варианты: ООО, Ооо, оОО и т.д.
    result = re.sub(r'ООО', 'ТОО', text)
    result = re.sub(r'Ооо', 'ТОО', result)
    result = re.sub(r'оoo', 'ТОО', result, flags=re.IGNORECASE)
    return result

# Запросы и поручения самому органу расследования (АФМ, ДЭР, СЭР, их подразделения): по практике следствия в план
# идут только внешние запросы — такие пункты удаляются
_SELF_REQUEST = re.compile(
    r'(?:направить\s+запрос\w*|истребовать(?:\s+сведения|\s+информацию)?|запросить(?:\s+сведения|\s+информацию)?'
    r'|получить\s+(?:сведения|данные|информацию)|поручить|дать\s+поручение|направить\s+поручение)'
    r'(?:\s+\S+){0,3}?\s+(?:в|из|у)?\s*(?:уполномоченн\w+\s+орган\w*\s+)?(?:подразделени\w+\s+|оперативн\w+\s+\w+\s+)?'
    r'(?:АФМ|ДЭР|СЭР|Агентств\w+\s+(?:Республики\s+Казахстан\s+)?по\s+финансовому\s+мониторингу'
    r'|оперативн\w+\s+подразделени|оперуполномоченн)', re.IGNORECASE)


# Поручения органу дознания о НСД, розыске, установлении местонахождения, задержании — процессуальные действия
# по УПК, а не получение сведений; они остаются
_LEGIT_ASSIGNMENT = re.compile(r'НСД|негласн|розыск|местонахожд|мест\w*\s+(?:жительств|работ|пребыван)|задержан|привод'
                               r'|охран|контрол\w+\s+(?:закуп|постав)|оперативно-розыскн|\bОРМ\b'
                               # «Криптон» АФМ прямо предусмотрен методиками (поиск цифровых активов)
                               r'|криптон', re.IGNORECASE)


def _is_self_request(text: str) -> bool:
    return bool(_SELF_REQUEST.search(text or '')) and not _LEGIT_ASSIGNMENT.search(text or '')


def _deadline_base_date():
    """
    Дата, от которой считаются сроки исполнения пунктов плана, — дата генерации плана (сегодня).
    От даты регистрации ЕРДР считать нельзя: дело может поступить к следователю через месяц
    после регистрации, и часть сроков оказалась бы уже истёкшей в момент составления плана.
    """
    from datetime import datetime
    return datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)


# Таблица транслитерации казахских букв → русские эквиваленты
_KAZ_TO_RUS = str.maketrans({
    'Ә': 'А', 'ә': 'а',   # казахская Ə/Ä
    'Ғ': 'Г', 'ғ': 'г',   # казахская Ğ
    'Қ': 'К', 'қ': 'к',   # казахская Q
    'Ң': 'Н', 'ң': 'н',   # казахская Ñ
    'Ө': 'О', 'ө': 'о',   # казахская Ö
    'Ұ': 'У', 'ұ': 'у',   # казахская Ū
    'Ү': 'У', 'ү': 'у',   # казахская Ü
    'Һ': 'Х', 'һ': 'х',   # казахская H
    'І': 'И', 'і': 'и',   # казахская I (без точки)
    'Ё': 'Е',              # ё → е (нейтрализация для единообразия)
})


def _transliterate_kaz_to_rus(text) -> str:
    """
    Заменяет казахские Unicode-буквы, отсутствующие в русском алфавите,
    на их русские эквиваленты. Принимает None и не-строковые значения.
    """
    if text is None:
        return ''
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return ''
    return text.translate(_KAZ_TO_RUS)



def detect_gender_in_names(fio) -> str:
    """Определяет пол фигурантов по ФИО на основе окончаний для правильного склонения глаголов в плане."""
    if isinstance(fio, list):
        fio = ", ".join(str(x) for x in fio if str(x).strip())
    elif not isinstance(fio, str):
        fio = str(fio or '')
    
    # Очистка от лишних квадратных скобок/кавычек
    fio = fio.strip().strip("[]'\"")

    if not fio or fio == 'Н/Д':
        return 'неизвестно'
    
    results = []
    # Разделяем фигурантов по запятой
    persons = [p.strip() for p in fio.split(',')]
    
    for person in persons:
        words = [w.strip() for w in person.split() if w.strip()]
        if not words:
            continue
            
        gender = 'неизвестно'
        # Проверяем отчество
        for word in words:
            word_lower = word.lower()
            if word_lower.endswith(('ович', 'евич', 'ич', 'ұлы', 'улы')):
                gender = 'мужской'
                break
            if word_lower.endswith(('овна', 'евна', 'ична', 'инична', 'қызы', 'кызы')):
                gender = 'женский'
                break
                
        # Если по отчеству не нашли, проверяем фамилию
        if gender == 'неизвестно':
            last_names = [words[0], words[-1]] if len(words) > 1 else [words[0]]
            for ln in last_names:
                ln_lower = ln.lower()
                if ln_lower.endswith(('ов', 'ев', 'ин', 'ый', 'ий', 'ский', 'ской')):
                    gender = 'мужской'
                    break
                # Окончания -овой, -евой, -иной — точно женский родительный падеж
                if ln_lower.endswith(('овой', 'евой', 'иной')):
                    gender = 'женский'
                    break
                # Окончания -ова, -ева, -ина:
                if ln_lower.endswith(('ова', 'ева', 'ина', 'ая', 'яя', 'ская')):
                    # Если после фамилии идут ИНИЦИАЛЫ (напр. Р.Ж. или А.Б.) и нет женского отчества/имени,
                    # то в юридическом тексте «в отношении Макарова Р.Ж.» это МУЖСКОЙ родительный падеж!
                    # (Для женщины в родительном падеже писалось бы «Макаровой Р.Ж.»)
                    rest_str = " ".join(words[1:]) if len(words) > 1 else ""
                    if rest_str and re.match(r'^[А-ЯЁ]\.[А-ЯЁ]\.?$', rest_str):
                        gender = 'мужской'
                        # Преобразуем имя в именительный падеж для вывода
                        person = f"{words[0][:-1]} {rest_str}"
                    else:
                        gender = 'женский'
                    break
                    
        if gender != 'неизвестно':
            results.append(f"{person} ({gender} пол)")
        else:
            results.append(person)
            
    return ', '.join(results)


def replace_placeholder_company(case_data: dict, real_name: str) -> dict:

    if not real_name:
        return case_data

    # Паттерн для поиска заглушек: ТОО/ООО «Х»/«X»/"Х"/"X" и т.д.
    placeholder_pattern = r'(?:ТОО|ООО)\s*[«"\u0027\u2018\u201c][ХXхx][»"\u0027\u2019\u201d]'

    text_fields = [
        'суть_правонарушения',
        'фабула_для_заголовка',
        'фигуранты',
        'дополнительные_сведения',
        'место_правонарушения',
        'тип_правонарушения',
    ]

    replaced_count = 0
    for field in text_fields:
        value = case_data.get(field)
        if value and isinstance(value, str) and value != 'Н/Д':
            new_value = re.sub(placeholder_pattern, real_name, value)
            if new_value != value:
                case_data[field] = new_value
                replaced_count += 1
                print(f"  ✅ Заменено в поле '{field}': {real_name}")

    # Всегда сохраняем реальное название
    case_data['реальное_название_тоо'] = real_name

    if replaced_count > 0:
        print(f"🔄 Заглушка заменена на реальное название в {replaced_count} полях")
    else:
        print(f"ℹ Заглушки не найдены в полях case_data (возможно, рапорт уже содержит реальное название)")

    return case_data

def _build_compact_fabula(raw_text: str, case_data: dict) -> str:

    if not raw_text or raw_text == 'Н/Д':
        return 'обнаружения сведений об уголовном правонарушении'

    text = raw_text.strip()

    # --- 1. Берём первые 1–2 предложения из документа ---
    # Разбиваем по точкам (с учётом сокращений вида "г." "ул.")
    sentences = re.split(r'(?<=[а-яА-Яa-zA-Z\d])\.\s+', text)
    first_sentences = '. '.join(s.strip() for s in sentences[:2] if s.strip())
    if not first_sentences:
        first_sentences = text

    # Ограничиваем длину для заголовка
    MAX_LEN = 400
    if len(first_sentences) > MAX_LEN:
        # Обрезаем по последнему пробелу в пределах лимита
        first_sentences = first_sentences[:MAX_LEN].rsplit(' ', 1)[0] + '...'

    return first_sentences.rstrip(',;')



def _confirmed_new_figurants(known: str, proposed: str, corpus: str) -> str:
    """
    Лицо, которое модель добавила к фигурантам, остаётся фигурантом, только если материалы это подтверждают:
    «подозреваемый …», «в отношении …», «признан подозреваемым», уведомление о защите. Руководитель, свидетель,
    «непосредственный руководитель» без таких данных фигурантом не становится — иначе в плане появляются
    «допрос подозреваемой», арест имущества и очные ставки «между подозреваемыми» без оснований.
    """
    from core.case_participants import find_person_mentions, normalize_text, find_suspect_keys, defended_persons
    known_keys = {m['key'] for m in find_person_mentions(normalize_text(known or ''))}
    confirmed = find_suspect_keys(normalize_text(corpus or '')) | {p['key'] for p in defended_persons(corpus or '')}
    kept = []
    for part in [p.strip() for p in (proposed or '').split(',') if p.strip()]:
        mentions = find_person_mentions(normalize_text(part))
        key = mentions[0]['key'] if mentions else None
        if key is None or key in known_keys or key in confirmed:
            kept.append(part)
        else:
            print(f"  🚫 [Участники] Модель добавила в фигуранты «{part}», но в материалах нет данных о подозрении — не фигурант")
    return ', '.join(kept) if kept else (known or 'Н/Д')


def _normalize_fio(fio: str) -> str:
    """«КУЛМАН С.К» -> «Кулман С.К.»: фамилия прописными и инициалы без точки не распознаются как ФИО."""
    m = re.match(r'^\s*([А-ЯЁӘҒҚҢӨҰҮҺІа-яёәғқңөұүһі-]{2,})\s+([А-ЯЁӘҒҚҢӨҰҮҺІ])\.?\s*([А-ЯЁӘҒҚҢӨҰҮҺІ])?\.?\s*$', fio or '')
    if not m:
        return (fio or '').strip()
    surname = m.group(1)
    if surname.isupper():
        surname = '-'.join(part.capitalize() for part in surname.split('-'))
    return f"{surname} {m.group(2)}." + (f"{m.group(3)}." if m.group(3) else '')


def _foreign_words(text: str, source: str) -> list:
    """Слова на латинице из строчных букв, которых нет в исходном тексте («constitutes»), — сбой языка модели."""
    src = (source or '').lower()
    return sorted({w for w in re.findall(r'(?<![\w.@/])[a-z]{3,}(?![\w.@/])', text or '') if w not in src})


def _restore_case(formatted: str, source: str) -> str:
    """
    Модель по инструкции «начинай с маленькой буквы» иногда пишет строчными всю фабулу
    («кгкп «ясли-сад»… кубжасаров р.ж.»). Регистр слов восстанавливается по исходному тексту;
    первая буква остаётся строчной (фабула продолжает фразу «по факту …»).
    """
    if not formatted or not source:
        return formatted
    cased = {}
    for m in re.finditer(r'[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]+', source):
        word = m.group(0)
        # Заглавная только из-за начала предложения («В период…») — не имя собственное
        before = source[:m.start()]
        sentence_start = re.search(r'(?:^|[.!?]\s*)$', before) is not None and \
            not re.search(r'(?:\b(?:г|гг|ул|пр|просп|обл|р|с|п|пос|им|мкр|д|кв)|\b[А-ЯЁ])\.\s*$', before, re.IGNORECASE)
        # «Р.Ж.» — инициалы (буква с точкой), а не начало предложения после точки; «В период» — не инициал
        is_initial = len(word) == 1 and source[m.end():m.end() + 1] == '.'
        # «Николаева Э.Д. …» в начале предложения — фамилия (за ней инициалы), а не просто заглавная буква
        before_initials = re.match(r'\s+[А-ЯЁӘҒҚҢӨҰҮҺІ]\.', source[m.end():m.end() + 4]) is not None
        # одна буква сохраняет заглавную только как инициал («Р А П О Р Т» — разрядка, а не имя)
        if word != word.lower() and (is_initial or (len(word) > 1 and (
                before_initials or not (sentence_start and word[1:] == word[1:].lower())))):
            cased.setdefault(word.lower(), word)
    restored = re.sub(r'[A-Za-zА-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]+',
                      lambda m: cased.get(m.group(0), m.group(0)) if m.group(0) == m.group(0).lower() else m.group(0),
                      formatted)
    # Однобуквенные инициалы («р.ж.» -> «Р.Ж.») после восстановленной фамилии
    restored = re.sub(r'(?<=[А-ЯЁ][а-яё]{2} )([а-яё])\.\s?([а-яё])\.',
                      lambda m: f"{m.group(1).upper()}.{m.group(2).upper()}.", restored)
    restored = re.sub(r'([А-ЯЁ][а-яё]+)\s+([а-яё])\.\s?([а-яё])\.',
                      lambda m: f"{m.group(1)} {m.group(2).upper()}.{m.group(3).upper()}.", restored)
    # Первое слово — строчное, если это не аббревиатура или имя собственное из источника
    first = re.match(r'[A-Za-zА-Яа-яЁё]+', restored)
    if first and first.group(0).lower() not in cased:
        restored = restored[:1].lower() + restored[1:]
    return restored


def _figurants_with_gender(figurants: str, corpus: str) -> str:
    """Пол фигурантов: сначала по полному ФИО с отчеством из материалов дела, иначе — по окончаниям."""
    from core.case_participants import gender_by_corpus
    if not figurants or figurants == 'Н/Д':
        return detect_gender_in_names(figurants)
    parts = []
    for person in [p.strip() for p in figurants.split(',') if p.strip()]:
        gender = gender_by_corpus(person, corpus)
        parts.append(f"{person} ({gender} пол)" if gender else detect_gender_in_names(person))
    return ', '.join(parts)


def _plan_year(case_data: dict) -> str:
    """Год для шапки плана: из даты рапорта или регистрации ЕРДР («14.05.2024», «5 мая 2024 г.»), иначе текущий."""
    from datetime import datetime
    for key in ('рапорт_дата', 'дата_регистрации_ердр'):
        m = re.search(r'(19|20)\d{2}', str(case_data.get(key) or ''))
        if m:
            return m.group(0)
    return str(datetime.now().year)


def _prepare_participants(case_data: dict, case_docs: list = None, extra_texts: list = None):
    """
    Собирает корпус материалов дела и список должностных лиц (не фигурантов).
    Список сохраняется в case_data['должностные_лица'], чтобы применяться и при live-генерации/дозагрузке.
    """
    from core.case_participants import build_source_corpus, extract_officials

    corpus = build_source_corpus(case_data, case_docs, extra_texts)
    officials = extract_officials(corpus, case_data.get('фио_следователя', ''))
    known = {o['key'] for o in officials}
    officials += [o for o in (case_data.get('должностные_лица') or []) if o.get('key') not in known]
    case_data['должностные_лица'] = officials
    if officials:
        print(f"  👮 Должностные лица (не фигуранты): " + ", ".join(f"{o['fio']} ({o['role']})" for o in officials[:15]))
    return corpus, officials


def _case_retriever(case_docs: list):
    """
    Поиск по документам ТОЛЬКО текущего дела. Общий LightRAG-сервер хранит документы всех дел
    в одном индексе, из-за чего в план попадали ФИО и схемы из чужих дел.
    """
    from core.case_participants import LocalCaseRetriever
    from core.doc_compress import compress_document
    print(f"  📂 Контекст строится только по документам текущего дела ({len(case_docs or [])} док.)")
    # В поиск идут сжатые протоколы: фрагменты берутся из показаний, а не из разъяснения прав по ст.78 УПК
    return LocalCaseRetriever([{**d, 'text': compress_document(d)} for d in case_docs or []])


def _parse_days(action: dict) -> int:
    try:
        return int(str(action.get("дней", 30)))
    except (ValueError, TypeError):
        matches = re.findall(r'(\d+)', str(action.get("дней", "")))
        return int(matches[-1]) if matches else 30


# Английские слова, которые модель вставляет в русский текст («выемка в premises ТОО…»)
_EN_GLITCH = {'premises': 'помещении', 'office': 'офисе', 'constitutes': 'является', 'including': 'включая',
              'regarding': 'по вопросу', 'documents': 'документов', 'and': 'и', 'with': 'с', 'for': 'для',
              'from': 'из', 'period': 'период', 'account': 'счёт', 'accounts': 'счетов'}
_EN_GLITCH_RE = re.compile(r'(?<![A-Za-z.@/])(' + '|'.join(_EN_GLITCH) + r')(?![A-Za-z.@/])')


def _fix_english_glitch(text: str) -> str:
    fixed = _EN_GLITCH_RE.sub(lambda m: _EN_GLITCH[m.group(1)], text or '')
    if fixed != text:
        print(f"  ⚖ [Текст] Английские слова заменены: '{text[:70]}…'")
    return fixed


def _clean_action_texts(action: dict, case_data: dict) -> None:
    """Текстовая очистка пункта: ТОО вместо ООО, без заглушек, автонумерации и запросов «самому себе»."""
    action['действие'] = _fix_english_glitch(_fix_ooo(action.get('действие', '')))
    action['цель'] = _fix_ooo(action.get('цель', ''))
    # Убираем заглушки в скобках: [указать адрес] — непрофессионально
    action['действие'] = re.sub(r',?\s*\[указать[^\]]*\]', '', action['действие'], flags=re.IGNORECASE).strip()
    action['действие'] = re.sub(r',?\s*\[вставить[^\]]*\]', '', action['действие'], flags=re.IGNORECASE).strip()
    # ── Убираем автонумерацию LLM (5.3.1., 1), 2. и т.п.) из начала текста ──
    action['действие'] = re.sub(r'^\s*\d+(?:\.\d+)*\.?\s*[).]?\s*', '', action['действие']).strip()
    # ── «Проверка показаний посредством очной ставки» — это очная ставка между участниками ──
    from core.procedural_validator import fix_confrontation_wording, fix_suspect_gender
    action['действие'] = fix_confrontation_wording(action['действие'])
    # «подозреваемого Петрову» — род по полу лица из материалов дела
    action['действие'] = fix_suspect_gender(action['действие'], case_data)
    action['цель'] = re.sub(r'^\s*\d+(?:\.\d+)*\.?\s*[).]?\s*', '', action['цель']).strip()
    # ── Запросы и поручения самому органу (АФМ/ДЭР) — только внешние запросы; пункт удаляется ──
    if action.get('статус') != 'Исполнен' and _is_self_request(action.get('действие', '')):
        print(f"  ✂ [Запросы] Запрос/поручение самому органу расследования удалён: '{action['действие'][:80]}'")
        action['действие'] = ''
    # ── Исправление типа юрлица: если ИП — убираем ошибочный ТОО/ООО ──────────
    _rc = case_data.get('реальное_название_тоо', '')
    if _rc and re.match(r'(?i)^\s*ИП\b', _rc):
        _ip_name = re.split(r'\s+', _rc.strip(), maxsplit=1)[-1]
        _pat = rf'(?:ТОО|ООО)\s*[«"\']{re.escape(_ip_name)}[»"\']'
        _rep = f'ИП {_ip_name}'
        action['действие'] = re.sub(_pat, _rep, action['действие'], flags=re.IGNORECASE)
        action['цель'] = re.sub(_pat, _rep, action['цель'], flags=re.IGNORECASE)


_DOC_REF_RE = re.compile(
    r',?\s*(?:(?:привлеч|назнач|вынес|составл|получ)\w*\s+)?(?:(?:по|согласно|на\s+основании)\s+)?'
    r'(?:постановлени|заключени|протокол|определени|приказ|ответ)\w*\s+(?:№\s*[\w/.\-]+\s+)?'
    r'от\s+(\d{1,2})\.(\d{1,2})\.(\d{4})(?:\s*(?:г\.|года?))?', re.IGNORECASE)


def _strip_unknown_doc_refs(action: dict, corpus: str) -> None:
    """
    Модель придумывает реквизиты: «допросить специалиста, привлечённого по постановлению от 15.10.2025», когда
    такого постановления в деле нет. Ссылка на документ с датой, которой нет в материалах дела, удаляется.
    """
    text = action.get('действие', '')
    if not corpus or 'от ' not in text:
        return

    def drop(m):
        d, mth, y = m.groups()
        variants = {f"{int(d):02d}.{int(mth):02d}.{y}", f"{int(d)}.{int(mth):02d}.{y}", f"{int(d)}.{int(mth)}.{y}"}
        if any(v in corpus for v in variants):
            return m.group(0)
        print(f"  ✂ [Реквизиты] Ссылки нет в материалах дела, удалена: '{m.group(0).strip(' ,')}'")
        return ''

    cleaned = _DOC_REF_RE.sub(drop, text)
    if cleaned != text:
        action['действие'] = re.sub(r'\s+,', ',', re.sub(r'\s{2,}', ' ', cleaned)).strip()


_PROTOCOL_INSPECTION = re.compile(
    r'осмотр\w*\s+(?:\w+\s+){0,2}?протокол\w*\s+(?:\w+\s+){0,2}?(?:допрос|очн\w+\s+ставк|опрос)', re.IGNORECASE)
_INSPECTED_ORGAN = re.compile(
    r'осмотр\w*\s+полученн\w+\s+(?:\w+\s+){0,4}?(?:из|от)\s+(.+?)(?=\s+(?:сведени\w*|о|об|на|с\s+цел\w*)\b|\s*\(|,|$)',
    re.IGNORECASE)


def _inspected_organ(text: str) -> str:
    """«осмотр полученных сведений по запросу из Министерства туризма и спорта РК» -> «минис|туриз|спорт»."""
    m = _INSPECTED_ORGAN.search(text or '')
    if not m:
        return ''
    words = [w[:5] for w in re.findall(r'[а-яёa-z]{3,}', m.group(1).lower()) if w not in ('запрос',)]
    return '|'.join(words[:3])


_INSPECTION_FROM = re.compile(
    r'^\s*произвести\s+осмотр\s+полученн\w+\s+(?:сведений\s+)?(?:по\s+запросу\s+)?из\s+(?P<organ>.+?)\s+'
    r'(?P<subject>(?:сведени|данн|информаци|выписк)\w*\b.*)$', re.IGNORECASE)
_SELF_ORGAN = re.compile(r'^(?:АФМ|ДЭР|СЭР|Агентств\w+\s+(?:Республики\s+Казахстан\s+)?по\s+финансовому\s+мониторингу)',
                         re.IGNORECASE)


def _organ_stems(organ: str) -> list:
    return [w[:5] for w in re.findall(r'[а-яёa-z]{3,}', organ.lower())
            if w not in ('электронн', 'системы', 'электронной', 'республики', 'казахстан')][:2]


def _inspections_to_requests(actions: list, other_texts: list) -> list:
    """
    «Произвести осмотр полученных из Kaspi.kz сведений о платежах …», когда запроса в Kaspi в плане нет, —
    это пропущенный запрос: пункт превращается в «Направить запрос …», а истребование и осмотр добавит цепочка.
    Для самой АФМ/ДЭР — поручение подразделению (запрос самому себе недопустим).
    """
    texts = [t.lower() for t in other_texts] + [(a.get('действие') or '').lower() for a in actions]
    result = []
    for a in actions:
        m = _INSPECTION_FROM.match(a.get('действие') or '')
        if not m or a.get('статус') == 'Исполнен':
            result.append(a)
            continue
        organ, subject = m.group('organ').strip(), m.group('subject').strip()
        stems = _organ_stems(organ)
        requested = stems and any(re.search(r'запрос|истребова', t) and all(s in t for s in stems) for t in texts)
        if requested:
            result.append(a)
            continue
        if _SELF_ORGAN.match(organ):
            # Только внешние запросы: «сведения из АФМ» без внешнего источника в план не идут
            print(f"  ✂ [Запросы] Осмотр сведений самого органа расследования удалён: '{a.get('действие', '')[:80]}'")
            continue
        new_text = f"Направить запрос в уполномоченный орган {organ} об истребовании {subject}"
        print(f"  ⚖ [Запросы] Осмотр без запроса заменён запросом: '{new_text[:90]}'")
        result.append({**a, 'действие': new_text})
    return result


def _refuser_in(text: str, refused: dict):
    """
    ФИО участника очной ставки («между X и Y») или лица, чьи показания проверяются на месте, если он отказался
    от дачи показаний: сопоставлять и проверять нечего.
    """
    from core.case_participants import find_person_mentions, normalize_text
    if not refused:
        return None
    norm = normalize_text(text)
    if re.search(r'очн\w*\s+ставк', norm, re.IGNORECASE):
        # Участники — только до предмета ставки: «между Зайцевой С.А. и Серікқызы К. по вопросу роли
        # Бекова Е.Н.» — Беков не участник (а «Серікқызы К.» с одним инициалом как ФИО не распознаётся)
        m = re.search(r'между\s+|ставк\w*\s+', norm, re.IGNORECASE)
        part = norm[m.end():] if m else norm
        end = re.search(r'\s(?:по|о|об|на\s+предмет|с\s+целью|в\s+целях|в\s+части|относительно|касательно|для|'
                        r'с\s+участием)\s|[,;:(]', part, re.IGNORECASE)
        found = find_person_mentions(part[:end.start()] if end else part)[:2]
    elif re.search(r'проверк\w*\s+показаний', norm, re.IGNORECASE):
        found = find_person_mentions(norm)[:1]
    else:
        return None
    return next((refused[p['key']] for p in found if p['key'] in refused), None)


def _filter_by_status(actions: list, case_data: dict, corpus: str, conducted: list = None) -> list:
    """
    Процессуальные фильтры, общие для первичной генерации и дозагрузки:
    статус участников, выдуманные ФИО, действия с адвокатами/экспертами/прокурорами, обыски у свидетелей,
    очные ставки с лицами, отказавшимися от дачи показаний. conducted — проведённые действия (при дозагрузке —
    вместе с новыми документами), по умолчанию — из case_data.
    """
    from core.case_participants import filter_actions_by_participants
    from core.conducted_actions import refused_persons
    from core.procedural_validator import (
        is_forbidden_coercive_action_against_protected_person,
        is_forbidden_attorney_action,
        is_forbidden_search_of_witness_or_victim,
    )
    from core.procedural_validator import is_answer_as_material_evidence, address_in_materials, _chain_step
    from core.case_participants import set_case_persons, case_person_keys
    # Адреса — из материалов дела (corpus — тексты всех документов дела и найденные в них адреса фигурантов)
    address_corpus = corpus or ''
    conducted = conducted if conducted is not None else case_data.get('проведённые_действия')
    participants = (case_data.get('должностные_лица') or []) + (case_data.get('иные_участники') or [])
    # Лица этого дела — по ним в пунктах плана распознаются и ФИО с одним инициалом («Серікқызы К.»)
    # (действует и для дальнейшей проверки: цепочки, сроки, дубли запросов)
    set_case_persons(case_person_keys(corpus, participants, conducted, case_data.get('фигуранты', '')))
    refused = refused_persons(conducted)
    result = []
    for action in actions:
        text = action.get('действие', '')
        if not text.strip():
            continue
        if is_forbidden_coercive_action_against_protected_person(text) \
                or is_forbidden_attorney_action(text) \
                or is_forbidden_search_of_witness_or_victim(text):
            continue
        if action.get('статус') != 'Исполнен':
            # Протокол допроса — не объект осмотра: показания оцениваются, а не «осматриваются»
            if _PROTOCOL_INSPECTION.search(text):
                print(f"  ✂ [Фильтр] Осмотр протокола допроса — не следственное действие: '{text[:70]}...'")
                continue
            if is_answer_as_material_evidence(text):
                print(f"  ✂ [Фильтр] Ответ на запрос — иной документ, не вещественное доказательство: '{text[:70]}...'")
                continue
            refuser = _refuser_in(text, refused)
            if refuser:
                print(f"  ✂ [Фильтр] {refuser} отказался(-ась) от дачи показаний — очная ставка и проверка "
                      f"показаний с ним не проводятся: '{text[:70]}...'")
                continue
            # Санкция, обыск, выемка по адресу, которого нет в материалах дела, — выдуманный адрес (автор, 06.11.2026)
            if _chain_step(text.lower()) in ('санкция', 'обыск', 'выемка') \
                    and not address_in_materials(text, address_corpus):
                print(f"  ✂ [Адрес] Адреса нет в материалах дела — пункт удалён: '{text[:110]}...'")
                continue
        result.append(action)
    return filter_actions_by_participants(result, corpus, participants)


# Этап методики (по названию) -> как понять, что он отражён в плане.
# Порядок важен: более специфичные этапы первыми.
_STAGE_CHECKS = [
    (r'арест', r'наложени\w*\s+арест|арест\w*\s+(?:на\s+)?(?:имуществ|денежн|сч[её]т|дол)|о\s+наложении\s+арест'),
    # ст.214: отсутствие регистрации/лицензии — ключевой элемент состава
    (r'лиценз', r'лиценз'),
    # ст.216: цифровой след ЭСФ (до общего «цифровой след»), регистрация и ЭЦП, фактическое исполнение
    (r'\bэсф\b|сч[её]т\w*-фактур',
     r'ис\s*эсф|журнал\w*[^.;]{0,40}(?:эсф|сч[её]т)|(?:запрос|истребова)\w*[^.;]{0,80}(?:\bэсф\b|сч[её]т\w*-фактур)'),
    (r'регистрацион\w*\s+данн|полномочи',
     r'правительство\s+для\s+граждан|регистрацион\w*\s+дел|госкорпорац|\bцон\b|\bнуц\b|'
     r'удостоверяющ\w*\s+центр|доверенност'),
    (r'фактическ\w*\s+исполнен',
     r'фактическ\w*\s+(?:выполнен|исполнен|отгруз|поставк|оказан|наличи)|реальност|'
     r'осмотр\w*[^.;]{0,60}(?:объект|склад|помещен)|перевозчик|субподряд|товарно-транспорт|\bттн\b'),
    # ст.190: общая часть и виды (строительство, субсидии, массовое, интернет)
    (r'основани\w*\s+передачи', r'договор|заявк|решени\w*\s+о\s+выплат|основани\w*\s+(?:передачи|выплаты)'),
    (r'реальност', r'осмотр\w*[^.;]{0,60}объект|реальност|поставщик|подрядчик|контрагент'),
    (r'смет', r'смет|проектно|договор\w*\s+подряд'),
    (r'движени\w*\s+средств', r'выпис|банк|движени\w*\s+(?:денеж|средств)'),
    (r'правил\w*\s+субсид', r'правил\w*\s+субсид|услови\w*\s+(?:получения\s+)?субсид'),
    (r'заявк', r'заявк|информационн\w+\s+систем|эцп'),
    (r'объект\w*\s+субсид', r'осмотр|поголов|серийн|поставщик'),
    (r'профильн\w*\s+исследован', r'исследован|экспертиз'),
    (r'реестр', r'реестр|сводн\w+\s+таблиц'),
    (r'дроппер|плат[её]жн\w*\s+сервис', r'дроппер|kaspi|каспи|плат[её]жн|банк|карт'),
    (r'цифров\w*\s+след', r'оператор\w*\s+связи|\bip\b|устройств|телефон|переписк|компьютерно'),
    # блоки видов ст.214 (214_вид_*.docx) — до общего «актив», иначе «цифровых активов» уйдёт туда
    (r'цифров\w*\s+актив|крипт', r'мфца|мцриап|cere|binance|бинанс|криптобирж|цифров\w*\s+актив'),
    (r'правов\w*\s+статус|подакциз|маркировк', r'акциз|мпт|маркировк|тн\s*вэд'),
    (r'маршрут|цепочк', r'маршрут|сергек|биллинг|gps|отправител|получател'),
    (r'нотари', r'нотари'),
    (r'комисси|заклад', r'комисси|мти|ломбард|заклад'),
    (r'посредник', r'посредник'),
    (r'фиксаци\w*\s+розыгрыш', r'осмотр\w*[^.;]{0,80}(?:аккаунт|публикац|интернет|сайт|страниц)'),
    (r'платеж\w*\s+участник', r'kaspi|каспи|плат[её]жн\w*\s+(?:организ|систем|сервис|платформ)|'
                              r'(?:поступлени|оплат)\w*\s+(?:от|за)\s+(?:участ|покуп|продаж|товар|услуг|курс|видеоурок)'),
    # «\bприз…» без «признание»
    (r'\bприз(?:ы|ов)\b', r'\bприз(?:ы|ов|а|ом|ам|ами|ах)?\b|победител'),
    (r'реклам|клиент', r'объявлен|реклам|заёмщ|заемщ|клиент'),
    # АРРФР само по себе этап не закрывает: запрос о лицензии (этап 3) — не поиск активов
    (r'актив|финансов\w*\s+расслед',
     r'кредитн\w*\s+бюро|депозит|брокер|ценн\w+\s+бумаг|финансов\w+\s+центр|мфца|криптон'),
    (r'налог', r'кгд|дгд|налог'),
    (r'банк', r'банк|бву|выпис'),
    # ст.189 «Обеспечение сохранности… имущества» — это поиск имущества (ПКБ, ЦДЦБ, АРРФР, МФЦА) и ограничения
    (r'сохранност',
     r'кредитн\w*\s+бюро|депозитари|аррфр|регулированию\s+и\s+развитию|финансов\w+\s+центр|мфца|криптон|'
     r'ограничени\w*\s+(?:на|прав|распоряж|сделок|сделк|переводов)|приостановлени\w*\s+регистрац'),
    (r'ограничени',
     r'ограничени\w*\s+(?:на|прав|распоряж|сделок|сделк|переводов)|приостановлени\w*\s+регистрац'),
    (r'проверк\w*\s+показаний|очн', r'очн\w*\s+ставк|проверк\w*\s+показаний'),
    (r'экспертиз', r'экспертиз'),
    (r'изъяти|обыск|выемк', r'выемк|обыск'),
    (r'анализ', r'анализ'),
    (r'сорм', r'сорм'),
    (r'нсд|негласн', r'нсд|негласн|снятие\s+информации|контроль\s+переговоров'),
    (r'сио\s*п|сиоп', r'сио\s*(?:п\s*и\s*)?псо|сиоп'),
    (r'потерпевш', r'потерпевш'),
    (r'осмотр', r'осмотр'),
    (r'допрос', r'допрос'),
]
# Этапы, которые не превращаются в пункты плана (первые 3 пункта обязательны всегда; завершение — не план)
_STAGE_SKIP = r'первичн\w*\s+анализ|завершени|окончани|срок'
# Проведённые действия, закрывающие этап («Постановление о наложении ареста» уже есть в деле)
_STAGE_CONDUCTED = {r'арест': {'арест'}, r'экспертиз': {'назначение_экспертизы', 'заключение'},
                    r'изъяти|обыск|выемк': {'выемка', 'обыск'}, r'проверк\w*\s+показаний|очн': {'очная_ставка'},
                    r'потерпевш': {'потерпевший'}}


def _methodology_stages(retrieved_context: str) -> list:
    """Этапы методики «Этап N. Название» с текстом — из блока методики в контексте генерации."""
    text = retrieved_context or ''
    start = text.find('=== МЕТОДИКА')
    end = text.find('=== ДАННЫЕ ИЗ МАТЕРИАЛОВ ДЕЛА')
    text = text[start if start >= 0 else 0: end if end > 0 else len(text)]
    stages = []
    # «Этап 8. Наложение ареста…» (189) и «ЭТАП 7: ВРЕМЕННЫЕ ОГРАНИЧЕНИЯ (срок: до 5 суток)» (190, 214, общая)
    pattern = r'(?im)^[ \t]*этап\s+(\d+)\s*[.:]?\s*([^\n]+)\n(.*?)(?=^[ \t]*этап\s+\d+|\Z)'
    for m in re.finditer(pattern, text, re.DOTALL):
        title = re.sub(r'\s*\(срок[^)]*\)', '', m.group(2)).strip()
        stages.append((f"Этап {m.group(1)}. {title}", m.group(3).strip()))
    return stages


def _stage_limits(retrieved_context: str) -> dict:
    """
    «Этап 9. Допросы (срок: до 9 суток)», «ЭТАП 10: ДОПРОСЫ (срок: до 9 суток)» -> {9: 9}: предельный срок этапа
    методики в сутках от даты плана. «До 5 суток после выявления имущества» отсчитывается от события — не берётся.
    """
    text = retrieved_context or ''
    start = text.find('=== МЕТОДИКА')
    end = text.find('=== ДАННЫЕ ИЗ МАТЕРИАЛОВ ДЕЛА')
    text = text[start if start >= 0 else 0: end if end > 0 else len(text)]
    limits = {}
    for m in re.finditer(r'(?im)^[ \t]*этап\s+(\d+)[^\n]*?\(срок:\s*до\s+(\d+)\s+(?:сут|дн)[^)\n]*\)', text):
        if not re.search(r'после', m.group(0), re.IGNORECASE):
            limits.setdefault(int(m.group(1)), int(m.group(2)))
    return limits


def _relative_stage_limits(retrieved_context: str) -> dict:
    """
    «Этап 16. Наложение ареста на имущество (срок: до 10 суток после ограничения)» -> {16: 10}: срок этапа методики,
    отсчитываемый от предыдущего шага цепочки пункта (арест — от ограничения того же лица). У пункта без предыдущего
    шага («до 5 суток после выявления имущества» — выявление отдельным пунктом не задано) такой срок не действует.
    """
    text = retrieved_context or ''
    start = text.find('=== МЕТОДИКА')
    end = text.find('=== ДАННЫЕ ИЗ МАТЕРИАЛОВ ДЕЛА')
    text = text[start if start >= 0 else 0: end if end > 0 else len(text)]
    limits = {}
    for m in re.finditer(r'(?im)^[ \t]*этап\s+(\d+)[^\n]*?\(срок:\s*до\s+(\d+)\s+(?:сут|дн)[^)\n]*\)', text):
        if re.search(r'после', m.group(0), re.IGNORECASE):
            limits.setdefault(int(m.group(1)), int(m.group(2)))
    return limits


def _inspection_stage(retrieved_context: str):
    """
    Номер этапа «Осмотры» методики дела: «Этап 11. Осмотры (срок: до 30 суток) — Осмотр изъятого и полученных
    сведений» (216, 190, 214), «ЭТАП 17: ОСМОТР» (общая, 218, 307). Сначала — этап осмотра, где сказано о полученных
    сведениях; иначе — этап с названием просто «Осмотр(ы)». Осмотры по виду («Осмотр объекта строительства») — нет.
    """
    cands = []
    for title, body in _methodology_stages(retrieved_context):
        m = re.match(r'Этап\s+(\d+)\.\s*(.*)', title)
        if m and re.match(r'\s*осмотр', m.group(2), re.IGNORECASE):
            cands.append((int(m.group(1)), m.group(2).strip().lower(), body.lower()))
    for n, _name, body in cands:
        if 'полученн' in body:
            return n
    for n, name, _body in cands:
        if re.fullmatch(r'осмотр\w*', name):
            return n
    return None


def _suspect_interview_stage(retrieved_context: str):
    """
    Номер этапа допросов подозреваемых методики дела: «Допросы подозреваемых» (190, 216 — 17), «ДОПРОС ФИГУРАНТОВ»
    (217 — 18); если такого нет — общий этап «Допросы» (189 — 5, 214 — 9, 218/307/общая — 10). Этапы допросов
    свидетелей, потерпевших, участников и дополнительных допросов не подходят.
    """
    general = None
    for title, _body in _methodology_stages(retrieved_context):
        m = re.match(r'Этап\s+(\d+)\.\s*(.*)', title)
        if not m:
            continue
        name = m.group(2).strip().lower()
        if re.match(r'допрос\w*\s+(?:подозреваем|фигурант)', name):
            return int(m.group(1))
        if general is None and re.fullmatch(r'допрос\w*', name):
            general = int(m.group(1))
    return general


def _restriction_stage(retrieved_context: str):
    """
    Номер этапа временных ограничений на имущество методики дела: «Этап 15. Временные ограничения на имущество»
    (216, 190), «Этап 13. …» (214, 214-1), «ЭТАП 7: ВРЕМЕННЫЕ ОГРАНИЧЕНИЯ» (общая, 218, 307). Нет такого этапа
    (189: сразу «Наложение ареста») — None: ограничение перед арестом не добавляется.
    """
    for title, _body in _methodology_stages(retrieved_context):
        m = re.match(r'Этап\s+(\d+)\.\s*(.*)', title)
        if m and re.search(r'ограничени', m.group(2), re.IGNORECASE) and not re.search(r'арест', m.group(2), re.I):
            return int(m.group(1))
    return None


def _missing_stages(actions: list, retrieved_context: str, case_data: dict) -> list:
    """Этапы методики, не отражённые ни одним пунктом плана и не закрытые проведёнными действиями."""
    # Обязательные первые 3 пункта (изучение материалов СОРМ, проверка по СИО ПСО) добавляются валидатором
    # позже — учитываем их сразу, чтобы не дозапрашивать уже покрытые этапы
    from core.procedural_validator import ensure_mandatory_first_3_items
    mandatory = ensure_mandatory_first_3_items([], case_data=case_data)
    # Запросы в СИО ПСО (не о банках) валидатор потом удаляет (проверка по базе — пункт 3): этап они не закрывают,
    # иначе «запрос в СИО ПСО … ценные бумаги» засчитывал финансовое расследование, которого в плане не остаётся
    def _kept(text):
        t = text.lower()
        return not (re.match(r'\s*(?:направить|направлен|истребовать|запросить)', t) and 'сио псо' in t
                    and not re.search(r'банк|\bбву\b|выпис|движени\w*\s+денежн', t))
    plan_text = "\n".join(t for t in ((a.get('действие') or '') for a in list(actions) + mandatory) if _kept(t)).lower()
    conducted_types = {c.get('тип') for c in case_data.get('проведённые_действия') or []}
    missing = []
    for title, body in _methodology_stages(retrieved_context):
        t = title.lower()
        if re.search(_STAGE_SKIP, t):
            continue
        # Условные этапы («[ЕСЛИ: установлен точный адрес…]») и альтернативные сценарии («A: …», «D: …»)
        # обязательными не являются: их применимость определяет модель по фабуле дела
        name = t.split('. ', 1)[-1]
        if re.match(r'\s*\[\s*если|\s*[a-zа-я]\s*:|\s*при\s+необход', name) or 'при необход' in name:
            continue
        for title_re, presence_re in _STAGE_CHECKS:
            if re.search(title_re, t):
                covered = re.search(presence_re, plan_text) or \
                    (conducted_types & _STAGE_CONDUCTED.get(title_re, set()))
                if not covered:
                    missing.append((title, body))
                break
    return missing


def _complete_by_methodology(actions: list, case_data: dict, retrieved_context: str, llm_client) -> list:
    """
    Проверка полноты плана по методике: если этап методики (например, «Наложение ареста на имущество»)
    не отражён ни одним пунктом, модель отдельным запросом дополняет план пунктами именно этого этапа.
    """
    missing = _missing_stages(actions, retrieved_context, case_data)
    if not missing:
        print("  ✅ [Полнота] Все этапы методики отражены в плане")
        return actions
    print("  🧩 [Полнота] Не отражены этапы методики: " + "; ".join(t for t, _ in missing))
    try:
        extra = llm_client.generate_stage_actions(case_data, missing, actions, retrieved_context) or []
    except Exception as e:
        print(f"  ⚠ [Полнота] Не удалось дополнить план: {e}")
        return actions
    extra = [a for a in extra if isinstance(a, dict) and a.get('действие')]
    print(f"  ➕ [Полнота] Добавлено пунктов по пропущенным этапам: {len(extra)}")
    return actions + extra


def _postprocess_generated_actions(generated_actions: list, case_data: dict, corpus: str) -> list:
    """Нумерация, сроки, очистка текста, процессуальные фильтры и сортировка по блокам УПК РК."""
    from datetime import timedelta
    from core.procedural_validator import validate_and_order_actions, enforce_chain_deadlines, distribute_by_days

    fio = case_data.get('фио_следователя', 'Следователь')
    # Сроки считаются от даты генерации плана
    base_date = _deadline_base_date()
    print(f"  📅 Базовая дата для сроков: {base_date.strftime('%d.%m.%Y')} (дата составления плана)")

    for idx, action in enumerate(generated_actions, start=1):
        action["номер"] = idx
        if not action.get("исполнитель") or action["исполнитель"] in ("Следователь", "Н/Д", ""):
            action["исполнитель"] = fio
        # Количество дней, сгенерированное нейросетью из методики → конкретная дата
        days_from_method = _parse_days(action)
        try:
            action["срок"] = (base_date + timedelta(days=days_from_method)).strftime("%d.%m.%Y")
        except Exception:
            action["срок"] = f"Через {days_from_method} дней"
        _clean_action_texts(action, case_data)
        _strip_unknown_doc_refs(action, corpus)

    generated_actions = _filter_by_status(generated_actions, case_data, corpus)
    # Процессуальная валидация и сортировка по 7 блокам УПК РК
    generated_actions = validate_and_order_actions(generated_actions, case_data=case_data)
    # Действия, уже проведённые по материалам дела, — статус «Исполнен» (и добавление недостающих)
    generated_actions = _apply_conducted(generated_actions, case_data)
    # Сроки по дням в очередности модели (не более 3 действий в день, в пределах срока этапа методики);
    # осмотр изъятого — не раньше выемки, ходатайство об аресте — не раньше постановления и т.д.
    figurants = case_data.get('фигуранты', '')
    generated_actions = distribute_by_days(generated_actions, base_date, figurants=figurants,
                                           stage_limits=case_data.get('сроки_этапов'),
                                           inspection_stage=case_data.get('этап_осмотров'),
                                           relative_limits=case_data.get('относительные_сроки_этапов'))
    return enforce_chain_deadlines(generated_actions, figurants=figurants)


def _apply_conducted(actions: list, case_data: dict, add_missing: bool = True) -> list:
    """Отмечает проведённые по делу действия статусом «Исполнен» и упорядочивает план."""
    conducted = case_data.get('проведённые_действия') or []
    if not conducted:
        return actions
    from core.conducted_actions import apply_conducted_statuses
    from core.procedural_validator import order_actions
    fio = case_data.get('фио_следователя') or 'Следователь'
    actions = apply_conducted_statuses(actions, conducted, fio, add_missing=add_missing)
    return order_actions(actions)


def generate_investigation_plan(case_data, include_template_actions: bool = True):
    """
    Генерирует детализированный план расследования на основе данных дела,
    фильтруя действия по статье УК РК и используя предопределенный маппинг для сроков.
    include_template_actions=False — только шапка плана (при LLM-генерации шаблонные действия
    всё равно заменяются сгенерированными, а их валидация лишь засоряла лог чужими пунктами).
    """
    uk_article_full = case_data.get('статья_ук_рк', 'Н/Д')
    # Очищаем статью — убираем "УК РК" если LLM её добавил (в документе "УК РК" уже есть)
    uk_article_full = re.sub(r'\s*УК\s*РК', '', uk_article_full, flags=re.IGNORECASE).strip()
    main_uk_article = extract_main_uk_article(uk_article_full)

    actual_investigator_fio = case_data.get('фио_следователя', 'Н/Д')
    actual_investigator_duty = case_data.get('должность_следователя', 'Следователь')

    if actual_investigator_fio and actual_investigator_duty and actual_investigator_fio in actual_investigator_duty:
        actual_investigator_full = actual_investigator_duty
    elif actual_investigator_fio == "Н/Д" and actual_investigator_duty == "Следователь":
        actual_investigator_full = "Следователь ФИО"
    else:
        actual_investigator_full = f"{actual_investigator_duty} {actual_investigator_fio}"

    actual_registration_date = case_data.get('дата_регистрации_ердр', 'Н/Д')

    filtered_actions = []
    current_number = 1
    for action_template in INVESTIGATION_PLAN_ACTIONS:
        relevant_articles = action_template.get("relevant_articles", [])

        if not relevant_articles or (main_uk_article and main_uk_article in relevant_articles):
            action_copy = action_template.copy()
            action_copy["номер"] = current_number

            methodology_stage = action_copy.get("methodology_stage")
            
            # Устаревшие маппинги убраны, используем базовый дефолт для оффлайн-шаблона
            current_srok = "В течение 30 суток"
            action_copy["срок"] = current_srok

            # Исполнитель
            if action_copy.get("исполнитель"):
                current_ispolnitel = action_copy.get("исполнитель")
            else:
                current_ispolnitel = actual_investigator_fio if actual_investigator_fio != "Н/Д" else "Следователь"
            
            action_copy["исполнитель"] = current_ispolnitel

            filtered_actions.append(action_copy)
            current_number += 1

    CITY_TO_REGION = {
        "астана": "по г. Астана",
        "алматы": "по г. Алматы",
        "шымкент": "по г. Шымкент",
        "уральск": "по Западно-Казахстанской области",
        "орал": "по Западно-Казахстанской области",
        "актобе": "по Актюбинской области",
        "ақтөбе": "по Актюбинской области",
        "атырау": "по Атырауской области",
        "актау": "по Мангистауской области",
        "ақтау": "по Мангистауской области",
        "костанай": "по Костанайской области",
        "қостанай": "по Костанайской области",
        "петропавл": "по Северо-Казахстанской области",
        "кокшетау": "по Акмолинской области",
        "көкшетау": "по Акмолинской области",
        "павлодар": "по Павлодарской области",
        "караганд": "по Карагандинской области",
        "қарағанды": "по Карагандинской области",
        "усть-камено": "по Восточно-Казахстанской области",
        "өскемен": "по Восточно-Казахстанской области",
        "семей": "по области Абай",
        "талдыкорган": "по области Жетісу",
        "талдықорған": "по области Жетісу",
        "конаев": "по Алматинской области",
        "қонаев": "по Алматинской области",
        "тараз": "по Жамбылской области",
        "кызылорд": "по Кызылординской области",
        "қызылорд": "по Кызылординской области",
        "туркестан": "по Туркестанской области",
        "түркістан": "по Туркестанской области",
        "жезказган": "по области Ұлытау",
        "жезқазған": "по области Ұлытау"
    }

    город_raw = str(case_data.get('место_правонарушения', '')).strip()
    
    # Если место пустое — извлекаем город из фабулы (|в г. Астанае ...)
    if not город_raw or 'Н/Д' in город_raw or город_raw in ('Нет', '', 'None', '-'):
        import re as _re
        фабула_для_города = str(case_data.get('фабула_для_заголовка', case_data.get('суть_правонарушения', '')))
        # Ищем паттерн вида 'в г. Астанае' или 'г. Астана', а также 'в городе Алматы'
        город_матч = _re.search(r'(?:г\.|городе)[\s\xa0]*([А-Яа-яЁёA-Za-z-]+)', фабула_для_города, _re.IGNORECASE)
        if город_матч:
            # Нормализуем: 'Астанае' → 'Астана'
            город_имя = город_матч.group(1).rstrip('е').rstrip('у')
            if город_имя.lower() in ['астана', 'астану', 'астане']: город_имя = 'Астана'
            elif город_имя.lower() in ['алматы']: город_имя = 'Алматы'
            elif город_имя.lower() in ['шымент', 'шымкенту', 'шымкенте']: город_имя = 'Шымкент'
            город_raw = f'г. {город_имя.capitalize()}'

    регион = str(case_data.get('регион_дэр', '')).strip()
    if not регион or 'Н/Д' in регион or регион in ('По', 'по', 'ДЭР', '', 'None', '-'):
        found_region = None
        поиск_текст = (город_raw + ' ' + str(case_data.get('фабула_для_заголовка', ''))).lower()
        for k, v in CITY_TO_REGION.items():
            if k.lower() in поиск_текст:
                found_region = v
                break
        регион = found_region if found_region else 'по __________________'
        
    город = город_raw
    if not город or 'Н/Д' in город or город in ('', 'None', '-'):
        город = 'г. __________________'

    # Сначала пытаемся взять готовую структурированную фабулу от LLM
    фабула = case_data.get('фабула_для_заголовка')
    if not фабула or фабула == 'Н/Д':
        # Fallback для старых данных (если PDF не перепарсили)
        фабула_raw = case_data.get('суть_правонарушения', '')
        фабула = _build_compact_fabula(фабула_raw, case_data)

    # Жёсткая замена ООО → ТОО (в Казахстане не используется ООО)
    фабула = _fix_ooo(фабула)

    # Очищаем номер дела от префиксов КУИ/СДТБТ
    номер_ердр_raw = case_data.get('номер_ердр', '_______')
    номер_ердр_raw = re.sub(r'^\s*(?:КУИ|СДТБТ)\s*[№#]?\s*', '', str(номер_ердр_raw)).strip()
    номер_ердр_raw = re.sub(r'^[№#]\s*', '', номер_ердр_raw).strip()

    plan_title_info = {
        "номер_дела": номер_ердр_raw,
        "факт": фабула,
        "статья_ук_рк": uk_article_full,
        "город": город,
        "регион_дэр": регион,
        "год": _plan_year(case_data)
    }

    if include_template_actions:
        from core.procedural_validator import validate_and_order_actions
        filtered_actions = validate_and_order_actions(filtered_actions, case_data=case_data)
    else:
        filtered_actions = []

    return {
        "plan_title_info": plan_title_info,
        "actions": filtered_actions
    }


_CASE_NUM_RE = re.compile(r'(?<!\d)(\d{15})(?!\d)')


def _erdr_from_case_docs(case_data: dict, case_docs: list) -> None:
    """
    Рапорт часто зарегистрирован в КУИ («зарегистрировано в КУИ № 257100120001222»), а дело — в ЕРДР под другим
    номером, который повторяется в постановлениях («досудебного расследования №257100121000085»). В шапку плана
    и имя файла идёт номер ЕРДР; номер КУИ сохраняется в case_data['номер_куи'].
    """
    current = re.sub(r'\D', '', str(case_data.get('номер_ердр') or ''))
    raport = str(case_data.get('текст_рапорта') or '')
    if current and not re.search(r'КУИ\W{0,5}' + current, raport, re.IGNORECASE):
        return
    counts = {}
    for doc in case_docs or []:
        text = doc.get('text') or ''
        for m in _CASE_NUM_RE.finditer(text):
            if re.search(r'КУИ\W{0,5}$', text[max(0, m.start() - 10):m.start()], re.IGNORECASE):
                continue
            counts[m.group(1)] = counts.get(m.group(1), 0) + 1
    counts.pop(current, None)
    if not counts:
        return
    erdr, n = max(counts.items(), key=lambda kv: kv[1])
    if n < 2:
        return
    if current:
        case_data['номер_куи'] = current
    case_data['номер_ердр'] = erdr
    print(f"  🔢 Номер ЕРДР по документам дела: {erdr}" + (f" (рапорт — КУИ № {current})" if current else ""))


def generate_investigation_plan_with_llm(case_data, llm_client=None,methodology_dir="data/methodology", input_dir=None, main_pdf_path=None, pre_index_only: bool = False, case_number: str = None, db_root: str = "/root/database/workspaces", new_uploaded_docs: list = None):
    from core.qualification_guardrail import sanitize_qualification
    from core.procedural_validator import validate_and_order_actions
    from core.case_participants import set_case_persons
    set_case_persons(())  # лица прежнего дела не переносятся; список лиц этого дела — при проверке пунктов

    if case_data and 'статья_ук_рк' in case_data:
        case_data['статья_ук_рк'] = sanitize_qualification(case_data.get('статья_ук_рк'),
                                                           source_text=case_data.get('текст_рапорта'))

    if case_number:
        case_data['case_number'] = case_number
        case_data['db_root'] = db_root
    # Документы дела загружаются ОДИН раз (раньше — дважды: для заключений и для остального,
    # и каждый скан дважды отправлялся на OCR)
    case_docs = []
    if llm_client:
        try:
            if case_number:
                from core.case_documents import load_case_documents_from_db
                case_docs = load_case_documents_from_db(case_number, db_root=db_root, exclude_path=main_pdf_path)
            elif input_dir:
                case_docs = load_case_documents(input_dir, exclude_path=main_pdf_path)
            if not case_docs:
                print("ℹ Дополнительных документов дела не найдено")
        except Exception as e:
            print(f"⚠ Ошибка загрузки документов дела: {e}")
    if not case_number:
        _erdr_from_case_docs(case_data, case_docs)

    real_company = ''
    # Реальное название ищется только если рапорт обезличен заглушкой «ТОО «Х»». Иначе из «заключения»
    # (например, постановления о привлечении специалиста) бралось место работы специалиста
    # и подавалось модели как название организации по делу
    placeholder = re.compile('(?:ТОО|ООО)\\s*[«"\'‘“][ХXхx][»"\'’”]')
    has_placeholder = any(placeholder.search(str(case_data.get(f) or ''))
                          for f in ('суть_правонарушения', 'фабула_для_заголовка', 'фигуранты', 'текст_рапорта'))
    if llm_client and has_placeholder:
        try:
            print("\n🔍 Шаг 0: Поиск заключений специалистов для извлечения реального названия ТОО...")
            conclusions = [d for d in case_docs if d.get("doc_type") == "заключение"]

            if conclusions:
                # Перебираем заключения до тех пор, пока не найдём реальное название
                for conclusion_doc in conclusions:
                    print(f"  📋 Анализирую заключение: {conclusion_doc['filename']}")
                    extracted_name = llm_client.extract_company_name_from_conclusion(
                        conclusion_doc['text']
                    )
                    if extracted_name:
                        real_company = extracted_name
                        print(f"  ✅ РЕАЛЬНОЕ название найдено в заключении: {real_company}")
                        break  # Нашли — дальше не ищем
                
                if not real_company:
                    print("  ⚠ Заключения найдены, но извлечь реальное название не удалось")
            else:
                print("  ℹ Заключений не найдено — будет использовано название из рапорта")
        except Exception as e:
            print(f"  ⚠ Ошибка при поиске заключений: {e}")

    # Централизованная замена заглушки на реальное название ВО ВСЕХ полях
    if real_company:
        print(f"\n🔄 Замена заглушки ТОО «Х» → {real_company} во всех полях...")
        case_data = replace_placeholder_company(case_data, real_company)
    elif has_placeholder:
        print("ℹ Реальное название не найдено, используется название из рапорта")

    # Шаг 1.5: Форматирование фабулы для заголовка
    if llm_client:
        print("\n🧠 Форматирование полнотекстовой фабулы через LLM...")
        raw_fabula = case_data.get('суть_правонарушения', '')
        if raw_fabula and raw_fabula != 'Н/Д':
            formatted_fabula = llm_client.format_compact_fabula(raw_fabula, real_company=real_company)
            # Модель иногда вставляет английские слова («что constitutes организацию лотереи»):
            # латиница, которой нет в исходной фабуле (в отличие от Instagram, Hyundai), — повтор, затем исходный текст
            for attempt in range(2):
                foreign = _foreign_words(formatted_fabula, raw_fabula)
                if not foreign:
                    break
                print(f"  ⚠ В фабуле посторонние слова на латинице: {', '.join(foreign)}"
                      + (" — повторный запрос" if attempt == 0 else " — используется исходная фабула"))
                formatted_fabula = llm_client.format_compact_fabula(raw_fabula, real_company=real_company) \
                    if attempt == 0 else _build_compact_fabula(raw_fabula, case_data)
            if formatted_fabula:
                case_data['фабула_для_заголовка'] = _restore_case(formatted_fabula, raw_fabula)

    # Шаг 2: Генерируем шапку плана
    base_plan = generate_investigation_plan(case_data, include_template_actions=False)
    
    # Сохраняем имена обработанных файлов, чтобы не дублировать их при дозагрузке
    processed_list = []
    if main_pdf_path:
        processed_list.append(os.path.basename(main_pdf_path))
    if 'case_docs' in locals() and case_docs:
        processed_list.extend([d.get('filename') for d in case_docs if d.get('filename')])
    base_plan['processed_docs'] = list(set(processed_list))
    base_plan["actions"] = []

    # Если нет LLM клиента — возвращаем пустой план
    if llm_client is None:
        print("⚠ LLM клиент не указан, невозможно сгенерировать план")
        return base_plan

    # Шаг 3: Загружаем и ИНДЕКСИРУЕМ методику в RAG (вместо прямой вставки)
    uk_article = case_data.get('статья_ук_рк', '')

    # ═══════════════════════════════════════════════════════════════════════
    # Интеграция RAG (Retrieval-Augmented Generation)
    # ═══════════════════════════════════════════════════════════════════════
    from core.rag_engine import RAGEngine

    if case_number:
        # Workspace отдельного дела в БД — изолирован от других дел
        workspace_dir = os.path.join(db_root, case_number)
        rag = RAGEngine(
            workspace_dir=workspace_dir,
            api_url=llm_client.api_url,
            llm_model=llm_client.model
        )
        print(f"  📂 Использую локальный RAG-контекст дела из БД (без повторной индексации): {workspace_dir}")
    else:
        # ─── 3а. Загрузка файлами: общий LightRAG-сервер не разделяет дела, поэтому
        # поиск идёт только по документам этого дела (включая главный рапорт)
        rag = _case_retriever(
            ([{"filename": "Рапорт ЕРДР", "text": case_data['текст_рапорта']}] if case_data.get('текст_рапорта') else [])
            + list(case_docs or []))

    # ─── Поиск настоящего следователя (из Постановления о принятии дела) ───
    print("🔎 Поиск следователя из Постановления о принятии дела...")
    target_doc_text = ""
    if 'case_docs' in locals() and case_docs:
        # Сначала ищем по имени файла (самый надежный способ)
        for doc in case_docs:
            fname = doc.get('filename', '').lower()
            if ('о_принятии' in fname and 'производству' in fname) or 'өз_өндірісіне' in fname:
                target_doc_text = doc.get('text', '')
                print(f"  ℹ Найден документ по имени: {doc.get('filename')}")
                break
        
        # Если по имени не нашли, ищем по тексту
        if not target_doc_text:
            for doc in case_docs:
                text_lower = doc.get('text', '').lower()
                if ('о принятии' in text_lower and 'к своему производству' in text_lower) or \
                        'өз өндірісіне қабылдау' in text_lower:
                    target_doc_text = doc.get('text', '')
                    print("  ℹ Найден документ по тексту")
                    break

        # Иначе — любое постановление следователя: «Следователь … АЛИМХАНОВ А.К., рассмотрев материалы…»
        if not target_doc_text:
            for doc in case_docs:
                if re.search(r'(?:[Сс]ледовател|тергеуші)[^\n]{0,200}?,\s*(?:рассмотрев|қарап)', doc.get('text', '')):
                    target_doc_text = doc.get('text', '')
                    print(f"  ℹ Следователь ищется в постановлении: {doc.get('filename')}")
                    break

    if target_doc_text:
        # Сначала пробуем regex (быстро, надёжно)
        import re as _re_inv
        # Ищем: "Следователь ... ФИО" или "следователь ФИО" в начале или конце документа
        # Учитываем, что OCR может разбивать текст на несколько строк (\n)
        regex_fio = None
        kz_up, kz_any = 'А-ЯЁӘҒҚҢӨҰҮҺІ', 'а-яёА-ЯЁӘҒҚҢӨҰҮҺІәғқңөұүһі'
        inv_patterns = [
            # «Следователь по ОВД … АЛИМХАНОВ А.К., рассмотрев» / «тергеушісі АЛИМХАНОВ А.К. №…»
            _re_inv.compile(rf'(?:[Сс]ледователь|тергеушісі)(?:.|\n){{0,200}}?\s([{kz_up}][{kz_any}]+[ \n]*[{kz_up}]\.[ \n]*[{kz_up}]\.)'
                            rf'(?=\s*(?:,\s*(?:рассмотрев|қарап)|№))'),
            _re_inv.compile(r'[Сс]ледователь(?:.|\n){0,200}?\s([А-ЯЁ][а-яёА-ЯЁ]+[ \n]*[А-ЯЁ]\.[ \n]*[А-ЯЁ]\.)'),
            _re_inv.compile(r'[Сс]ледователь(?:.|\n){0,200}?\s([А-ЯЁ][а-яёА-ЯЁ]+[ \n]*[А-ЯЁ]\s*\.\s*[А-ЯЁ]\s*\.)'),
            _re_inv.compile(r'постановил(?:.|\n){0,150}?([А-ЯЁ][а-яёА-ЯЁ]+[ \n]*[А-ЯЁ]\.[ \n]*[А-ЯЁ]\.)', _re_inv.IGNORECASE)
        ]
        for pat in inv_patterns:
            m = pat.findall(target_doc_text)
            if m:
                # Берем первое совпадение (в начале документа "Старший следователь ... ФИО, рассмотрев")
                raw_fio = m[0].strip()
                regex_fio = _re_inv.sub(r'\s+', ' ', raw_fio).replace(' .', '.').replace('. ', '.')
                # Для гарантии правильного формата:
                regex_fio = regex_fio.replace(' ', ' ', 1) # просто пробел
                # «АЛИМХАНОВ А.К.» -> «Алимханов А.К.»
                surname, _, initials = regex_fio.partition(' ')
                if surname.isupper() and len(surname) > 1:
                    regex_fio = f"{surname.capitalize()} {initials}".strip()
                break
        
        if regex_fio:
            case_data['фио_следователя'] = regex_fio
            print(f"  ✅ Найден следователь (regex): {regex_fio}")
            # Должность — из того же постановления («Следователь по ОВД Департамента … по городу Астана»),
            # иначе в плане остаётся должность автора рапорта (оперуполномоченный дежурной службы)
            surname = regex_fio.split()[0]
            m_role = None
            for text in [target_doc_text] + [d.get('text', '') for d in case_docs]:
                m_role = _re_inv.search(rf'((?:[Сс]тарший\s+)?[Сс]ледователь[^,№;:]{{0,160}}?)\s+{_re_inv.escape(surname)}',
                                        text, _re_inv.IGNORECASE)
                if m_role:
                    break
            if m_role:
                case_data['должность_следователя'] = _re_inv.sub(r'\s+', ' ', m_role.group(1)).strip()
                print(f"  ✅ Должность следователя: {case_data['должность_следователя']}")
        else:
            # Если regex не помог — пробуем LLM
            fio_prompt = f"""Из следующего текста постановления извлеки ФИО следователя, который ВЫНЕС это постановление.
ФИО обычно указано в конце документа после слова "Следователь" (например: "Следователь ... Болатов Б.Б.").
Верни ТОЛЬКО ФИО в формате "Фамилия И.О." (например: Болатов Б.Б.), без лишних слов.
Если не можешь найти — верни Н/Д.

Текст: {target_doc_text[:5000]}"""
            try:
                extracted_fio = llm_client._call_api([{"role": "user", "content": fio_prompt}], temperature=0.1, max_tokens=50).strip()
                extracted_fio = _normalize_fio(extracted_fio.replace('"', '').replace("'", "").strip())
                if extracted_fio and extracted_fio != "Н/Д" and len(extracted_fio) < 50 and " " in extracted_fio:
                    case_data['фио_следователя'] = extracted_fio
                    print(f"  ✅ Найден следователь (LLM): {extracted_fio}")
                else:
                    print(f"  ℹ Следователь из постановления не найден, используем: {case_data.get('фио_следователя', 'Н/Д')}")
            except Exception as e:
                print(f"  ⚠ Ошибка при поиске следователя: {e}")
    else:
        print(f"  ℹ Документ 'Постановление о принятии...' не найден, используем: {case_data.get('фио_следователя', 'Н/Д')}")

    # ─── 3б. Индексируем методику в RAG (ВАРИАНТ A: специфичная ИЛИ общая) ─
    print("📚 Индексирую методику в RAG (Вариант A)...")
    methodology_docs = []
    try:
        from core.methodology_loader import load_specific_or_general
        # Фабула выбирает блок вида преступления (ст.214: подакцизные товары, криптоактивы, займы)
        fabula_for_type = "\n".join(str(case_data.get(k) or '') for k in
                                    ('фабула_для_заголовка', 'суть_правонарушения', 'текст_рапорта'))[:8000]
        method_result = load_specific_or_general(methodology_dir, uk_article, fabula_for_type)
        if method_result.get("text"):
            source_label = method_result.get("source", "методика")
            methodology_docs.append({
                "filename": method_result.get("filename", "методика"),
                "text": method_result["text"],
                "type": f"методика_{source_label}"
            })
            if not case_number:
                rag.add_documents(methodology_docs)
            print(f"  ✅ Загружена методика [{source_label}]: {method_result.get('filename')}")
        else:
            print("  ℹ Методики не найдены")
    except Exception as e:
        print(f"⚠ Ошибка индексации методик: {e}")

    # --- 3v. Build context: methodology (algorithm section only) + case facts from RAG ---
    fabula_for_search = case_data.get('фабула_для_заголовка', case_data.get('суть_правонарушения', ''))

    methodology_full_text = method_result.get("text", "") if 'method_result' in locals() else ""

    # Extract only the ALGORITHM section from the methodology to drastically reduce token usage.
    # The server has ~12k-13k total token limit (input+output).
    # Input must stay under ~7,000 tokens to leave enough room for a 20+ action JSON response.
    def _extract_algorithm_section(text: str) -> str:
        """
        Extracts the core ALGORITHM / STAGES section from a methodology document.
        Skips preamble and explanatory sections, keeping only the actionable stages.
        """
        if not text:
            return text
        # Методика в пределах лимита передаётся целиком: вводные разделы (специфика состава, статусы
        # участников, требования Порядка СЭР) так же важны, как этапы, и не должны отрезаться
        if len(text) <= 12000:
            print(f"  ℹ Методика передаётся целиком ({len(text):,} симв.)")
            return text
        # Look for section markers that typically begin the actionable plan
        stage_markers = [
            'АЛГОРИТМ ПЛАНА', 'АЛГОРИТМ РАССЛЕДОВАНИЯ', 'ЭТАПЫ РАССЛЕДОВАНИЯ',
            'ЭТАП 1', 'Этап 1', 'ПЛАН РАССЛЕДОВАНИЯ', 'АЛГОРИТМ:', 'АЛГОРИТМ ',
        ]
        best_idx = len(text)
        for marker in stage_markers:
            idx = text.find(marker)
            if idx != -1 and idx < best_idx:
                best_idx = idx

        if best_idx < len(text) // 2:  # Found a marker in the first half
            extracted = text[best_idx:]
            print(f"  ℹ Методика: извлечён алгоритм ({len(extracted):,} из {len(text):,} симв.)")
            return extracted
        else:
            # No clear marker found — use the full text but cap it
            print(f"  ℹ Методика: маркер алгоритма не найден, используется полный текст")
            return text

    algorithm_text = _extract_algorithm_section(methodology_full_text)

    # Hard cap on methodology: 12,000 chars — leaves maximum budget for dense case facts
    MAX_METHODOLOGY_CHARS = 12000
    if len(algorithm_text) > MAX_METHODOLOGY_CHARS:
        print(f"  ⚠ Алгоритм методики обрезан до {MAX_METHODOLOGY_CHARS:,} симв. (был {len(algorithm_text):,})")
        algorithm_text = algorithm_text[:MAX_METHODOLOGY_CHARS]

    # Case facts from RAG: k=30 to fetch the most salient and diverse evidence chunks across the 50 documents
    # Build retrieved_context: methodology algorithm FIRST, facts second.
    # Total target: strictly under 26,000 chars (~6,500 tokens) to guarantee safety under the server's 28k char limit.
    MAX_CONTEXT_CHARS = APPEND_CONTEXT_CHARS
    method_block = f"=== МЕТОДИКА РАССЛЕДОВАНИЯ (ЭТАПЫ ПЛАНА) ===\n{algorithm_text}\n\n"

    # Если все документы дела в сжатом виде (протоколы — шапка и показания) помещаются в лимит, модель получает их
    # целиком: поиск по фрагментам нужен только большим делам, где всё не помещается
    from core.doc_compress import build_documents_context, compress_document
    facts_budget = MAX_CONTEXT_CHARS - len(method_block) - 80
    compressed_total = sum(len(compress_document(d)) + 60 for d in case_docs or [])
    if case_docs and compressed_total <= facts_budget:
        facts_context = build_documents_context(case_docs, facts_budget)
        print(f"🧠 Факты дела: все {len(case_docs)} док. целиком в сжатом виде ({compressed_total:,} симв.)")
    else:
        facts_query = f"Все фигуранты, имена, подозреваемые, названия ТОО, ИП, суммы ущерба, адреса, банковские счета. Фабула: {fabula_for_search}"
        print("🧠 Извлечение фактических данных из RAG (оптимизированный выбор ключевых улик)...")
        facts_context = rag.search_relevant_context(facts_query, k=30)
        if not facts_context and case_docs:
            print("  ℹ [fallback] Поиск пуст, контекст строится из всех документов с поровну разделённым лимитом...")
            facts_context = build_documents_context(case_docs, facts_budget)
    facts_block = f"=== ДАННЫЕ ИЗ МАТЕРИАЛОВ ДЕЛА (используй для заполнения) ===\n{facts_context}" if facts_context else ""

    available_for_facts = MAX_CONTEXT_CHARS - len(method_block)
    if available_for_facts < 0:
        method_block = method_block[:MAX_CONTEXT_CHARS]
        facts_block = ""
        available_for_facts = 0
    elif len(facts_block) > available_for_facts:
        print(f"  ⚠ Факты дела обрезаны до {available_for_facts:,} симв. (алгоритм методики сохранён полностью)")
        facts_block = facts_block[:available_for_facts]

    retrieved_context = method_block + facts_block
    print(f"  ℹ Контекст: алгоритм {len(method_block):,} симв., факты {len(facts_block):,} симв., итого {len(retrieved_context):,} симв.")




    # Шаг 3в-2: Процессуальный статус участников — кто ведёт дело / надзирает / защищает / даёт заключение
    print("\n👮 Шаг 3в-2: Определение должностных лиц (следователи, руководство ДЭР, прокуроры, адвокаты, эксперты)...")
    source_corpus, officials = _prepare_participants(case_data, case_docs, [facts_context])
    from core.case_participants import officials_prompt_block, remove_officials_from_figurants
    excluded_persons = officials_prompt_block(officials)

    # Шаг 3в-3: Какие следственные действия уже проведены (протоколы, заключения, постановления, ответы)
    try:
        from core.conducted_actions import detect_conducted_actions
        docs_for_detection = list(case_docs or [])
        if input_dir and os.path.isdir(input_dir):
            # Сканы, текст которых не извлечён (OCR недоступен), загрузчик отбрасывает —
            # но по названию файла («Протокол выемки у Инкербаевой А.Б.») действие всё равно видно
            loaded = {d.get('filename') for d in docs_for_detection}
            main_name = os.path.basename(main_pdf_path) if main_pdf_path else None
            docs_for_detection += [
                {'filename': f, 'text': ''} for f in sorted(os.listdir(input_dir))
                if f not in loaded and f != main_name and os.path.isfile(os.path.join(input_dir, f))
                and not f.startswith(('.', '~$'))]
        case_data['проведённые_действия'] = detect_conducted_actions(docs_for_detection, officials)
    except Exception as e:
        print(f"  ⚠ Не удалось определить проведённые действия: {e}")
        case_data['проведённые_действия'] = []

    # Шаг 3в-4: Потерпевшие, их представители, свидетели, специалисты — не фигуранты
    from core.case_participants import other_participants
    others = other_participants(case_data['проведённые_действия'], source_corpus, officials)
    # Соучастники, дело в отношении которых окончено (в выделенном деле), — не фигуранты этого дела
    from core.case_participants import completed_case_persons
    known_others = {o['key'] for o in others}
    others += [p for p in completed_case_persons(
        "\n".join([source_corpus, str(case_data.get('суть_правонарушения') or ''),
                   str(case_data.get('фабула_для_заголовка') or '')])) if p['key'] not in known_others]
    case_data['иные_участники'] = others
    if others:
        print("  👥 Иные участники (не фигуранты): " + ", ".join(f"{o['fio']} ({o['role']})" for o in others))
        excluded_persons = "\n".join(filter(None, [excluded_persons, officials_prompt_block(others)]))

    # Шаг 3г: Извлечение ВСЕХ фигурантов
    # ─── Два прохода: сначала лучшее имя из основного контекста,
    # ─── затем ПОЛНЫЙ список через 4 специализированных RAG-запроса ───────────
    current_figurant = case_data.get('фигуранты', 'Н/Д')
    if isinstance(current_figurant, list):
        current_figurant = ", ".join(str(x) for x in current_figurant if str(x).strip())
    elif not isinstance(current_figurant, str):
        current_figurant = str(current_figurant or '')
    # Из рапорта модель берёт и тех, кто упомянут рядом с подозреваемым («непосредственный руководитель
    # директор ТОО Андреева Г.М.»): кроме первого лица, фигурант — только при данных о подозрении
    first_figurant = current_figurant.split(',')[0].strip()
    if first_figurant != current_figurant.strip():
        current_figurant = _confirmed_new_figurants(first_figurant, current_figurant, source_corpus)
    case_data['фигуранты'] = current_figurant
    print(f"\n🔎 Шаг 3г: Поиск всех фигурантов (известно: '{current_figurant}')...")

    # Прогон 1: лучшее имя из основного RAG-контекста
    try:
        best_name = llm_client.extract_best_suspect_name(
            current_figurant=current_figurant,
            rag_context=retrieved_context,
            investigator_name=case_data.get('фио_следователя', ''),
            investigator_role=case_data.get('должность_следователя', ''),
            excluded_persons=excluded_persons,
        )
        from core.case_participants import find_person_mentions as _fpm, normalize_text as _nt
        cur_m, best_m = _fpm(_nt(current_figurant)), _fpm(_nt(best_name or ''))
        # «Орлова А.Е.» -> «Орловой А.Е.» — то же лицо в другом падеже: имя не меняем
        same_person = cur_m and {m['key'] for m in cur_m} == {m['key'] for m in best_m} \
            and sum(bool(m.get('patronymic')) for m in best_m) <= sum(bool(m.get('patronymic')) for m in cur_m)
        if best_name and best_name != current_figurant and same_person:
            print(f"  ℹ [1] Имя не изменилось (то же лицо): '{current_figurant}'")
        elif best_name and best_name != current_figurant:
            print(f"  ✅ [1] Основной фигурант: '{current_figurant}' → '{best_name}'")
            case_data['фигуранты'] = best_name
        else:
            print(f"  ℹ [1] Имя не изменилось: '{current_figurant}'")
    except Exception as e:
        print(f"  ⚠ [1] Ошибка при обогащении имени: {e}")

    # Прогон 2: ищем ВСЕХ фигурантов через 4 отдельных RAG-запроса
    print("  🔎 [2] Расширенный поиск ВСЕХ фигурантов (4 RAG-запроса)...")
    try:
        ctx_suspects = rag.search_relevant_context(
            "подозреваемый обвиняемый проверяемый фигурант в отношении", k=20)
        ctx_names = rag.search_relevant_context(
            "фамилия имя отчество гражданин ФИО директор руководитель", k=20)
        ctx_protocols = rag.search_relevant_context(
            "протокол допроса потерпевший свидетель показал пояснил", k=15)
        ctx_conclusion = rag.search_relevant_context(
            "заключение экспертизы совершил хищение ущерб", k=15)

        all_figurants = llm_client.extract_all_suspects(
            contexts=[ctx_suspects, ctx_names, ctx_protocols, ctx_conclusion],
            investigator_name=case_data.get('фио_следователя', ''),
            investigator_role=case_data.get('должность_следователя', ''),
            existing_figurants=case_data.get('фигуранты', ''),
            excluded_persons=excluded_persons,
        )
        if all_figurants and all_figurants != case_data.get('фигуранты', ''):
            print(f"  ✅ [2] Все фигуранты: '{all_figurants}'")
            case_data['фигуранты'] = _confirmed_new_figurants(case_data.get('фигуранты', ''), all_figurants,
                                                              source_corpus)
        else:
            print(f"  ℹ [2] Дополнительных фигурантов не найдено")
    except Exception as e:
        print(f"  ⚠ [2] Ошибка при расширенном поиске фигурантов: {e}")

    # Финал: транслитерация казахских букв → русские; должностные лица не могут быть фигурантами
    case_data['фигуранты'] = _transliterate_kaz_to_rus(case_data.get('фигуранты') or '')
    case_data['фигуранты'] = remove_officials_from_figurants(case_data['фигуранты'], officials + others)
    from core.case_participants import add_defended_to_figurants, nominative_figurants
    case_data['фигуранты'] = add_defended_to_figurants(case_data['фигуранты'], source_corpus)
    case_data['фигуранты'] = nominative_figurants(case_data['фигуранты'], source_corpus)
    case_data['фигуранты_с_полом'] = _figurants_with_gender(case_data['фигуранты'], source_corpus)
    print(f"  🔤 Итого фигурантов: '{case_data['фигуранты']}' (Определен пол: {case_data['фигуранты_с_полом']})")

    # Шаг 3д: Обогащение адресов фигурантов/юрлиц из RAG
    print("\n🔎 Шаг 3д: Расширенный поиск адресов из материалов дела...")
    try:
        ctx_addr = rag.search_relevant_context(
            "адрес место жительства регистрации город улица прописка офис", k=15)
        addresses = llm_client.extract_all_addresses(contexts=[ctx_addr])
        if addresses and addresses != "Н/Д":
            print(f"  ✅ Найдены адреса: \n{addresses}")
            case_data['адреса_фигурантов'] = addresses
        else:
            print("  ℹ Адреса не найдены")
    except Exception as e:
        print(f"  ⚠ Ошибка при поиске адресов: {e}")
        case_data['адреса_фигурантов'] = "Н/Д"

    print("\n🧠 Шаг 3е: Анализ стратегии расследования (по методике)...")
    strategy_analysis = ""
    try:
        strategy_analysis = llm_client.analyze_investigation_strategy(case_data, retrieved_context)
        print("  ✅ Анализ стратегии успешно завершен.")
    except Exception as e:
        print(f"  ⚠ Ошибка при анализе стратегии: {e}")

    print("\n🧠 Начинаю полную генерацию следственных действий через LLM (RAG)...")
    
    # Сохраняем весь собранный контекст для Варианта А (Пре-индексация)
    pre_context_data = {
        "case_data": case_data,
        "retrieved_context": retrieved_context,
        "strategy_analysis": strategy_analysis,
        # Материалы дела — для проверки ФИО в плане при live-генерации
        "source_corpus": source_corpus
    }
    номер = sanitize_filename(case_data.get('номер_ердр', case_data.get('номер_дела', 'unknown')))
    os.makedirs(os.path.join("data", "plans"), exist_ok=True)
    pre_context_file = os.path.join("data", "plans", f"{номер}_pre_context.json")
    with open(pre_context_file, "w", encoding="utf-8") as f:
        json.dump(pre_context_data, f, ensure_ascii=False, indent=2)
    print(f"💾 Пре-индексированный контекст сохранен для Варианта А: {pre_context_file}")
    
    if pre_index_only:
        print("✅ Вариант А: Пре-индексация завершена. Генерация действий отложена до команды 'в прямом эфире'.")
        base_plan['case_data'] = case_data
        base_plan['actions'] = []
        save_plan_to_json(base_plan)
        return base_plan
        
    # Обрезаем стратегию, чтобы промпт вмещался в лимит модели
    if len(strategy_analysis) > 2000:
        strategy_analysis = strategy_analysis[:2000] + "\n...[стратегия урезана]..."
    try:
        generated_actions = llm_client.generate_all_actions(
            case_data=case_data,
            retrieved_context=retrieved_context,
            strategy_analysis=strategy_analysis
        )
        
        if generated_actions:
            # Полнота по этапам методики (арест имущества, запросы об активах, проверка показаний и т.д.)
            generated_actions = _complete_by_methodology(generated_actions, case_data, retrieved_context, llm_client)
            # Предельные сроки этапов методики — для расстановки дат (сохраняются в плане для дозагрузки)
            case_data['сроки_этапов'] = _stage_limits(retrieved_context)
            case_data['относительные_сроки_этапов'] = _relative_stage_limits(retrieved_context)
            # Этап «Осмотры» — предел для осмотра полученных по запросам сведений
            case_data['этап_осмотров'] = _inspection_stage(retrieved_context)
            # Этап временных ограничений — для ограничения, добавляемого перед арестом
            case_data['этап_ограничений'] = _restriction_stage(retrieved_context)
            # Этап допросов подозреваемых — для первоначального допроса каждого фигуранта
            case_data['этап_допросов_подозреваемых'] = _suspect_interview_stage(retrieved_context)
            # Нумерация, сроки, очистка, статус участников (выдуманные ФИО, сотрудники органа,
            # адвокаты, эксперты), процессуальная валидация и сортировка по блокам УПК РК
            generated_actions = _postprocess_generated_actions(generated_actions, case_data, source_corpus)

            base_plan["actions"] = generated_actions
            print(f"✅ Успешно сгенерировано {len(generated_actions)} следственных действий")
        else:
            print("⚠ LLM не смог сгенерировать действия")
            base_plan["ошибка_генерации"] = "Модель не вернула ни одного следственного действия."

    except Exception as e:
        print(f"⚠ Ошибка при генерации действий: {e}")
        base_plan["ошибка_генерации"] = f"Ошибка при генерации следственных действий: {e}"

    if not base_plan.get("actions") and case_data.get('проведённые_действия'):
        # Модель ничего не вернула — план всё равно отражает уже проведённое по материалам дела
        base_plan["actions"] = _apply_conducted(validate_and_order_actions([], case_data=case_data), case_data)
        print(f"  ℹ В план включены только обязательные и уже проведённые действия ({len(base_plan['actions'])})")



    # Сохраняем данные дела внутри плана (нужно при дозагрузке)
    base_plan['case_data'] = case_data
    save_plan_to_json(base_plan)

    return base_plan


# ─────────────────────────────────────────────────────────────────────────────
# Хранилище планов (JSON)
# ─────────────────────────────────────────────────────────────────────────────

PLANS_DIR = os.path.join(os.path.dirname(__file__), '..', 'data', 'plans')


def sanitize_filename(name: str) -> str:
    """Очищает строку для использования в качестве имени файла."""
    if not name:
        return 'unknown'
    # Заменяем все недопустимые символы (включая слеш) на подчеркивание
    name = re.sub(r'[\\/*?:"<>|\x00-\x1f]', '_', str(name)).strip()
    # «..» и имена из одних точек указывают на родительскую папку
    name = name.lstrip('.')
    return name or 'unknown'


def normalize_plan_statuses(plan: dict) -> dict:
    """
    Гарантирует, что каждый пункт плана содержит поле 'статус'.
    Пункты без статуса получают значение 'Не исполнен' по умолчанию.
    Вызывается перед сохранением и перед отдачей JSON клиенту.
    """
    for action in plan.get('actions', []):
        if not action.get('статус'):
            action['статус'] = 'Не исполнен'
    if plan.get('actions'):
        plan['actions'] = executed_to_end(plan['actions'], (plan.get('case_data') or {}).get('проведённые_действия'))
    return plan


def _date_key(value: str):
    m = re.match(r'\s*(\d{1,2})\.(\d{1,2})\.(\d{4})', str(value or ''))
    return (0, int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else (1, 0, 0, 0)


def _answer_date(request: dict, done: list, conducted: list) -> str:
    """
    Дата первого ответа на исполненный запрос без даты (в деле — проект запроса с пустыми полями номера и даты).
    Пара «запрос — ответ» — по органу в проведённых действиях («запрос в нао» и «ответ с нао»),
    иначе — по адресату и предмету среди исполненных пунктов «Истребовать сведения …» / «Получены сведения …».
    """
    from core.procedural_validator import _chain_step, _request_identity, _same_request
    record = next((c for c in conducted or [] if c.get('тип') == 'запрос'
                   and c.get('файл') and c.get('файл') == request.get('основание_статуса')), None)
    if record and record.get('органы'):
        dates = [c.get('дата') for c in conducted if c.get('тип') == 'ответ' and c.get('дата')
                 and set(c.get('органы') or []) & set(record['органы'])]
    else:
        ident = _request_identity(request.get('действие', ''))
        dates = [a.get('срок') for a in done if _date_key(a.get('срок'))[0] == 0
                 and (_chain_step(a.get('действие', '').lower()) in ('истребование', 'осмотр_сведений')
                      or a.get('действие', '').startswith('Получены сведения по запросу'))
                 and _same_request(ident, _request_identity(a.get('действие', '')))]
    return min(dates, key=_date_key) if dates else ''


def executed_to_end(actions: list, conducted: list = None) -> list:
    """
    Исполненные пункты — в конце плана, по дате документа-основания (без даты — последними);
    неисполненные сохраняют свой порядок (блоки УПК, цепочки запросов). Нумерация — сквозная заново.
    Запрос без даты стоит сразу перед ответом на него (дата в графе «Срок» не выдумывается).
    Действует для первичного плана, дозагрузки, обновления статусов и правок через API.
    """
    from core.procedural_validator import _chain_step
    pending = [a for a in actions if a.get('статус') != 'Исполнен']
    done = [a for a in actions if a.get('статус') == 'Исполнен']
    keys = {}
    for a in done:
        key = _date_key(a.get('срок'))
        if key[0] == 1 and (_chain_step(a.get('действие', '').lower()) == 'запрос'
                            or any(c.get('тип') == 'запрос' and c.get('файл') == a.get('основание_статуса')
                                   for c in conducted or [] if c.get('файл'))):
            answered = _answer_date(a, done, conducted)
            if answered:
                keys[id(a)] = _date_key(answered) + (0,)
                continue
        keys[id(a)] = key + (1,)
    done.sort(key=lambda a: keys[id(a)])
    ordered = pending + done
    for idx, action in enumerate(ordered, start=1):
        action['номер'] = idx
    return ordered
def save_plan_to_json(plan: dict, plans_dir: str = PLANS_DIR) -> str:
    """Сохраняет план в {plans_dir}/{номер_дела}.json и в базу данных PostgreSQL. Возвращает путь."""
    os.makedirs(plans_dir, exist_ok=True)
    # Нормализуем статусы перед записью — поле 'статус' обязательно для каждого пункта
    plan = normalize_plan_statuses(plan)
    # Отметка о достижении порога исполнения (уведомление руководству обновить план)
    from core.plan_statistics import update_plan_notice
    update_plan_notice(plan)
    номер_raw = plan.get('plan_title_info', {}).get('номер_дела', 'unknown')
    номер = sanitize_filename(номер_raw)
    filepath = os.path.join(plans_dir, f"{номер}.json")
    
    # 1. Сохранение в JSON
    with open(filepath, 'w', encoding='utf-8') as f:
        import json
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print(f"💾 План сохранён в файл: {filepath}")

    # 2. Сохранение в PostgreSQL
    case_number = plan.get('case_data', {}).get('case_number') or plan.get('plan_title_info', {}).get('номер_дела') or plan.get('case_data', {}).get('номер_ердр')
    if case_number and case_number != 'unknown':
        try:
            from core.db import save_plan_to_db
            save_plan_to_db(case_number, plan)
        except Exception as e:
            print(f"⚠ Ошибка при сохранении плана в PostgreSQL: {e}")

    # 3. Дополнительно сохраняем план в директорию БД дела, если она существует (фолбэк)
    db_root = plan.get('case_data', {}).get('db_root') or '/root/database/workspaces'
    if case_number:
        db_workspace_dir = os.path.join(db_root, sanitize_filename(case_number))
        # Пишем только в папку дела внутри корня БД, а не в произвольную существующую папку
        root_real = os.path.realpath(db_root)
        try:
            inside_root = os.path.commonpath([root_real, os.path.realpath(db_workspace_dir)]) == root_real
        except ValueError:  # разные диски в Windows
            inside_root = False
        if inside_root and os.path.exists(db_workspace_dir):
            db_filepath = os.path.join(db_workspace_dir, "plan.json")
            try:
                with open(db_filepath, 'w', encoding='utf-8') as f:
                    json.dump(plan, f, ensure_ascii=False, indent=2)
                print(f"💾 План также сохранён в директорию БД: {db_filepath}")
            except Exception as e:
                print(f"⚠ Ошибка при сохранении плана в директорию БД: {e}")
    return filepath


def load_plan_from_json(plan_path: str) -> dict:
    """Загружает план из JSON-файла."""
    import json
    with open(plan_path, 'r', encoding='utf-8') as f:
        return json.load(f)


def generate_plan_live_from_context(case_number: str, llm_client) -> dict:
    """
    Вариант А: Выполняет живую генерацию действий в прямом эфире
    на основе заранее подготовленного контекста (pre_context.json).
    """
    import time
    from datetime import datetime, timedelta
    
    номер = sanitize_filename(case_number)
    pre_context_file = os.path.join("data", "plans", f"{номер}_pre_context.json")
    
    if not os.path.exists(pre_context_file):
        raise FileNotFoundError(f"Файл контекста для дела №{case_number} не найден: {pre_context_file}")
        
    print(f"\n⚡ [Вариант А] Загрузка подготовленного контекста: {pre_context_file}")
    with open(pre_context_file, "r", encoding="utf-8") as f:
        pre_ctx = json.load(f)
        
    case_data = pre_ctx["case_data"]
    retrieved_context = pre_ctx["retrieved_context"]
    strategy_analysis = pre_ctx["strategy_analysis"]
    
    # Генерируем базовую структуру шапки плана
    base_plan = generate_investigation_plan(case_data, include_template_actions=False)
    base_plan['case_data'] = case_data
    base_plan['processed_docs'] = []
    
    print("🧠 Запуск LLM-генерации действий в прямом эфире...")
    t0 = time.time()
    
    if len(strategy_analysis) > 2000:
        strategy_analysis = strategy_analysis[:2000] + "\n...[стратегия урезана]..."
        
    generated_actions = llm_client.generate_all_actions(
        case_data=case_data,
        retrieved_context=retrieved_context,
        strategy_analysis=strategy_analysis
    )
    
    elapsed = time.time() - t0
    print(f"⏱ Ответ от LLM получен за {elapsed:.2f} сек.")
    
    if generated_actions:
        # Корпус материалов дела сохранён при пре-индексации; для старых контекстов —
        # то, что видела модель (контекст + данные дела)
        source_corpus = pre_ctx.get("source_corpus")
        if not source_corpus:
            from core.case_participants import build_source_corpus
            source_corpus = build_source_corpus(case_data, extra_texts=[retrieved_context])
        generated_actions = _complete_by_methodology(generated_actions, case_data, retrieved_context, llm_client)
        case_data['сроки_этапов'] = _stage_limits(retrieved_context)
        case_data['относительные_сроки_этапов'] = _relative_stage_limits(retrieved_context)
        case_data['этап_осмотров'] = _inspection_stage(retrieved_context)
        case_data['этап_ограничений'] = _restriction_stage(retrieved_context)
        case_data['этап_допросов_подозреваемых'] = _suspect_interview_stage(retrieved_context)
        generated_actions = _postprocess_generated_actions(generated_actions, case_data, source_corpus)

        base_plan["actions"] = generated_actions
        print(f"✅ Успешно обработаны {len(generated_actions)} следственных действий в прямом эфире")
    else:
        print("⚠ LLM не смог сгенерировать действия")
        base_plan["ошибка_генерации"] = "Модель не вернула ни одного следственного действия."
        if case_data.get('проведённые_действия'):
            from core.procedural_validator import validate_and_order_actions
            base_plan["actions"] = _apply_conducted(validate_and_order_actions([], case_data=case_data), case_data)
        
    save_plan_to_json(base_plan)
    return base_plan


# ─────────────────────────────────────────────────────────────────────────────
# Детерминированная дедупликация новых действий
# ─────────────────────────────────────────────────────────────────────────────

_ACTION_TYPE_KEYWORDS = [
    ('выемка_носители', ['выемку электронных носителей', 'выемка электронных носителей', 'архивами электронной почты', 'журналами доступа к системе 1с']),
    ('выемка_банк',     ['выемку в банк', 'выемку документов банк', 'банках второго уровня', 'юридических дел банк']),
    ('запрос_бву',      ['направить запрос в ао', 'направить запросы в ао', 'kaspi bank', 'halyk bank', 'bereke bank', 'отбасы банк']),
    ('запрос_нао',      ['нао «правительство для граждан»', 'нао «государственная корпорация', 'сведений о зарегистрированном имуществе']),
    ('экспертиза_экон', ['судебно-экономическую экспертизу', 'судебно-экономической экспертиз']),
    ('экспертиза_комп', ['компьютерно-техническую экспертизу', 'компьютерно-техническ']),
    ('арест',           ['наложить арест', 'арест имущества', 'временное ограничение']),
    ('осмотр_документов', ['осмотр документов', 'осмотра документов', 'осмотр протокола']),
]


_FP_STOP = set('''произвести провести осмотр осмотреть направить направлен запрос запроса истребовать истребовании
вынести постановление предоставлении предоставить сведений сведения данных данные информации полученных полученные
изъятых изъятые банковских банковские выписок выписки предмет целью также всех всем период уполномоченный орган
организации документов документы копий установления установить счетам счетов'''.split())


def _action_fingerprint(action_text: str) -> str:
    """
    Возвращает нормализованный fingerprint действия:
    «тип_действия|ключевое_слово_1|ключевое_слово_2»
    Используется для семантической дедупликации.
    """
    t = action_text.lower()

    # Тип действия
    action_type = 'общее'
    for atype, kws in _ACTION_TYPE_KEYWORDS:
        if any(kw in t for kw in kws):
            action_type = atype
            break

    # Предмет действия: первые значимые слова (кириллица и латиница — «Дельта Бизнес», «Hyundai», «Омега»),
    # без служебных слов формулировки: иначе «запрос в Kaspi о счетах ИП Омега» и «запрос в Kaspi о владельцах
    # счетов» давали один отпечаток «направить|запрос|каспи|банк|предоставлении|сведений» и склеивались
    words = [w.lower() for w in re.findall(r'[А-ЯЁа-яёA-Za-z]{4,}', action_text)]
    key_names = tuple(sorted(set(list(dict.fromkeys(w for w in words if w not in _FP_STOP))[:6])))

    return f"{action_type}|{'|'.join(key_names)}"


def _load_saved_corpus(case_data: dict) -> str:
    """Корпус материалов дела, сохранённый при первичной генерации (pre_context.json)."""
    номер = sanitize_filename(case_data.get('номер_ердр', case_data.get('номер_дела', 'unknown')))
    path = os.path.join("data", "plans", f"{номер}_pre_context.json")
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f).get("source_corpus", "") or ""
        except Exception:
            pass
    return ""


def _case_stage_limits(case_data: dict) -> dict:
    """
    Сроки этапов методики дела; у планов, созданных до их сохранения, — из сохранённого контекста генерации.
    Заодно — номер этапа «Осмотры» (case_data['этап_осмотров']), если его ещё нет в плане.
    """
    if case_data.get('сроки_этапов') is None or 'этап_осмотров' not in case_data or 'этап_ограничений' not in case_data \
            or 'этап_допросов_подозреваемых' not in case_data or 'относительные_сроки_этапов' not in case_data:
        номер = sanitize_filename(case_data.get('номер_ердр', case_data.get('номер_дела', 'unknown')))
        path = os.path.join("data", "plans", f"{номер}_pre_context.json")
        context = ''
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    context = json.load(f).get("retrieved_context", "") or ""
            except Exception:
                pass
        if case_data.get('сроки_этапов') is None:
            case_data['сроки_этапов'] = _stage_limits(context)
        if 'этап_осмотров' not in case_data:
            case_data['этап_осмотров'] = _inspection_stage(context)
        # без сохранённого контекста этап не известен — тогда он берётся у ограничений, уже стоящих в плане
        if 'этап_ограничений' not in case_data and context:
            case_data['этап_ограничений'] = _restriction_stage(context)
        if 'этап_допросов_подозреваемых' not in case_data and context:
            case_data['этап_допросов_подозреваемых'] = _suspect_interview_stage(context)
        if 'относительные_сроки_этапов' not in case_data and context:
            case_data['относительные_сроки_этапов'] = _relative_stage_limits(context)
    return case_data['сроки_этапов']


def _norm_doc_name(name: str) -> str:
    name = re.sub(r'\.(?:pdf|docx?|txt|xlsx)$', '', str(name or '').strip(), flags=re.IGNORECASE)
    return re.sub(r'[\s_.\-()«»"]+', ' ', name).strip().lower()


def _check_action_sources(actions: list, sources: list) -> list:
    """
    Пункт дозагрузки обязан опираться на документ дела («основание»: имя файла) или на этап методики.
    Ссылка на файл, которого в деле нет, — выдумка модели: такой пункт удаляется. Без основания — остаётся.
    """
    known = [_norm_doc_name(s) for s in sources or [] if s]
    kept = []
    for a in actions:
        basis = str(a.get('основание') or '').strip()
        b = _norm_doc_name(basis)
        if not b or re.match(r'(?:методик|этап)', b):
            kept.append(a)
            continue
        words = set(b.split())
        ok = any(b in k or k in b or (words and len(words & set(k.split())) / len(words) >= 0.6) for k in known)
        if ok:
            kept.append(a)
        else:
            print(f"  ⛔ [Основание] Документа нет в деле («{basis[:60]}»), пункт удалён: '{a.get('действие', '')[:70]}'")
    return kept


def _append_sanctions(new_actions: list, existing: list, case_data: dict) -> list:
    """
    Дозагрузка — как при генерации: перед новой выемкой (обыском) — санкция, если её нет ни среди новых пунктов,
    ни в плане (216, дозагрузка: четыре выемки банковских выписок без санкций); повторная санкция или выемка того же
    адреса / объекта, что уже в плане, — дубль (предметы дописываются к пункту плана); у новой санкции без
    исполнения — выемка (обыск). Добавляются только пункты для новых выемок и санкций, план не дополняется.
    """
    from core.procedural_validator import (ensure_sanction_before_every_seizure, dedupe_sanctions,
                                           ensure_execution_after_sanction)
    existing = list(existing or [])
    new_ids = {id(a) for a in new_actions}
    out = ensure_sanction_before_every_seizure(existing + list(new_actions), case_data=case_data)
    base_ids = new_ids | {id(a) for a in existing}
    # добавленная санкция стоит прямо перед своей выемкой: берём только санкции к новым выемкам
    added = {id(x) for i, x in enumerate(out) if id(x) not in base_ids
             and i + 1 < len(out) and id(out[i + 1]) in new_ids}
    new_actions = [x for x in out if id(x) in new_ids or id(x) in added]
    new_ids = {id(a) for a in new_actions}
    survived = {id(a) for a in dedupe_sanctions(existing + new_actions)}
    new_actions = [a for a in new_actions if id(a) in survived]
    new_ids = {id(a) for a in new_actions}
    out = ensure_execution_after_sanction(existing + new_actions, case_data=case_data)
    base_ids = new_ids | {id(a) for a in existing}
    added = {id(x) for i, x in enumerate(out) if id(x) not in base_ids and i > 0 and id(out[i - 1]) in new_ids}
    return [x for x in out if id(x) in new_ids or id(x) in added]


def _filter_append_actions(new_actions: list, plan: dict, new_docs: list, retrieved_context: str,
                           new_conducted: list = None, sources: list = None) -> list:
    """
    Те же процессуальные фильтры, что и при первичной генерации: статус участников,
    выдуманные ФИО, действия с адвокатами/сотрудниками органа, обыски у свидетелей.
    Потерпевшие и свидетели определяются и по НОВЫМ протоколам: допрошенный в качестве
    потерпевшего в дозагруженном документе не может стать объектом запроса о счетах и кредитах.
    """
    if not new_actions:
        return new_actions
    case_data = plan.get('case_data', {})
    existing_texts = [a.get('действие', '') for a in plan.get('actions', [])]
    _, officials = _prepare_participants(case_data, new_docs, [retrieved_context])
    from core.case_participants import build_source_corpus, other_participants
    corpus = build_source_corpus(case_data, new_docs,
                                 [_load_saved_corpus(case_data), retrieved_context] + existing_texts)
    all_conducted = (case_data.get('проведённые_действия') or []) + (new_conducted or [])
    # Подзащитные из новых уведомлений о защите — фигуранты, а не свидетели
    from core.case_participants import add_defended_to_figurants, defended_persons
    figurants_before = case_data.get('фигуранты', '')
    case_data['фигуранты'] = add_defended_to_figurants(figurants_before, corpus)
    if case_data['фигуранты'] != figurants_before:
        case_data['фигуранты_с_полом'] = _figurants_with_gender(case_data['фигуранты'], corpus)
    defended = {p['key'] for p in defended_persons(corpus)}
    case_data['иные_участники'] = [o for o in case_data.get('иные_участники') or [] if o.get('key') not in defended]
    known = {o.get('key') for o in case_data.get('иные_участники') or []}
    case_data['иные_участники'] = (case_data.get('иные_участники') or []) + [
        o for o in other_participants(all_conducted, corpus, officials) if o['key'] not in known]
    for action in new_actions:
        _clean_action_texts(action, case_data)
    new_actions = _check_action_sources(
        new_actions, (sources or []) + [d.get('filename', '') for d in new_docs or []] + list(plan.get('processed_docs') or []))
    new_actions = _filter_by_status(new_actions, case_data, corpus, conducted=all_conducted)
    # Банковские санкции (выемки) по разным владельцам одного вида — один пункт, как при генерации
    from core.procedural_validator import merge_per_person_bank_items
    new_actions = merge_per_person_bank_items(new_actions)
    new_actions = _append_sanctions(new_actions, plan.get('actions', []), case_data)
    # Осмотр без запроса -> запрос; для запросов — цепочка «запрос → истребование → осмотр», как при генерации
    from core.procedural_validator import (ensure_request_inspection_followups, deduplicate_requests,
                                           ensure_seizure_before_inspection)
    new_actions = _inspections_to_requests(new_actions, existing_texts)
    # Адресат, явно противоречащий предмету запроса, — по компетенции органа (как при генерации)
    from core.procedural_validator import fix_request_addressee
    new_actions = fix_request_addressee(new_actions)
    # Повторный запрос к тому же адресату по тому же предмету — и среди новых пунктов, и относительно плана
    survived = {id(a) for a in deduplicate_requests(list(plan.get('actions', [])) + new_actions)}
    new_actions = [a for a in new_actions if id(a) in survived]
    new_actions = ensure_request_inspection_followups(new_actions, case_data=case_data)
    # Осмотр изъятого без выемки (ни в плане, ни в материалах) — осмотр ответа на запрос или выемка перед ним
    new_actions = ensure_seizure_before_inspection(new_actions, case_data=case_data,
                                                   context=plan.get('actions'), conducted=all_conducted)
    # Вещдок из ответа на запрос — с учётом осмотров и выемок, уже стоящих в плане
    from core.procedural_validator import drop_answer_evidence
    new_actions = drop_answer_evidence(new_actions, context=plan.get('actions'))

    # Повторный допрос/осмотр того, что уже есть в плане, — дубль
    from core.procedural_validator import deduplicate_semantic
    kept_ids = {id(a) for a in deduplicate_semantic(list(plan.get('actions', [])) + new_actions)}
    new_actions = [a for a in new_actions if id(a) in kept_ids]
    # Этапы методики (ограничений, допросов подозреваемых) — у старых планов из сохранённого контекста
    _case_stage_limits(case_data)
    # Первоначальный допрос — перед новым допросом подозреваемого по существу и каждому фигуранту, у которого его
    # нет ни в плане, ни в материалах
    from core.procedural_validator import ensure_initial_suspect_interview
    new_actions = ensure_initial_suspect_interview(new_actions, case_data=case_data, context=plan.get('actions'),
                                                   conducted=all_conducted)
    # Новый допрос свидетелем лица, которое в плане допрашивается как подозреваемый, — лишний
    from core.procedural_validator import drop_witness_interview_of_suspect
    new_actions = drop_witness_interview_of_suspect(new_actions, case_data=case_data, context=plan.get('actions'),
                                                    conducted=all_conducted)
    # Новый арест — с временным ограничением, если его нет ни в плане, ни в материалах
    from core.procedural_validator import ensure_restriction_before_arrest
    new_actions = ensure_restriction_before_arrest(new_actions, case_data=case_data, context=plan.get('actions'),
                                                   conducted=all_conducted)
    # Осмотр ответа того же органа уже есть в плане («…осмотр полученных сведений из Министерства …»)
    inspected = {_inspected_organ(t) for t in existing_texts} - {''}
    kept = []
    for a in new_actions:
        organ = _inspected_organ(a.get('действие', ''))
        if organ and organ in inspected:
            print(f"  ✂ [Дозагрузка] Осмотр ответа этого органа уже есть в плане: '{a.get('действие', '')[:70]}...'")
            continue
        kept.append(a)
    return kept


def _verify_llm_executed(llm_executed: set, existing_actions: list, conducted: list, det_executed: set) -> set:
    """
    Отметки «исполнено» от модели принимаются только с опорой на документы дела:
      • допрос, очная ставка, обыск/выемка, экспертиза, запрос — только при документе ИМЕННО по этому
        лицу / органу / виду экспертизы (протокол допроса свидетеля не закрывает допрос подозреваемого);
      • общие шаги (осмотр изъятого, вещдоки, санкция) — если поступил документ такого вида,
        но не для банковских выписок и ответов на запросы: для них нужен сам ответ;
      • анализ и прочее без документа — не принимаются.
    """
    from core.conducted_actions import _action_type, match_conducted
    types_present = {c.get('тип') for c in conducted or []}
    verified = set()
    for action in existing_actions:
        num = action.get('номер')
        if num not in llm_executed or num in det_executed:
            continue
        text = action.get('действие', '') or ''
        t = text.lower()
        a_type = _action_type(t)
        if match_conducted(text, conducted):
            verified.add(num)
        elif a_type in ('осмотр', 'вещдок', 'санкция') and a_type in types_present \
                and not re.search(r'выпис|банк|бву|запрос|\bответ(?!ствен)|полученн', t) \
                and (a_type == 'санкция' or re.search(r'изъят|выемк|обыск', t)):
            # Общий шаг по ИЗЪЯТОМУ («осмотр изъятых документов»). Осмотр/вещдоки по материалам,
            # «полученным» по запросу (выписки, регистрационные дела), требуют самого ответа
            verified.add(num)
        else:
            print(f"  ⛔ [B] Отметка модели отклонена (нет документа-подтверждения): №{num} «{text[:70]}»")
    return verified


def _append_conducted(existing_actions: list, new_actions: list, case_data: dict, conducted: list) -> list:
    """
    Дозагрузка: действия, проведённые по новым документам, отмечаются «Исполнен» в плане,
    а отсутствующие в плане — дописываются в конец со статусом «Исполнен».
    Существующие пункты не перенумеровываются (на номера ссылаются ручные статусы).
    """
    from core.procedural_validator import PROCEDURAL_BLOCKS, classify_action_block

    def _with_blocks(actions):
        # Новые пункты получают блок УПК РК так же, как при первичной генерации
        for a in actions:
            a['направление'] = PROCEDURAL_BLOCKS[classify_action_block(a)]
        return actions

    if not conducted:
        return _with_blocks(list(new_actions))
    from core.conducted_actions import apply_conducted_statuses
    known = {(c.get('файл'), c.get('тип')) for c in case_data.get('проведённые_действия') or []}
    case_data.setdefault('проведённые_действия', []).extend(
        c for c in conducted if (c.get('файл'), c.get('тип')) not in known)

    combined = list(existing_actions) + list(new_actions)
    n_before = len(combined)
    apply_conducted_statuses(combined, conducted, case_data.get('фио_следователя') or 'Следователь', add_missing=True)
    added = combined[n_before:]
    last_num = max((a.get('номер', 0) or 0 for a in combined[:n_before]), default=0)
    for idx, action in enumerate(added, start=last_num + 1):
        action['номер'] = idx
    return _with_blocks(list(new_actions) + added)


def _dedup_new_actions(existing_actions: list, new_actions: list) -> list:
    """
    Убирает из new_actions:
    1. Точные совпадения по всему тексту. По первым 80 символам совпадали разные пункты: «Истребовать сведения по
       запросу в НАО «Правительство для граждан» о регистрации ТОО «…»» о четырёх разных ТОО (216, дозагрузка)
    2. Семантические дубли — действия того же типа с теми же ключевыми словами.
    """
    def _norm(text):
        return re.sub(r'\s+', ' ', (text or '').lower()).strip(' .,;')

    existing_exact = set()
    existing_fps   = set()
    for a in existing_actions:
        text = a.get('действие', '')
        existing_exact.add(_norm(text))
        existing_fps.add(_action_fingerprint(text))

    result = []
    for a in new_actions:
        text = a.get('действие', '')
        key80 = _norm(text)
        fp    = _action_fingerprint(text)

        if key80 in existing_exact:
            print(f"  ⛔ [dedup-exact] Дубль: {text[:90]}")
            continue
        if fp in existing_fps and fp != 'общее|':
            print(f"  ⛔ [dedup-fp]   Семантический дубль ({fp}): {text[:90]}")
            continue

        result.append(a)
        existing_exact.add(key80)
        existing_fps.add(fp)

    removed = len(new_actions) - len(result)
    if removed:
        print(f"  ✂ Дедупликатор убрал {removed} дублирующих действий из {len(new_actions)}")
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Детерминированная проверка исполнения пунктов плана по названиям файлов
# ─────────────────────────────────────────────────────────────────────────────

def _deterministic_executed_check(existing_actions: list, new_docs: list, officials: list = None) -> set:
    """
    Pre-pass: определяет исполненные пункты плана по новым документам без участия LLM.
    Допросы, очные ставки, обыски, выемки, осмотры, экспертизы, аресты и запросы сопоставляются
    по КОНКРЕТНОМУ лицу / организации / виду экспертизы / адресату (протокол допроса одного свидетеля
    не делает исполненными все допросы плана). Остальные категории — по ключевым словам.
    Возвращает set с номерами ГАРАНТИРОВАННО исполненных пунктов.
    """
    from core.conducted_actions import detect_conducted_actions, match_conducted

    executed = set()
    conducted = detect_conducted_actions(new_docs, officials)
    for action in existing_actions:
        num = action.get("номер")
        if num is not None and match_conducted(action.get("действие", ""), conducted):
            executed.add(num)

    # Каждый ключ = tuple ключевых слов для поиска в имени файла или начале текста
    # Каждое значение = tuple ключевых слов, которые ищем в тексте действия плана
    KEYWORDS_MAP = [
        # --- Проверка по базам данных (СИОПСО, ЕРДР и т.п.) ---
        (
            ("выписка сиопсо", "ответ сиопсо", "проверка базы", "справка недвижимост",
             "сведения реестр", "ответ нао правительство"),
            ("сиопсо", "база данных", "проверка по базе", "реестр", "нао"),
        ),
        # --- НСД / ОРМ ---
        (
            ("материалы нсд", "результаты нсд", "материалы орм", "аудио", "видео запис",
             "перехват", "прослушивани"),
            ("нсд", "орм", "негласн", "оперативн", "наблюдение"),
        ),
        # --- Международное поручение ---
        (
            ("международное поручение", "ответ иностранн", "ответ зарубеж"),
            ("международное поручение", "иностранн", "зарубеж"),
        ),
        # --- Анализ движения денег / финансовый анализ ---
        (
            ("анализ движения", "справка анализ доходов", "справка финанс",
             "финансово-аналитическ"),
            ("анализ движения", "анализ денежных", "источники доходов", "сумму преступного",
             "провести анализ"),
        ),
    ]

    # Собираем типы документов, подтверждённых новыми файлами
    confirmed_action_keywords = set()
    for doc in new_docs:
        fname_lower = doc.get("filename", "").lower()
        text_lower  = doc.get("text", "")[:600].lower()
        combined    = fname_lower + " " + text_lower
        for (doc_kws, action_kws) in KEYWORDS_MAP:
            if any(kw in combined for kw in doc_kws):
                confirmed_action_keywords.add(action_kws)  # добавляем tuple как hashable

    if not confirmed_action_keywords:
        return executed

    # Матчим к пунктам плана
    for action in existing_actions:
        action_text = action.get("действие", "").lower()
        num = action.get("номер")
        if num is None:
            continue
        for action_kws in confirmed_action_keywords:
            if any(kw in action_text for kw in action_kws):
                executed.add(num)
                break
    return executed


# ─────────────────────────────────────────────────────────────────────────────
# Обновление статусов — ТОЛЬКО статусы, без добавления новых действий
# ─────────────────────────────────────────────────────────────────────────────

def update_plan_status(plan_path: str, llm_client, new_db_docs: list = None,
                       new_docs_paths=None) -> dict:
    """
    Обновляет статусы существующих пунктов плана на основе новых документов-подтверждений.

    В ОТЛИЧИЕ от append_documents_to_plan:
      ✅ Анализирует новые документы и отмечает исполненные пункты.
      ❌ НЕ генерирует новые следственные действия.
      ❌ НЕ изменяет тексты существующих пунктов.

    Args:
        plan_path:       путь к JSON-файлу плана
        llm_client:      экземпляр LLMClient
        new_db_docs:     список новых документов из БД [{filename, text, ...}]
        new_docs_paths:  список путей к файлам (альтернатива new_db_docs)

    Returns:
        обновлённый словарь плана
    """
    print(f"\n🔄 РЕЖИМ ОБНОВЛЕНИЯ СТАТУСОВ (без генерации новых действий)")
    print(f"📂 Загружаю текущий план: {plan_path}")
    plan = load_plan_from_json(plan_path)
    existing_actions = plan.get('actions', [])
    case_data = plan.get('case_data', {})
    processed_docs = plan.get('processed_docs', [])
    print(f"📝 Пунктов в плане: {len(existing_actions)}")

    # ── Собираем документы-подтверждения ──────────────────────────────────────
    docs_to_analyze = []
    newly_processed = []

    if new_db_docs:
        for doc in new_db_docs:
            fname = doc.get("filename")
            if fname and fname not in processed_docs and fname not in newly_processed:
                docs_to_analyze.append(doc)
                newly_processed.append(fname)
            elif fname:
                print(f"⏩ Документ {fname} уже был обработан ранее, пропускаем.")

    elif new_docs_paths:
        for path in new_docs_paths:
            try:
                if os.path.isdir(path):
                    from core.case_documents import load_case_documents
                    docs = load_case_documents(path)
                else:
                    from core.case_documents import read_document
                    doc_text = read_document(path)
                    docs = [{"text": doc_text, "filename": os.path.basename(path),
                              "type": "другой_документ"}] if doc_text else []
                for doc in docs:
                    fname = doc.get("filename")
                    if fname and fname not in processed_docs and fname not in newly_processed:
                        docs_to_analyze.append(doc)
                        newly_processed.append(fname)
                    elif fname:
                        print(f"⏩ Документ {fname} уже был обработан ранее, пропускаем.")
            except Exception as e:
                print(f"⚠ Ошибка при загрузке {path}: {e}")

    if not docs_to_analyze:
        print("⚠ Нет новых документов для анализа статусов")
        return plan

    print(f"📄 Документов-подтверждений для анализа: {len(docs_to_analyze)}")

    # ── Краткий листинг документов (имена + фрагменты) для LLM ────────────────
    docs_summary_lines = []
    for doc in docs_to_analyze:
        fname = doc.get("filename", "")
        snippet = doc.get("text", "")[:120].replace("\n", " ")
        docs_summary_lines.append(f"[ФАЙЛ: {fname}]\n{snippet}")
    docs_summary = "\n\n".join(docs_summary_lines)

    # ── RAG-контекст ────────────────────────────────────────────────────────────
    from core.rag_engine import RAGEngine
    from core.llm_utils import DEFAULT_API_URL, DEFAULT_MODEL

    case_number = case_data.get('case_number') or case_data.get('номер_ердр') or case_data.get('номер_дела')
    db_root = case_data.get('db_root', '/root/database/workspaces')

    if case_number and os.path.exists(os.path.join(db_root, case_number)):
        workspace_dir = os.path.join(db_root, case_number)
        print(f"  📂 Использую локальный RAG-контекст дела: {workspace_dir}")
        rag = RAGEngine(
            workspace_dir=workspace_dir,
            api_url=llm_client.api_url if llm_client else DEFAULT_API_URL,
            llm_model=llm_client.model if llm_client else DEFAULT_MODEL
        )
    else:
        # Не общий LightRAG: иначе пункты отмечаются исполненными по документам чужих дел
        rag = _case_retriever(docs_to_analyze)

    print("🔍 RAG-поиск по трём запросам для анализа статусов...")
    ctx_interrogations = rag.search_relevant_context(
        "Протокол допроса показания свидетель потерпевший подозреваемый", k=15)
    ctx_responses = rag.search_relevant_context(
        "Ответ на запрос выписка сведения из банка КГД НАО ПФР оператор", k=15)
    ctx_procedural = rag.search_relevant_context(
        "Протокол обыска выемка осмотр постановление арест заключение эксперт", k=15)

    raw_context = "\n\n".join(filter(None, [
        "=== КРАТКОЕ СОДЕРЖАНИЕ ЗАГРУЖЕННЫХ ДОКУМЕНТОВ-ПОДТВЕРЖДЕНИЙ ===",
        docs_summary,
        "=== ИЗВЛЕЧЁННЫЕ ФРАГМЕНТЫ (RAG) ===",
        ctx_interrogations, ctx_responses, ctx_procedural
    ]))
    if len(raw_context) > 22000:
        print(f"  ⚠ Контекст обрезан до 22,000 симв. (был {len(raw_context):,})")
        retrieved_context = raw_context[:22000]
    else:
        retrieved_context = raw_context

    # ── Слой A: детерминированная проверка ────────────────────────────────────
    print("🔍 [A] Детерминированная проверка исполнения по именам файлов...")
    det_executed = _deterministic_executed_check(existing_actions, docs_to_analyze,
                                                 case_data.get('должностные_лица'))
    if det_executed:
        print(f"  ✅ [A] Детерминированно исполнено: {sorted(det_executed)}")
    else:
        print("  ℹ [A] Детерминированно ни один пункт не подтверждён")

    # ── Слой B: LLM-анализ через update_statuses_only (без генерации новых) ──
    print("🔍 [B] LLM-анализ статусов (ТОЛЬКО обновление, без новых действий)...")
    llm_executed = llm_client.update_statuses_only(
        existing_actions, retrieved_context, docs_summary=docs_summary
    )
    if llm_executed:
        print(f"  ✅ [B] LLM обнаружил исполненных пунктов: {sorted(llm_executed)}")
    else:
        print("  ℹ [B] LLM не обнаружил исполненных пунктов")

    from core.conducted_actions import detect_conducted_actions
    conducted = (case_data.get('проведённые_действия') or []) + \
        detect_conducted_actions(docs_to_analyze, case_data.get('должностные_лица'))
    llm_executed = _verify_llm_executed(llm_executed, existing_actions, conducted, det_executed)

    executed_set = llm_executed | det_executed
    if executed_set:
        print(f"✅ Итого исполнено (A ∪ B): {len(executed_set)} → {sorted(executed_set)}")
    else:
        print("ℹ Исполненных пунктов не обнаружено")

    # ── Обновляем только статусы существующих действий ────────────────────────
    # ⚠ ЗАЩИТА: пропускаем пункты с флагом статус_заблокирован=True.
    # Такой флаг устанавливается вручную руководителем/следователем через
    # /api/manual_status и снимается ТОЛЬКО вручную.
    updated_count = 0
    skipped_locked = 0
    for action in existing_actions:
        action_num = action.get('номер')
        if action.get('статус_заблокирован', False):
            skipped_locked += 1
            print(f"  🔒 Пункт №{action_num} заблокирован вручную — автообновление пропущено")
            continue
        if action_num in executed_set:
            if action.get('статус') != 'Исполнен':
                action['статус'] = 'Исполнен'
                action['срок'] = ''  # будущий срок у исполненного пункта; дата документа проставится ниже
                updated_count += 1
        else:
            if not action.get('статус'):
                action['статус'] = 'Не исполнен'

    # Даты документов-оснований для исполненных пунктов (режим статусов: новых пунктов не добавляем)
    from core.conducted_actions import apply_conducted_statuses
    apply_conducted_statuses(existing_actions, conducted, case_data.get('фио_следователя') or 'Следователь',
                             add_missing=False)

    print(f"🔄 Обновлено статусов: {updated_count} пунктов → 'Исполнен'")
    if skipped_locked:
        print(f"🔒 Пропущено заблокированных пунктов: {skipped_locked} (требуется ручное обновление)")
    print(f"📌 Количество действий в плане НЕ изменилось: {len(existing_actions)}")

    # ── Фиксируем обработанные файлы и сохраняем план ─────────────────────────
    plan['actions'] = existing_actions
    plan['processed_docs'] = processed_docs + newly_processed
    save_plan_to_json(plan)
    print("✅ Статусы обновлены, план сохранён.")
    return plan


# ─────────────────────────────────────────────────────────────────────────────
# Дозагрузка документов → добавление новых пунктов в план
# ─────────────────────────────────────────────────────────────────────────────

def _methodology_brief(case_data: dict, methodology_dir: str, max_chars: int = 3500) -> str:
    """
    Этапы методики (с подключённым по фабуле видом преступления) кратко: «Этап N. Название — суть».
    При дозагрузке модель соотносит новые показания с этапами («Призы и победители», «Платежи участников»).
    """
    from core.methodology_loader import load_specific_or_general
    import contextlib, io
    fabula = "\n".join(str(case_data.get(k) or '') for k in
                       ('фабула_для_заголовка', 'суть_правонарушения', 'текст_рапорта'))[:8000]
    with contextlib.redirect_stdout(io.StringIO()):
        method = load_specific_or_general(methodology_dir, case_data.get('статья_ук_рк', ''), fabula)
    stages = _methodology_stages(f"=== МЕТОДИКА\n{method.get('text', '')}\n=== ДАННЫЕ ИЗ МАТЕРИАЛОВ ДЕЛА")
    lines = []
    for title, body in stages:
        gist = re.split(r'(?<=[.;:])\s', re.sub(r'\s+', ' ', body), maxsplit=1)[0][:160]
        lines.append(f"{title} — {gist}")
    return "\n".join(lines)[:max_chars]


def _build_append_context(new_docs: list, case_data: dict, llm_client, methodology_dir: str) -> str:
    """
    Контекст дозагрузки: (1) выжимки КАЖДОГО нового документа — лица, организации, платежи, имущество,
    отказы, защитники, пробелы; (2) этапы методики; (3) сжатые тексты документов — сколько поместится.
    Раньше: три RAG-запроса со склейкой и обрезкой с начала — модель видела ~26 из 70 тыс. символов.
    """
    from core.doc_compress import build_documents_context
    from core.doc_facts import build_facts_context, facts_text
    parts = []
    facts = build_facts_context(llm_client, new_docs) if llm_client else []
    if facts:
        # Выжимки — не больше 60 % лимита, поровну на документ (при 50 документах каждый остаётся представлен)
        share = int(APPEND_CONTEXT_CHARS * 0.6) // len(facts)
        texts = [t if len(t) <= share else t[:share - 12].rsplit(' ', 1)[0] + ' …[сокр.]'
                 for t in (facts_text(f) for f in facts)]
        parts.append("=== ВЫЖИМКИ НОВЫХ ДОКУМЕНТОВ (по каждому документу) ===\n" + "\n\n".join(texts))
    try:
        brief = _methodology_brief(case_data, methodology_dir)
    except Exception as e:
        brief = ''
        print(f"  ⚠ Этапы методики для дозагрузки не получены: {e}")
    if brief:
        parts.append("=== ЭТАПЫ МЕТОДИКИ (соотноси новые факты с этапами) ===\n" + brief)
    used = sum(len(p) + 2 for p in parts)
    title = "=== ТЕКСТЫ НОВЫХ ДОКУМЕНТОВ (протоколы сжаты: шапка и показания) ==="
    budget = APPEND_CONTEXT_CHARS - used - len(title) - 2
    if budget > 1500:
        parts.append(title + "\n\n" + build_documents_context(new_docs, budget))
    context = "\n\n".join(parts)
    raw_len = sum(len(d.get('text') or '') for d in new_docs)
    print(f"  📄 Контекст дозагрузки: {len(new_docs)} док., {raw_len:,} симв. текста -> {len(context):,} симв. "
          f"(выжимки: {len(facts)}, этапы методики: {'да' if brief else 'нет'}, лимит {APPEND_CONTEXT_CHARS:,})")
    return context


def append_documents_to_plan(plan_path, new_docs_paths=None,llm_client=None, methodology_dir="data/methodology", pre_index_only: bool = False, new_db_docs: list = None):

    """
    Добавляет новые следственные действия в существующий план
    на основе дополнительных документов.
    """
    from datetime import datetime, timedelta
    from core.case_participants import set_case_persons
    set_case_persons(())  # лица прежнего дела не переносятся; список лиц этого дела — при проверке пунктов

    print(f"📂 Загружаю текущий план: {plan_path}")
    plan = load_plan_from_json(plan_path)
    existing_actions = plan.get('actions', [])
    case_data = plan.get('case_data', {})
    processed_docs = plan.get('processed_docs', [])
    print(f"📝 Уже в плане: {len(existing_actions)} действий")

    new_docs_to_index = []
    newly_processed = []
    unread_docs = []  # сканы без извлечённого текста — только для определения проведённых действий по названию

    if new_db_docs:
        for doc in new_db_docs:
            fname = doc.get("filename")
            if fname and fname not in processed_docs and fname not in newly_processed:
                new_docs_to_index.append(doc)
                newly_processed.append(fname)
            elif fname:
                print(f"⏩ Документ {fname} уже был обработан ранее, пропускаем.")
    elif new_docs_paths:
        for path in new_docs_paths:
            try:
                if os.path.isdir(path):
                    from core.case_documents import load_case_documents
                    docs = load_case_documents(path)
                    candidates = [f for f in sorted(os.listdir(path))
                                  if os.path.isfile(os.path.join(path, f)) and not f.startswith(('.', '~$'))]
                else:
                    from core.case_documents import read_document
                    doc_text = read_document(path)
                    docs = [{"text": doc_text, "filename": os.path.basename(path), "type": "другой_документ"}] if doc_text else []
                    candidates = [os.path.basename(path)]

                for doc in docs:
                    fname = doc.get("filename")
                    if fname and fname not in processed_docs and fname not in newly_processed:
                        new_docs_to_index.append(doc)
                        newly_processed.append(fname)
                    elif fname:
                        print(f"⏩ Документ {fname} уже был обработан ранее, пропускаем.")

                # Сканы, текст которых не извлечён (OCR недоступен): для содержательного анализа их нет,
                # но по названию («Протокол выемки у Инкербаевой А.Б.») проведённое действие видно
                loaded = {d.get("filename") for d in docs}
                for fname in candidates:
                    if fname not in loaded and fname not in processed_docs and fname not in newly_processed:
                        unread_docs.append({"filename": fname, "text": ""})
                        newly_processed.append(fname)

            except Exception as e:
                print(f"⚠ Ошибка при загрузке {path}: {e}")

    if not new_docs_to_index and not unread_docs:
        print("⚠ Нет новых документов для обработки")
        return plan
    if unread_docs:
        print(f"  ℹ Без текста (скан, OCR недоступен) — учитываются только по названию: "
              f"{[d['filename'] for d in unread_docs]}")

    # ── Формируем краткий листинг загруженных файлов с фрагментами текста ──
    # Он будет передан в LLM как «ЗАГРУЖЕННЫЕ ФАЙЛЫ» — чтобы модель видела
    # имена документов ("Протокол допроса.pdf") и могла сматчить их с планом.
    docs_summary_lines = []
    for doc in new_docs_to_index:
        fname = doc.get("filename", "")
        snippet = doc.get("text", "")[:120].replace("\n", " ")
        docs_summary_lines.append(f"[ФАЙЛ: {fname}]\n{snippet}")
    docs_summary = "\n\n".join(docs_summary_lines)

    # ── Контекст: все новые документы, сжатые (шапка + показания), с поровну разделённым лимитом ──
    # Раньше: три RAG-запроса по 15 фрагментов, склейка с повторами и обрезка по лимиту с начала — модель
    # видела ~26 из 70 тыс. символов, показания части свидетелей не попадали к ней целиком
    retrieved_context = _build_append_context(new_docs_to_index, case_data, llm_client, methodology_dir)

    # Проведённые действия по новым документам (протоколы, заключения, постановления, ответы)
    from core.conducted_actions import detect_conducted_actions
    new_conducted = detect_conducted_actions(new_docs_to_index + unread_docs, case_data.get('должностные_лица'))

    if pre_index_only:
        print("🔍 [A] Детерминированная проверка исполнения по именам файлов (Вариант А)...")
        det_executed = _deterministic_executed_check(existing_actions, new_docs_to_index + unread_docs,
                                                     case_data.get('должностные_лица'))
        if det_executed:
            print(f"  ✅ [A] Детерминированно исполнено: {sorted(det_executed)}")
            
        append_ctx = {
            "plan_path": plan_path,
            "base_plan": plan,
            "retrieved_context": retrieved_context,
            "docs_summary": docs_summary,
            "det_executed": list(det_executed),
            "newly_processed": newly_processed,
            "conducted": new_conducted
        }
        номер = sanitize_filename(case_data.get('номер_ердр', case_data.get('номер_дела', 'unknown')))
        os.makedirs(os.path.join("data", "plans"), exist_ok=True)
        append_ctx_file = os.path.join("data", "plans", f"{номер}_append_pre_context.json")
        with open(append_ctx_file, "w", encoding="utf-8") as f:
            json.dump(append_ctx, f, ensure_ascii=False, indent=2)
        print(f"💾 Пре-индексированный контекст дозагрузки сохранен: {append_ctx_file}")
        print("✅ Вариант А: Пре-индексация дозагрузки завершена. Запустите live-plan --append для генерации в прямом эфире.")
        return plan

    print("⭐ Генерирую новые действия на основе новых документов...")
    new_actions = llm_client.generate_append_actions(
        base_plan=plan,
        retrieved_context=retrieved_context
    )

    # ── Детерминированная дедупликация ─────────────────────────────────────
    print("🔎 Дедупликация новых действий (точная + семантическая)...")
    new_actions = _dedup_new_actions(existing_actions, new_actions) if new_actions else []
    new_actions = _filter_append_actions(new_actions, plan, new_docs_to_index, retrieved_context, new_conducted,
                                         sources=[d.get("filename", "") for d in unread_docs])
    if not new_actions:
        print("⚠ LLM не сгенерировал новых действий")

    # ────────────────────────────────────────────────────────────────────────
    # Шаг: Определяем, какие пункты старого плана ИСПОЛНЕНЫ
    # Слой A: детерминированный — по именам/тексту файлов (быстро, надёжно)
    # Слой B: LLM — для нестандартных документов (fallback)
    # Итог: объединение A ∪ B
    # ────────────────────────────────────────────────────────────────────────
    print("🔍 [A] Детерминированная проверка исполнения по именам файлов...")
    det_executed = _deterministic_executed_check(existing_actions, new_docs_to_index + unread_docs,
                                                 case_data.get('должностные_лица'))
    if det_executed:
        print(f"  ✅ [A] Детерминированно исполнено: {sorted(det_executed)}")
    else:
        print("  ℹ [A] Детерминированно ни один пункт не подтверждён")

    print("🔍 [B] LLM-анализ исполнения пунктов старого плана...")
    executed_set = llm_client.mark_executed_actions(
        existing_actions, retrieved_context, docs_summary=docs_summary
    )
    if executed_set:
        print(f"  ✅ [B] LLM обнаружил исполненных пунктов: {sorted(executed_set)}")
    else:
        print("  ℹ [B] LLM не обнаружил исполненных пунктов")
    executed_set = _verify_llm_executed(executed_set, existing_actions,
                                        (case_data.get('проведённые_действия') or []) + new_conducted, det_executed)

    # Объединяем оба слоя
    executed_set = executed_set | det_executed
    if executed_set:
        print(f"✅ Итого исполнено (A ∪ B): {len(executed_set)} → {sorted(executed_set)}")
    else:
        print("ℹ Исполненных пунктов не обнаружено")

    # Проставляем статус всем существующим действиям
    # ⚠ ЗАЩИТА: пропускаем пункты с флагом статус_заблокирован=True
    for action in existing_actions:
        action_num = action.get('номер')
        if action.get('статус_заблокирован', False):
            print(f"  🔒 Пункт №{action_num} заблокирован вручную — автообновление пропущено")
            continue
        if action_num in executed_set:
            if action.get('статус') != 'Исполнен':
                action['срок'] = ''  # дата документа-основания проставится при сопоставлении ниже
            action['статус'] = 'Исполнен'
        else:
            if not action.get('статус'):  # не перезатираем уже установленный статус
                action['статус'] = 'Не исполнен'

    # Сроки новых пунктов считаются от даты дозагрузки (дополнения плана)
    base_date = _deadline_base_date()
    print(f"  📅 Базовая дата для сроков (append): {base_date.strftime('%d.%m.%Y')} (дата дополнения плана)")

    last_num = max((a.get('номер', 0) for a in existing_actions), default=0)
    fio = case_data.get('фио_следователя', 'Следователь')

    for idx, action in enumerate(new_actions, start=last_num + 1):
        action['номер'] = idx
        action['статус'] = 'Не исполнен'  # новые действия всегда не исполнены
        if not action.get('исполнитель') or action['исполнитель'] in ('Следователь', 'Н/Д', ''):
            action['исполнитель'] = fio
            
        try:
            days = int(str(action.get('дней', 30)))
        except (ValueError, TypeError):
            matches = re.findall(r'(\d+)', str(action.get('дней', '')))
            if matches:
                days = int(matches[-1])
            else:
                days = 30
        try:
            deadline_date = base_date + timedelta(days=int(days))
            action['срок'] = deadline_date.strftime('%d.%m.%Y')
        except Exception:
            action['срок'] = f"Через {days} дней"

    new_actions = _append_conducted(existing_actions, new_actions, case_data, new_conducted)
    plan['actions'] = existing_actions + new_actions
    # Сроки новых пунктов — не раньше предыдущих шагов цепочки; сроки утверждённых пунктов не меняются
    from core.procedural_validator import enforce_chain_deadlines, distribute_by_days
    distribute_by_days(plan['actions'], base_date, figurants=case_data.get('фигуранты', ''),
                       stage_limits=_case_stage_limits(case_data), movable=new_actions,
                       inspection_stage=case_data.get('этап_осмотров'),
                       relative_limits=case_data.get('относительные_сроки_этапов'))
    enforce_chain_deadlines(plan['actions'], movable=new_actions, figurants=case_data.get('фигуранты', ''))
    plan['processed_docs'] = processed_docs + newly_processed
    print(f"➕ Добавлено {len(new_actions)} новых действий. Итого: {len(plan['actions'])}")
    save_plan_to_json(plan)
    return plan


def generate_append_live_from_context(case_number: str, llm_client) -> dict:
    """
    Вариант А: Выполняет живое добавление действий и отметку исполненных
    на основе заранее подготовленного контекста дозагрузки (_append_pre_context.json).
    """
    import time
    from datetime import datetime, timedelta
    
    номер = sanitize_filename(case_number)
    append_ctx_file = os.path.join("data", "plans", f"{номер}_append_pre_context.json")
    
    if not os.path.exists(append_ctx_file):
        raise FileNotFoundError(f"Файл пре-индексации дозагрузки для дела №{case_number} не найден: {append_ctx_file}")
        
    print(f"\n⚡ [Вариант А] Загрузка подготовленного контекста дозагрузки: {append_ctx_file}")
    with open(append_ctx_file, "r", encoding="utf-8") as f:
        append_ctx = json.load(f)
        
    plan = append_ctx["base_plan"]
    retrieved_context = append_ctx["retrieved_context"]
    docs_summary = append_ctx["docs_summary"]
    det_executed = set(append_ctx["det_executed"])
    newly_processed = append_ctx["newly_processed"]
    
    existing_actions = plan.get('actions', [])
    case_data = plan.get('case_data', {})
    processed_docs = plan.get('processed_docs', [])
    
    print("⭐ Запуск LLM-генерации новых действий в прямом эфире...")
    t0 = time.time()
    new_actions = llm_client.generate_append_actions(
        base_plan=plan,
        retrieved_context=retrieved_context
    )

    # ── Детерминированная дедупликация ─────────────────────────────────────
    print("🔎 Дедупликация новых действий (точная + семантическая)...")
    new_actions = _dedup_new_actions(existing_actions, new_actions) if new_actions else []
    new_actions = _filter_append_actions(new_actions, plan, [], retrieved_context, append_ctx.get("conducted") or [],
                                         sources=newly_processed)
    elapsed = time.time() - t0
    print(f"⏱ Новые действия сгенерированы за {elapsed:.2f} сек.")
    
    if not new_actions:
        print("⚠ LLM не сгенерировал новых действий")
        new_actions = []
        
    print("🔍 [B] Запуск LLM-анализа исполнения пунктов старого плана в прямом эфире...")
    t1 = time.time()
    executed_set = llm_client.mark_executed_actions(
        existing_actions, retrieved_context, docs_summary=docs_summary
    )
    elapsed_b = time.time() - t1
    print(f"⏱ Статусы проанализированы за {elapsed_b:.2f} сек.")
    
    if executed_set:
        print(f"  ✅ [B] LLM обнаружил исполненных пунктов: {sorted(executed_set)}")
    else:
        print("  ℹ [B] LLM не обнаружил исполненных пунктов")
    executed_set = _verify_llm_executed(executed_set, existing_actions,
                                        (case_data.get('проведённые_действия') or []) + (append_ctx.get("conducted") or []),
                                        det_executed)

    executed_set = executed_set | det_executed
    if executed_set:
        print(f"✅ Итого исполнено (A ∪ B): {len(executed_set)} → {sorted(executed_set)}")

    for action in existing_actions:
        action_num = action.get('номер')
        if action.get('статус_заблокирован', False):
            print(f"  🔒 Пункт №{action_num} заблокирован вручную — автообновление пропущено")
            continue
        if action_num in executed_set:
            if action.get('статус') != 'Исполнен':
                action['срок'] = ''  # дата документа-основания проставится при сопоставлении ниже
            action['статус'] = 'Исполнен'
        else:
            if not action.get('статус'):
                action['статус'] = 'Не исполнен'
                
    # Сроки новых пунктов считаются от даты дозагрузки (дополнения плана)
    base_date = _deadline_base_date()
    print(f"  📅 Базовая дата для сроков (live-append): {base_date.strftime('%d.%m.%Y')} (дата дополнения плана)")
    last_num = max((a.get('номер', 0) for a in existing_actions), default=0)
    fio = case_data.get('фио_следователя', 'Следователь')
    
    for idx, action in enumerate(new_actions, start=last_num + 1):
        action['номер'] = idx
        action['статус'] = 'Не исполнен'
        if not action.get('исполнитель') or action['исполнитель'] in ('Следователь', 'Н/Д', ''):
            action['исполнитель'] = fio
            
        try:
            days = int(str(action.get('дней', 30)))
        except (ValueError, TypeError):
            matches = re.findall(r'(\d+)', str(action.get('дней', '')))
            if matches:
                days = int(matches[-1])
            else:
                days = 30
        try:
            deadline_date = base_date + timedelta(days=int(days))
            action['срок'] = deadline_date.strftime('%d.%m.%Y')
        except Exception:
            action['срок'] = f"Через {days} дней"

    new_actions = _append_conducted(existing_actions, new_actions, case_data, append_ctx.get("conducted") or [])
    plan['actions'] = existing_actions + new_actions
    from core.procedural_validator import enforce_chain_deadlines, distribute_by_days
    distribute_by_days(plan['actions'], base_date, figurants=case_data.get('фигуранты', ''),
                       stage_limits=_case_stage_limits(case_data), movable=new_actions,
                       inspection_stage=case_data.get('этап_осмотров'),
                       relative_limits=case_data.get('относительные_сроки_этапов'))
    enforce_chain_deadlines(plan['actions'], movable=new_actions, figurants=case_data.get('фигуранты', ''))
    plan['processed_docs'] = processed_docs + newly_processed
    print(f"➕ Добавлено {len(new_actions)} новых действий. Итого: {len(plan['actions'])}")
    
    save_plan_to_json(plan)
    return plan
