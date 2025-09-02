from docx import Document
from docx.shared import Inches, Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_ALIGN_VERTICAL

# Функция для отладки: читаем сохраненный документ и выводим содержимое таблицы
def debug_read_docx_table(file_path):
    try:
        doc = Document(file_path)
        print(f"\n--- Отладка содержимого сохраненного DOCX: {file_path} ---")
        tables = doc.tables
        if not tables:
            print("В документе нет таблиц.")
            return

        table = tables[0]
        print(f"Найдена таблица с {len(table.rows)} строками и {len(table.columns)} колонками.")

        header_cells = table.rows[0].cells
        srok_col_idx = -1
        ispolnitel_col_idx = -1
        for i, cell in enumerate(header_cells):
            if cell.text.strip() == 'Срок':
                srok_col_idx = i
            if cell.text.strip() == 'Исполнитель':
                ispolnitel_col_idx = i
            
        if srok_col_idx == -1:
            print("Колонка 'Срок' не найдена в заголовках таблицы.")
        if ispolnitel_col_idx == -1:
            print("Колонка 'Исполнитель' не найдена в заголовках таблицы.")

        print(f"Содержимое колонок (первые 5 строк):")
        for i, row in enumerate(table.rows):
            if i == 0:
                continue
            if i > 5:
                break
            try:
                cell_text_srok = row.cells[srok_col_idx].text.strip() if srok_col_idx != -1 else "N/A"
                cell_text_ispolnitel = row.cells[ispolnitel_col_idx].text.strip() if ispolnitel_col_idx != -1 else "N/A"
                print(f"  Строка {i} - Срок: '{cell_text_srok}', Исполнитель: '{cell_text_ispolnitel}'")
            except IndexError:
                print(f"  Строка {i}: (ошибка доступа к ячейке)")
        print("--- Конец отладки содержимого DOCX ---")
    except Exception as e:
        print(f"Ошибка при чтении сохраненного DOCX для отладки: {e}")


def create_investigation_plan_doc(plan_data, output_path):
    """
    Создает документ Word (.docx) со сгенерированным планом расследования,
    включая колонку "Цель".
    """
    document = Document()

    sections = document.sections
    for section in sections:
        section.top_margin = Inches(0.7)
        section.bottom_margin = Inches(0.7)
        section.left_margin = Inches(0.7)
        section.right_margin = Inches(0.7)
    
    available_width = Inches(7.1)

    plan_title_info = plan_data.get("plan_title_info", {})
    current_year = plan_title_info.get('год', '_______')

    p = document.add_paragraph()
    p.add_run('«УТВЕРЖДАЮ»').bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    p = document.add_paragraph()
    p.add_run('Заместитель руководитель ДЭР').bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    document.add_paragraph('____________________________').alignment = WD_ALIGN_PARAGRAPH.RIGHT
    document.add_paragraph('____________________________').alignment = WD_ALIGN_PARAGRAPH.RIGHT
    document.add_paragraph(f'«_______» ____________________ {current_year} г.').alignment = WD_ALIGN_PARAGRAPH.RIGHT

    document.add_paragraph()
    p = document.add_paragraph()
    p.add_run('ПЛАН').bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    plan_text = document.add_paragraph(
        f"по уголовному делу №{plan_title_info.get('номер_дела', '_______')} "
        #f"по факту {plan_title_info.get('факт', '___________________________')} "
        f"по ст. {plan_title_info.get('статья_ук_рк', '_______ УК РК.')} УК РК."
    )
    plan_text.alignment = WD_ALIGN_PARAGRAPH.CENTER
    document.add_paragraph(f"{current_year} г.").alignment = WD_ALIGN_PARAGRAPH.RIGHT

    document.add_paragraph()

    table = document.add_table(rows=1, cols=5, style='Table Grid') 
    table.autofit = False
    table.allow_autofit = False

    table.columns[0].width = Inches(0.3)
    table.columns[4].width = Inches(0.8)
    table.columns[3].width = Inches(1.5)
    table.columns[2].width = Inches(2.0)
    
    col_width_deystviya = available_width - Inches(0.3) - Inches(0.8) - Inches(1.5) - Inches(2.0)
    table.columns[1].width = col_width_deystviya

    hdr_cells = table.rows[0].cells
    hdr_cells[0].text = '№ п.п.'
    hdr_cells[1].text = 'Следственные действия'
    hdr_cells[2].text = 'Цель'
    hdr_cells[3].text = 'Исполнитель'
    hdr_cells[4].text = 'Срок'

    for cell in hdr_cells:
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.bold = True
            paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

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
    p.add_run('Следователь').bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT

    p = document.add_paragraph('____________________________').alignment = WD_ALIGN_PARAGRAPH.LEFT
    document.add_paragraph()
    p = document.add_paragraph()
    p.add_run('«Согласовано»').bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT

    p = document.add_paragraph()
    p.add_run('Руководитель СУ').bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.LEFT

    document.add_paragraph('____________________________').alignment = WD_ALIGN_PARAGRAPH.LEFT

    document.save(output_path)
    print(f"Документ плана расследования сохранен по пути: {output_path}")
