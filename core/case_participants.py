# core/case_participants.py
"""
Процессуальный статус участников и проверка ФИО в плане по материалам КОНКРЕТНОГО дела.

1. build_source_corpus      — текст всех материалов дела (рапорт + документы), по которому проверяются ФИО.
2. extract_officials        — должностные лица, которые НЕ являются фигурантами: следователи и руководство
                              ДЭР/АФМ, оперуполномоченные, прокуроры, судьи, адвокаты, эксперты, специалисты.
3. filter_actions_by_participants — убирает пункты плана:
     • с выдуманными ФИО (которых нет в материалах дела);
     • со следственными действиями в отношении сотрудников органа, прокуроров, судей, адвокатов;
     • с допросом эксперта/специалиста «в качестве свидетеля» (исправляет статус).
4. LocalCaseRetriever       — поиск контекста ТОЛЬКО по документам текущего дела (замена общего
                              LightRAG-индекса, в котором смешиваются документы разных дел).
"""

import re
from contextvars import ContextVar
from typing import Dict, List, Optional

_KAZ_TO_RUS = str.maketrans({
    'Ә': 'А', 'ә': 'а', 'Ғ': 'Г', 'ғ': 'г', 'Қ': 'К', 'қ': 'к', 'Ң': 'Н', 'ң': 'н',
    'Ө': 'О', 'ө': 'о', 'Ұ': 'У', 'ұ': 'у', 'Ү': 'У', 'ү': 'у', 'Һ': 'Х', 'һ': 'х',
    'І': 'И', 'і': 'и', 'Ё': 'Е', 'ё': 'е',
})

_UP = 'А-ЯЁӘҒҚҢӨҰҮҺІ'
_LO = 'а-яёәғқңөұүһі'

# «Иванов И.И.», «Иванова А. Б.»
FIO_INITIALS = re.compile(rf'(?<![{_UP}{_LO}])([{_UP}][{_LO}]{{1,}}(?:-[{_UP}][{_LO}]+)?)\s*([{_UP}])\.\s*([{_UP}])\.?')
# «Иванов Иван Иванович», «Нигматов Мирболат Маратович», «Сейтова Алия Абилбековна»
FIO_FULL = re.compile(
    rf'(?<![{_UP}{_LO}])([{_UP}][{_LO}]{{2,}}(?:-[{_UP}][{_LO}]+)?)\s+([{_UP}][{_LO}]{{2,}})\s+'
    # Отчество в любом падеже: «Даировна», «Даировну», «Даировной», «Маратовичем», «Маратовичу».
    # Окончание проверяется целиком, иначе «Банков Второго Уровня» («-овн-я») принимается за ФИО
    rf'([{_UP}][{_LO}]+(?:(?:ович|евич|ьич|ич)(?:а|у|ем|е)?|(?:овн|евн|ичн|иничн)(?:а|ы|е|у|ой)|ұлы|улы|қызы|кызы)'
    rf'(?![{_LO}]))'
)

# Роли, исключающие статус фигуранта
ROLE_PATTERNS = [
    ('сотрудник органа', r'(?:ст(?:арш\w*)?\.?\s*)?следовател\w*|дознавател\w*|оперуполномоченн\w*|'
                         r'тергеуш\w*|жедел\s+уәкіл\w*|'
                         # Отметка ИС ЕРДР: «Документ подготовил и подписал: Жайлаубаев К.К.»
                         r'документ\s+подготовил\s+и\s+подписал\s*:?'),
    ('руководство органа', r'(?:и\.\s?о\.\s*)?(?:заместител\w*\s+)?(?:руководител\w*|начальник\w*)'
                           # «Департамент» — только органа расследования: руководитель Департамента внутреннего
                           # государственного аудита (ДВГА), госдоходов, полиции — не руководство органа по делу
                           r'(?=(?:[^.;]|\.(?=\S)){0,80}(?:ДЭР|Департамент\w*\s+(?:экономическ|Агентств|по\s+противодейств)|'
                           r'СУ\b|следственн\w+\s+управлени|АФМ|Агентств\w*\s+(?:Республики\s+Казахстан\s+)?по\s+(?:финансов|противодейств)|'
                           r'экономических\s+расследований|ЭРД|ЭТД))'),
    ('прокурор', r'(?:помощник\w*\s+|заместител\w*\s+)?прокурор\w*'),
    ('судья', r'(?:следственн\w+\s+)?судь\w*|судь[ия]'),
    ('защитник', r'адвокат\w*|защитник\w*|қорғаушы\w*'),
    ('эксперт', r'эксперт(?!из)\w*|сарапшы\w*'),
    # «Специалист» — только процессуальный (давший заключение / привлечённый), а не должность в компании
    ('специалист', r'(?:заключени\w*\s+|привлеч\w*\s+(?:в\s+качестве\s+)?)специалист\w*|'
                   r'специалист\w*(?=[^.;]{0,60}(?:заключени|исследовани|ДВГА|аудит|ревизи))|'
                   r'аудитор\w*|ревизор\w*|оценщик\w*'),
]

_FIO_INLINE = (rf'[{_UP}][{_LO}]{{1,}}(?:-[{_UP}][{_LO}]+)?\s+(?:[{_UP}][{_LO}]{{2,}}\s+[{_UP}][{_LO}]+|'
               rf'[{_UP}]\.\s*[{_UP}]\.?)')

# Смешение кириллицы и латиницы внутри слова («Даulet») — признак искажённого/выдуманного ФИО
_MIXED_SCRIPT = re.compile(r'[А-Яа-яЁё][A-Za-z]|[A-Za-z][А-Яа-яЁё]')

# Роль перед ФИО: «Старший следователь СУ Департамента … по⏎области Жайлаубаев К.К.»
# В документах ЕРДР шапка разбита переносами строк, поэтому одиночный перенос фразу не обрывает;
# точка внутри сокращений («г.Астана», «и.о.») — тоже. Обрывают: конец фразы, пустая строка и слова,
# после которых идёт уже ДРУГОЕ лицо («Старшему следователю … от Инкербаевой», «следователь … рассмотрев
# материалы в отношении Макарова»).
_ROLE_BEFORE_GAP = (r'(?:(?!\bот\b|рассмотр|допросил|разъяснил|в\s+отношении|произв|подозрева|свидетел|'
                    r'потерпевш|представител|специалист|эксперт)(?:[^.;:?\n]|\.(?=\S)|\n(?!\s*\n))){0,140}?')
# «:» и «?» тоже обрывают: «Вопрос следователя: Поясните, у кого находилась ЭЦП … Инкербаевой А.Б.?»
# ФИО перед ролью: «Иванов И.И., старший следователь» — только в той же строке и через запятую/тире
# (подпись заявителя, после которой идёт «Заявление принял: Старший следователь …», не считается)
_ROLE_AFTER_GAP = r'[ \t]*[,–—-][ \t]*(?:[^.;\n]{0,25}?[ \t])?'

# Маркеры подозреваемого — такой человек остаётся фигурантом, даже если имеет должность.
# ФИО должно идти сразу за маркером (не дальше одной фразы и без «:» — «Отношение к подозреваемому: ---»)
_SUSPECT_MARKERS = r'(?<!\bк\s)(?:подозреваем\w*|обвиняем\w*|в\s+отношении|привлеч\w+\s+в\s+качестве\s+подозреваем\w*|' \
                   r'признан\w*\s+подозреваем\w*|свидетел\w+,\s+имеющ\w+\s+право\s+на\s+защиту)'
# Промежуток не переходит через должность и дату: «об объявлении в розыск подозреваемого «15» октября …
# Заместитель руководителя … Кулман С.К.» — Кулман не подозреваемый
_SUSPECT_GAP = (r'(?:(?!(?i:заместител|руководител|следовател|начальник|прокурор|судь|тергеуш)|«\s*\d)'
                r'(?:[^.;:\n]|\.(?=\S)|\n(?!\s*\n))){0,90}?')


def normalize_text(text: str) -> str:
    return (text or '').translate(_KAZ_TO_RUS)


def surname_stem(surname: str) -> str:
    """Основа фамилии без падежного окончания: Нигматовым -> нигматов, Рахимбаевой -> рахимбаев."""
    s = normalize_text(surname).lower()
    for end in ('ыми', 'ими', 'ого', 'ому', 'ой', 'ым', 'им', 'ом', 'ей', 'ую', 'ая', 'ы', 'у', 'а', 'е', 'ю', 'я'):
        if s.endswith(end) and len(s) - len(end) >= 4:
            return s[:-len(end)]
    return s


def _person_key(surname: str, initial: str) -> str:
    return f"{surname_stem(surname)}|{normalize_text(initial).upper()}"


# Слова, которые стоят перед инициалами подписанта, но фамилиями не являются:
# «Республики Казахстан А.М. Манасиден», «по городу Астана А.А. Иванов»
# Сравнение по основе слова целиком: «Судаков» или «Центров» — фамилии, «Суд», «Центр» — нет
_NOT_SURNAME_WORDS = (
    'казахстан астана алматы шымкент караганда павлодар актобе атырау актау костанай кызылорда тараз туркестан '
    'уральск петропавловск кокшетау талдыкорган жезказган семей экибастуз темиртау республика область район город '
    'департамент управление министерство комитет агентство институт центр филиал отдел суд прокуратура подпись '
    'дата приложение')


def _stem_set(words: str) -> set:
    return {surname_stem(w) for w in words.split()}


# Фамилия прописными перед инициалами: «КУЛМАН С.К.» -> «Кулман С.К.» (длина и позиции не меняются)
_UPPER_SURNAME = re.compile(rf'(?<![{_UP}{_LO}])([{_UP}]{{3,}})(?=\s+[{_UP}]\.)')


def find_person_mentions(text: str) -> List[Dict]:
    """Все упоминания ФИО в тексте: [{fio, surname, initial, key, start, end}]."""
    global _NOT_SURNAME
    if _NOT_SURNAME is None:
        _NOT_SURNAME = _stem_set(_NOT_SURNAME_WORDS)
    text = _UPPER_SURNAME.sub(lambda m: m.group(1).capitalize(), text or '')
    return [r for r in _find_person_mentions(text) if surname_stem(r['surname']) not in _NOT_SURNAME]


_NOT_SURNAME = None

# «Серікқызы К.», «Акбар А.» — фамилия и один инициал (у казахских ФИО отчества часто нет).
# Само по себе это не ФИО («Республики К.», «Приложение А.»), поэтому принимается, только если такое лицо
# есть в материалах ТЕКУЩЕГО дела (set_case_persons): список лиц строится по документам каждого дела заново
FIO_ONE_INITIAL = re.compile(rf'(?<![{_UP}{_LO}])([{_UP}][{_LO}]{{2,}}(?:-[{_UP}][{_LO}]+)?)\s+([{_UP}])\.(?!\s*[{_UP}]\.)')
_CASE_PERSONS: ContextVar[frozenset] = ContextVar('case_persons', default=frozenset())


def set_case_persons(keys) -> None:
    """Лица текущего дела (ключи «фамилия|инициал»), по которым распознаются ФИО с одним инициалом."""
    _CASE_PERSONS.set(frozenset(keys or ()))


def case_person_keys(corpus: str = '', participants: Optional[List[Dict]] = None,
                     conducted: Optional[List[Dict]] = None, figurants: str = '') -> set:
    """
    Все лица дела по его материалам: ФИО в документах (полные и с двумя инициалами), фигуранты,
    допрошенные по протоколам (в т.ч. без отчества — «Серікқызы Кәмила»), должностные лица и иные участники.
    """
    keys = {p['key'] for p in _find_person_mentions(normalize_text(corpus or ''))}
    keys |= {p['key'] for p in _find_person_mentions(normalize_text(figurants or ''))}
    keys |= {o['key'] for o in participants or [] if o.get('key')}
    keys |= {k for c in conducted or [] for k in c.get('лица') or []}
    return keys


def _find_person_mentions(text: str) -> List[Dict]:
    res = []
    for m in FIO_FULL.finditer(text or ''):
        res.append({'fio': m.group(0), 'surname': m.group(1), 'initial': m.group(2)[0],
                    'first': m.group(2), 'patronymic': m.group(3),
                    'key': _person_key(m.group(1), m.group(2)[0]), 'start': m.start(), 'end': m.end()})
    for m in FIO_INITIALS.finditer(text or ''):
        if any(r['start'] <= m.start() < r['end'] for r in res):
            continue
        res.append({'fio': m.group(0), 'surname': m.group(1), 'initial': m.group(2),
                    'first': None, 'patronymic': None,
                    'key': _person_key(m.group(1), m.group(2)), 'start': m.start(), 'end': m.end()})
    known = _CASE_PERSONS.get()
    if known:
        for m in FIO_ONE_INITIAL.finditer(text or ''):
            key = _person_key(m.group(1), m.group(2))
            if key in known and not any(r['start'] <= m.start() < r['end'] for r in res):
                res.append({'fio': m.group(0), 'surname': m.group(1), 'initial': m.group(2),
                            'first': None, 'patronymic': None, 'key': key, 'start': m.start(), 'end': m.end()})
    res.sort(key=lambda r: r['start'])
    return res


def build_source_corpus(case_data: Optional[Dict] = None, case_docs: Optional[List[Dict]] = None,
                        extra_texts: Optional[List[str]] = None) -> str:
    """Текст всех материалов дела, доступных системе (для проверки ФИО и статусов)."""
    parts = []
    if case_data:
        for key in ('текст_рапорта', 'суть_правонарушения', 'фабула_для_заголовка', 'фигуранты',
                    'адреса_фигурантов', 'реальное_название_тоо', 'фио_следователя'):
            val = case_data.get(key)
            if val and isinstance(val, str) and val != 'Н/Д':
                parts.append(val)
    for doc in case_docs or []:
        if doc.get('text'):
            parts.append(doc['text'])
    for t in extra_texts or []:
        if t:
            parts.append(t)
    return normalize_text('\n\n'.join(parts))


class PersonIndex:
    """Индекс людей, упомянутых в материалах дела: фамилия-основа -> набор инициалов имени."""

    def __init__(self, corpus: str):
        self.by_stem: Dict[str, set] = {}
        self.full_names: set = set()
        for p in find_person_mentions(corpus):
            stem = surname_stem(p['surname'])
            self.by_stem.setdefault(stem, set()).add(normalize_text(p['initial']).upper())
            if p['first']:
                self.full_names.add((stem, normalize_text(p['first']).lower()[:4]))
        # Фамилия может встречаться и без инициалов («гр. Нигматов пояснил…»)
        self.words = set(re.findall(rf'[{_LO}]{{4,}}', corpus.lower()))

    def is_known(self, mention: Dict) -> bool:
        stem = surname_stem(mention['surname'])
        initial = normalize_text(mention['initial']).upper()
        initials = self.by_stem.get(stem)
        if initials is not None:
            if initial not in initials:
                return False
            if mention.get('first'):
                first4 = normalize_text(mention['first']).lower()[:4]
                has_full = any(s == stem for s, _ in self.full_names)
                # Если в деле полное имя есть — оно должно совпадать с именем в плане
                if has_full and (stem, first4) not in self.full_names:
                    return False
            return True
        # ФИО в деле не найдено ни в одной форме с инициалами — допускаем только фамилию, стоящую в тексте
        return any(w.startswith(stem) for w in self.words) and not mention.get('first')


def extract_officials(corpus: str, investigator_fio: str = '') -> List[Dict]:
    """
    Находит должностных лиц (не фигурантов) в материалах дела.
    Возвращает [{fio, key, role}]. Лица с маркерами подозреваемого не включаются.
    """
    corpus = normalize_text(corpus)
    officials: Dict[str, Dict] = {}

    def _add(fio: str, surname: str, initial: str, role: str):
        key = _person_key(surname, initial)
        if key not in officials:
            officials[key] = {'fio': re.sub(r'\s+', ' ', fio).strip(), 'key': key, 'role': role}

    mentions_all = find_person_mentions(corpus)
    for role, pat in ROLE_PATTERNS:
        # Роль перед ФИО: «Старший следователь СУ ДЭР по г. Астана Кабденов Б.Н.»
        # Регистр игнорируется только для роли: ФИО обязано начинаться с заглавной («по г.Астана» — не ФИО)
        before = re.compile(rf'(?<![{_LO}])(?i:(?:{pat}){_ROLE_BEFORE_GAP})(?={_FIO_INLINE})')
        for m in before.finditer(corpus):
            mentions = find_person_mentions(corpus[m.end():m.end() + 80])
            if mentions and mentions[0]['start'] == 0:
                p = mentions[0]
                _add(p['fio'], p['surname'], p['initial'], role)
        # ФИО перед ролью: «Кабденов Б.Н., старший следователь»
        for p in mentions_all:
            tail = corpus[p['end']:p['end'] + 45]
            if re.match(rf'{_ROLE_AFTER_GAP}(?:{pat})', tail, re.IGNORECASE):
                _add(p['fio'], p['surname'], p['initial'], role)

    investigator_keys = set()
    if investigator_fio and investigator_fio != 'Н/Д':
        for p in find_person_mentions(normalize_text(investigator_fio)):
            officials[p['key']] = {'fio': re.sub(r'\s+', ' ', p['fio']).strip(), 'key': p['key'],
                                   'role': 'сотрудник органа'}
            investigator_keys.add(p['key'])

    # Исключаем тех, кто в материалах прямо назван подозреваемым/лицом «в отношении»;
    # следователь, ведущий дело, подозреваемым не бывает
    suspects = find_suspect_keys(corpus) - investigator_keys
    return [o for k, o in officials.items() if k not in suspects]


def find_suspect_keys(corpus: str, strong_only: bool = False) -> set:
    """
    Лица, прямо названные в материалах подозреваемыми / «в отношении» которых ведётся расследование.
    «В отношении» засчитывается только в уголовно-процессуальном контексте: «выговор в отношении
    руководителя Инкербаевой А.Б.» — дисциплинарная мера, а не подозрение.
    strong_only=True — только явные «подозреваемый/обвиняемый/свидетель, имеющий право на защиту».
    """
    corpus = normalize_text(corpus)
    suspects = set()
    suspect_re = re.compile(rf'(?i:{_SUSPECT_MARKERS}{_SUSPECT_GAP})(?={_FIO_INLINE})')
    for m in suspect_re.finditer(corpus):
        marker = m.group(0).lower()
        # «в отношении подозреваемого Петрова П.П.» — уже явный маркер, контекст не нужен
        if re.match(r'в\s+отношении', marker) and not re.search(r'подозреваем|обвиняем', marker):
            if strong_only:
                continue
            before = corpus[max(0, m.start() - 150):m.start()].lower()
            if not re.search(r'расследован|ердр|уголовн|досудебн|возбужд|зарегистр|подозрен|правонарушени', before):
                continue
        # «в отношении Иванова И.И. и Петрова П.П.» — все лица перечня подряд, а не только первое
        # (перечень потерпевших — нет: «в отношении потерпевших …» не делает их подозреваемыми)
        tail, pos = corpus[m.end():m.end() + 200], 0
        for p in find_person_mentions(tail):
            if p['start'] != pos:
                break
            suspects.add(p['key'])
            sep = re.match(r'\s*(?:,|\bи\b|\bжәне\b)\s*', tail[p['end']:])
            if not sep or 'потерпевш' in marker:
                break
            pos = p['end'] + sep.end()
    # «Признать Петрова Петра Петровича подозреваемым» — ФИО перед словом «подозреваемым»
    for m in re.finditer(rf'(?i:признат\w*|признан\w*)\s+(?={_FIO_INLINE})', corpus):
        for p in find_person_mentions(corpus[m.end():m.end() + 80])[:1]:
            if p['start'] == 0 and re.match(r'[^.;\n]{0,40}?(?i:подозреваем|обвиняем)', corpus[m.end() + p['end']:m.end() + p['end'] + 60]):
                suspects.add(p['key'])
    return suspects


# Процессуальный статус по проведённым действиям → роль «иного участника» (не фигуранта)
_CONDUCTED_ROLE = {
    'потерпевшего': 'потерпевший',
    'представителя потерпевшего': 'представитель потерпевшего',
    'свидетеля': 'свидетель',
    'специалиста': 'специалист',
    'эксперта': 'эксперт',
}


COMPLETED_CASE_ROLE = 'лицо, уголовное дело в отношении которого окончено (выделено)'
_FIO_LIST = (rf'(?:[{_UP}][{_LO}]+(?:-[{_UP}][{_LO}]+)?\s+[{_UP}]\.\s?(?:[{_UP}]\.?)?'
             rf'(?:\s*(?:,|\bи\b|\bжәне\b)\s*)?)+')
_COMPLETED_CASE = [
    # «уголовное дело в отношении Беленцовой Е.В. и Мергенбаева Д.А. завершено и передано для ознакомления»
    re.compile(r'дел\w*\s+в\s+отношении\s+(?P<who>[^;\n]{3,160}?)\s+(?:было\s+|был\w*\s+)?'
               r'(?:заверш|окончен|направлен\w*\s+(?:в\s+суд|прокурор)|прекращен)', re.IGNORECASE),
    # «Беленцова Е.В. және Мергенбаев Д.А.-ға қатысты қылмыстық іс аяқталып»
    re.compile(rf'(?P<who>{_FIO_LIST})\s*-?\s*(?:ға|ге|қа|ке|на|не)?\s+қатысты\s+қылмыстық\s+іс\s+'
               rf'(?:аяқтал|сотқа\s+жіберіл|тоқтатыл)', re.IGNORECASE),
]


def completed_case_persons(corpus: str) -> List[Dict]:
    """
    Лица, уголовное дело в отношении которых окончено, направлено в суд или прекращено (в выделенном деле —
    соучастники по основному делу). В этом деле они не фигуранты: арест их имущества, обыски, проверка счетов
    в плане недопустимы; допрос возможен.
    """
    result = {}
    for pat in _COMPLETED_CASE:
        # Казахский шаблон — по исходному тексту (normalize_text заменяет қ, ә, і и т.д.)
        for m in pat.finditer(corpus or ''):
            for p in find_person_mentions(normalize_text(m.group('who'))):
                result.setdefault(p['key'], {'fio': p['fio'], 'key': p['key'], 'role': COMPLETED_CASE_ROLE})
    return list(result.values())


# «УВЕДОМЛЕНИЕ О ЗАЩИТЕ (ПРЕДСТАВИТЕЛЬСТВЕ) … Адвокат Рахимгалиев Жасулан Маратбекович … Беков Ерболат
# Нурлыбекулы 27.09.1995 г.р. (фамилия, имя, отчество … физического лица, подлежащего защите …)»
_DEFENSE_NOTICE = re.compile(r'УВЕДОМЛЕНИЕ\s+О\s+ЗАЩИТЕ', re.IGNORECASE)
# «(фамилия, имя, отчество (при его наличии) физического лица, подлежащего защите» — со вложенной скобкой
_DEFENSE_CLIENT = re.compile(r'([^\n]{5,120})\n\s*\(\s*фамилия,\s*имя,\s*отчество.{0,80}?подлежащего\s+защите',
                             re.IGNORECASE | re.DOTALL)
_DEFENSE_LAWYER = re.compile(r'Адвокат\s+([А-ЯЁ][а-яё]+(?:\s+[А-ЯЁ][а-яё]+){1,2})')


def defense_notices(corpus: str) -> List[Dict]:
    """Уведомления адвоката о защите: [{'lawyer': ФИО, 'client': ФИО, 'key': ключ подзащитного}]."""
    text = corpus or ''
    starts = [m.start() for m in _DEFENSE_NOTICE.finditer(text)]
    result, seen = [], set()
    for i, st in enumerate(starts):
        block = text[st: starts[i + 1] if i + 1 < len(starts) else st + 2500]
        client_m = _DEFENSE_CLIENT.search(block)
        if not client_m:
            continue
        client_line = re.sub(r'\s+\d{1,2}\.\d{1,2}\.\d{4}.*$', '', client_m.group(1)).strip(' ,')
        mentions = find_person_mentions(normalize_text(client_line))
        if not mentions or mentions[0]['key'] in seen:
            continue
        seen.add(mentions[0]['key'])
        lawyer_m = _DEFENSE_LAWYER.search(block)
        result.append({'lawyer': lawyer_m.group(1) if lawyer_m else '', 'client': client_line,
                       'key': mentions[0]['key']})
    return result


def defended_persons(corpus: str) -> List[Dict]:
    return [{'key': n['key'], 'fio': n['client']} for n in defense_notices(corpus)]


def add_defended_to_figurants(figurants: str, corpus: str) -> str:
    """Подзащитные из уведомлений о защите, которых нет среди фигурантов, добавляются к ним (в форме «Фамилия И.О.»)."""
    current = figurants if figurants and figurants != 'Н/Д' else ''
    known = {m['key'] for m in find_person_mentions(normalize_text(current))}
    added = []
    for n in defense_notices(corpus):
        if n['key'] in known:
            continue
        parts = n['client'].split()
        short = f"{parts[0]} {parts[1][0]}." + (f"{parts[2][0]}." if len(parts) > 2 else '') if len(parts) >= 2 else n['client']
        added.append(short)
        known.add(n['key'])
        print(f"  ⚖ [Участники] В фигуранты добавлен подзащитный ({n['lawyer'] or 'адвокат'}): {short}")
    return ', '.join(p for p in [current] + added if p) or 'Н/Д'


def other_participants(conducted: List[Dict], corpus: str, officials: Optional[List[Dict]] = None) -> List[Dict]:
    """
    Потерпевшие, их представители, свидетели, специалисты — по протоколам и постановлениям дела.
    Они не фигуранты: запросы об их счетах, имуществе и кредитах, обыски у них в плане недопустимы.
    Лица, прямо названные подозреваемыми (свидетель, позже ставший подозреваемым), сюда не попадают.
    """
    # Лицо, которому в деле назначен защитник (уведомление о защите), — фигурант, а не свидетель:
    # даже допрошенное «в качестве свидетеля», оно свидетель, имеющий право на защиту
    defended = {p['key'] for p in defended_persons(corpus)}
    suspects = find_suspect_keys(corpus) | defended
    # Для признанных потерпевшими — только явный маркер «подозреваемый»: косвенное «в отношении»
    # не должно превращать потерпевшего в фигуранта
    strong_suspects = find_suspect_keys(corpus, strong_only=True) | defended
    known = {o['key'] for o in (officials or [])}
    result: Dict[str, Dict] = {}
    for c in conducted or []:
        if c.get('тип') == 'потерпевший':
            role = 'представитель потерпевшего'
        elif c.get('тип') == 'допрос':
            role = _CONDUCTED_ROLE.get(c.get('роль') or '')
        else:
            role = None
        if not role:
            continue
        blocking = strong_suspects if 'потерпевш' in role else suspects
        for key, fio in zip(c.get('лица') or [], c.get('фио') or []):
            if key in blocking or key in known:
                continue
            if key in result:
                # Потерпевший важнее свидетеля: статус не понижаем
                if 'потерпевш' in role and 'потерпевш' not in result[key]['role']:
                    result[key]['role'] = role
                continue
            result[key] = {'fio': fio, 'key': key, 'role': role}
    return list(result.values())


# «Николаева Э.Д. и другие лица» — «другие лица» не фигурант: план не может планировать допрос «других лиц»
_GENERIC_FIGURANT = re.compile(r'^(?:и\s+)?(?:другие|иные|прочие|неустановленные|не\s*установленные|третьи)\s+лиц[аоы]?$'
                               r'|^(?:и\s+)?другие$|^басқа\s+(?:да\s+)?тұлғалар$', re.IGNORECASE)


def nominative_figurants(figurants: str, corpus: str) -> str:
    """
    «Орловой А.Е.» -> «Орлова А.Е.», «Макарова Р.Ж.» -> «Макаров Р.Ж.»: фигурант пишется в
    именительном падеже. Замена делается, только если такая форма того же лица есть в материалах дела.
    """
    if not figurants or figurants == 'Н/Д':
        return figurants
    corpus_n = normalize_text(corpus or '')
    parts = []
    for part in re.split(r'\s*,\s*', figurants):
        m = re.match(rf'^\s*([{_UP}][{_LO}]+?)(ой|ою|ого|ому|ым|ом|у|а)?(\s+[{_UP}]\.\s?[{_UP}]?\.?)\s*$', part)
        if not m or not m.group(2):
            parts.append(part)
            continue
        stem, ending, initials = m.groups()
        # кандидаты именительного падежа: «-ой» -> «-а»; «-а/-у/-ым/-ом/-ого/-ому» -> без окончания
        candidates = [stem + 'а'] if ending in ('ой', 'ою') else [stem]
        replaced = part
        for cand in candidates:
            if re.search(rf'(?<![{_LO}]){re.escape(normalize_text(cand))}{re.escape(normalize_text(initials))}',
                         corpus_n):
                replaced = cand + initials
                break
        if replaced != part:
            print(f"  ⚖ [Участники] Фигурант в именительном падеже: {part} -> {replaced}")
        parts.append(replaced)
    return ', '.join(parts)


def remove_officials_from_figurants(figurants: str, officials: List[Dict]) -> str:
    """Убирает из строки фигурантов должностных лиц, иных участников (потерпевших, свидетелей) и «других лиц»."""
    if not figurants or figurants == 'Н/Д':
        return figurants
    roles = {o['key']: o.get('role', '') for o in officials or []}
    kept = []
    for part in re.split(r'\s*[,;]\s*', figurants):
        if _GENERIC_FIGURANT.match(part.strip().rstrip('.')):
            print(f"  🚫 [Участники] Из фигурантов исключено обобщение: {part}")
            continue
        mentions = find_person_mentions(normalize_text(part))
        if mentions and mentions[0]['key'] in roles:
            print(f"  🚫 [Участники] Из фигурантов исключено лицо ({roles[mentions[0]['key']]}): {part}")
            continue
        kept.append(part)
    return ', '.join(kept) if kept else 'Н/Д'


def gender_by_corpus(fio: str, corpus: str) -> Optional[str]:
    """
    Пол по полному ФИО из материалов дела: «Инкербаева А.Б.» -> «Инкербаева Алия Бекболатовна» -> женский.
    По одной фамилии с инициалами пол не определить: «Макарова Р.Ж.» — это и родительный падеж мужской фамилии.
    """
    mentions = find_person_mentions(normalize_text(fio))
    if not mentions:
        return None
    key = mentions[0]['key']
    for p in find_person_mentions(normalize_text(corpus)):
        if p['key'] == key and p.get('patronymic'):
            patr = p['patronymic'].lower()
            if re.search(r'(?:овн|евн|ичн|қызы|кызы)', patr):
                return 'женский'
            if re.search(r'(?:ович|евич|ич|ұлы|улы)', patr):
                return 'мужской'
    return _gender_by_surname_forms(fio, corpus)


def _gender_by_surname_forms(fio: str, corpus: str) -> Optional[str]:
    """
    Без отчества пол виден по падежным формам фамилии в материалах: «принадлежит Николаевой Э.Д.» — только женская
    форма; «Макаровым Р.Ж.», «Макаров Р.Ж. показал» — только мужская. «Макарова Р.Ж.» неоднозначна.
    """
    m = re.match(r'\s*([А-ЯЁӘҒҚҢӨҰҮҺІ][а-яёәғқңөұүһі]+?)(?:а|ой|у|ым|ом|е)?\s+([А-ЯЁ])\.\s*([А-ЯЁ])?', fio or '')
    if not m:
        return None
    word = re.match(r'\s*(\S+)', fio).group(1)
    stem = re.sub(r'(?:ова|ева|ёва|ина|ына|ов|ев|ёв|ин|ын)(?:а|ой|у|ым|ом|е)?$', '', word)
    suffix = re.search(r'(ов|ев|ёв|ин|ын)(?:а|ой|у|ым|ом|е)?$', word)
    if not suffix or len(stem) < 2:
        return None
    base = stem + suffix.group(1)                     # «Толеген» + «ов»
    initial = m.group(2)
    tail = rf'\s+{initial}\.'                         # фамилия рядом с инициалом того же лица
    if re.search(rf'\b{base}ой{tail}', corpus or ''):
        return 'женский'
    if re.search(rf'\b{base}(?:ым)?{tail}', corpus or ''):
        return 'мужской'
    return None


def officials_prompt_block(officials: List[Dict]) -> str:
    if not officials:
        return ''
    lines = [f"  - {o['fio']} — {o['role']}" for o in officials[:40]]
    return "\n".join(lines)


# Действия, для которых участие защитника допустимо («допросить подозреваемого с участием защитника»)
_WITH_PARTICIPATION = re.compile(r'(?:с\s+участием|при\s+участии|в\s+присутствии)\s+(?:\w+\s+){0,2}$', re.IGNORECASE)

# Следственные действия в отношении сотрудников органа без указания ФИО
_STAFF_TARGET = re.compile(
    r'(?:допрос\w*|очн\w+\s+ставк\w*|обыск\w*|выемк\w*|опрос\w*)[^.;]{0,60}?'
    r'(?:сотрудник\w*\s+(?:ДЭР|АФМ|Департамент\w*|Агентств\w*|следственн\w*|полици\w*)|'
    r'оперуполномоченн\w*|следовател\w*|дознавател\w*|руководител\w*\s+(?:ДЭР|СУ|Департамент\w*|следственн\w*))',
    re.IGNORECASE)


def _mention_role(mention: Dict, officials_by_key: Dict[str, Dict]) -> Optional[str]:
    o = officials_by_key.get(mention['key'])
    return o['role'] if o else None


def filter_actions_by_participants(actions: List[Dict], corpus: str,
                                   officials: Optional[List[Dict]] = None) -> List[Dict]:
    """
    Проверяет каждого человека, упомянутого в пункте плана:
      • ФИО нет в материалах дела → пункт удаляется (галлюцинация);
      • сотрудник органа / прокурор / судья → пункт удаляется;
      • адвокат/защитник как объект действия → пункт удаляется (участие «с участием защитника» допустимо);
      • эксперт/специалист допрашивается «в качестве свидетеля» → статус исправляется,
        принудительные действия в отношении него → пункт удаляется.
    """
    if not actions:
        return actions
    index = PersonIndex(normalize_text(corpus)) if corpus and corpus.strip() else None
    officials_by_key = {o['key']: o for o in (officials or [])}
    result = []

    for act in actions:
        text = act.get('действие', '') or ''
        norm = normalize_text(text)
        drop_reason = None

        if _STAFF_TARGET.search(norm):
            drop_reason = 'следственное действие в отношении сотрудников органа расследования'
        elif _MIXED_SCRIPT.search(re.sub(r'«[^»]*»|"[^"]*"', '', norm)):
            drop_reason = 'искажённое ФИО (смешение кириллицы и латиницы)'

        for p in find_person_mentions(norm):
            if drop_reason:
                break
            role = _mention_role(p, officials_by_key)
            if role in ('сотрудник органа', 'руководство органа', 'прокурор', 'судья'):
                drop_reason = f"{p['fio']} — {role}, ведёт дело или надзирает за ним, не фигурант"
            elif role == 'защитник':
                if not _WITH_PARTICIPATION.search(norm[:p['start']]):
                    drop_reason = f"{p['fio']} — защитник/адвокат"
            elif role in ('потерпевший', 'представитель потерпевшего', 'свидетель', COMPLETED_CASE_ROLE):
                # Допрос, очная ставка, выемка документов у представителя организации — допустимы.
                # Проверка счетов, кредитов, имущества и обыск — только в отношении подозреваемых
                # Допрос, очная ставка, выемка у представителя организации, осмотр изъятого у него,
                # ознакомление с результатами — законные действия с потерпевшим/свидетелем
                is_testimony = re.match(r'\s*(?:допрос|провести\s+(?:дополнительн\w+\s+)?допрос|провести\s+очн|'
                                        r'произвести\s+выемк|выемк|ознаком)', norm, re.IGNORECASE) \
                    or re.search(r'осмотр\w*[^.;]{0,120}изъят', norm, re.IGNORECASE)
                # \b: «счёт», но не «рас-счёт-но-платёжные ведомости»
                if not is_testimony and re.search(
                        r'обыск|выпис|\bсч[её]т|кредит|имуществ|недвижим|автотранспорт|сио\s*псо|\bарест|'
                        r'залог|движени\w*\s+денежн|доход', norm, re.IGNORECASE):
                    others = [q for q in find_person_mentions(norm)
                              if q['key'] != p['key'] and q['key'] not in officials_by_key]
                    stripped = re.sub(rf'\s*(?:,|\bи\b)\s*{re.escape(p["fio"])}|{re.escape(p["fio"])}\s*(?:,|\bи\b)\s*',
                                      ' ', text).strip()
                    if others and stripped != text:
                        # «… о кредитах Макарова Р.Ж. и Инкербаевой А.Б.» — оставляем только подозреваемого
                        print(f"  ⚖ [Участники] Из пункта убран {role} {p['fio']}: {text[:80]}...")
                        act['действие'] = text = re.sub(r'\s{2,}', ' ', stripped)
                        norm = normalize_text(text)
                    else:
                        drop_reason = (f"{p['fio']} — {role}, а не подозреваемый: "
                                       f"проверка счетов/имущества и обыск недопустимы")
            elif role in ('эксперт', 'специалист'):
                if re.search(r'обыск|выемк|арест|очн\w*\s+ставк|изъят', norm, re.IGNORECASE):
                    drop_reason = f"принудительное действие в отношении лица со статусом «{role}» ({p['fio']})"
                else:
                    fixed = re.sub(r'(допрос\w*\s+)(?:в\s+качестве\s+)?свидетел\w*', rf'\1{role}а ', text,
                                   count=1, flags=re.IGNORECASE)
                    fixed = re.sub(r'в\s+качестве\s+свидетеля', f'в качестве {role}а', fixed, flags=re.IGNORECASE)
                    fixed = re.sub(r'\s+,', ',', re.sub(r'\s{2,}', ' ', fixed))
                    if fixed != text:
                        print(f"  ⚖ [Участники] Статус {p['fio']} исправлен на «{role}»")
                        act['действие'] = text = fixed
            elif index is not None and not index.is_known(p):
                drop_reason = f"лицо «{p['fio']}» не фигурирует в материалах дела"

        if drop_reason:
            print(f"  ⛔ [Участники] Удалён пункт ({drop_reason}): {text[:90]}...")
            continue
        result.append(act)
    return result


class LocalCaseRetriever:
    """
    Поиск контекста только по документам текущего дела (без общего LightRAG-индекса).
    Интерфейс совместим с RAGEngine.search_relevant_context.
    """

    CHUNK = 1500

    def __init__(self, documents: List[Dict]):
        self.chunks = []
        for doc in documents or []:
            text = doc.get('text') or ''
            name = doc.get('filename', 'Документ')
            for i in range(0, len(text), self.CHUNK):
                chunk = text[i:i + self.CHUNK]
                self.chunks.append((name, i // self.CHUNK, chunk, set(self._stems(chunk))))

    @staticmethod
    def _stems(text: str) -> List[str]:
        return [w[:5] for w in re.findall(rf'[{_LO}a-z]{{4,}}', normalize_text(text).lower())]

    def search_relevant_context(self, query: str, k: int = 6) -> str:
        if not self.chunks:
            return ''
        q = set(self._stems(query))
        scored = []
        for order, (name, idx, chunk, stems) in enumerate(self.chunks):
            score = len(q & stems) + (0.5 if idx == 0 else 0)  # начало документа обычно содержит шапку и ФИО
            scored.append((score, -order, name, chunk))
        scored.sort(reverse=True)
        picked = scored[:k]
        # Возвращаем фрагменты в исходном порядке документов
        picked.sort(key=lambda x: -x[1])
        return "\n\n".join(f"[{name}]:\n{chunk}" for _, _, name, chunk in picked)

    def add_documents(self, documents: List[Dict]):
        pass
