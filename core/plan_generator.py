# core/plan_generator.py

import re
import os
from datetime import datetime, timedelta 

from core.templates import INVESTIGATION_PLAN_ACTIONS

# Определяем маппинг сроков здесь, напрямую в коде
PERIOD_MAP = {
  "Быстрая фиксация":             "Дни 1-10",
  "Сбор базовых доказательств":   "Дни 1-30",
  "Аналитический блок":           "Дни 15-60",
  "Процессуальная фиксация":      "Дни 30-90",
  "Международное сотрудничество": "Дни 60-120",
  "Квалификация и обвинение":     "Дни 90-120",
  "Завершение ДР":                "До дня 120",
  "Параллельно":                  "Параллельно",
  "Обеспечение": "Дни 15-45"
}

# --- НОВАЯ ВСПОМОГАТЕЛЬНАЯ ФУНКЦИЯ ---
def calculate_due_date(start_date_str, duration_str):
    """
    Вычисляет конечную дату, прибавляя дни из строки-длительности к начальной дате.
    """
    # 1. Извлекаем дату из строки "05.10.2023г. в 17:28"
    date_match = re.search(r'(\d{2}\.\d{2}\.\d{4})', start_date_str)
    if not date_match:
        return "Н/Д" # Возвращаем "недоступно", если не можем распарсить дату
    
    try:
        start_date = datetime.strptime(date_match.group(1), "%d.%m.%Y")
    except ValueError:
        return "Н/Д"

    # 2. Извлекаем максимальное количество дней из строки "Дни 1-30"
    numbers = re.findall(r'\d+', duration_str)
    if not numbers:
        return duration_str # Если в строке нет чисел (например, "Параллельно"), возвращаем ее как есть

    days_to_add = int(numbers[-1]) # Берем последнее число, оно будет максимальным в диапазоне

    # 3. Вычисляем новую дату
    due_date = start_date + timedelta(days=days_to_add)

    # 4. Форматируем результат в "ДД.ММ.ГГГГ"
    return due_date.strftime("%d.%m.%Y")
# --- КОНЕЦ НОВОЙ ФУНКЦИИ ---


def extract_main_uk_article(uk_article_string):
    match = re.match(r'(\d+)', uk_article_string)
    if match:
        return match.group(1)
    return None

def generate_investigation_plan(case_data):
    """
    Генерирует детализированный план расследования на основе данных дела,
    фильтруя действия по статье УК РК и вычисляя конкретные сроки.
    """
    uk_article_full = case_data.get('статья_ук_рк', 'Н/Д')
    main_uk_article = extract_main_uk_article(uk_article_full)

    methodology_durations = PERIOD_MAP

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

            # --- ИЗМЕНЕНИЕ: ЛОГИКА ВЫЧИСЛЕНИЯ СРОКА ---
            methodology_stage = action_copy.get("methodology_stage")
            
            if methodology_stage and methodology_stage in methodology_durations:
                duration_str = methodology_durations[methodology_stage]
                action_copy["срок"] = calculate_due_date(actual_registration_date, duration_str)
            elif action_copy.get("срок"):
                # Если в шаблоне задан специфичный срок, оставляем его как есть
                pass
            else:
                # В качестве дефолтного срока используем просто дату регистрации без изменений
                action_copy["срок"] = re.search(r'(\d{2}\.\d{2}\.\d{4})', actual_registration_date).group(1) if re.search(r'(\d{2}\.\d{2}\.\d{4})', actual_registration_date) else actual_registration_date
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---

            if not action_copy.get("исполнитель"):
                action_copy["исполнитель"] = actual_investigator_full

            filtered_actions.append(action_copy)
            current_number += 1

    plan_title_info = {
        "номер_дела": case_data.get('номер_ердр', '_______'),
        "факт": case_data.get('суть_правонарушения', 'обнаружении сведений об уголовном правонарушении'),
        "статья_ук_рк": uk_article_full,
        "год": case_data.get('рапорт_дата', ' ').split()[-2] if case_data.get('рапорт_дата') and len(case_data.get('рапорт_дата').split()) >= 3 else '_______'
    }

    return {
        "plan_title_info": plan_title_info,
        "actions": filtered_actions
    }
