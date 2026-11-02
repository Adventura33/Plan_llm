#!/usr/bin/env python3
# quick_fix_dates.py - Быстрое исправление дат в существующем плане

import sys
import os
sys.path.append('.')

from utils.date_calculator import calculate_deadline_date

def fix_existing_plan():
    """
    Исправляет существующий план, заменяя диапазоны дат на конкретные даты.
    """
    print("🔧 ИСПРАВЛЕНИЕ СРОКОВ В СУЩЕСТВУЮЩЕМ ПЛАНЕ")
    print("="*50)
    
    # Базовая дата из казахского рапорта
    base_date = "2 қыркүйек 2025 жыл"  # 02.09.2025
    print(f"📅 Базовая дата: {base_date}")
    
    # Маппинг периодов, которые мы видим в плане
    period_mapping = {
        "Дни 1-10": "Быстрая фиксация",
        "Дни 1-30": "Сбор базовых доказательств", 
        "Дни 15-60": "Аналитический блок",
        "Дни 30-90": "Процессуальная фиксация",
        "Дни 15-45": "Обеспечение",
        "Дни 90-120": "Квалификация и обвинение"
    }
    
    print("\n📋 ИСПРАВЛЕННЫЕ СРОКИ:")
    print("-" * 60)
    
    for period, stage in period_mapping.items():
        deadline = calculate_deadline_date(base_date, period)
        print(f"{stage:<35} {period:<15} -> {deadline}")
    
    print("\n🚀 Теперь запустите план с исправлением:")
    print("python3 -m cli.main generate-plan --debug")

def create_fixed_plan():
    """
    Создает план с исправленными датами через основную систему.
    """
    print("\n🔄 ЗАПУСК СИСТЕМЫ С ИСПРАВЛЕНИЕМ ДАТ...")
    
    from core.parser import RaportParser
    from core.plan_generator import generate_investigation_plan
    from utils.doc_formatter import create_investigation_plan_doc
    
    # Файлы
    pdf_file = "data/input/рапорт.pdf"
    output_file = "data/output/plan_with_dates.docx"
    
    print(f"📄 Файл: {pdf_file}")
    print(f"💾 Выход: {output_file}")
    
    # 1. Извлечение данных
    parser = RaportParser()
    case_data = parser.parse_raport_pdf_with_llm(pdf_file)
    
    if not case_data:
        print("❌ Не удалось извлечь данные")
        return
    
    # 2. Исправление данных
    if case_data.get('статья_ук_рк') in ['02', '179, 184-баптары', 'Н/Д']:
        case_data['статья_ук_рк'] = '218 ч.1'
        
    if not case_data.get('суть_правонарушения') or case_data['суть_правонарушения'] == 'Н/Д':
        case_data['суть_правонарушения'] = 'отмывание денежных средств, полученных преступным путем'
    
    # 3. Генерация плана
    investigation_plan = generate_investigation_plan(case_data)
    
    # 4. ИСПРАВЛЕНИЕ СРОКОВ НА КОНКРЕТНЫЕ ДАТЫ
    base_date = case_data.get('рапорт_дата', '02.09.2025')
    print(f"📅 Базовая дата для расчета: {base_date}")
    
    PERIOD_MAP = {
        "Быстрая фиксация": "Дни 1-10",
        "Сбор базовых доказательств": "Дни 1-30", 
        "Аналитический блок": "Дни 15-60",
        "Процессуальная фиксация": "Дни 30-90",
        "Квалификация и обвинение": "Дни 90-120",
        "Обеспечение": "Дни 15-45"
    }
    
    fixed_count = 0
    for action in investigation_plan.get('actions', []):
        methodology_stage = action.get('methodology_stage')
        
        if methodology_stage and methodology_stage in PERIOD_MAP:
            period_string = PERIOD_MAP[methodology_stage]
            deadline_date = calculate_deadline_date(base_date, period_string)
            
            old_deadline = action.get('срок', 'Н/Д')
            action['срок'] = deadline_date
            
            print(f"  ✅ {action.get('номер', '?'):2}. {old_deadline} -> {deadline_date}")
            fixed_count += 1
        elif action.get('срок') in ['Н/Д', None, '']:
            deadline_date = calculate_deadline_date(base_date, 'Дни 1-30')
            action['срок'] = deadline_date
            fixed_count += 1
    
    print(f"\n📊 Исправлено {fixed_count} сроков из {len(investigation_plan.get('actions', []))}")
    
    # 5. Создание документа
    os.makedirs("data/output", exist_ok=True)
    
    try:
        create_investigation_plan_doc(investigation_plan, output_file)
        print(f"✅ План с исправленными датами сохранен: {output_file}")
        
        # Статистика
        info = investigation_plan.get('plan_title_info', {})
        print(f"\n📋 ИТОГ:")
        print(f"   Номер дела: {info.get('номер_дела')}")
        print(f"   Статья: {info.get('статья_ук_рк')}")
        print(f"   Действий: {len(investigation_plan.get('actions', []))}")
        print(f"   Все сроки рассчитаны от даты: {base_date}")
        
    except Exception as e:
        print(f"❌ Ошибка создания документа: {e}")

if __name__ == "__main__":
    fix_existing_plan()
    
    response = input("\n❓ Создать исправленный план сейчас? (y/n): ")
    if response.lower() in ['y', 'yes', 'да', 'д']:
        create_fixed_plan()
    else:
        print("💡 Для создания плана с исправленными датами запустите:")
        print("   python3 quick_fix_dates.py")
