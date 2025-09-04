# core/localization.py

TRANSLATIONS = {
    "plan_title": {
        "ru": "ПЛАН",
        "kk": "ЖОСПАР"
    },
    "plan_subtitle_template": {
        "ru": "... по ст. {article_full_string} УК РК.", 
        "kk": "... ҚР ҚК-нің {article_full_string} бойынша ..."
    },
    "header_sample": {
        "ru": "ОБРАЗЕЦ",
        "kk": "ҮЛГІ"
    },
    "header_approve": {
        "ru": "«УТВЕРЖДАЮ»",
        "kk": "«БЕКІТЕМІН»"
    },
    "header_deputy": {
        "ru": "Заместитель руководитель ДЭР",
        "kk": "ЭТД басшысының орынбасары"
    },
    "table_col_number": {
        "ru": "№ п.п.",
        "kk": "№ р/с"
    },
    "table_col_action": {
        "ru": "Следственные действия",
        "kk": "Тергеу әрекеттері"
    },
    "table_col_aim": {
        "ru": "Цель",
        "kk": "Мақсаты"
    },
    "table_col_executor": {
        "ru": "Исполнитель",
        "kk": "Орындаушы"
    },
    "table_col_due_date": {
        "ru": "Срок",
        "kk": "Мерзімі"
    },
    "footer_investigator": {
        "ru": "Следователь",
        "kk": "Тергеуші"
    },
    "footer_agreed": {
        "ru": "«Согласовано»",
        "kk": "«КЕЛІСІЛДІ»"
    },
    "footer_head_su": {
        "ru": "Руководитель СУ",
        "kk": "ТБ басшысы"
    },
}

MONTHS = {
    "ru": {
        'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
        'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
    },
    "kk": {
        'қаңтар': 1, 'ақпан': 2, 'наурыз': 3, 'сәуір': 4, 'мамыр': 5, 'маусым': 6,
        'шілде': 7, 'тамыз': 8, 'қыркүйек': 9, 'қазан': 10, 'қараша': 11, 'желтоқсан': 12
    }
}

def get_text(key, lang, **kwargs):
    """
    Возвращает текст для заданного ключа и языка, форматируя его.
    """
    if lang == 'kk':
        text_template = TRANSLATIONS.get(key, {}).get("kk", key)
    else:
        text_template = TRANSLATIONS.get(key, {}).get("ru", key)
        
    return text_template.format(**kwargs)
