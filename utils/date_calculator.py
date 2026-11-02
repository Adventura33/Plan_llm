# utils/date_calculator.py

import re
from datetime import datetime, timedelta

def parse_kazakh_date(date_string):
    """
    Парсит казахские даты в различных форматах.
    """
    if not date_string or date_string == "Н/Д":
        return None
        
    # Убираем лишние пробелы
    date_string = date_string.strip()
    
    # Казахские месяцы
    kazakh_months = {
        'қаңтар': 1, 'ақпан': 2, 'наурыз': 3, 'сәуір': 4, 'мамыр': 5, 'маусым': 6,
        'шілде': 7, 'тамыз': 8, 'қыркүйек': 9, 'қазан': 10, 'қараша': 11, 'желтоқсан': 12
    }
    
    # Русские месяцы для резерва
    russian_months = {
        'января': 1, 'февраля': 2, 'марта': 3, 'апреля': 4, 'мая': 5, 'июня': 6,
        'июля': 7, 'августа': 8, 'сентября': 9, 'октября': 10, 'ноября': 11, 'декабря': 12
    }
    
    try:
        # Формат: 2025 жыл 2 қыркүйек или 2 қыркүйек 2025 жыл
        match = re.search(r'(\d{4})\s*жыл\s*(\d{1,2})\s*(\w+)', date_string)
        if not match:
            match = re.search(r'(\d{1,2})\s*(\w+)\s*(\d{4})\s*жыл?', date_string)
            if match:
                day, month_name, year = match.groups()
                year, month_name, day = year, month_name, day
            else:
                # Формат: dd.mm.yyyy
                match = re.search(r'(\d{1,2})\.(\d{1,2})\.(\d{4})', date_string)
                if match:
                    day, month, year = match.groups()
                    return datetime(int(year), int(month), int(day))
                else:
                    return None
        else:
            year, day, month_name = match.groups()
        
        # Ищем месяц в казахском словаре
        month_num = None
        for kz_month, num in kazakh_months.items():
            if kz_month in month_name.lower():
                month_num = num
                break
        
        # Если не нашли в казахском, пробуем русский
        if not month_num:
            for ru_month, num in russian_months.items():
                if ru_month in month_name.lower():
                    month_num = num
                    break
        
        if month_num:
            return datetime(int(year), month_num, int(day))
            
    except (ValueError, AttributeError):
        pass
    
    return None

def extract_max_days_from_period(period_string):
    """
    Извлекает максимальное количество дней из строки периода.
    
    Примеры:
    "Дни 1-10" -> 10
    "Дни 15-60" -> 60
    "До дня 120" -> 120
    "Параллельно" -> 0
    """
    if not period_string or period_string == "Н/Д":
        return 30  # Дефолт 30 дней
    
    # Паттерны для извлечения дней
    patterns = [
        r'Дни\s*(\d+)-(\d+)',      # "Дни 1-10"
        r'До\s*дня\s*(\d+)',       # "До дня 120"
        r'(\d+)-(\d+)\s*дн',       # "1-10 дней"
        r'(\d+)\s*дн',             # "30 дней"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, period_string, re.IGNORECASE)
        if match:
            groups = match.groups()
            if len(groups) == 2:  # Диапазон
                return int(groups[1])  # Берем максимум
            else:  # Одно число
                return int(groups[0])
    
    # Специальные случаи
    if "параллельно" in period_string.lower():
        return 0
    if "немедленно" in period_string.lower():
        return 1
    
    return 30  # Дефолт

def calculate_deadline_date(base_date_string, period_string):
    """
    Рассчитывает конкретную дату дедлайна на основе базовой даты и периода.
    
    Args:
        base_date_string: Дата рапорта (например, "2 қыркүйек 2025 жыл" или "02.09.2025")
        period_string: Период (например, "Дни 1-10")
    
    Returns:
        Строка с датой в формате "dd.mm.yyyy" или исходный период, если не удалось распарсить
    """
    # Парсим базовую дату
    base_date = parse_kazakh_date(base_date_string)
    
    if not base_date:
        # Если не удалось распарсить дату, возвращаем исходный период
        return period_string
    
    # Извлекаем количество дней
    days_to_add = extract_max_days_from_period(period_string)
    
    if days_to_add == 0:  # Параллельно
        return "Параллельно"
    
    # Рассчитываем дату дедлайна
    deadline_date = base_date + timedelta(days=days_to_add)
    
    # Возвращаем в формате dd.mm.yyyy
    return deadline_date.strftime("%d.%m.%Y")

def format_deadline_with_period_info(base_date_string, period_string):
    """
    Форматирует срок с указанием и конкретной даты, и периода.
    
    Returns:
        Строка вида "12.09.2025 (Дни 1-10)" или просто дату
    """
    deadline_date = calculate_deadline_date(base_date_string, period_string)
    
    if deadline_date == period_string:  # Не удалось рассчитать
        return period_string
    elif deadline_date == "Параллельно":
        return "Параллельно"
    else:
        return f"{deadline_date}"  # Только дата для чистоты

# Тестовые функции
def test_date_calculator():
    """Тестирует функции расчета дат."""
    print("🧪 ТЕСТ КАЛЬКУЛЯТОРА ДАТ")
    print("="*40)
    
    test_cases = [
        ("2 қыркүйек 2025 жыл", "Дни 1-10"),
        ("02.09.2025", "Дни 15-60"), 
        ("2025 жыл 2 қыркүйек", "До дня 120"),
        ("02.09.2025", "Параллельно"),
        ("Н/Д", "Дни 1-30"),
    ]
    
    for base_date, period in test_cases:
        result = calculate_deadline_date(base_date, period)
        print(f"📅 {base_date} + {period} = {result}")

if __name__ == "__main__":
    test_date_calculator()