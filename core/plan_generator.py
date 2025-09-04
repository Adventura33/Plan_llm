# core/plan_generator.py

import re
import os
from datetime import datetime, timedelta

from core.templates import INVESTIGATION_PLAN_ACTIONS

PERIOD_MAP = {
  "Быстрая фиксация": "Дни 1-10",
  "Сбор базовых доказательств": "Дни 1-30",
  "Аналитический блок": "Дни 15-60",
  "Процессуальная фиксация": "Дни 30-90",
  "Международное сотрудничество": "Дни 60-120",
  "Квалификация и обвинение": "Дни 90-120",
  "Завершение ДР": "До дня 120",
  "Параллельно": "Параллельно",
  "Обеспечение": "Дни 15-45"
}

def calculate_due_date(start_date_iso_str, duration_str):
    if not isinstance(start_date_iso_str, str) or not re.match(r'\d{4}-\d{2}-\d{2}', start_date_iso_str.split()[0]):
        return "Н/Д"
    try:
        start_date = datetime.strptime(start_date_iso_str.split()[0], "%Y-%m-%d")
    except ValueError:
        return "Н/Д"
    numbers = re.findall(r'\d+', duration_str)
    if not numbers:
        return duration_str
    days_to_add = int(numbers[-1])
    due_date = start_date + timedelta(days=days_to_add)
    return due_date.strftime("%d.%m.%Y")

def extract_main_uk_article(uk_article_string):
    if not isinstance(uk_article_string, str): return None
    match = re.search(r'(\d+)', uk_article_string)
    if match: return match.group(1)
    return None

def generate_investigation_plan(case_data, lang):
    uk_article_full = case_data.get('статья_ук_рк', 'Н/Д')
    main_uk_article = extract_main_uk_article(uk_article_full)

    registration_date_iso = case_data.get('дата_регистрации_ердр_iso')

    executor_fio = case_data.get('фио_следователя', "Н/Д")
    executor_duty = case_data.get('должность_следователя', "Н/Д")
    
    if executor_duty != "Н/Д" and executor_fio != "Н/Д":
        if executor_fio in executor_duty:
             actual_investigator_full = executor_duty
        else:
             actual_investigator_full = f"{executor_duty} {executor_fio}"
    elif executor_fio != "Н/Д":
        actual_investigator_full = executor_fio
    else:
        actual_investigator_full = "Следователь ФИО"

    filtered_actions = []
    current_number = 1
    for action_template in INVESTIGATION_PLAN_ACTIONS:
        relevant_articles = action_template.get("relevant_articles", [])
        if not relevant_articles or (main_uk_article and main_uk_article in relevant_articles):
            action_copy = action_template.copy()
            action_copy["номер"] = current_number
            methodology_stage = action_copy.get("methodology_stage")
            if methodology_stage and methodology_stage in PERIOD_MAP:
                duration_str = PERIOD_MAP[methodology_stage]
                action_copy["срок"] = calculate_due_date(registration_date_iso, duration_str)
            else:
                action_copy["срок"] = case_data.get('дата_регистрации_ердр', 'Н/Д')
            if not action_copy.get("исполнитель"):
                action_copy["исполнитель"] = actual_investigator_full
            filtered_actions.append(action_copy)
            current_number += 1

    year_from_iso = None
    if case_data.get('рапорт_дата_iso'):
        try:
            year_from_iso = datetime.strptime(case_data.get('рапорт_дата_iso'), '%Y-%m-%d').year
        except: pass
    
    plan_title_info = {
        "номер_дела": case_data.get('номер_ердр', '_______'),
        "факт": case_data.get('суть_правонарушения', '_______'),
        "статья_ук_рк": uk_article_full,
        "год": year_from_iso or datetime.now().year
    }
    
    return {
        "plan_title_info": plan_title_info,
        "actions": filtered_actions
    }
