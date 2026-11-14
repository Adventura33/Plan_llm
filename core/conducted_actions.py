# core/conducted_actions.py
"""
Проведённые по делу следственные и процессуальные действия.

1. detect_conducted_actions — по документам дела (имя файла + шапка текста) определяет, какие действия
   уже проведены и с кем/чем: «допрос свидетеля Оспанова К.К.», «выемка у Инкербаевой А.Б.»,
   «назначена судебно-экономическая экспертиза», «ответ из АО «Kaspi Bank»».
2. match_conducted — сопоставляет пункт плана с проведённым действием по ТИПУ и ЛИЦУ/ОБЪЕКТУ
   (а не только по типу: протокол допроса одного свидетеля не делает исполненными все допросы).
3. apply_conducted_statuses — отмечает совпавшие пункты «Исполнен», а проведённые действия,
   которых нет в плане, добавляет в план отдельными пунктами со статусом «Исполнен».
"""

import re
import unicodedata
from typing import Dict, List, Optional

from core.case_participants import find_person_mentions, normalize_text

STATUS_DONE = 'Исполнен'

# Роли допрашиваемых (в родительном падеже, как в названии протокола)
_ROLES = [
    ('свидетеля, имеющего право на защиту', r'свидетел\w*,?\s+имеющ\w+\s+прав\w*\s+на\s+защиту'),
    ('подозреваемого', r'подозреваем\w*'),
    ('обвиняемого', r'обвиняем\w*'),
    ('представителя потерпевшего', r'представител\w+\s+потерпевш\w*'),
    ('потерпевшего', r'потерпевш\w*'),
    ('эксперта', r'эксперт(?!из)\w*'),
    ('специалиста', r'специалист\w*'),
    ('свидетеля', r'свидетел\w*|куәгер\w*|куә\b'),
]

_EXPERTISE_KINDS = [
    ('финансово-экономическая', r'финансово-эконом'),
    ('экономическая', r'эконом'),
    ('бухгалтерская', r'бухгалтер'),
    ('товароведческая', r'товаровед'),
    ('почерковедческая', r'почерковед'),
    ('компьютерно-техническая', r'компьютерн'),
    ('технико-криминалистическая', r'технико-криминалист'),
    ('строительная', r'строительн'),
    ('оценочная', r'оценочн'),
    ('химическая', r'химическ'),
    ('психолого-филологическая', r'психолого-филолог'),
    ('лингвистическая', r'лингвист|филолог'),
    ('фоноскопическая', r'фоноскоп'),
    ('психолого-психиатрическая', r'психиатр|психолог'),
]

# Адресаты запросов (для сопоставления «Направить запрос в X» ↔ «Ответ из X»)
_ORGANS = [
    ('кгд', r'\bкгд\b|государственных\s+доходов|\bдгд\b|\bугд\b'),
    ('нао', r'\bнао\b|правительство\s+для\s+граждан|госкорпораци'),
    ('аррфр', r'аррфр|регулированию\s+и\s+развитию\s+финансового'),
    # «Государственное кредитное бюро» — оператор ЕСБД (страхование), а не ПКБ (кредиты и залоги)
    ('пкб', r'\bпкб\b|перво\w*\s+кредитн\w*\s+бюро'),
    ('есбд', r'\bесбд\b|единая\s+страхов|государственн\w*\s+кредитн\w*\s+бюро'),
    ('криптон', r'криптон'),
    # Без них запрос в ЦДЦБ/МФЦА имел «пустого» адресата и совпадал с запросом в НАО по общим словам (216 v2)
    ('цдцб', r'\bцдцб\b|центр\w*\s+депозитари'),
    ('мфца', r'\bмфца\b|международн\w+\s+финансов\w+\s+центр|\baifc\b'),
    # международные каналы поиска активов — тоже свои адресаты (216 v3: запрос через INTERPOL «совпал» с ЦДЦБ)
    ('международные', r'interpol|интерпол|egmont|эгмонт|\bcarin\b|\barin\b'),
    ('енпф', r'енпф|пенсионн'),
    ('kaspi', r'kaspi|каспи'),
    ('halyk', r'halyk|халык|народн\w+\s+банк'),
    ('forte', r'forte|форте'),
    ('jusan', r'jusan|jysan|жусан'),
    ('bereke', r'bereke|береке'),
    ('bcc', r'центркредит|\bbcc\b'),
    ('freedom', r'freedom|фридом'),
    ('eurasian', r'евразийск|eurasian'),
    ('otbasy', r'отбасы|otbasy'),
    ('home', r'home\s*credit|хоум\s*кредит'),
    ('оператор', r'kcell|beeline|tele2|altel|казахтелеком|оператор\w*\s+(?:сотовой\s+)?связи'),
    ('игорный_комитет', r'игорн\w*\s+бизнес|туризма\s+и\s+спорта|ойын\s+бизнес'),
    ('оператор_лотереи', r'с[әа]тт[іи]\s+ж[ұу]лд[ыұу]з|оператор\w*\s+лотере'),
    ('полиция', r'полици|\bуап\b|\bмвд\b|внутренних\s+дел|\bспон\b'),
    ('meta', r'instagram|инстаграм|\bmeta\b|facebook'),
]

# Тип документа → проведённое действие. Порядок важен: более специфичные первыми.
_DOC_TYPES = [
    # «Протокол проверки и уточнения показаний на месте»
    ('проверка_показаний', r'пр\w{0,2}токол\w*\s+проверк\w*\s+(?:и\s+уточнени\w*\s+)?показаний'),
    ('очная_ставка', r'пр\w{0,2}токол\w*\s+очн\w*\s+ставк'),
    ('допрос', r'пр\w{0,2}токол\w*\s+(?:о\s+)?(?:дополнительн\w+\s+)?допрос|жауап\s+алу\s+хаттам'),
    ('обыск', r'пр\w{0,2}токол\w*\s+(?:личного\s+)?обыск|тінту\s+хаттам'),
    ('выемка', r'пр\w{0,2}токол\w*\s+выемк|акт\w*\s+выемк|алу\s+хаттам'),
    ('осмотр', r'пр\w{0,2}токол\w*\s+осмотр|акт\w*\s+осмотр|қарау\s+хаттам'),
    # «Заключение эксперта», «Заключение специалиста», а также файл, который начинается словом «Заключение»
    # («Заключение_ГУ_«Комитет…»», «заключение__13095_(психолого-филологическая)»)
    ('заключение', r'заключени\w*\s+(?:судебн\w+\s+)?(?:эксперт|специал)|^\W*\d*\W*заключени|сараптама\s+қорытынды'),
    ('назначение_экспертизы', r'постановлени\w*\s+о\s+назначении\s+[\w\s-]*(?:экспертиз|экспертн\w*\s+[\w\s-]*исследовани)'),
    ('вещдок', r'постановлени\w*\s+о\s+(?:признании|приобщении)[\w\s-]*вещественн'),
    ('арест', r'постановлени\w*\s+о\s+(?:наложении\s+)?(?:арест|ограничени|временн\w+\s+ограничени)'),
    # «Қаулы жәбірленуші деп тану туралы» — казахское постановление о признании потерпевшим
    ('потерпевший', r'постановлени\w*\s+о\s+признании[\w\s-]*потерпевш|жәбірленуші\s+деп\s+тану'),
    ('санкция', r'постановлени\w*\s+о\s+производстве\s+(?:выемк|обыск)'),
    # «Постановление о привлечении специалиста», «привличение_специалиста_ГУ…» (с опечаткой, без слова «постановление»)
    ('специалист', r'(?:постановлени\w*\s+о\s+)?привл\w*\s+(?:в\s+качестве\s+)?специал'),
    ('ответ', r'ответ\w*\s+(?:на\s+запрос|из|от|с|со)\b|выпис\w*\s+(?:по\s+)?(?:счет|счёт|движени)|'
              r'сведени\w*\s+из\s+(?:банк|кгд|нао)|справк\w*\s+(?:из\s+)?банк'),
    # Исходящий запрос следователя («15. запрос_АО_«Сәтті_Жұлдыз»») — пункт «Направить запрос» исполнен
    ('запрос', r'^\W*\d*\W*запрос\w*\b'),
    # Процессуальные решения по подозреваемому (определяются только по названию файла)
    ('подозреваемый', r'постановлени\w*\s+о\s+признании\s+(?:лица\s+)?(?:в\s+качестве\s+)?подозреваем'),
    ('квалификация', r'постановлени\w*\s+о\s+квалификации'),
    ('мера_пресечения', r'мер\w*\s+пресечени'),
    ('розыск', r'розыск'),
    ('защитник', r'постановлени\w*\s+о\s+назначении\s+защитник'),
    ('сио_псо', r'сио\s*п\w*\s*со|сиопсо'),
    ('установление', r'рапорт\w*[^.]{0,40}?по\s+установлени'),
]

# Типы, которые определяются только по названию файла: в тексте других документов эти слова встречаются
# мимоходом («объявлен в розыск», «избрана мера пресечения») и сделали бы документ не тем, что он есть
_NAME_ONLY_TYPES = {'запрос', 'подозреваемый', 'квалификация', 'мера_пресечения', 'розыск', 'защитник',
                    'сио_псо', 'установление'}

_TYPE_LABEL = {
    'проверка_показаний': 'Проведена проверка показаний на месте',
    'очная_ставка': 'Проведена очная ставка',
    'допрос': 'Проведён допрос',
    'обыск': 'Проведён обыск',
    'выемка': 'Проведена выемка',
    'осмотр': 'Проведён осмотр',
    'заключение': 'Получено заключение',
    'назначение_экспертизы': 'Назначена экспертиза',
    'вещдок': 'Вынесено постановление о признании (приобщении) вещественных доказательств',
    'арест': 'Вынесено постановление о наложении ареста (ограничения) на имущество',
    'потерпевший': 'Вынесено постановление о признании потерпевшим',
    'санкция': 'Вынесено постановление о производстве',
    'специалист': 'Вынесено постановление о привлечении специалиста',
    'ответ': 'Получены сведения по запросу',
    'запрос': 'Направлен запрос',
    'подозреваемый': 'Вынесено постановление о признании лица подозреваемым',
    'квалификация': 'Вынесено постановление о квалификации деяния подозреваемого',
    'мера_пресечения': 'Возбуждено ходатайство о санкционировании меры пресечения',
    'розыск': 'Объявлен розыск',
    'защитник': 'Вынесено постановление о назначении защитника',
    'сио_псо': 'Проведена проверка по БД СИО ПСО',
    'установление': 'Получен рапорт об установлении',
}

# Метки в документе, после которых указано ФИО лица, с которым проведено действие:
# (шаблон, сколько символов может быть между меткой и ФИО)
_PERSON_LABELS = [
    (r'(?:фамилия,?\s*имя,?\s*отчество|ф\.\s?и\.\s?о\.?|тегі,?\s*аты,?\s*(?:әкесінің\s+аты)?)\s*(?:\([^)]*\))?\s*[:\-–]?\s*', 3),
    # «допросил по уголовному делу № … в качестве специалиста ГУ "Отдел образования" Беркутбаева Майя Базарбаевна»
    (r'допросил\w*[^:]{0,120}?в\s+качестве\s+[\w()]+\s*:?\s*', 80),
    (r'допрошен\w*\s+(?:в\s+качестве\s+\w+\s+)?', 3),
    # «Произвести выемку у исполняющего обязанности руководителя КГКП «Ясли-сад №27» Инкербаевой А.Б.»
    (r'(?:выемк\w*|обыск\w*)\s+(?:у|в\s+жилище|по\s+месту\s+жительства)\s+(?:гр\.?\s*|гражданин\w*\s+)?', 150),
    (r'представител\w+\s+юридическ\w+\s+лица\s*[–—-]?\s*потерпевш\w*\s*', 3),
    (r'очн\w*\s+ставк\w*\s+между\s+', 3),
    (r'показаний\s+(?:на\s+месте\s+)?(?:подозреваем\w+|обвиняем\w+|свидетел\w+|потерпевш\w+)\s+', 3),
]

# Латинские буквы-двойники внутри русских слов («Пpотокол» с латинской p) — к кириллице
_HOMOGLYPHS = str.maketrans('aceopxykmtbhACEOPXYKMTBH', 'асеорхукмтвнАСЕОРХУКМТВН')


def _fix_homoglyphs(text: str) -> str:
    return re.sub(r'\w+', lambda m: m.group(0).translate(_HOMOGLYPHS)
                  if re.search(r'[А-Яа-яЁё]', m.group(0)) else m.group(0), text)


def _fix_mojibake(text: str) -> str:
    """
    «╨Я╤А╨╛╤В╨╛╨║╨╛╨╗» -> «Протокол»: имя файла в UTF-8, распакованное из ZIP в кодировке DOS (CP866/CP437).
    """
    if text and re.search(r'[╨╤]', text):
        for codepage in ('cp866', 'cp437'):
            try:
                return text.encode(codepage).decode('utf-8')
            except (UnicodeEncodeError, UnicodeDecodeError):
                continue
    return text


def _norm(text: str) -> str:
    text = unicodedata.normalize('NFC', _fix_mojibake(text or ''))
    return re.sub(r'[_]+', ' ', text)


def _title_if_upper(snippet: str) -> str:
    """«ОСПАНОВ КАНАТ КАЙРАТОВИЧ» -> «Оспанов Канат Кайратович» (ФИО в бланках часто прописными)."""
    return re.sub(r'(?<![А-ЯЁа-яёӘәҒғҚқҢңӨөҰұҮүҺһІі])[А-ЯЁӘҒҚҢӨҰҮҺІ]{3,}(?![А-ЯЁа-яёӘәҒғҚқҢңӨөҰұҮүҺһІі])',
                  lambda m: m.group(0).capitalize(), snippet)


def _persons_in(snippet: str) -> List[Dict]:
    return find_person_mentions(normalize_text(_title_if_upper(snippet)))


def _role_of(text_lower: str) -> str:
    for label, pat in _ROLES:
        if re.search(pat, text_lower):
            return label
    return ''


def _action_role(text_lower: str, mentions: List[Dict]) -> str:
    """
    Статус допрашиваемого в пункте плана — по тексту ДО его ФИО или сразу после («Допросить свидетеля
    Макарову С.Д.», «Допросить Макарова Р.Ж. в качестве подозреваемого»). Слова «подозреваемым»
    в предмете допроса («…о родственных отношениях с подозреваемым») статус не определяют.
    """
    if not mentions:
        return _role_of(text_lower)
    first = mentions[0]
    before = text_lower[:first['start']]
    after = text_lower[first['end']:first['end'] + 60]
    m = re.match(r'[\s,]*в\s+качестве\s+(.+)', after)
    return _role_of(before) or (_role_of(m.group(1)) if m else '')


def _expertise_kind(text_lower: str) -> str:
    # «Департамент экономических расследований» — название органа, а не вид экспертизы
    text_lower = re.sub(r'эконом\w*\s+расследовани\w*', ' ', text_lower or '')
    for label, pat in _EXPERTISE_KINDS:
        if re.search(pat, text_lower):
            return label
    return ''


def _organs(text_lower: str) -> List[str]:
    return [key for key, pat in _ORGANS if re.search(pat, text_lower)]


def _quoted_names(text: str) -> List[str]:
    """Названия организаций в кавычках: «Express Gold» -> express gold. Поля бланка («_09_», «15») — не названия."""
    names = [q.strip().lower() for q in re.findall(r'[«"]([^»"]{2,60})[»"]', text or '')]
    return [n for n in names if re.search(r'[a-zа-яәғқңөұүһі]{3,}', n)]


_MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа',
           'сентября', 'октября', 'ноября', 'декабря']
# Месяц в дате шапки — в любом падеже (««04» август 2025 г.», 216) и по-казахски (««07» тамыз 2025 жыл»); казахские
# буквы в OCR бывают заменены русскими («кантар», «желтоксан»)
_MONTH_STEMS = [
    (1, r'январ|қаңтар|кантар|қантар'), (2, r'феврал|ақпан|акпан'), (3, r'март|наурыз'),
    (4, r'апрел|сәуір|сауір|сэуір|сауир'), (5, r'ма[йяю]\b|мамыр'), (6, r'июн|маусым'), (7, r'июл|шілде|шилде'),
    (8, r'август|тамыз'), (9, r'сентябр|қыркүйек|кыркуйек|қыркуйек'), (10, r'октябр|қазан|казан'),
    (11, r'ноябр|қараша|караша'), (12, r'декабр|желтоқсан|желтоксан'),
]


def _month_number(word: str):
    w = (word or '').lower()
    return next((n for n, pat in _MONTH_STEMS if re.match(rf'(?:{pat})', w)), None)


def _org_tokens(name: str) -> set:
    """«Ясли-сад №27 г.Павлодара» и «Ясли-сад № 27 города Павлодара» -> {'ясли', 'сад', '27', 'павло'}."""
    name = name.lower().replace('ё', 'е')
    words = {w[:5] for w in re.findall(r'[a-zа-яәғқңөұүһі]{3,}', name)}
    stop = {'город', 'облас', 'район', 'ленин'}
    return (words - stop) | set(re.findall(r'\d+', name))


def _orgs_overlap(a, b) -> bool:
    """Одна и та же организация, записанная по-разному (сокращения, пробелы, «г.» / «города»)."""
    for x in a or []:
        tx = _org_tokens(x)
        for y in b or []:
            ty = _org_tokens(y)
            common = tx & ty
            digits_x, digits_y = {t for t in tx if t.isdigit()}, {t for t in ty if t.isdigit()}
            if digits_x and digits_y and not (digits_x & digits_y):
                continue  # «Ясли-сад №27» и «Ясли-сад №12» — разные организации
            if len(common) >= 2 or (common and (common == tx or common == ty)):
                return True
    return False


def _doc_date(text: str) -> str:
    """
    Дата составления — первая дата в шапке документа («14 мая 2024 года» или «14.05.2024»).
    Дата регистрации ЕРДР в тексте постановления идёт позже и не берётся.
    """
    # День, вписанный в поле бланка: «_15_».05.2024, «_15_» мая 2024, «15» мая 2024
    head = re.sub(r'«\s*_*\s*(\d{1,2})\s*_*\s*»', lambda m: f"{int(m.group(1)):02d}", text[:2500])
    head = re.sub(r'\b(\d{2})\s+\.(\d{2})', r'\1.\2', head)
    candidates = []  # (позиция, дата, год)
    for m in re.finditer(r'\b(\d{2})[./](\d{2})[./](\d{4})(?!\d)', head):  # и «04.11.2025г.»
        candidates.append((m.start(), m.end(), f"{m.group(1)}.{m.group(2)}.{m.group(3)}", int(m.group(3))))
    for m in re.finditer(r'«?(\d{1,2})»?\s+([а-яёәғқңөұүһі]{3,})\s+(\d{4})', head, re.IGNORECASE):
        month = _month_number(m.group(2))
        if month is None or not 1 <= int(m.group(1)) <= 31:
            continue
        candidates.append((m.start(), m.end(), f"{int(m.group(1)):02d}.{month:02d}.{m.group(3)}", int(m.group(3))))
    if not candidates:
        return ''

    def _is_other_date(start: int, end: int) -> bool:
        """
        Дата рождения, регистрации ЕРДР/КУИ, выдачи удостоверения/лицензии — не дата документа.
        По-казахски дата рождения — «Туған күні: 27.04.1971 ж.т.» (216, протокол Фёдорова).
        """
        before = head[max(0, start - 90):start].lower()
        after = head[end:end + 20].lower()
        # признаки рождения — в той же строке: ««12» қараша 2024 жыл⏎Туған күні: …» — дата протокола
        line_before = before[-25:].rsplit('\n', 1)[-1]
        line_after = after.split('\n', 1)[0]
        return bool(re.search(r'г\.\s?р|года?\s+рожд|рожд|ж\.\s?т\b|туған|туган|туылған|туылган',
                              line_after + ' ' + line_before)
                    or re.search(r'ердр|куи|зарегистр|удостоверени|лиценз|свидетельств|выдан|аттестат|стаж', before))

    # Даты за пределами «кластера» дела (например, 2005 год рядом с 2024-м) — не дата документа
    own = [c for c in sorted(candidates) if not _is_other_date(c[0], c[1])]
    if not own:
        # В документе только даты рождения / регистрации / выдачи (запрос без даты в шапке) — даты документа нет
        return ''
    latest_year = max(c[3] for c in own)
    plausible = [c for c in own if c[3] >= latest_year - 1]
    return plausible[0][2]


def _doc_title(filename: str) -> str:
    title = _norm(filename)
    title = re.sub(r'\.(pdf|docx?|txt|xlsx)$', '', title, flags=re.IGNORECASE)
    title = re.sub(r'^\s*\d+\s*[.)]\s*', '', title)  # «13. Протокол…» -> «Протокол…»
    title = re.sub(r'(?i)\bпртокол', 'Протокол', title)  # частая опечатка в названиях файлов
    title = title[:1].upper() + title[1:]
    return re.sub(r'\s+', ' ', title).strip(' .')


# Название файла, по которому видно, что это за документ («Протокол (доп) допроса…»), а не «Некорректный файл»
_DOC_WORD = re.compile(r'пр\w{0,2}токол|постановлени|заключени|экспертиз|допрос|обыск|выемк|осмотр|ответ|запрос|'
                       r'рапорт|\bакт\b|справк|выписк|сведени|уведомлени|хаттам|қаулы|қорытынды', re.IGNORECASE)


def _own_section(text: str, doc_type: str) -> str:
    """
    Файл-пакет («12. Заключение аудита»: сопроводительное письмо, опись, рапорт КУИ от 28.04, затем само
    заключение от 21.04) — дата и признаки документа берутся из его раздела: от строки-заголовка
    («Заключение государственного аудитора…»), за которой идёт дата.
    """
    pat = re.compile(dict(_DOC_TYPES)[doc_type], re.MULTILINE | re.IGNORECASE)
    if pat.search(text[:300]):
        return text
    for m in pat.finditer(text):
        line = text.rfind('\n', 0, m.start()) + 1
        if not text[line:m.start()].strip() and _doc_date(text[line:line + 400]):
            return text[line:]
    return text


def _target_persons(head: str, filename: str, official_keys: set) -> List[Dict]:
    """ФИО лица, с которым проведено действие: из имени файла, затем по меткам бланка, затем первое не-должностное."""
    for source in (_norm(filename), head):
        for label, max_offset in _PERSON_LABELS:
            for m in re.finditer(label, source, re.IGNORECASE):
                found = [p for p in _persons_in(source[m.end():m.end() + max_offset + 90])
                         if p['start'] <= max_offset and p['key'] not in official_keys]
                if found:
                    return found[:1]
    # Без метки берём ФИО только из названия файла: первое ФИО в тексте — обычно подозреваемый
    # из фабулы («в отношении бухгалтера Макарова Р.Ж.»), а не лицо, с которым проведено действие
    persons = [p for p in _persons_in(_norm(filename)) if p['key'] not in official_keys]
    return persons[:1] or _blank_field_person(head, filename, official_keys)


_LETTERS = 'А-ЯЁа-яёӘәҒғҚқҢңӨөҰұҮүҺһІі'
# «Фамилия, имя, отчество: Серікқызы Камила» / «Тегі, аты, әкесінің аты: …»
_FIO_FIELD = re.compile(r'(?:Фамилия,?\s*имя,?\s*отчество|Тегі,?\s*аты,?\s*әкесінің\s+аты)[^:\n]{0,25}:\s*([^\n]{3,80})',
                        re.IGNORECASE)
# «протокол_допроса_свидетеля_Серікқызы_Камила.pdf», «…_свидетеля_Акбар_А._.pdf»
_FIO_IN_NAME = re.compile(rf'(?:свидет|потерпевш|подозреваем|специалист|эксперт)\w*[\s_]+'
                          rf'([{_LETTERS}]{{2,}}(?:[\s_]+[{_LETTERS}]{{1,}}\.?){{1,2}})', re.IGNORECASE)


def _blank_field_person(head: str, filename: str, official_keys: set) -> List[Dict]:
    """
    Казахские ФИО из двух частей («Серікқызы Камила», «Акбар А.») обычный поиск ФИО (фамилия + имя + отчество
    или инициалы) не находит. Тогда ФИО берётся из поля бланка протокола, а если OCR его не прочитал — из имени файла.
    """
    from core.case_participants import _person_key
    name = re.sub(r'\.(?:pdf|docx?|txt|xlsx)$', '', _norm(filename), flags=re.IGNORECASE)
    for source, pat in ((head, _FIO_FIELD), (name, _FIO_IN_NAME)):
        m = pat.search(source or '')
        if not m:
            continue
        words = re.findall(rf'[{_LETTERS}]+\.?', _title_if_upper(m.group(1)))
        words = [w for w in words if w[0].isupper()][:3]
        if len(words) < 2 or len(words[0]) < 3:
            continue
        key = _person_key(words[0], words[1][0])
        if key in official_keys:
            continue
        fio = ' '.join(words) if len(words[1]) > 2 else f"{words[0]} {words[1][0]}."
        return [{'fio': fio, 'key': key, 'start': 0, 'end': 0}]
    return []


_DECISION_PART = re.compile(r'П\s*О\s*С\s*Т\s*А\s*Н\s*О\s*В\s*И\s*Л|ҚАУЛЫ\s+ЕТТІМ|ПОСТАНОВЛЯЮ')


_FORM_WORD = r'[А-ЯЁӘҒҚҢӨҰҮҺІ][а-яёәғқңөұүһі]+(?:-[А-ЯЁ][а-яё]+)?'


def _form_person(text: str) -> List[Dict]:
    """
    Бланк с полями «5. ФАМИЛИЯ 5.1 Захаревич 6. ИМЯ … 6.1 Валерий 7: Евгеньевич» (постановление о
    межгосударственном розыске, карточки учёта) — ФИО собирается из полей.
    """
    from core.case_participants import _person_key
    sm = re.search(rf'ФАМИЛИЯ[^А-ЯЁа-яё]{{0,25}}({_FORM_WORD})', text or '')
    if not sm:
        return []
    nm = re.search(rf'\bИМЯ\b[\s\S]{{0,80}}?({_FORM_WORD})', text[sm.end():sm.end() + 300])
    if not nm:
        return []
    rest = text[sm.end() + nm.end(): sm.end() + nm.end() + 60]
    pm = re.search(rf'({_FORM_WORD})', rest)
    surname, first = sm.group(1), nm.group(1)
    patronymic = pm.group(1) if pm and re.search(r'(?:вич|вна|чна|улы|ұлы|кызы|қызы)$', pm.group(1)) else ''
    fio = ' '.join(x for x in (surname, first, patronymic) if x)
    return [{'fio': fio, 'key': _person_key(surname, first[0]), 'start': 0, 'end': 0}]


def _decision_persons(text: str, official_keys: set) -> List[Dict]:
    """
    Лицо, о котором вынесено решение: из полей бланка («ФАМИЛИЯ … ИМЯ …»), иначе первое ФИО в резолютивной
    части («ПОСТАНОВИЛ: 1. Объявить в розыск …»), иначе в начале документа.
    """
    form = [p for p in _form_person(text) if p['key'] not in official_keys]
    if form:
        return form
    m = _DECISION_PART.search(text or '')
    for part in ((text[m.end():m.end() + 1500],) if m else ()) + ((text or '')[:4000],):
        found = [p for p in _persons_in(part) if p['key'] not in official_keys]
        if found:
            return found[:1]
    return []


_REFUSAL = re.compile(
    r'(?<!не\s)от\s+дачи\s+показаний\s+(?:отказываюсь|отказывается|отказался|отказалась|отказ\b)'
    r'|(?<!не\s)отказал(?:ся|ась)\s+от\s+дачи\s+показаний'
    r'|воспользовал\w*\s+(?:правом|ст\.?\s*77)[^.\n]{0,60}Конституци'
    r'|айғақ\s+беруден\s+бас\s+тарт(?:амын|ты)', re.IGNORECASE)

# Документы, которые без указания лица не имеют смысла (кого признали потерпевшим, кого допросили)
_PERSON_REQUIRED = {'потерпевший', 'подозреваемый', 'допрос', 'очная_ставка', 'мера_пресечения'}
# Незаполненное поле лица в бланке: «1.   ж.т. осы қылмыстық іс бойынша жәбірленуші деп тануды»,
# «1. Признать   г.р. потерпевшим» (нет ни ФИО, ни года рождения), «Фамилия, имя, отчество: ______⏎Дата рождения:»
# (подчёркивания после _norm — пробелы). Значение на следующей строке («отчество:⏎Серікқызы Камила») — не пустое
# поле: пустым считается, только если сразу за ним идёт следующее поле бланка
_BLANK_PERSON = re.compile(
    r'(?:^|[\n:;]|\b\d{1,2}\.)[ \t]*(?:признать[ \t]+)?(?:г\.[ \t]?р\.|ж\.[ \t]?т\.)'
    r'|(?:Фамилия,?\s*имя,?\s*отчество|Тегі,?\s*аты,?\s*әкесінің\s+аты)[^:\n]{0,25}:[ \t_]*\n[ \t]*[^\n:]{2,40}:',
    re.IGNORECASE | re.MULTILINE)


def detect_conducted_actions(documents: List[Dict], officials: Optional[List[Dict]] = None) -> List[Dict]:
    """
    Возвращает список проведённых действий (JSON-сериализуемый, хранится в case_data):
    [{тип, роль, лица: [ключи], фио: [ФИО], вид_экспертизы, органы, организации, дата, документ, файл}]
    """
    official_keys = {o['key'] for o in (officials or [])
                     if o.get('role') in ('сотрудник органа', 'руководство органа', 'прокурор', 'судья')}
    result = []
    seen_texts = {}
    for doc in documents or []:
        filename = doc.get('filename', '') or ''
        text = _fix_homoglyphs(_norm(doc.get('text', '') or ''))
        # Тот же документ под другим именем («5. …Петровой…» и «42. …Петровой…», 216) — одно действие
        if len(text) > 300 and text in seen_texts:
            print(f"  ⏭ [Проведено] «{filename}» — тот же документ, что «{seen_texts[text]}»: повторно не учитывается")
            continue
        if len(text) > 300:
            seen_texts[text] = filename
        original_filename = filename
        filename = _fix_homoglyphs(_norm(filename))
        # Резолютивная часть постановлений («ПОСТАНОВИЛ: Произвести выемку у …») идёт после 2–3 тыс. символов
        head = text[:6000]
        # Тип — по названию файла; по тексту только если название неинформативно
        # (в тексте постановления о назначении экспертизы встречается «заключение эксперта» и т.п.)
        name_probe = _norm(filename).lower()
        text_probe = text[:600].lower()
        name_type = next((t for t, pat in _DOC_TYPES if re.search(pat, name_probe)), None)
        doc_type = name_type \
            or next((t for t, pat in _DOC_TYPES if t not in _NAME_ONLY_TYPES and re.search(pat, text_probe)), None)
        if not doc_type:
            continue
        own = _own_section(text, doc_type)
        text_probe = own[:600].lower()
        probe = name_probe + ' \n ' + text_probe
        title = _doc_title(filename)

        item = {
            'тип': doc_type,
            'роль': '',
            'лица': [],
            'фио': [],
            'вид_экспертизы': '',
            'органы': [],
            'организации': _quoted_names(filename + ' ' + head[:3000]),
            'дата': _doc_date(own),
            # «19. Некорректный_файл.pdf» о документе ничего не говорит — в пункт плана идёт только вид действия
            'документ': title if name_type or _DOC_WORD.search(title) else '',
            'файл': original_filename,
        }
        if doc_type in ('допрос', 'очная_ставка', 'обыск', 'выемка', 'арест', 'потерпевший', 'санкция',
                        'проверка_показаний'):
            if doc_type == 'допрос':
                # Роль — из названия протокола или «допросил в качестве …», а не из любого упоминания в тексте
                in_role = re.search(r'в\s+качестве\s+([^:\n]{3,60})', head, re.IGNORECASE)
                item['роль'] = _role_of(name_probe) or (_role_of(in_role.group(1).lower()) if in_role else '') \
                    or _role_of(text_probe)
                # «От дачи показаний отказываюсь» (в бланке стоит и «…не отказываюсь» — это не отказ)
                item['отказ'] = bool(_REFUSAL.search(text))
                # Место работы и должность из анкетной части — для пунктов «Допросить бухгалтера ТОО «…»» без ФИО
                item['должность'] = _workplace(head)
            if doc_type == 'очная_ставка':
                m = re.search(r'между\s+', head, re.IGNORECASE)
                persons = [p for p in _persons_in(head[m.end():m.end() + 200] if m else head)
                           if p['key'] not in official_keys][:2]
            else:
                persons = _target_persons(head, filename, official_keys)
                # Лицо в протоколе (поле бланка) и в названии файла — разные: «1. Протокол_допроса_свидетеля_
                # Васильева_Т.Б.» содержит допрос Волкова А.Н. (216). Учитывается документ, файл — под вопросом
                in_name = [p for p in _persons_in(filename) if p['key'] not in official_keys]

                def _close(k1, k2):
                    # «Лебедевой А.Ж.» в названии и «Лебедева Алия» в протоколе — опечатка, то же лицо
                    from difflib import SequenceMatcher
                    s1, i1 = (k1.split('|') + [''])[:2]
                    s2, i2 = (k2.split('|') + [''])[:2]
                    return i1 == i2 and SequenceMatcher(None, s1, s2).ratio() >= 0.8

                if persons and in_name and not any(_close(persons[0]['key'], p['key']) for p in in_name):
                    print(f"  ⚠ [Проведено] «{original_filename}»: в названии файла — {in_name[0]['fio']}, "
                          f"в документе — {persons[0]['fio']}; учтён документ")
            item['лица'] = [p['key'] for p in persons]
            item['фио'] = [re.sub(r'\s+', ' ', p['fio']).strip() for p in persons]
        if doc_type in ('подозреваемый', 'квалификация', 'мера_пресечения', 'розыск', 'защитник',
                        'сио_псо', 'установление'):
            # Лицо — из названия файла («…СИОПСО__Захаревич_В.Е.») или из резолютивной части постановления
            persons = [p for p in _persons_in(_norm(filename)) if p['key'] not in official_keys][:1] \
                or _decision_persons(text, official_keys)
            item['лица'] = [p['key'] for p in persons]
            item['фио'] = [re.sub(r'\s+', ' ', p['fio']).strip() for p in persons]
        # Бланк с незаполненным лицом — документ не составлен (проект или ошибочный файл), действие не проведено.
        # Потерпевший-организация («признать потерпевшим ГУ «…»») — без такого пустого поля и учитывается
        if doc_type in _PERSON_REQUIRED and not item['лица'] and _BLANK_PERSON.search(own[:6000]):
            print(f"  ⚠ [Проведено] «{original_filename}»: в документе не указано лицо (поле ФИО не заполнено) — "
                  f"не считается исполненным")
            continue
        if doc_type in ('заключение', 'назначение_экспертизы'):
            # Вид — по названию файла, иначе по началу текста
            item['вид_экспертизы'] = _expertise_kind(name_probe) or _expertise_kind(text_probe)
        if doc_type == 'заключение':
            # Заключение специалиста (исследование) — не экспертиза: не закрывает «Назначить … экспертизу»
            # «Заключение государственного аудитора, привлеченного … в качестве специалиста» (ДВГА) — тоже специалист
            item['специалист'] = bool(re.search(r'специал', name_probe)
                                      or re.search(r'заключени\w*\s+(?:специал|[^.\n]{0,80}?привлеч\w*[^.\n]{0,60}?'
                                                   r'в\s+качестве\s+специал)', text_probe))
        if doc_type in ('заключение', 'назначение_экспертизы'):
            item['дополнительная'] = bool(re.search(r'дополнительн|повторн', probe))
        if doc_type in ('ответ', 'запрос'):
            # Адресат — по названию файла («ответ_с_Комитета…», «запрос_АО_«Сәтті_Жұлдыз»»); в шапке текста
            # стоят реквизиты самого органа расследования, по ним адресат не определить
            item['органы'] = _organs(name_probe) or _organs(text_probe)
        if doc_type == 'санкция':
            item['роль'] = 'обыска' if re.search(r'обыск', probe) else 'выемки'
        if doc_type == 'арест':
            # Постановление о временном ограничении распоряжения имуществом — не арест: закрывает свой пункт
            item['ограничение'] = _is_restriction_title(name_probe) \
                or (not re.search(r'арест', name_probe) and _is_restriction_title(text_probe))
        if doc_type == 'осмотр':
            # Что осмотрено — по названию и началу протокола («… документов, поступивших из …»)
            item['органы'] = _organs(name_probe) or _organs(text_probe)
        if doc_type in ('обыск', 'выемка', 'санкция', 'осмотр'):
            item['адреса'] = _addresses(head)
        result.append(item)

    if result:
        print(f"  📑 Проведённые действия по материалам дела: {len(result)}")
        for c in result:
            who = ', '.join(c['фио']) or c['вид_экспертизы'] or ', '.join(c['органы'])
            print(f"     • {c['тип']}{(' ' + c['роль']) if c['роль'] else ''}{(' — ' + who) if who else ''}"
                  f"{' [ОТКАЗ от дачи показаний]' if c.get('отказ') else ''} ({c['документ'] or c['файл']})")
    return result


_WORKPLACE = re.compile(
    r'(?:место\s+работы(?:\s+или\s+уч[её]бы)?(?:\s*,\s*должность)?|род\s+занятий(?:\s+или\s+должность)?|должность'
    r'|жұмыс\s+орны[^:<\n]{0,30})\s*:?\s*(?:</th>\s*<td[^>]*>)?\s*([^\n<]{2,200})', re.IGNORECASE)


def _workplace(head: str) -> str:
    """Место работы и должность допрошенного из анкетной части протокола (поля бланка, в т.ч. в таблице)."""
    found = [m.group(1).strip(' .;-–—').lower() for m in _WORKPLACE.finditer(head or '')]
    return '; '.join(dict.fromkeys(f for f in found if re.search(r'[a-zа-яёәғқңөұүһі]{3,}', f)))[:300]


def _is_restriction_title(text_lower: str) -> bool:
    return bool(re.search(r'временн\w*\s+ограничени|ограничени\w*\s+(?:на\s+)?распоряжени', text_lower or ''))


def _addresses(text: str) -> List[str]:
    """Адреса из документа («… по адресу: г. Астана, ул. Мира, д. 1») в виде ключей «город|улица|номера»."""
    from core.procedural_validator import _address_key
    keys = []
    for m in re.finditer(r'по\s+адресу\s*:?\s*([^\n]{5,160})', text or '', re.IGNORECASE):
        key = _address_key(m.group(1))
        if key and key not in keys:
            keys.append(key)
    return keys


def _is_restriction_doc(c: Dict) -> bool:
    """Документ вида «арест» — постановление о временном ограничении (у записей до 14.11.2026 — по названию)."""
    if 'ограничение' in c:
        return bool(c['ограничение'])
    title = _norm(f"{c.get('документ') or ''} {c.get('файл') or ''}").lower()
    return _is_restriction_title(title) and not re.search(r'арест', title)


# Банк в пункте без названия банка («в банки второго уровня», «банковские выписки»)
_BANK_WORD = re.compile(r'\bбву\b|банк(?!рот)|\bbank\b|выпис\w*\s+(?:по\s+)?(?:сч[её]т|движени)|движени\w*\s+денежн',
                        re.IGNORECASE)
_BANK_ORGANS = {'kaspi', 'halyk', 'forte', 'jusan', 'bereke', 'bcc', 'freedom', 'eurasian', 'otbasy', 'home'}
# «Вынести постановление о получении банковских выписок … и ходатайствовать о его санкционировании»
_BANK_DECREE = re.compile(r'постановлени\w*\s+о\s+(?:получении|истребовании)\s+[^.;]{0,60}?(?:банковск|выпис)', re.IGNORECASE)
# Изучение первичных материалов: «Проанализировать рапорт об обнаружении сведений …», «Изучить заявление …»
_STUDY_ITEM = re.compile(r'\s*(?:проанализировать|изучить|изучение|анализ)\s+(?:\w+\s+){0,2}?'
                         r'(?:рапорт|заявлени|материал\w*\s+(?:дела|проверки|доследственн)|акт\w*\s+(?:ревизи|проверк))',
                         re.IGNORECASE)
# Анализ движения денег по счетам: «Проанализировать сведения о движении денежных средств …»
_MONEY_ANALYSIS = re.compile(r'\s*(?:проанализировать|провести\s+анализ|анализ)\b', re.IGNORECASE)


def _is_bank_doc(c: Dict) -> bool:
    title = _norm(f"{c.get('документ') or ''} {c.get('файл') or ''}").lower()
    return bool(set(c.get('органы') or []) & _BANK_ORGANS or _BANK_WORD.search(title))


def _addressee_names(text: str) -> List[str]:
    """Названия в кавычках из адресата запроса: «Направить запрос в ТОО «А» о …», «… по запросу из ТОО «А» (…)»."""
    m = re.search(r'(?:запрос\w*\s+в\s+|по\s+запросу\s+из\s+|истребовать\s+из\s+)(.+?)'
                  r'(?:\s+(?:о|об|на|для|в\s+отношении)\s|\s*\(|[.;]|$)', text or '', re.IGNORECASE | re.DOTALL)
    return _quoted_names(m.group(1)) if m else []


def _named_in_title(names: List[str], c: Dict) -> bool:
    """Организация-адресат названа в названии документа («запрос ТОО А», «Ответ с ТОО «А»»)."""
    title = _org_tokens(_norm(f"{c.get('документ') or ''} {c.get('файл') or ''}"))
    return any(_org_tokens(n) and _org_tokens(n) <= title for n in names)


def _status_type(text_lower: str) -> Optional[str]:
    """
    Вид пункта плана для сопоставления с документами дела: _action_type и формулировки, которые он не различает
    (на порядок пунктов и цепочки не влияет — там свой _chain_step).
    """
    t = text_lower.strip()
    if _is_restriction_title(t) and not re.search(r'арест', t):
        return 'ограничение'
    if re.search(r'проверк\w*\s+(?:и\s+уточнени\w*\s+)?показаний', t) and not re.search(r'очн\w*\s+ставк', t):
        return 'проверка_показаний'
    found = _action_type(t)
    if found:
        return found
    if re.match(r'изъять\b', t):
        return 'выемка'
    if re.match(r'истребовать\b', t):
        return 'ответ'
    if _BANK_DECREE.search(t):
        return 'санкция'
    return None


def refused_persons(conducted: List[Dict]) -> Dict[str, str]:
    """
    Лица, отказавшиеся от дачи показаний, — {ключ: ФИО}. Если то же лицо допрошено и без отказа
    (дал показания на другом допросе), отказ не учитывается.
    """
    refused, testified = {}, set()
    for c in conducted or []:
        if c.get('тип') != 'допрос':
            continue
        for key, fio in zip(c.get('лица') or [], c.get('фио') or []):
            if c.get('отказ'):
                refused.setdefault(key, fio)
            else:
                testified.add(key)
    return {k: v for k, v in refused.items() if k not in testified}


def _action_type(text_lower: str) -> Optional[str]:
    """Тип пункта плана в терминах _DOC_TYPES."""
    t = text_lower.strip()
    if re.search(r'очн\w*\s+ставк', t):
        return 'очная_ставка'
    if re.match(r'\s*(?:допросить|провести\s+(?:дополнительн\w+\s+)?допрос|допрос)', t):
        return 'допрос'
    if re.search(r'признан\w*\s+(?:в\s+качестве\s+)?вещественн|вещественн\w+\s+доказательств', t):
        return 'вещдок'
    if re.search(r'назнач\w*[\w\s-]*экспертиз', t):
        return 'назначение_экспертизы'
    if re.search(r'признать[\w\s-]*потерпевш|признании[\w\s-]*потерпевш', t):
        return 'потерпевший'
    if re.search(r'(?:наложить|наложении)\s+(?:арест|ограничени)|арест\w*\s+(?:на\s+)?имуществ', t):
        return 'арест'
    if re.match(r'\s*(?:произвести|провести)?\s*осмотр', t):
        return 'осмотр'
    if re.search(r'мер\w*\s+пресечени', t):
        return 'мера_пресечения'
    if re.search(r'розыск', t):
        return 'розыск'
    # [^;] а не [^.;]: «Признать Захаревича В.Е. подозреваемым» — точки в инициалах
    if re.search(r'признат\w*[^;]{0,80}?подозреваем|признании[^;]{0,40}?подозреваем', t):
        return 'подозреваемый'
    if re.search(r'квалификаци\w*\s+деяни|о\s+квалификации', t):
        return 'квалификация'
    if re.search(r'назнач\w*\s+защитник', t):
        return 'защитник'
    if re.search(r'установ\w*\s+(?:места|местонахожд|фактическ\w+\s+местонахожд|полн\w+\s+анкетн)', t):
        return 'установление'
    # Проверка по базе СИО ПСО — не запрос, не анализ выписок («…выписки, полученные через БД СИО ПСО / из БВУ»)
    if re.search(r'сио\s*п\w*\s*со|сиопсо', t) and not re.search(r'запрос|истребова|выпис|банк|движени\w*\s+денеж|анализ', t):
        return 'сио_псо'
    if re.search(r'выемк|обыск', t):
        if re.search(r'санкционир|ходатайств|постановлени', t):
            return 'санкция'
        if re.match(r'\s*(?:произвести|провести|осуществить)?\s*(?:выемк|обыск)', t):
            return 'обыск' if 'обыск' in t else 'выемка'
    if re.search(r'направить\s+запрос|истребовать\s+сведени|запросить', t):
        return 'ответ'
    return None


# Должность в пункте «Допросить <должность> ТОО «…»»; общие слова (представитель, сотрудник) должность не уточняют
_POSITION_STEMS = ['бухгалтер', 'директор', 'руководител', 'учредител', 'кассир', 'менеджер', 'юрист', 'инженер',
                   'прораб', 'кладовщик', 'водител', 'секретар', 'заместител']
_STAFF_WORD = re.compile(r'представител|сотрудник|работник|должностн\w+\s+лиц|' + '|'.join(_POSITION_STEMS))


def _same_position(text_lower: str, orgs: set, c: Dict) -> bool:
    """
    Пункт «Допросить бухгалтера ТОО «А» …» без ФИО и протокол допроса: в анкетной части протокола место работы —
    та же организация, а если в пункте названа должность — та же должность. Подозреваемых так не закрывают.
    """
    position = c.get('должность') or ''
    head = re.split(r'[«"]', text_lower, maxsplit=1)[0]
    if not position or not orgs or not _STAFF_WORD.search(head) or c.get('роль') in ('подозреваемого', 'обвиняемого'):
        return False
    if not any(_org_tokens(o) and _org_tokens(o) <= _org_tokens(position) for o in orgs):
        return False
    wanted = [s for s in _POSITION_STEMS if s in head]
    return not wanted or any(s in position for s in wanted)


def match_conducted(action_text: str, conducted: List[Dict]) -> Optional[Dict]:
    """
    Находит проведённое действие, соответствующее пункту плана.
    Совпадение засчитывается только по конкретному лицу / организации / виду экспертизы / адресату запроса.
    Общие пункты без конкретики («Произвести осмотр изъятых документов») не отмечаются исполненными.
    """
    if not action_text or not conducted:
        return None
    t = _norm(action_text).lower()
    a_type = _status_type(t)
    if not a_type:
        return None

    mentions = find_person_mentions(normalize_text(_norm(action_text)))
    persons = {p['key'] for p in mentions}
    if a_type == 'допрос' and mentions:
        # Допрашиваемый — первое лицо в пункте. В «Допросить свидетеля Макарову С.Д. о деньгах,
        # полученных от Макарова Р.Ж.» второе лицо — предмет показаний, а не допрашиваемый
        persons = {mentions[0]['key']}
    orgs = set(_quoted_names(action_text))
    role = _action_role(t, mentions) if a_type == 'допрос' else ''
    address = ''
    if a_type in ('обыск', 'выемка', 'санкция') and not persons:
        from core.procedural_validator import _address_key
        address = _address_key(_norm(action_text))

    if a_type == 'ограничение':
        # Постановление о временном ограничении закрывает пункт, когда оно вынесено по всем названным в пункте
        # лицам (один пункт может перечислять нескольких), либо по организации из пункта
        decrees = [c for c in conducted if c.get('тип') == 'арест' and _is_restriction_doc(c)]
        covered = {k for c in decrees for k in c.get('лица') or []}
        for c in decrees:
            if (persons and persons <= covered and persons & set(c.get('лица') or [])) \
                    or (not persons and _orgs_overlap(orgs, c.get('организации'))):
                return c
        return None

    for c in conducted:
        c_type = c.get('тип')
        c_persons = set(c.get('лица') or [])
        # Отказ от дачи показаний не закрывает допрос по существу: пункт плана остаётся открытым
        if c.get('отказ') and a_type == 'допрос':
            continue
        if a_type == 'допрос' and c_type == 'допрос':
            if persons and c_persons:
                # Допрос того же лица; статус (свидетель → подозреваемый) должен совпадать
                if persons & c_persons and (not role or not c.get('роль') or role == c.get('роль')):
                    return c
            elif not persons and role in ('эксперта', 'специалиста', 'представителя потерпевшего', 'потерпевшего') \
                    and role == c.get('роль'):
                return c
            elif not persons and _same_position(t, orgs, c):
                # «Допросить бухгалтера ТОО «А» …» без ФИО — допрошен работник этой организации в этой должности
                return c
        elif a_type == 'очная_ставка' and c_type == 'очная_ставка':
            if len(persons & c_persons) >= 2 or (c_persons and c_persons <= persons):
                return c
        elif a_type == 'проверка_показаний' and c_type == 'проверка_показаний':
            if persons & c_persons:
                return c
        elif a_type == 'арест' and c_type == 'арест' and _is_restriction_doc(c):
            continue  # временное ограничение — не арест: у него свой пункт плана
        elif a_type in ('обыск', 'выемка', 'арест', 'потерпевший') and c_type == a_type:
            if (persons & c_persons) or (_orgs_overlap(orgs, c.get('организации'))):
                return c
            # «Произвести обыск в квартире по адресу: …» без лица — протокол обыска по тому же адресу
            if address and address in (c.get('адреса') or []):
                return c
        elif a_type == 'санкция' and c_type == 'санкция':
            same_kind = ('обыск' in t) == (c.get('роль') == 'обыска')
            if same_kind and ((persons & c_persons) or (_orgs_overlap(orgs, c.get('организации')))
                              or (address and address in (c.get('адреса') or []))):
                return c
        elif a_type == 'назначение_экспертизы' and c_type in ('назначение_экспертизы', 'заключение'):
            if c.get('специалист'):
                continue
            # «Назначить дополнительную/повторную экспертизу» закрывает только такой же документ
            if bool(re.search(r'дополнительн|повторн', t)) != bool(c.get('дополнительная')):
                continue
            kind = _expertise_kind(t)
            if kind and kind == c.get('вид_экспертизы'):
                return c
        elif a_type in ('подозреваемый', 'квалификация', 'мера_пресечения', 'розыск', 'защитник', 'установление') \
                and c_type == a_type:
            # Решение по тому же лицу; если лицо в пункте не названо — по виду решения
            if not persons or not c_persons or persons & c_persons:
                return c
        elif a_type == 'сио_псо' and c_type == 'сио_псо':
            return c
        elif a_type == 'ответ' and c_type in ('ответ', 'запрос'):
            # «Направить запрос» закрывает и отправленный запрос, и ответ; «Истребовать сведения» — только ответ
            if c_type == 'запрос' and re.match(r'\s*истребовать', t):
                continue
            a_organs = set(_organs(t))
            if a_organs & set(c.get('органы') or []):
                return c
            if not a_organs:
                # Адресат не из списка органов: банк без названия («в банки второго уровня», «банковские выписки») —
                # запрос в банк или ответ банка; организация в кавычках — документ с её названием в заголовке
                if _BANK_WORD.search(t):
                    # владелец счёта, если он назван и в пункте, и в документе (в названии — ФИО, в тексте —
                    # организации), должен быть тем же
                    title_persons = {p['key'] for p in _persons_in(_norm(f"{c.get('документ') or ''} {c.get('файл') or ''}"))}
                    if _is_bank_doc(c) and not (persons and title_persons and not (persons & title_persons)) \
                            and not (orgs and c.get('организации') and not _orgs_overlap(orgs, c.get('организации'))):
                        return c
                elif _named_in_title(_addressee_names(action_text), c):
                    return c
        elif a_type in ('осмотр', 'вещдок') and c_type == a_type:
            if (persons & c_persons) or (_orgs_overlap(orgs, c.get('организации'))):
                return c
            # «Произвести осмотр полученных сведений по запросу из КГД …» — протокол осмотра сведений этого органа;
            # «… осмотр полученных банковских выписок …» — протокол осмотра выписок
            if a_type == 'осмотр' and re.search(r'полученн|по\s+запросу|\bответ(?!ств)', t):
                c_organs = set(c.get('органы') or _organs(_norm(f"{c.get('документ') or ''} {c.get('файл') or ''}").lower()))
                if set(_organs(t)) & c_organs or (not _organs(t) and _BANK_WORD.search(t) and _is_bank_doc(c)):
                    return c
            # «Осмотр изъятых документов» / «вещдоки по изъятому» без конкретики: если в деле ровно один
            # такой документ, это он (иначе исполненный пункт остаётся без даты, а документ дублируется)
            same_type = [x for x in conducted if x.get('тип') == a_type]
            if len(same_type) == 1 and re.search(r'изъят|выемк|обыск', t) \
                    and not re.search(r'выпис|банк|полученн|запрос', t):
                return c
    return None


def conducted_action_item(c: Dict, fio_investigator: str) -> Dict:
    """Пункт плана для проведённого действия, которого не было среди сгенерированных."""
    label = _TYPE_LABEL.get(c['тип'], 'Проведено')
    details = ''
    if c['тип'] == 'допрос':
        details = f" {c['роль'] or 'лица'}" + (f": {', '.join(c['фио'])}" if c['фио'] else '')
        if c.get('отказ'):
            details += " (от дачи показаний отказался(-ась))"
    elif c['тип'] == 'очная_ставка' and c['фио']:
        details = f" между {' и '.join(c['фио'])}"
    elif c['тип'] in ('обыск', 'выемка') and c['фио']:
        details = f" у {', '.join(c['фио'])}"
    elif c['тип'] == 'проверка_показаний' and c['фио']:
        details = f" ({', '.join(c['фио'])})"
    elif c['тип'] == 'санкция':
        details = f" {c['роль'] or 'выемки'}" + (f" ({', '.join(c['фио'])})" if c['фио'] else '')
    elif c['тип'] in ('подозреваемый', 'квалификация', 'мера_пресечения', 'розыск', 'защитник',
                      'сио_псо', 'установление') and c['фио']:
        details = f" ({', '.join(c['фио'])})"
    elif c['тип'] == 'заключение' and c.get('специалист'):
        label = 'Получено заключение специалиста'
        kind = re.sub(r'ая$', 'ое', c['вид_экспертизы']) if c['вид_экспертизы'] else ''
        details = f" ({kind} исследование)" if kind else ''
    elif c['тип'] in ('заключение', 'назначение_экспертизы') and c['вид_экспертизы']:
        details = f" ({c['вид_экспертизы']} экспертиза)"
    doc = c.get('документ') or ''
    date = f" от {c['дата']}" if c.get('дата') else ''
    return {
        'действие': (f"{label}{details}" + (f" — {doc}" if doc else '') + date).strip(),
        'цель': 'Действие проведено, результаты закреплены в материалах уголовного дела.',
        'результат': f"{doc or label}{date}",
        'исполнитель': fio_investigator,
        'срок': c.get('дата') or '',
        'статус': STATUS_DONE,
        'основание_статуса': c.get('файл', ''),
    }


def apply_conducted_statuses(actions: List[Dict], conducted: List[Dict], fio_investigator: str,
                             add_missing: bool = True) -> List[Dict]:
    """
    1. Пункты плана, совпавшие с проведёнными действиями, получают статус «Исполнен» и ссылку на документ.
    2. Санкция перед исполненной выемкой/обыском и истребование после исполненного запроса — тоже исполнены.
    3. Проведённые действия, которых в плане нет, добавляются отдельными пунктами со статусом «Исполнен».
    """
    if not conducted:
        return actions
    used = set()
    for act in actions:
        if act.get('статус_заблокирован'):
            continue
        c = match_conducted(act.get('действие', ''), conducted)
        if c is not None:
            act['статус'] = STATUS_DONE
            act['основание_статуса'] = c.get('файл', '')
            # Срок исполненного пункта — фактическая дата по документу. Если дату не прочитать (скан без OCR),
            # срок не указываем: будущий срок, рассчитанный моделью, у исполненного пункта вводит в заблуждение
            act['срок'] = c.get('дата') or ''
            used.add(id(c))
            print(f"  ✅ [Проведено] «{act.get('действие', '')[:70]}» — {c.get('документ')}")

    # Связанные шаги процессуальной цепочки: выемка/обыск проведены → санкция на них получена;
    # ответ на запрос получен → запрос направлен и сведения истребованы
    def _ids(text):
        return ({p['key'] for p in find_person_mentions(normalize_text(_norm(text)))}, set(_quoted_names(text)))

    done = [a for a in actions if a.get('статус') == STATUS_DONE]
    for act in done:
        t = act.get('действие', '').lower()
        a_type = _status_type(t)
        persons, orgs = _ids(act.get('действие', ''))
        organs = set(_organs(t))
        for other in actions:
            if other is act or other.get('статус') == STATUS_DONE or other.get('статус_заблокирован'):
                continue
            o_text = other.get('действие', '')
            o_lower = o_text.lower()
            o_type = _status_type(o_lower)
            linked = False
            if a_type in ('обыск', 'выемка') and o_type == 'санкция' \
                    and ('обыск' in o_lower) == (a_type == 'обыск'):
                o_persons, o_orgs = _ids(o_text)
                linked = bool((persons & o_persons) or _orgs_overlap(orgs, o_orgs))
            elif a_type == 'ответ' and o_type == 'ответ' and organs:
                linked = bool(organs & set(_organs(o_lower)))
            elif a_type == 'ответ' and o_type == 'санкция' and _BANK_DECREE.search(o_lower) and _BANK_WORD.search(t):
                # Банк выдал выписки по счетам тех же лиц (организаций) — постановление о их получении вынесено
                o_persons, o_orgs = _ids(o_text)
                linked = bool((persons & o_persons) or _orgs_overlap(orgs, o_orgs))
            if linked:
                other['статус'] = STATUS_DONE
                other['основание_статуса'] = act.get('основание_статуса', '')
                other['срок'] = ''  # собственного документа нет — точная дата неизвестна

    if add_missing:
        missing = [c for c in conducted if id(c) not in used]
        for c in missing:
            actions.append(conducted_action_item(c, fio_investigator))
        if missing:
            print(f"  ➕ [Проведено] Добавлено в план проведённых действий: {len(missing)}")
    return actions


# Материалы, с которыми дело поступило к следователю или которые он получил от других: заключение, ответ на запрос,
# сведения из баз, рапорт об установлении. Остальные документы — действия и решения самого следователя
_NOT_INVESTIGATOR_STEP = {'заключение', 'ответ', 'сио_псо', 'установление'}
# Решения, в которых круг причастных лиц уже назван
_CIRCLE_FIXED = {'подозреваемый', 'квалификация'}


def _dmy(value):
    m = re.match(r'\s*(\d{1,2})\.(\d{1,2})\.(\d{4})', str(value or ''))
    try:
        return (int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None
    except ValueError:
        return None


def close_analytical_items(actions: List[Dict], conducted: Optional[List[Dict]] = None, registered: str = '') -> List[Dict]:
    """
    Аналитические первые пункты плана не оставляют своего документа, поэтому их исполнение устанавливается
    по косвенным признакам (автор методики, 14.11.2026):
      • «Изучение заявления, рапорта …» — исполнено, когда по делу проведено следственное действие: без изучения
        материалов его не провести. Считаются документы следователя (протокол, постановление, запрос) с датой
        не раньше регистрации в ЕРДР (registered) и исполненные пункты плана — следственные действия;
        заключение, ответы на запросы и сведения из баз, с которыми дело поступило, не считаются;
      • «Определение круга лиц …» — исполнено, когда вынесено постановление о признании подозреваемым или
        о квалификации деяния либо исполнена проверка круга лиц по БД СИО ПСО;
      • «Проанализировать рапорт (заявление, материалы проверки) …» — то же изучение первичных материалов:
        признак тот же, что у «Изучения»;
      • «Проанализировать сведения о движении денежных средств (банковские выписки) …» — исполнено, когда
        составлен протокол осмотра банковских выписок тех же лиц (организаций): анализ фиксируется в нём.
    Пункты, статус которых руководитель закрепил вручную (статус_заблокирован), не трогаются; исполненный пункт
    обратно не открывается. «основание_статуса» — признак, по которому пункт закрыт.
    """
    from core.procedural_validator import _is_mandatory

    def _kind(a):
        t = re.sub(r'^\s*\d+(?:\.\d+)*\.?\s*[).]?\s*', '', a.get('действие', '') or '').strip().lower()
        if not _is_mandatory(a):
            if _MONEY_ANALYSIS.match(t) and _BANK_WORD.search(t):
                return 'анализ_денег'
            return 'изучение' if _STUDY_ITEM.match(t) else None
        return 'изучение' if t.startswith('изучение') else 'круг_лиц' if t.startswith('определение') else 'проверка_круга'

    def _ids(text):
        return ({p['key'] for p in find_person_mentions(normalize_text(_norm(text)))}, set(_quoted_names(text)))

    def _same_holder(a_ids, b_persons, b_orgs):
        # лица (организации) названы с обеих сторон — должны совпадать; с одной стороны не названы — не мешает
        persons, orgs = a_ids
        if persons and b_persons and not (persons & set(b_persons)):
            return False
        return not (orgs and b_orgs and not _orgs_overlap(orgs, b_orgs))

    kinds = {id(a): _kind(a) for a in actions}
    open_items = [a for a in actions if kinds[id(a)] in ('изучение', 'круг_лиц', 'анализ_денег')
                  and a.get('статус') != STATUS_DONE and not a.get('статус_заблокирован')]
    if not open_items:
        return actions

    reg = _dmy(registered)
    steps, circle = [], []  # (дата для сортировки, дата текстом, признак)
    for c in conducted or []:
        day = _dmy(c.get('дата'))
        name = c.get('документ') or _TYPE_LABEL.get(c.get('тип'), 'документ')
        dated = f"{name} от {c['дата']}" if c.get('дата') else name
        if c.get('тип') in _CIRCLE_FIXED:
            circle.append((day or (9999,), c.get('дата') or '', f"круг лиц определён в документе: {dated}"))
        if c.get('тип') == 'сио_псо':
            circle.append((day or (9999,), c.get('дата') or '', f"проведена проверка круга лиц по БД СИО ПСО: {dated}"))
        if c.get('тип') not in _NOT_INVESTIGATOR_STEP and not (reg and (not day or day < reg)):
            steps.append((day or (9999,), c.get('дата') or '', f"проведено следственное действие: {dated}"))
    for a in actions:
        if a.get('статус') != STATUS_DONE:
            continue
        text = a.get('действие', '') or ''
        # «срок» исполненного пункта — дата документа, если пункт закрыт по документу; у закрытого вручную в нём
        # остаётся плановый срок, поэтому дата признака тогда не указывается
        day = _dmy(a.get('срок')) if a.get('основание_статуса') else None
        date_text = a.get('срок') if day else ''
        if kinds[id(a)] == 'проверка_круга':
            circle.append((day or (9999,), date_text, "исполнена проверка круга лиц по БД СИО ПСО"))
        elif kinds[id(a)] is None and _status_type(text.lower()) not in (None, 'сио_псо', 'установление') \
                and not (reg and day and day < reg):
            steps.append((day or (9999,), date_text, f"исполнен пункт плана: {text[:90].rstrip()}"))

    def _statement_inspections(item):
        """Осмотры банковских выписок тех же лиц (организаций): протоколы из дела и исполненные пункты плана."""
        ids, found = _ids(item.get('действие', '') or ''), []
        for c in conducted or []:
            if c.get('тип') == 'осмотр' and _is_bank_doc(c) and _same_holder(ids, c.get('лица'), c.get('организации')):
                name = c.get('документ') or _TYPE_LABEL['осмотр']
                found.append((_dmy(c.get('дата')) or (9999,), c.get('дата') or '',
                              f"осмотрены банковские выписки: {name}" + (f" от {c['дата']}" if c.get('дата') else '')))
        for a in actions:
            text = a.get('действие', '') or ''
            if a.get('статус') == STATUS_DONE and _status_type(text.lower()) == 'осмотр' and _BANK_WORD.search(text) \
                    and _same_holder(ids, *_ids(text)):
                day = _dmy(a.get('срок')) if a.get('основание_статуса') else None
                found.append((day or (9999,), a.get('срок') if day else '', f"исполнен пункт плана: {text[:90].rstrip()}"))
        return found

    for a in open_items:
        kind = kinds[id(a)]
        found = steps if kind == 'изучение' else circle if kind == 'круг_лиц' else _statement_inspections(a)
        if not found:
            continue
        _key, when, basis = min(found, key=lambda x: x[0])
        a['статус'] = STATUS_DONE
        a['основание_статуса'] = f"косвенный признак — {basis}"
        a['срок'] = when  # дата признака; если её нет — срок не указывается, как у других исполненных без даты
        print(f"  ✅ [Косвенно] «{(a.get('действие') or '')[:60]}» — {basis}")
    return actions


def conducted_prompt_block(conducted: List[Dict], limit: int = 40) -> str:
    """Список проведённых действий для промпта LLM."""
    if not conducted:
        return ''
    lines = []
    for c in conducted[:limit]:
        item = conducted_action_item(c, '')
        lines.append(f"  - {item['действие']}")
    return "\n".join(lines)
