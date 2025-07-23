import pandas as pd
import os

# Укажите точный путь к вашему Excel-файлу
# Убедитесь, что он совпадает с тем, который вы используете в main.py
excel_file_path = os.path.join('data', 'methodology', 'Типовой план ЖФ 16.09.2023.xlsx')

# Попробуем сначала "орм жф", затем "орм 1 вариант"
sheet_names_to_check = ["орм жф", "орм 1 вариант"]
found_sheet = None

print(f"Попытка чтения Excel-файла: {excel_file_path}")

if not os.path.exists(excel_file_path):
    print(f"Ошибка: Файл Excel не найден по пути: {excel_file_path}")
else:
    try:
        xls = pd.ExcelFile(excel_file_path)
        print(f"Все листы в файле: {xls.sheet_names}")

        for sheet_name in sheet_names_to_check:
            if sheet_name in xls.sheet_names:
                found_sheet = sheet_name
                break

        if found_sheet:
            df = pd.read_excel(xls, sheet_name=found_sheet)
            print(f"\n--- Данные с листа '{found_sheet}' ---")
            print(f"Найденные колонки: {df.columns.tolist()}")
            
            # Выведем первые 5 строк, чтобы увидеть содержимое колонок
            print("\nПервые 5 строк данных (для проверки содержимого):")
            print(df.head())

            # Попытаемся найти "Этап" и "Срок (диапазон)"
            print("\nПроверка наличия ожидаемых колонок:")
            expected_stage_col_names = ['Этап', 'Название раздела'] # Добавляем возможные варианты из вашего описания
            expected_time_col_names = ['Срок (диапазон)', 'Сроки (чтобы взять из колонки)'] # Добавляем возможные варианты

            actual_stage_col = None
            actual_time_col = None

            for col in expected_stage_col_names:
                if col in df.columns:
                    actual_stage_col = col
                    break
            for col in expected_time_col_names:
                if col in df.columns:
                    actual_time_col = col
                    break
            
            if actual_stage_col:
                print(f"Обнаружена колонка для этапов: '{actual_stage_col}'")
            else:
                print(f"Внимание: Колонка для этапов ({expected_stage_col_names}) не найдена.")

            if actual_time_col:
                print(f"Обнаружена колонка для сроков: '{actual_time_col}'")
            else:
                print(f"Внимание: Колонка для сроков ({expected_time_col_names}) не найдена.")

            print("\n--- Конец данных с листа ---")

        else:
            print(f"Не найден ни один из ожидаемых листов ({sheet_names_to_check}) в Excel-файле.")

    except Exception as e:
        print(f"Произошла ошибка при чтении Excel-файла: {e}")
