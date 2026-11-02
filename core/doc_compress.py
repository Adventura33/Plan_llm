# core/doc_compress.py
"""
Сжатие документов дела для контекста модели.

Протокол допроса на 40–60 % состоит из бланка: разъяснение прав (ст. 64, 71, 78 УПК), порядок допроса,
подписка о неразглашении, OCR удостоверения личности, штампы ЭЦП на каждой странице. Для плана нужны
шапка (кто, когда, в каком качестве, анкетные данные) и сами показания. Маркеры — стандартные формулировки
бланков УПК РК (рус./каз.); если их нет, документ передаётся целиком.

Сжатый текст используется только для контекста модели. Определение участников, проведённых действий и дат
работает по полному тексту документа.
"""
import re
from typing import Dict, List

# Начало показаний
_TESTIMONY_START = re.compile(
    r'дал\(?а?\)?\s+следующие\s+показания'
    r'|показал\w*\s+следующее'
    r'|предложено\s+рассказать'
    r'|по\s+существу\s+(?:заданных|известных|дела|уголовного)'
    r'|вопрос\s+следователя'
    r'|^\s*вопрос\s*\d*\s*[:.]'
    r'|ознакомившись\s+с\s+правами'
    r'|от\s+дачи\s+показаний\s+отказ'
    r'|мынадай\s+(?:жауап|айғақ|түсінік)'
    r'|мәні\s+бойынша'
    r'|тергеушінің\s+сұрағы'
    r'|^\s*сұрақ\s*[:.]',
    re.IGNORECASE | re.MULTILINE)

# Конец показаний: заключительная часть протокола, подписка, данные удостоверения
_TESTIMONY_END = re.compile(
    r'по\s+окончании\s+(?:допроса|следственного\s+действия)'
    r'|предъявлен\w*\s+для\s+ознакомления'
    r'|ознакомлен\w*\s+с\s+протоколом'
    r'|с\s+(?:настоящим\s+)?протоколом\s+(?:допроса\s+)?ознакомлен'
    r'|протокол\s+(?:мною\s+)?прочитан'
    r'|с\s+моих\s+слов'
    r'|допросил,?\s+протокол\s+составил'
    r'|^\s*допросил\s*:'
    r'|на\s+этом\s+допрос\w*(?:\s+\w+)?\s+оконч'
    r'|звуко-?\s*и\s*\(или\)\s*видеозапись'
    r'|^\s*ПОДПИСКА\s*$'
    r'|IDKAZ'
    r'|хаттамамен\s+таныс'
    r'|хаттаманы\s+оқы'
    r'|менің\s+сөзім',
    re.IGNORECASE | re.MULTILINE)

# Начало разъяснения прав — граница шапки
_RIGHTS_START = re.compile(r'разъясн\w*|түсіндірілді|құқықтары', re.IGNORECASE)

# Служебные строки, не несущие фактов
_NOISE_LINE = re.compile(
    r'^\s*(?:Документ\s+подготовил\s+и\s+подписал|QR-код\s+содержит|ИС\s+«Единый\s+реестр'
    r'|в\s+PDF\s+формате,\s+подписанного|должность,\s+наименование\s+государственного|дату\s+и\s+время\s+подписания)',
    re.IGNORECASE)

_PROTOCOL_NAME = re.compile(r'протокол\w*[\s_]+(?:\(?доп\w*\)?[\s_]+)?(?:допрос|очн|дополнительного[\s_]+допрос)'
                            r'|жауап\s+алу\s+хаттама', re.IGNORECASE)

_HEADER_MAX = 1500
_MIN_TESTIMONY = 200


def drop_noise(text: str) -> str:
    """Убирает штампы ЭЦП и строки, повторяющиеся на каждой странице (колонтитулы, подписи)."""
    lines = (text or '').splitlines()
    counts = {}
    for line in lines:
        key = line.strip()
        if len(key) >= 12:
            counts[key] = counts.get(key, 0) + 1
    kept = [line for line in lines
            if not _NOISE_LINE.match(line) and counts.get(line.strip(), 0) < 3]
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(kept)).strip()


def is_interrogation_protocol(doc: Dict) -> bool:
    name = (doc.get('filename') or '').replace('_', ' ')
    head = (doc.get('text') or '')[:400]
    return bool(_PROTOCOL_NAME.search(name) or _PROTOCOL_NAME.search(head))


def compress_protocol(text: str) -> str:
    """Шапка протокола (до разъяснения прав) + показания (до заключительной части). Без маркеров — как есть."""
    text = drop_noise(text)
    start_m = _TESTIMONY_START.search(text)
    if not start_m:
        return text
    start = start_m.start()
    rights = _RIGHTS_START.search(text, 0, start)
    header = text[:rights.start() if rights else min(start, _HEADER_MAX)][:_HEADER_MAX].strip()
    end = len(text)
    for m in _TESTIMONY_END.finditer(text, start):
        if m.start() - start >= _MIN_TESTIMONY:
            end = m.start()
            break
    testimony = text[start:end].strip()
    # Приложения после подписей: уведомление адвоката о защите важно для статуса допрошенного
    from core.case_participants import defense_notices
    notes = [f"[Приложено уведомление о защите: адвокат {n['lawyer'] or '—'}, подзащитный {n['client']}]"
             for n in defense_notices(text[end:])]
    return "\n".join([f"{header}\n[…разъяснение прав и порядок допроса опущены…]\n{testimony}"] + notes)


_CONCLUSION_NAME = re.compile(r'заключени|қорытынды', re.IGNORECASE)
_CONCLUSION_FINDINGS = re.compile(r'^\s*(?:выводы?|заключительная\s+часть|қорытынды)\s*[:.]?\s*$|^\s*выводы?\s*:',
                                  re.IGNORECASE | re.MULTILINE)
_CONCLUSION_HEAD = 3500
_CONCLUSION_TAIL = 8000


def compress_conclusion(text: str) -> str:
    """
    Большое заключение эксперта/специалиста: начало (кто, по какому делу, какие вопросы) + раздел «Выводы»,
    который стоит в конце. Исследовательская часть (таблицы, расчёты) опускается.
    """
    text = drop_noise(text)
    if len(text) <= _CONCLUSION_HEAD + _CONCLUSION_TAIL:
        return text
    found = list(_CONCLUSION_FINDINGS.finditer(text, _CONCLUSION_HEAD))
    tail_start = found[-1].start() if found else len(text) - _CONCLUSION_TAIL
    return (f"{text[:_CONCLUSION_HEAD].rstrip()}\n[…исследовательская часть опущена…]\n"
            f"{text[tail_start:tail_start + _CONCLUSION_TAIL].strip()}")


def compress_document(doc: Dict) -> str:
    text = doc.get('text') or ''
    if is_interrogation_protocol(doc):
        return compress_protocol(text)
    if _CONCLUSION_NAME.search((doc.get('filename') or '') + ' ' + text[:300]):
        return compress_conclusion(text)
    return drop_noise(text)


def build_documents_context(docs: List[Dict], budget: int) -> str:
    """
    Контекст по всем документам с поровну разделённым лимитом: короткий документ берётся целиком,
    освободившийся объём уходит длинным. Ни один документ не теряется целиком (раньше склейка фрагментов
    обрезалась по лимиту с начала, и последние документы не попадали к модели).
    """
    items = []
    for doc in docs or []:
        body = compress_document(doc)
        if body:
            items.append((f"[ФАЙЛ: {doc.get('filename', 'документ')}]\n", body))
    if not items:
        return ''
    overhead = sum(len(h) + 2 for h, _ in items)
    remaining = max(budget - overhead, 0)
    shares = {}
    pending = sorted(range(len(items)), key=lambda i: len(items[i][1]))
    while pending:
        share = remaining // len(pending)
        i = pending[0]
        if len(items[i][1]) <= share:
            shares[i] = len(items[i][1])
            remaining -= shares[i]
            pending.pop(0)
        else:
            for j in pending:
                shares[j] = share
            break
    parts = []
    for i, (head, body) in enumerate(items):
        limit = shares.get(i, 0)
        cut = body if len(body) <= limit else body[:max(limit - 14, 0)].rsplit(' ', 1)[0] + ' …[сокращено]'
        parts.append(head + cut)
    return "\n\n".join(parts)
