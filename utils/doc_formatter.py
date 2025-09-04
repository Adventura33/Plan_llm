# utils/doc_formatter.py

from docx import Document
from docx.shared import Inches
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_ALIGN_VERTICAL
from core.localization import get_text

def create_investigation_plan_doc(plan_data, output_path, lang):
    """
    Создает документ Word (.docx) со сгенерированным планом расследования.
    """
    document = Document()

    # --- Настройка полей страницы ---
    sections = document.sections
    for section in sections:
        section.top_margin = Inches(0.7)
        section.bottom_margin = Inches(0.7)
        section.left_margin = Inches(0.7)
        section.right_margin = Inches(0.7)
    
    available_width = Inches(7.1)
    plan_title_info = plan_data.get("plan_title_info", {})
    current_year = plan_title_info.get('год', '_______')

    # --- Верхняя часть документа (титульная шапка) ---
    p = document.add_paragraph()
    p.add_run(get_text("header_sample", lang)).bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    p = document.add_paragraph()
    p.add_run(get_text("header_approve", lang)).bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    p = document.add_paragraph()
    p.add_run(get_text("header_deputy", lang)).bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    document.add_paragraph('____________________________').alignment = WD_ALIGN_PARAGRAPH.RIGHT
    document.add_paragraph(f'«_______» ____________________ {current_year} г.').alignment = WD_ALIGN_PARAGRAPH.RIGHT

    document.add_paragraph() # Пустая строка
    
    p = document.add_paragraph()
    p.add_run(get_text("plan_title", lang)).bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # --- Формирование подзаголовка плана ---
    subtitle = ""
    if lang == 'kk':
        # Для казахского языка формируем строку с правильным порядком слов
        subtitle = f"№{plan_title_info.get('номер_дела', '_______')} қылмыстық іс бойынша " \
                   f"«{plan_title_info.get('факт', '_______')}» фактісі бойынша " \
                   f"{plan_title_info.get('статья_ук_рк', '_______')} бойынша қатарлас қаржылық тергеп-тексеру."
    else:
        # Для русского языка
        subtitle = f"параллельного финансового расследования уголовного дела №{plan_title_info.get('номер_дела', '_______')} " \
                   f"по факту «{plan_title_info.get('факт', '_______')}» " \
                   f"по {plan_title_info.get('статья_ук_рк', '_______')}."

    plan_text = document.add_paragraph(subtitle)
    plan_text.alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.add_paragraph(f"{current_year} г.").alignment = WD_ALIGN_PARAGRAPH.RIGHT

    document.add_paragraph() # Пустая строка

    # --- Создание и форматирование таблицы ---
    table = document.add_table(rows=1, cols=5, style='Table Grid') 
    table.autofit = False
    table.allow_autofit = False

    # Настройка ширины колонок
    table.columns[0].width = Inches(0.3)
    table.columns[4].width = Inches(0.8)
    table.columns[3].width = Inches(1.5)
    table.columns[2].width = Inches(2.0)
    col_width_deystviya = available_width - Inches(0.3) - Inches(0.8) - Inches(1.5) - Inches(2.0)
    table.columns[1].width = col_width_deystviya

    # Заголовки таблицы
    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = get_text("table_col_number", lang)
    hdr_cells[1].text = get_text("table_col_action", lang)
    hdr_cells[2].text = get_text("table_col_aim", lang)
    hdr_cells[3].text = get_text("table_col_executor", lang)
    hdr_cells[4].text = get_text("table_col_due_date", lang)

    # Форматирование заголовков
    for cell in hdr_cells:
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.bold = True
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

    # Заполнение таблицы данными
    for action in plan_data.get("actions", []):
        row_cells = table.add_row().cells
        row_cells[0].text = str(action.get("номер", ""))
        row_cells[1].text = action.get("действие", "")
        row_cells[2].text = action.get("цель", "")
        row_cells[3].text = action.get("исполнитель", "")
        row_cells[4].text = action.get("срок", "")
        for i, cell in enumerate(row_cells):
            for paragraph in cell.paragraphs:
                paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if i == 0 else WD_ALIGN_PARAGRAPH.LEFT
                cell.vertical_alignment = WD_ALIGN_VERTICAL.TOP

    # --- Нижняя часть документа ---
    document.add_paragraph()
    document.add_paragraph(
        "Следственное действие должно раскрывать цель его проведения, участников и сроки исполнения, при этом, "
        "следственные мероприятия необходимо группировать по направлениям исследований."
    )
    document.add_paragraph(
        "При этом, каждое планируемое следственное действие и мероприятие должны быть отражены раздельно."
    )
    document.add_paragraph()
    document.add_paragraph(
        "Данные следственные действия не являются исчерпывающими, по мере необходимости провести и другие "
        "следственно-оперативные мероприятия с составлением дополнительного плана."
    )
    
    document.add_paragraph()
    p = document.add_paragraph()
    p.add_run(get_text("footer_investigator", lang)).bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT

    p = document.add_paragraph('____________________________').alignment = WD_ALIGN_PARAGRAPH.LEFT
    document.add_paragraph()
    p = document.add_paragraph()
    p.add_run(get_text("footer_agreed", lang)).bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT

    p = document.add_paragraph()
    p.add_run(get_text("footer_head_su", lang)).bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT

    document.add_paragraph('____________________________').alignment = WD_ALIGN_PARAGRAPH.LEFT

    document.save(output_path)
    print(f"Документ плана расследования сохранен по пути: {output_path}")
