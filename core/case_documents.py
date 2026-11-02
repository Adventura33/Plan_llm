# core/case_documents.py

"""
Модуль для загрузки и классификации документов уголовного дела.
Поддерживает: рапорты, заключения, заявления и другие процессуальные документы.
"""

import os
import re
from core.methodology_loader import load_pdf_file, load_docx_file, load_text_file, load_xlsx_file


# Маппинг расширений на загрузчики
FILE_LOADERS = {
    '.txt': load_text_file,
    '.pdf': load_pdf_file,
    '.docx': load_docx_file,
    '.xlsx': load_xlsx_file,
}

# Ключевые слова для определения типа документа
DOC_TYPE_KEYWORDS = {
    "рапорт_оперуполномоченного": [
        "оперуполномоченный", "оперативный", "опер", "орм", "орд",
        "оперативно-розыскн", "оперуполномоч"
    ],
    "заключение": [
        "заключение", "эксперт", "экспертиза", "специалист",
        "исследование", "вывод"
    ],
    "заявление": [
        "заявление", "заявитель", "потерпевший", "прошу",
        "обращение", "жалоба"
    ],
    "протокол": [
        "протокол", "допрос", "осмотр", "обыск", "выемка"
    ],
    "справка": [
        "справка", "справка-меморандум", "аналитическая справка",
        "информация"
    ],
    "рапорт_ердр": [
        "ердр", "сдтбт", "қдтж", "регистрации", "уголовного правонарушения"
    ],
}

def score_raport_filename(filename: str) -> int:
    """
    Присваивает вес файлу, чтобы выбрать наилучший вариант Главного Рапорта.
    """
    name_lower = filename.lower()
    score = 0
    if "уведомление" in name_lower or "талон" in name_lower:
        return -100
    if "рапорт" in name_lower:
        score += 50
    if "ердр" in name_lower or "сдтбт" in name_lower:
        score += 1000  # Максимальный приоритет для ЕРДР
    if "куи" in name_lower:
        score -= 10  # КУИ хуже чем неопознанный рапорт
    return score


def detect_document_type(filename, text):
    """
    Определяет тип документа по имени файла и содержимому.
    
    Returns:
        str: тип документа (рапорт_оперуполномоченного, заключение, заявление, и т.д.)
    """
    # Сначала проверяем имя файла
    filename_lower = filename.lower()
    text_lower = (text[:2000] if text else "").lower()  # Проверяем только начало текста
    
    best_type = "другой_документ"
    best_score = 0
    
    for doc_type, keywords in DOC_TYPE_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            if keyword in filename_lower:
                score += 3  # Совпадение в имени файла весит больше
            if keyword in text_lower:
                score += 1
        
        if score > best_score:
            best_score = score
            best_type = doc_type
    
    return best_type


def read_document(file_path):
    """
    Читает один документ дела (PDF/DOCX/XLSX/TXT) и возвращает его текст или None.
    Используется при дозагрузке отдельного файла (append-to-plan -d путь/к/файлу.pdf).
    """
    ext = os.path.splitext(file_path)[1].lower()
    loader = FILE_LOADERS.get(ext)
    if loader is None:
        print(f"  ⚠ Неподдерживаемый формат файла: {file_path}")
        return None
    text = loader(file_path)
    return text.strip() if text and text.strip() else None


def load_case_documents(input_dir, exclude_path=None):
    """
    Загружает все документы дела из указанной директории,
    исключая главный рапорт ЕРДР (если передан exclude_path).
    
    Args:
        input_dir: путь к папке с документами дела
        exclude_path: путь к главному рапорту, который нужно исключить
        
    Returns:
        list[dict]: список словарей {filename, text, doc_type, file_path}
    """
    documents = []
    
    if not os.path.exists(input_dir):
        print(f"⚠ Папка документов дела не найдена: {input_dir}")
        return documents
    
    # Нормализуем путь исключения
    exclude_abs = os.path.abspath(exclude_path) if exclude_path else None
    
    print(f"📂 Загрузка документов дела из: {input_dir}")
    
    for filename in sorted(os.listdir(input_dir)):
        # Пропускаем скрытые и временные файлы
        if filename.startswith('.') or filename.startswith('~$'):
            continue
            
        file_path = os.path.join(input_dir, filename)
        
        if not os.path.isfile(file_path):
            continue
        
        # Пропускаем главный рапорт
        if exclude_abs and os.path.abspath(file_path) == exclude_abs:
            print(f"  🔹 Пропущен (главный рапорт): {filename}")
            continue
        
        ext = os.path.splitext(filename)[1].lower()
        loader = FILE_LOADERS.get(ext)
        
        if loader is None:
            continue
        
        print(f"  📄 Загружаю: {filename}")
        text = loader(file_path)
        
        if text and text.strip():
            doc_type = detect_document_type(filename, text)
            documents.append({
                "filename": filename,
                "text": text.strip(),
                "doc_type": doc_type,
                "file_path": file_path,
            })
            print(f"     ✅ Тип: {doc_type} | {len(text)} символов")
        else:
            print(f"     ⚠ Пуст или не удалось прочитать: {filename}")
    
    print(f"📂 Загружено {len(documents)} дополнительных документов дела")
    return documents


def load_conclusion_documents(input_dir, exclude_path=None):
    """
    Загружает ТОЛЬКО документы типа 'заключение' из указанной директории.
    Заключения содержат реальные названия компаний (ТОО, АО, ИП),
    которые в рапортах скрыты (ТОО «Х»).

    Args:
        input_dir: путь к папке с документами дела
        exclude_path: путь к файлу, который нужно исключить

    Returns:
        list[dict]: список словарей {filename, text, doc_type, file_path}
                    содержащих ТОЛЬКО заключения специалистов/экспертов
    """
    all_docs = load_case_documents(input_dir, exclude_path=exclude_path)
    conclusions = [
        doc for doc in all_docs
        if doc.get("doc_type") == "заключение"
    ]
    if conclusions:
        print(f"🔎 Найдено {len(conclusions)} заключений специалистов/экспертов")
    else:
        print("ℹ Заключений специалистов не найдено в папке")
    return conclusions


def load_case_documents_from_db(case_number: str, db_root: str = "/root/database/workspaces", exclude_path=None):
    """
    Загружает все документы уголовного дела из пред-индексированной базы данных LightRAG (PostgreSQL).
    При сбое или отсутствии записей в БД откатывается на чтение локального kv_store_full_docs.json.
    """
    import os
    from core.db import fetch_documents_from_db
    
    documents = []
    exclude_abs = os.path.abspath(exclude_path) if exclude_path else None
    
    # Сначала пытаемся прочитать из PostgreSQL
    print(f"📂 [DB] Загрузка документов дела из PostgreSQL для workspace/case_number: {case_number}")
    db_docs = []
    try:
        db_docs = fetch_documents_from_db(case_number)
    except Exception as e:
        print(f"⚠ [DB] Ошибка при чтении из PostgreSQL: {e}. Попробуем JSON-фолбэк.")
        
    if db_docs:
        for doc in db_docs:
            content = doc.get("content", "")
            file_path = doc.get("doc_name", "")
            filename = os.path.basename(file_path) if file_path else "unknown"
            
            # Пропускаем исключенный файл
            if exclude_abs and file_path and os.path.abspath(file_path) == exclude_abs:
                print(f"  🔹 Пропущен (главный рапорт): {filename}")
                continue
                
            if content and content.strip():
                doc_type = detect_document_type(filename, content)
                documents.append({
                    "filename": filename,
                    "text": content.strip(),
                    "doc_type": doc_type,
                    "file_path": file_path,
                })
                print(f"     ✅ [БД] Тип: {doc_type} | {len(content)} символов | {filename}")
        
        if documents:
            print(f"📂 [БД] Успешно загружено {len(documents)} документов из PostgreSQL")
            return documents
            
    # Фолбэк на JSON
    workspace_dir = os.path.join(db_root, case_number)
    kv_file = os.path.join(workspace_dir, "kv_store_full_docs.json")
    
    if not os.path.exists(kv_file):
        print(f"⚠ Файл kv_store_full_docs.json не найден по пути: {kv_file}")
        return documents
        
    print(f"📂 [JSON-Fallback] Загрузка документов дела из БД: {kv_file}")
    
    try:
        import json
        with open(kv_file, "r", encoding="utf-8") as f:
            full_docs_data = json.load(f)
    except Exception as e:
        print(f"⚠ Ошибка при чтении kv_store_full_docs.json: {e}")
        return documents
        
    for doc_id, doc_info in full_docs_data.items():
        content = doc_info.get("content", "")
        file_path = doc_info.get("file_path", "")
        if not file_path:
            file_path = doc_info.get("filename", "")
            
        filename = os.path.basename(file_path) if file_path else "unknown"
        
        # Пропускаем исключенный файл
        if exclude_abs and file_path and os.path.abspath(file_path) == exclude_abs:
            print(f"  🔹 Пропущен (главный рапорт): {filename}")
            continue
            
        if content and content.strip():
            doc_type = detect_document_type(filename, content)
            documents.append({
                "filename": filename,
                "text": content.strip(),
                "doc_type": doc_type,
                "file_path": file_path,
            })
            print(f"     ✅ [БД-JSON] Тип: {doc_type} | {len(content)} символов | {filename}")
            
    print(f"📂 [БД-JSON] Загружено {len(documents)} документов дела из JSON")
    return documents


def load_conclusion_documents_from_db(case_number: str, db_root: str = "/root/database/workspaces", exclude_path=None):
    """
    Загружает ТОЛЬКО документы типа 'заключение' из базы данных.
    """
    all_docs = load_case_documents_from_db(case_number, db_root=db_root, exclude_path=exclude_path)
    conclusions = [
        doc for doc in all_docs
        if doc.get("doc_type") == "заключение"
    ]
    if conclusions:
        print(f"🔎 Найдено {len(conclusions)} заключений специалистов/экспертов в БД")
    else:
        print("ℹ Заключений специалистов не найдено в БД")
    return conclusions

