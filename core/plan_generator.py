# core/plan_generator.py

import re
import os

from core.templates import INVESTIGATION_PLAN_ACTIONS

# Определяем маппинг сроков здесь, напрямую в коде
# --- ИЗМЕНЕНИЕ: ЗАМЕНА ДЛИННОГО ТИРЕ НА ОБЫЧНЫЙ ДЕФИС В СРОКАХ ---
PERIOD_MAP = {
  "Быстрая фиксация":             "Дни 1-10",  # Использован обычный дефис
  "Сбор базовых доказательств":   "Дни 1-30",  # Использован обычный дефис
  "Аналитический блок":           "Дни 15-60", # Использован обычный дефис
  "Процессуальная фиксация":      "Дни 30-90", # Использован обычный дефис
  "Международное сотрудничество": "Дни 60-120",# Использован обычный дефис
  "Квалификация и обвинение":     "Дни 90-120",# Использован обычный дефис
  "Завершение ДР":                "До дня 120",# Использован обычный дефис
  "Параллельно":                  "Параллельно",
  "Обеспечение": "Дни 15-45"
}
# --- КОНЕЦ ИЗМЕНЕНИЯ ---


def extract_main_uk_article(uk_article_string):
    match = re.match(r'(\d+)', uk_article_string)
    if match:
        return match.group(1)
    return None

def generate_investigation_plan(case_data):
    """
    Генерирует детализированный план расследования на основе данных дела,
    фильтруя действия по статье УК РК и используя предопределенный маппинг для сроков.
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

    print(f"\n--- Отладка формирования действий плана ---")
    for action_template in INVESTIGATION_PLAN_ACTIONS:
        relevant_articles = action_template.get("relevant_articles", [])

        if not relevant_articles or (main_uk_article and main_uk_article in relevant_articles):
            action_copy = action_template.copy()

            action_copy["номер"] = current_number

            methodology_stage = action_copy.get("methodology_stage")
            current_srok = ""

            print(f"  Действие #{current_number}: '{action_copy['действие']}'")
            print(f"    methodology_stage из шаблона: '{methodology_stage}'")
            print(f"    Проверка наличия '{methodology_stage}' в загруженной методологии: {methodology_stage in methodology_durations if methodology_stage else False}")

            if methodology_stage and methodology_stage in methodology_durations:
                current_srok = methodology_durations[methodology_stage]
                print(f"    Срок взят из PERIOD_MAP: '{current_srok}'")
            elif action_copy.get("срок"):
                current_srok = action_copy.get("срок")
                print(f"    Срок взят из шаблона (специфичный): '{current_srok}'")
            else:
                current_srok = actual_registration_date
                print(f"    Дефолтный срок (дата ЕРДР): '{current_srok}'")
            
            action_copy["срок"] = current_srok # <--- Убедитесь, что это кириллическая 'с'

            current_ispolnitel = ""
            if action_copy.get("исполнитель"):
                current_ispolnitel = action_copy.get("исполнитель")
                print(f"    Исполнитель из шаблона: '{current_ispolnitel}'")
            else:
                current_ispolnitel = actual_investigator_full
                print(f"    Исполнитель по умолчанию (из рапорта): '{current_ispolnitel}'")
            
            action_copy["исполнитель"] = current_ispolnitel

            filtered_actions.append(action_copy)
            current_number += 1
    print(f"--- Конец отладки формирования действий плана ---\n")

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
