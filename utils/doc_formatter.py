# utils/doc_formatter.py

from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.enum.section import WD_ORIENT
import datetime

def create_investigation_plan_doc(plan_data, output_path):
    """
    Создает документ Word (.docx) со сгенерированным планом расследования.
    Упрощенная версия без ошибок tuple index.
    """
    try:
        document = Document()

        # Установка полей страницы и альбомной ориентации
        sections = document.sections
        for section in sections:
            section.orientation = WD_ORIENT.LANDSCAPE
            # Меняем местами ширину и высоту
            new_width, new_height = section.page_height, section.page_width
            section.page_width = new_width
            section.page_height = new_height
            
            section.top_margin = Inches(0.7)
            section.bottom_margin = Inches(0.7)
            section.left_margin = Inches(0.7)
            section.right_margin = Inches(0.7)

        # УТВЕРЖДАЮ
        p = document.add_paragraph()
        p.add_run('«УТВЕРЖДАЮ»').bold = True
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

        # Получаем данные о плане
        plan_title_info = plan_data.get("plan_title_info", {})
        регион = plan_title_info.get('регион_дэр', 'по __________________')

        p = document.add_paragraph()
        p.add_run(f'Заместитель руководителя ДЭР {регион}').bold = True
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

        # Расчет текущей даты для полей
        now = datetime.datetime.now()
        months = ["января", "февраля", "марта", "апреля", "мая", "июня", 
                  "июля", "августа", "сентября", "октября", "ноября", "декабря"]
        month_str = months[now.month - 1]
        date_str = f"«{now.day:02d}» {month_str} {now.year} г."

        # Поля для подписи (дата заполняется вручную при подписании)
        document.add_paragraph('____________________________').alignment = WD_ALIGN_PARAGRAPH.RIGHT
        document.add_paragraph('____________________________').alignment = WD_ALIGN_PARAGRAPH.RIGHT
        document.add_paragraph('«__» _____________ 20__ г.').alignment = WD_ALIGN_PARAGRAPH.RIGHT

        # Заголовок ПЛАН
        document.add_paragraph()
        p = document.add_paragraph()
        p.add_run('ПЛАН').bold = True
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # Детализация плана
        номер_дела = plan_title_info.get('номер_дела', '_______')
        # Убираем дублирующий префикс КУИ (номер уже содержит цифры)
        import re as _re_doc
        номер_дела = _re_doc.sub(r'^\s*(?:КУИ|СДТБТ)\s*[№#]?\s*', '', str(номер_дела)).strip()
        номер_дела = _re_doc.sub(r'^[№#]\s*', '', номер_дела).strip()  # убираем оставшийся №
        город = plan_title_info.get('город', 'г. __________________')

        # Нормализация статьи: убираем «Статья»/«статья», «ст.»/«ст » в начале, «часть»→«ч.», «пункт»→«п.»
        import re as _re
        статья = plan_title_info.get('статья_ук_рк', '_______')
        статья = _re.sub(r'(?i)^статья\s*', '', статья).strip()
        статья = _re.sub(r'(?i)^ст\.\s*', '', статья).strip()   # убираем «ст.» если LLM уже добавил
        статья = _re.sub(r'(?i)\bчасть\b', 'ч.', статья)
        статья = _re.sub(r'(?i)\bпункт\b', 'п.', статья)
        статья = _re.sub(r'\s+', ' ', статья).strip().strip(',')

        plan_text = document.add_paragraph(
            f"по уголовному делу №{номер_дела} по ст. {статья} УК РК"
        )
        plan_text.alignment = WD_ALIGN_PARAGRAPH.CENTER
        '''
        фабула = plan_title_info.get('факт', '').strip()
        if фабула:
            fabula_p = document.add_paragraph(фабула)
            fabula_p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        '''
        
        # Создаем невидимую таблицу для города (слева) и даты (справа)
        date_table = document.add_table(rows=1, cols=2)
        date_cells = date_table.rows[0].cells
        
        city_p = date_cells[0].paragraphs[0]
        city_p.add_run(город)
        city_p.alignment = WD_ALIGN_PARAGRAPH.LEFT
        
        date_p = date_cells[1].paragraphs[0]
        date_p.add_run(date_str)
        date_p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

        document.add_paragraph()

        # Создание таблицы с 5 колонками
        table = document.add_table(rows=1, cols=5, style='Table Grid')
        table.autofit = False

        # Задаем ширину столбцов (адаптировано под альбомную ориентацию)
        widths = [Inches(0.5), Inches(3.5), Inches(3.0), Inches(1.5), Inches(1.5)]
        for idx, width in enumerate(widths):
            table.columns[idx].width = width
            table.rows[0].cells[idx].width = width

        # Заголовки таблицы
        hdr_cells = table.rows[0].cells
        hdr_cells[0].text = '№ п.п.'
        hdr_cells[1].text = 'Следственные действия'
        hdr_cells[2].text = 'Цель'
        hdr_cells[3].text = 'Исполнитель'
        hdr_cells[4].text = 'Срок'

        # Применение жирного шрифта к заголовкам
        for cell in hdr_cells:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.bold = True
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER

        # Заполнение таблицы данными
        # Пункты — по блокам, исполненные — последним блоком (и для планов, сохранённых до этого правила);
        # перед первым пунктом каждого блока — строка-заголовок на всю ширину таблицы
        # Документ строится по тому же виду плана, что отдаёт API (normalize_plan_statuses): блоки, статусы,
        # единое окончание текста пунктов. Сам plan_data не меняется
        from core.plan_generator import normalize_plan_statuses
        shown = normalize_plan_statuses({"actions": [dict(a) for a in plan_data.get("actions", [])],
                                         "case_data": plan_data.get("case_data") or {}})
        current_block = None
        for action in shown["actions"]:
            if action.get("блок_номер") != current_block:
                current_block = action.get("блок_номер")
                header_cells = table.add_row().cells
                merged = header_cells[0].merge(header_cells[-1])
                merged.text = str(action.get("блок", ""))
                for paragraph in merged.paragraphs:
                    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
                    for run in paragraph.runs:
                        run.bold = True

            # Создаем новую строку
            new_row = table.add_row()
            cells = new_row.cells
            
            for idx, width in enumerate(widths):
                cells[idx].width = width
            
            # Заполняем каждую ячейку по отдельности
            cells[0].text = str(action.get("номер", ""))
            cells[1].text = str(action.get("действие", ""))
            cells[2].text = str(action.get("цель", "Следственное действие"))
            
            исполнитель = action.get("исполнитель", "Следователь")
            if not исполнитель or исполнитель == "Н/Д":
                исполнитель = "Следователь"
            cells[3].text = str(исполнитель)
            
            срок = str(action.get("срок", "В течение 30 дней")).strip()
            if not срок or срок == "Н/Д":
                срок = "В течение 30 дней"
            
            import re
            # Убираем возможные дубликаты префиксов (до, Исполнить до)
            clean_srok = re.sub(r'(?i)^(исполнить\s+)?(до\s+)+', '', срок).strip()
            clean_srok = re.sub(r'(?i)^исполнить\s+', '', clean_srok).strip()
            clean_srok = re.sub(r'(?i)\bв\s+течение\s+в\b', 'в течение', clean_srok).strip()

            статус = action.get("статус", "")
            if статус == "Исполнен":
                срок_текст = "Исполнен"
            else:
                if clean_srok and re.match(r'^\d{2}[./-]\d{2}[./-]\d{4}', clean_srok):
                    срок_текст = f"Исполнить до {clean_srok}"
                elif clean_srok.lower().startswith("в течение"):
                    срок_текст = f"Исполнить {clean_srok.lower()}"
                else:
                    срок_текст = f"Исполнить до {clean_srok}"

            cells[4].text = срок_текст

        # Добавление заключительных параграфов
        document.add_paragraph()
        
        document.add_paragraph()
        document.add_paragraph(
            "Данные следственные действия не являются исчерпывающими, по мере необходимости провести и другие "
            "следственно-оперативные мероприятия с составлением дополнительного плана."
        )
        
        # Подписи в конце
        document.add_paragraph()
        p = document.add_paragraph()
        p.add_run('Следователь').bold = True

        document.add_paragraph('____________________________')
        document.add_paragraph()
        p = document.add_paragraph()
        p.add_run('«Согласовано»').bold = True

        p = document.add_paragraph()
        p.add_run('Руководитель СУ').bold = True

        document.add_paragraph('____________________________')

        # Сохранение документа
        document.save(output_path)
        print(f"Документ плана расследования сохранен: {output_path}")
        
        return True
        
    except Exception as e:
        print(f"Ошибка при создании DOCX документа: {e}")
        print(f"   Тип ошибки: {type(e).__name__}")
        import traceback
        print(f"   Трассировка: {traceback.format_exc()}")
        return False
