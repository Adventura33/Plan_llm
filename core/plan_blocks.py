# core/plan_blocks.py
"""
Блоки плана расследования.

План — не сплошной список: пункты разложены по блокам (BLOCKS), внутри блока идут по сроку.
Блок определяется по самому действию (action_block), цепочка остаётся в одном блоке:
«запрос → истребование → осмотр ответа», «постановление → выемка → осмотр изъятого → вещдок».
Все исполненные пункты — в последнем блоке, по дате документа-основания.

У каждого пункта плана (в файле, БД и ответе API):
  «блок_номер» — номер блока, «блок_код» — постоянный код, «блок» — название; «направление» — «БЛОК N: название».
В ответе API дополнительно plan['blocks'] — блоки плана по порядку со счётчиками (plan_blocks).

Раскладка делается в одном месте — arrange_by_blocks (вызывается из normalize_plan_statuses при сохранении
и отдаче плана и из utils/doc_formatter).
"""
import re
from datetime import date
from typing import Dict, List, Optional

EXECUTED = 'Исполнен'
EXECUTED_BLOCK = 'executed'
BLOCKS_KEY = 'blocks'

# (код, название) — в порядке следования в плане. Код постоянный: по нему блок узнают фронт и бэк сайта;
# название и порядок можно менять здесь
BLOCKS = [
    ('analysis', 'Анализ материалов и организационные мероприятия'),
    ('evidence', 'Выемки, обыски, осмотры и вещественные доказательства'),
    ('requests', 'Запросы в уполномоченные органы и организации'),
    ('finance', 'Финансовый след: банковские счета и движение денег'),
    ('assets', 'Установление имущества и активов фигурантов'),
    ('witnesses', 'Допросы свидетелей, потерпевших и специалистов'),
    ('expertise', 'Экспертизы и исследования'),
    ('suspects', 'Допросы подозреваемых и процессуальные решения в отношении них'),
    ('arrest', 'Ограничение распоряжения имуществом и арест'),
    ('verification', 'Очные ставки и проверка показаний'),
    (EXECUTED_BLOCK, 'Исполненные мероприятия'),
]
_NUMBER = {code: n for n, (code, _title) in enumerate(BLOCKS, start=1)}
_TITLE = dict(BLOCKS)

_NUMBERING = re.compile(r'^\s*\d+(?:\.\d+)*\.?\s*[).]?\s*')

# Банк: адресат (банк, БВУ) или предмет — выписки и движение денег по счетам. «Лицевые счета» налогоплательщика,
# «счета-фактуры, выписанные …», «брокерские счета» банковскими не считаются
_BANK = re.compile(r'\bбву\b|банк(?!рот)|\bbank\b|движени\w*\s+денежн|расч[её]тн\w+\s+сч[её]т'
                   r'|\bвыпис(?:к\w*|ок)\s+(?:по\s|о\s|движени|и\s+движени)', re.IGNORECASE)
# Органы и системы, у которых узнают об имуществе и активах: регистрация прав, кредиты и залоги, страхование,
# ценные бумаги, цифровые и зарубежные активы. Названы по компетенции — к материалам дела список не привязан
_ASSET_ORGANS = re.compile(
    r'\bпкб\b|кредитн\w+\s+бюро|\bесбд\b|депозитари|\bцдцб\b|\bмфца\b|\bаррфр\b'
    r'|регулировани\w+\s+и\s+развити\w+\s+финансового\s+рынка|interpol|интерпол|egmont|эгмонт|\bc?arin\b|криптон'
    r'|криптобирж', re.IGNORECASE)
# Предмет — имущество и активы
_ASSET_SUBJECT = re.compile(
    r'недвижим|имуществ|автотранспорт|транспортн\w+\s+средств|земельн\w+\s+участ|зарегистрированн\w+\s+прав'
    r'|ценн\w+\s+бумаг|цифров\w+\s+актив|криптоактив|криптокошел|\bкредит\w*|\bзалог\w*|страхов|зарубежн\w+\s+актив'
    r'|актив\w*\s+за\s+рубежом|бенефициар', re.IGNORECASE)
# Экспертиза, исследование, привлечение специалиста, получение заключения
_EXPERTISE = re.compile(
    r'экспертиз|(?:назнач|поруч|провести|привле)\w*[^.;]{0,80}?(?:исследовани|специалист)'
    r'|(?:получить|истребовать)\s+заключени\w+\s+(?:специалист|эксперт)', re.IGNORECASE)
# Решения в отношении подозреваемого: признание, квалификация, мера пресечения, задержание, розыск
_SUSPECT_DECISION = re.compile(
    r'признат\w*[^;]{0,80}?подозреваем|признании[^;]{0,40}?подозреваем|квалификаци\w+\s+деяни|о\s+квалификации'
    r'|предъяв\w*[^.;]{0,40}?(?:подозрени|обвинени)|мер\w*\s+пресечени|задержа\w+|объяв\w+[^.;]{0,30}?розыск'
    r'|назнач\w*\s+защитник', re.IGNORECASE)
_SUSPECT_WORD = re.compile(r'(?:допрос\w*|провести\s+(?:дополнительн\w+\s+)?допрос)\s+(?:в\s+качестве\s+)?(?:подозреваем|обвиняем)'
                           r'|(?:в\s+качестве|как)\s+(?:подозреваем|обвиняем)', re.IGNORECASE)
_WITNESS_FIRST = re.compile(r'\s*(?:допросить|провести\s+допрос)\s+(?:в\s+качестве\s+)?свидетел(?!\w*,?\s+имеющ)', re.IGNORECASE)
_RESTRICTION = re.compile(r'временн\w*\s+ограничени|ограничени\w*\s+(?:на\s+)?распоряжени', re.IGNORECASE)
_INSPECTION = re.compile(r'(?:(?:произвести|провести)\s+)?осмотр\w*|осмотреть', re.IGNORECASE)
# Осмотр места: объекта, помещения, участка, интернет-ресурса (в отличие от осмотра документов и сведений)
_PLACE = re.compile(r'\b(?:мест[ао]\b|объект|помещени|офис|здани|территори|жилищ|участк|склад|интернет|аккаунт|сайт|страниц)',
                    re.IGNORECASE)
_PAPER = re.compile(r'сведени|отч[её]тност|деклараци|ответ|регистрационн\w+\s+дел', re.IGNORECASE)
_REQUEST_WORD = re.compile(r'запрос|истребова', re.IGNORECASE)


def block_number(code: str) -> int:
    return _NUMBER.get(code, _NUMBER['analysis'])


def block_title(code: str) -> str:
    return _TITLE.get(code, _TITLE['analysis'])


def _bank_object(text: str) -> bool:
    """
    Изымаются (осматриваются, приобщаются) банковские документы: банк — в начале описания объекта («выемки
    банковских выписок …», «осмотр изъятых выписок по счетам …»). Обыск в жилище или выемка из офиса, где среди
    изымаемого названы и банковские карты, — блок выемок и обысков.
    """
    if re.search(r'обыск', text, re.IGNORECASE):
        return False
    head = re.search(r'вещественн\w+\s+доказательств\w*(?:\s+изъят\w+)?|осмотр\w*\s+изъят\w+|выемк\w+|изъять|получени\w+',
                     text, re.IGNORECASE)
    return bool(head and re.search(r'банк(?!рот)|\bbank\b|\bбву\b|\bвыпис(?:к\w*|ок)\b|движени\w*\s+денежн',
                                   text[head.end(): head.end() + 60], re.IGNORECASE))


def _source_block(text: str) -> str:
    """Блок запроса, истребования и осмотра сведений — по адресату и предмету: банк, имущество и активы, прочее."""
    if _ASSET_ORGANS.search(text):
        return 'assets'
    if _BANK.search(text):
        return 'finance'
    return 'assets' if _ASSET_SUBJECT.search(text) else 'requests'


def _is_suspect_interview(text: str, figurant_keys: set) -> bool:
    from core.procedural_validator import _suspect_interviewee
    if _WITNESS_FIRST.match(text):
        return False
    return bool(_SUSPECT_WORD.search(text)) or _suspect_interviewee(text, figurant_keys or set()) is not None


def action_block(action: Dict, figurant_keys: Optional[set] = None) -> str:
    """
    Код тематического блока пункта — по самому действию (цель не учитывается: «изобличить подозреваемого» у выемки
    не делает её допросом). Статус не учитывается: исполненные пункты в свой блок собирает arrange_by_blocks.
    figurant_keys — ключи фигурантов дела: «Допросить Иванова И.И. …» без слова «подозреваемого» — допрос
    подозреваемого, если Иванов фигурант.
    """
    from core.procedural_validator import _ARREST_RE, _VERIFY_RE, _chain_step, _is_mandatory
    text = _NUMBERING.sub('', action.get('действие') or '').strip()
    t = text.lower()
    if _is_mandatory({'действие': text}):
        return 'analysis'
    step = _chain_step(t)
    # Что осматривается, названо сразу после «осмотр» («с участием специалиста» пропускается)
    inspection = _INSPECTION.match(t)
    inspected = re.sub(r'^\s*с\s+(?:участием|привлечением)\s+специалист\w*', '', t[inspection.end():])[:70] if inspection else ''
    # Осмотр места, объекта, помещения, интернет-ресурса
    if inspection and step != 'осмотр_изъятого' and _PLACE.search(inspected):
        return 'evidence'

    # Цепочка запроса: запрос → истребование → осмотр ответа
    if step in ('запрос', 'истребование', 'осмотр_сведений'):
        return _source_block(t)
    # Цепочка изъятия: постановление → выемка (обыск) → осмотр изъятого → вещественные доказательства
    if step in ('санкция', 'выемка', 'обыск', 'осмотр_изъятого', 'вещдок'):
        return 'finance' if _bank_object(t) else 'evidence'

    if step in ('очная_ставка', 'проверка_показаний') or re.search(_VERIFY_RE, t) or re.match(r'провер\w+\s+показани', t):
        return 'verification'
    if step in ('ограничение', 'арест', 'санкция_ареста') or re.search(_ARREST_RE, t) or _RESTRICTION.search(t):
        return 'arrest'
    if step == 'допрос':
        return 'suspects' if _is_suspect_interview(text, figurant_keys) else 'witnesses'
    if _SUSPECT_DECISION.search(t):
        return 'suspects'
    if inspection:
        # осмотр документов и сведений — к их источнику; прочие осмотры — к осмотрам
        if _PAPER.search(inspected) or _BANK.search(inspected) or _ASSET_SUBJECT.search(inspected):
            return _source_block(t)
        return 'evidence'
    if _EXPERTISE.search(t):
        return 'expertise'
    if re.match(r'(?:вынести\s+постановлени\w+\s+о\s+)?признан?\w*[^;]{0,80}?потерпевш|установить\s+(?:и\s+допросить\s+)?'
                r'(?:потерпевш|свидетел)', t):
        return 'witnesses'
    if _BANK.search(t):
        return 'finance'
    if _ASSET_ORGANS.search(t) or _ASSET_SUBJECT.search(t):
        return 'assets'
    if _REQUEST_WORD.search(t):
        return 'requests'
    return 'analysis'


# Порядок шагов цепочки при одинаковом сроке (в старых планах шаги одной цепочки могли стоять в один день)
_STEP_RANK = {'санкция': 0, 'запрос': 0, 'ограничение': 0,
              'выемка': 1, 'обыск': 1, 'истребование': 1, 'арест': 1,
              'осмотр_изъятого': 2, 'осмотр_сведений': 2, 'санкция_ареста': 2,
              'вещдок': 3, 'назначение_экспертизы': 3}


def _date_key(value) -> tuple:
    m = re.match(r'\s*(\d{1,2})\.(\d{1,2})\.(\d{4})', str(value or ''))
    return (0, int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else (1, 0, 0, 0)


def _set_block(action: Dict, code: str) -> None:
    action['блок_номер'] = block_number(code)
    action['блок_код'] = code
    action['блок'] = block_title(code)
    action['направление'] = f"БЛОК {block_number(code)}: {block_title(code)}"


def _pending_blocks(pending: List[Dict], figurants: str, figurant_keys: set) -> Dict[int, str]:
    """
    {id(пункт): код блока} для неисполненных пунктов. Истребование и осмотр ответа стоят в блоке своего запроса:
    в их тексте адресат и предмет часто сокращены («… по запросу из КГД (составить протокол осмотра)»), а запрос
    мог быть об имуществе. Связь — та же, что при расстановке сроков (_chain_links); если пункт связан с запросами
    из разных блоков, остаётся блок по его собственному тексту.
    """
    from core.procedural_validator import _chain_links, _chain_step
    codes = {id(a): action_block(a, figurant_keys) for a in pending}
    steps = {id(a): _chain_step((a.get('действие') or '').lower()) for a in pending}
    if not any(s in ('истребование', 'осмотр_сведений') for s in steps.values()):
        return codes
    _order, preds = _chain_links(pending, figurants or '')

    def _requests(a, seen=()):
        found = []
        for p in preds.get(id(a), []):
            if id(p) in seen:
                continue
            if steps.get(id(p)) == 'запрос':
                found.append(p)
            elif steps.get(id(p)) == 'истребование':
                found.extend(_requests(p, seen + (id(a),)))
        return found

    for a in pending:
        # осмотр места («Провести осмотр объектов …») по своему тексту уже отнесён к осмотрам — он не ответ на запрос
        if steps[id(a)] in ('истребование', 'осмотр_сведений') and codes[id(a)] in ('requests', 'finance', 'assets'):
            request_codes = {codes[id(r)] for r in _requests(a)}
            if len(request_codes) == 1:
                codes[id(a)] = request_codes.pop()
    return codes


def arrange_by_blocks(actions: List[Dict], conducted: Optional[List[Dict]] = None, figurants: str = '') -> List[Dict]:
    """
    Раскладывает план по блокам и нумерует пункты заново (сквозная нумерация 1..N):
      - неисполненные — по блокам BLOCKS; внутри блока — по сроку; при равном сроке — по шагу цепочки
        (постановление и запрос раньше выемки и истребования, те — раньше осмотра), затем в прежнем порядке;
      - обязательные первые пункты (изучение материалов, круг лиц, проверка по базам) — первыми в блоке анализа;
      - исполненные — в последнем блоке, по дате документа-основания; без даты — в конце, а исполненный запрос
        без даты — перед ответом на него (executed_order).
    Повторный вызов порядок не меняет. Действует для первичного плана, дозагрузки, смены статусов и правок через API.
    """
    from core.case_participants import find_person_mentions, normalize_text
    from core.plan_generator import executed_order
    from core.procedural_validator import _chain_step, _is_mandatory

    figurant_keys = {p['key'] for p in find_person_mentions(normalize_text(figurants or ''))}
    pending = [a for a in actions if a.get('статус') != EXECUTED]
    done = [a for a in actions if a.get('статус') == EXECUTED]
    codes = _pending_blocks(pending, figurants, figurant_keys)
    for a in pending:
        _set_block(a, codes[id(a)])
    for a in done:
        _set_block(a, EXECUTED_BLOCK)

    position = {id(a): i for i, a in enumerate(pending)}
    rank = {id(a): _STEP_RANK.get(_chain_step((a.get('действие') or '').lower()), 1) for a in pending}
    pending.sort(key=lambda a: (a['блок_номер'], 0 if _is_mandatory(a) else 1, _date_key(a.get('срок')),
                                rank[id(a)], position[id(a)]))
    ordered = pending + executed_order(done, conducted)
    for idx, action in enumerate(ordered, start=1):
        action['номер'] = idx
    return ordered


def plan_blocks(plan: Dict, today: Optional[date] = None) -> List[Dict]:
    """
    Блоки плана по порядку — для отрисовки заголовков на фронте и в документе сайта. Пустые блоки не выводятся.
      number — номер блока (= «блок_номер» пунктов), code — постоянный код, title — название;
      total — пунктов в блоке; executed — из них исполнено; overdue — не исполнено и срок прошёл (на сегодня);
      first_number, last_number — номера первого и последнего пункта блока; action_numbers — номера всех пунктов.
    Считается при отдаче плана (просрочка зависит от даты), в файле и БД не хранится. План должен быть разложен
    по блокам (normalize_plan_statuses): блок пункта берётся из его «блок_код».
    """
    from core.plan_statistics import is_overdue
    today = today or date.today()
    blocks = {}
    for a in plan.get('actions') or []:
        code = a.get('блок_код')
        if code not in _NUMBER:
            code = EXECUTED_BLOCK if a.get('статус') == EXECUTED else 'analysis'
        b = blocks.setdefault(code, {'number': block_number(code), 'code': code, 'title': block_title(code),
                                     'total': 0, 'executed': 0, 'overdue': 0,
                                     'first_number': None, 'last_number': None, 'action_numbers': []})
        b['total'] += 1
        b['executed'] += int(a.get('статус') == EXECUTED)
        b['overdue'] += int(is_overdue(a, today))
        b['action_numbers'].append(a.get('номер'))
    result = sorted(blocks.values(), key=lambda b: b['number'])
    for b in result:
        b['first_number'], b['last_number'] = b['action_numbers'][0], b['action_numbers'][-1]
    return result


def attach_blocks(plan: Dict, today: Optional[date] = None) -> Dict:
    """Добавляет plan['blocks'] для ответа клиенту (при сохранении плана поле убирается)."""
    plan[BLOCKS_KEY] = plan_blocks(plan, today)
    return plan
