import os
import sys
import json
import uuid

# Добавляем путь к корню проекта, чтобы импортировать модули
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.case_documents import FILE_LOADERS, detect_document_type

def read_file_content(file_path):
    ext = os.path.splitext(file_path)[1].lower()
    
    # Поддержка JSON файлов напрямую
    if ext == '.json':
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    # Если это словарь, пробуем взять текстовые поля
                    if 'content' in data:
                        return str(data['content'])
                    if 'text' in data:
                        return str(data['text'])
                return json.dumps(data, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"⚠ Ошибка чтения JSON {file_path}: {e}")
            return None

    loader = FILE_LOADERS.get(ext)
    if loader is None:
        # Для других неизвестных файлов пробуем читать как простой текст
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception as e:
            print(f"⚠ Пропущен неизвестный формат: {file_path}")
            return None
            
    try:
        return loader(file_path)
    except Exception as e:
        print(f"⚠ Ошибка при чтении {file_path}: {e}")
        return None

def convert_folder_to_db_workspace(source_dir, case_number, db_root="data/mock_db"):
    if not os.path.exists(source_dir):
        print(f"❌ Исходная папка не найдена: {source_dir}")
        return
        
    workspace_dir = os.path.join(db_root, str(case_number))
    os.makedirs(workspace_dir, exist_ok=True)
    
    kv_store_path = os.path.join(workspace_dir, "kv_store_full_docs.json")
    
    # Загружаем существующий kv_store, если он есть
    kv_store = {}
    if os.path.exists(kv_store_path):
        try:
            with open(kv_store_path, "r", encoding="utf-8") as f:
                kv_store = json.load(f)
            print(f"🔄 Загружен существующий kv_store ({len(kv_store)} записей)")
        except Exception as e:
            print(f"⚠ Ошибка чтения существующего kv_store: {e}")

    added_count = 0
    
    for filename in sorted(os.listdir(source_dir)):
        if filename.startswith('.') or filename.startswith('~$'):
            continue
            
        file_path = os.path.join(source_dir, filename)
        if not os.path.isfile(file_path):
            continue
            
        # Проверяем, есть ли уже такой файл в базе
        already_exists = False
        for doc_id, doc_info in kv_store.items():
            if os.path.basename(doc_info.get("file_path", "")) == filename:
                already_exists = True
                break
                
        if already_exists:
            print(f"⏩ Файл {filename} уже есть в БД, пропускаем.")
            continue
            
        print(f"📄 Обработка файла: {filename}...")
        content = read_file_content(file_path)
        
        if content and content.strip():
            doc_id = f"doc_{uuid.uuid4().hex[:8]}"
            kv_store[doc_id] = {
                "content": content.strip(),
                "file_path": f"data/mock_db/{case_number}/{filename}"
            }
            added_count += 1
            print(f"   ✅ Успешно добавлен как {doc_id} ({len(content)} символов)")
            
    if added_count > 0:
        with open(kv_store_path, "w", encoding="utf-8") as f:
            json.dump(kv_store, f, ensure_ascii=False, indent=2)
        print(f"\n🎉 Успешно сохранено {added_count} новых документов в {kv_store_path}!")
        print(f"Всего документов в базе: {len(kv_store)}")
    else:
        print("\nℹ Новых файлов для добавления не найдено.")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Конвертация папки с файлами в формат БД workspaces для тестов")
    parser.add_argument("-s", "--source", required=True, help="Путь к папке с исходными документами")
    parser.add_argument("-n", "--case-number", required=True, help="Номер дела для базы данных")
    parser.add_argument("-d", "--db-root", default="data/mock_db", help="Путь к корню тестовой БД")
    
    args = parser.parse_args()
    convert_folder_to_db_workspace(args.source, args.case_number, args.db_root)
