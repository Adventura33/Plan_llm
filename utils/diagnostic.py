# utils/diagnostic.py

import os
import re
from pypdf import PdfReader

def diagnose_pdf_file(pdf_path):
    """
    Диагностирует PDF файл и выводит подробную информацию для отладки.
    """
    print(f"\n🔍 ДИАГНОСТИКА PDF ФАЙЛА: {pdf_path}")
    print("="*60)
    
    if not os.path.exists(pdf_path):
        print("❌ Файл не найден")
        return False
    
    try:
        # Базовая информация о файле
        file_size = os.path.getsize(pdf_path)
        print(f"📁 Размер файла: {file_size:,} байт ({file_size/1024:.1f} KB)")
        
        # Читаем PDF
        reader = PdfReader(pdf_path)
        print(f"📄 Количество страниц: {len(reader.pages)}")
        
        if reader.metadata:
            print(f"📝 Метаданные PDF:")
            for key, value in reader.metadata.items():
                if value:
                    print(f"   {key}: {value}")
        
        # Проверяем каждую страницу
        total_text_length = 0
        pages_with_text = 0
        
        for page_num, page in enumerate(reader.pages):
            try:
                text = page.extract_text()
                text_length = len(text.strip()) if text else 0
                total_text_length += text_length
                
                if text_length > 0:
                    pages_with_text += 1
                
                print(f"   Страница {page_num + 1}: {text_length} символов", 
                      "✓" if text_length > 50 else "⚠️" if text_length > 0 else "❌")
                
                # Показываем начало текста для первой страницы
                if page_num == 0 and text and len(text.strip()) > 0:
                    preview = text.strip()[:200].replace('\n', ' ')
                    print(f"   Превью: {preview}...")
                    
            except Exception as e:
                print(f"   Страница {page_num + 1}: Ошибка извлечения - {e}")
        
        print(f"\n📊 ИТОГИ:")
        print(f"   Страниц с текстом: {pages_with_text}/{len(reader.pages)}")
        print(f"   Общий объем текста: {total_text_length:,} символов")
        
        if total_text_length == 0:
            print("❌ PDF не содержит извлекаемого текста")
            print("   Возможные причины:")
            print("   - Документ создан из сканированных изображений")
            print("   - Требуется OCR обработка")
            print("   - PDF защищен от извлечения текста")
            return False
        elif total_text_length < 100:
            print("⚠️  PDF содержит очень мало текста")
            print("   Проверьте качество документа")
            return False
        else:
            print("✅ PDF подходит для обработки")
            return True
            
    except Exception as e:
        print(f"❌ Критическая ошибка при чтении PDF: {e}")
        return False

def analyze_extracted_text(text):
    """
    Анализирует извлеченный текст и ищет ключевые паттерны.
    """
    print(f"\n🔍 АНАЛИЗ ИЗВЛЕЧЕННОГО ТЕКСТА")
    print("="*50)
    
    if not text or not text.strip():
        print("❌ Текст пуст")
        return {}
    
    print(f"📊 Длина текста: {len(text):,} символов")
    print(f"📊 Количество слов: {len(text.split()):,}")
    print(f"📊 Количество строк: {len(text.split('\\n'))}")
    
    # Определяем язык документа
    russian_indicators = ['рапорт', 'следователь', 'ердр', 'уголовного', 'правонарушения']
    kazakh_indicators = ['баянат', 'баяндама', 'тергеуші', 'қылмыстық', 'құқық']
    
    russian_count = sum(1 for word in russian_indicators if word in text.lower())
    kazakh_count = sum(1 for word in kazakh_indicators if word in text.lower())
    
    if kazakh_count > russian_count:
        print("🌐 Язык документа: Казахский")
        print("⚠️  Документ на казахском языке может требовать специальной обработки")
    elif russian_count > 0:
        print("🌐 Язык документа: Русский")
    else:
        print("🌐 Язык документа: Неопределен")
    
    # Поиск ключевых паттернов (адаптированных для обоих языков)
    patterns = {
        'ЕРДР номер': [
            r'ЕРДР[^\d]*(\d{15})',
            r'№\s*(\d{15})',
            r'номер[^\d]*(\d{15})',
            r'(\d{15})'  # Любой 15-значный номер
        ],
        'Статья УК РК': [
            r'ст\.\s*(\d+(?:\s*ч\.\s*\d+)?(?:\s*п\.\s*\d+)?)',
            r'статья\s*(\d+(?:\s*ч\.\s*\d+)?(?:\s*п\.\s*\d+)?)',
            r'(\d+)\s*ч\.\s*(\d+)\s*п\.\s*(\d+)',
            r'УК\s*РК[^\d]*(\d+)'
        ],
        'ФИО следователя': [
            r'Я,\s*([А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.[А-ЯЁ]\.)',
            r'следователь[^,]*,?\s*([А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.[А-ЯЁ]\.)',
            r'составил[^,]*,?\s*([А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.[А-ЯЁ]\.)',
            r'тергеуші[^,]*,?\s*([А-ЯЁ][а-яё]+\s+[А-ЯЁ]\.[А-ЯЁ]\.)'
        ],
        'Дата': [
            r'(\d{1,2}\s+[а-яё]+\s+\d{4}\s*ж\.?)',  # казахский формат
            r'(\d{1,2}\s+[а-яё]+\s+\d{4}\s*г\.?)',  # русский формат
            r'(\d{2}\.\d{2}\.\d{4})',
            r'(\d{1,2}/\d{1,2}/\d{4})'
        ]
    }
    
    found_data = {}
    
    print(f"\n🎯 НАЙДЕННЫЕ ПАТТЕРНЫ:")
    
    for category, pattern_list in patterns.items():
        found_matches = []
        for pattern in pattern_list:
            matches = re.findall(pattern, text, re.IGNORECASE)
            if matches:
                found_matches.extend(matches)
        
        if found_matches:
            # Убираем дубликаты, сохраняя порядок
            unique_matches = []
            for match in found_matches:
                if isinstance(match, tuple):
                    match_str = ' '.join(str(m) for m in match if m)
                else:
                    match_str = str(match)
                if match_str not in unique_matches:
                    unique_matches.append(match_str)
            
            found_data[category] = unique_matches
            print(f"   ✅ {category}: {unique_matches}")
        else:
            print(f"   ❌ {category}: не найдено")
    
    # Поиск возможных проблем
    print(f"\n⚠️  ВОЗМОЖНЫЕ ПРОБЛЕМЫ:")
    
    issues = []
    
    # Проверяем наличие ключевых слов для русского и казахского языков
    if not any(word in text.lower() for word in ['рапорт', 'баянат', 'баяндама']):
        issues.append("Не найдено слова 'рапорт' или его казахского эквивалента")
    
    if not any(word in text.lower() for word in ['следователь', 'тергеуші']):
        issues.append("Не найдено слова 'следователь' или его казахского эквивалента")
        
    if not any(word in text.lower() for word in ['ердр', 'қдтж']):
        issues.append("Не найдено упоминания ЕРДР")
    
    if len(text.split()) < 50:
        issues.append("Слишком короткий текст для рапорта")
    
    if not issues:
        print("   ✅ Проблем не обнаружено")
    else:
        for issue in issues:
            print(f"   ⚠️  {issue}")
    
    return found_data

def test_llm_model(model_name="llama3"):
    """
    Тестирует доступность и работу LLM модели.
    """
    print(f"\n🤖 ТЕСТИРОВАНИЕ МОДЕЛИ LLM: {model_name}")
    print("="*50)
    
    try:
        import ollama
        
        print("🔄 Попытка прямого тестирования модели...")
        
        # Пропускаем проверку списка моделей и сразу тестируем модель
        try:
            test_response = ollama.chat(
                model=model_name,
                messages=[{'role': 'user', 'content': 'Ответь одним словом: тест'}]
            )
            
            if test_response and 'message' in test_response:
                response_text = test_response['message']['content'].strip()
                print(f"📝 Ответ модели: {response_text}")
                print(f"✅ Модель {model_name} работает корректно")
                
                # Дополнительный тест на русском языке
                print("🧪 Тестирую работу с русским языком...")
                russian_test = ollama.chat(
                    model=model_name,
                    messages=[{'role': 'user', 'content': 'Извлеки имя: "Меня зовут Иван Иванов". Верни только имя.'}]
                )
                
                if russian_test and 'message' in russian_test:
                    russian_response = russian_test['message']['content'].strip()
                    print(f"📝 Ответ на русском: {russian_response}")
                    if 'иван' in russian_response.lower():
                        print("✅ Модель корректно работает с русским языком")
                    else:
                        print("⚠️  Возможны проблемы с обработкой русского языка")
                
                return True
            else:
                print("❌ Неожиданный формат ответа от модели")
                return False
                
        except Exception as chat_error:
            print(f"❌ Ошибка при тестировании модели: {chat_error}")
            
            # Проверим, что сервер Ollama вообще доступен
            try:
                import requests
                response = requests.get("http://localhost:11434/api/tags", timeout=5)
                if response.status_code == 200:
                    models_data = response.json()
                    if 'models' in models_data:
                        available_models = [model.get('name', '') for model in models_data['models']]
                        print(f"📋 Доступные модели через HTTP API: {available_models}")
                        
                        if model_name in available_models or f"{model_name}:latest" in available_models:
                            print(f"✅ Модель {model_name} найдена, но есть проблемы с Python API")
                            print("💡 Попробуйте переустановить ollama: pip install --upgrade ollama")
                        else:
                            print(f"❌ Модель {model_name} не установлена")
                            print(f"   Запустите: ollama pull {model_name}")
                    else:
                        print("❌ Неожиданный формат ответа от Ollama API")
                else:
                    print(f"❌ Сервер Ollama недоступен (код ответа: {response.status_code})")
            except ImportError:
                print("💡 Для дополнительной диагностики установите: pip install requests")
            except Exception as http_error:
                print(f"❌ Сервер Ollama недоступен: {http_error}")
                print("   Убедитесь что Ollama запущен: ollama serve")
            
            return False
            
    except ImportError:
        print("❌ Модуль ollama не установлен")
        print("   Установите: pip install ollama")
        return False
    except Exception as e:
        print(f"❌ Общая ошибка: {e}")
        return False
    
def full_diagnostic(pdf_path, model_name="llama3"):
    """
    Полная диагностика: PDF файл + извлечение текста + LLM модель.
    """
    print("🔧 ПОЛНАЯ ДИАГНОСТИКА СИСТЕМЫ")
    print("="*60)
    
    # 1. Проверяем PDF
    pdf_ok = diagnose_pdf_file(pdf_path)
    
    if not pdf_ok:
        print("\n❌ Диагностика остановлена: проблемы с PDF файлом")
        return False
    
    # 2. Извлекаем и анализируем текст
    try:
        # Импортируем только если нужно
        import sys
        import os
        sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        
        from core.parser import RaportParser
        parser = RaportParser()
        text = parser._extract_text_from_pdf(pdf_path)
        
        if text:
            found_patterns = analyze_extracted_text(text)
        else:
            print("❌ Не удалось извлечь текст из PDF")
            return False
            
    except Exception as e:
        print(f"❌ Ошибка при извлечении текста: {e}")
        print(f"   Детали ошибки: {type(e).__name__}")
        return False
    
    # 3. Тестируем LLM
    llm_ok = test_llm_model(model_name)
    
    # Итоговое заключение
    print(f"\n🎯 ИТОГОВОЕ ЗАКЛЮЧЕНИЕ:")
    print(f"   PDF файл: {'✅ OK' if pdf_ok else '❌ Проблема'}")
    print(f"   Извлечение текста: {'✅ OK' if text else '❌ Проблема'}")
    print(f"   LLM модель: {'✅ OK' if llm_ok else '❌ Проблема'}")
    
    if pdf_ok and text and llm_ok:
        print("✅ Система готова к работе!")
        
        # Дополнительные рекомендации для казахского документа
        if found_patterns and any('казахский' in str(v).lower() for v in found_patterns.values()):
            print("\n💡 РЕКОМЕНДАЦИИ:")
            print("   - Документ на казахском языке может требовать специального промпта")
            print("   - Рассмотрите использование модели, обученной на казахском языке")
            print("   - Возможно, потребуется перевод ключевых терминов")
        
        return True
    else:
        print("❌ Обнаружены проблемы, требующие устранения")
        
        if not llm_ok:
            print("\n🔧 ИНСТРУКЦИИ ПО УСТРАНЕНИЮ ПРОБЛЕМ С LLM:")
            print("1. Запустите Ollama сервер в отдельном терминале:")
            print("   ollama serve")
            print("2. Установите модель:")
            print(f"   ollama pull {model_name}")
            print("3. Проверьте статус:")
            print("   ollama list")
        
        return False
