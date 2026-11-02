# core/parser.py

import os
import re
from core.llm_utils import LLMClient, DEFAULT_API_URL, DEFAULT_MODEL
from pypdf import PdfReader

class RaportParser:
    def __init__(self, api_url=DEFAULT_API_URL, model=DEFAULT_MODEL):
        self.llm_client = LLMClient(api_url=api_url, model=model)

    def _extract_text_from_pdf(self, pdf_path):
        """
        Улучшенное извлечение текста из PDF с предварительной очисткой.
        """
        text_content = ""
        try:
            reader = PdfReader(pdf_path)
            
            if not reader.pages:
                print("PDF файл не содержит страниц")
                return None
                
            print(f"Найдено {len(reader.pages)} страниц в PDF")
            
            # Извлекаем текст со всех страниц
            for page_num, page in enumerate(reader.pages):
                try:
                    page_text = page.extract_text()
                    if page_text:
                        # Очищаем и нормализуем текст страницы
                        cleaned_text = self._clean_extracted_text(page_text)
                        text_content += f"\n--- Страница {page_num + 1} ---\n{cleaned_text}\n"
                        print(f"Извлечен текст со страницы {page_num + 1} ({len(cleaned_text)} символов)")
                    else:
                        print(f"Страница {page_num + 1} не содержит текста")
                except Exception as e:
                    print(f"Ошибка при извлечении текста со страницы {page_num + 1}: {e}")
                    continue
            
            if text_content.strip() and len(text_content.strip()) > 50:
                print(f"Общий объем извлеченного текста: {len(text_content)} символов")
                return text_content
            else:
                print("Текст слишком короткий или отсутствует (возможно скан). Пробую запустить OCR...")
                from core.methodology_loader import _ocr_with_cache
                ocr_text = _ocr_with_cache(pdf_path)
                if ocr_text:
                    print(f"OCR успешно извлек {len(ocr_text)} символов")
                    return ocr_text
                print("Не удалось извлечь текст ни с одной страницы PDF")
                return None

        except Exception as e:
            print(f"Критическая ошибка при чтении PDF файла: {e}")
            return None

    def _clean_extracted_text(self, text):
        """
        Очищает извлеченный из PDF текст от артефактов и нормализует его.
        """
        if not text:
            return ""
            
        # Убираем лишние пробелы и переносы строк
        text = re.sub(r'\s+', ' ', text)
        
        # Убираем возможные артефакты PDF
        text = re.sub(r'[^\w\s\.,;:\-«»""()№/\n]', '', text, flags=re.UNICODE)
        
        # Нормализуем кавычки
        text = text.replace('«', '"').replace('»', '"')
        text = text.replace('"', '"').replace('"', '"')
        
        # Убираем повторяющиеся пробелы
        text = ' '.join(text.split())
        
        return text.strip()

    def _preprocess_text_for_llm(self, text):
        """
        Предварительная обработка текста перед отправкой в LLM.
        """
        # Ограничиваем длину текста (LLM имеют ограничения по токенам)
        max_chars = 15000  # Примерно 3000-4000 токенов
        if len(text) > max_chars:
            print(f"Текст слишком длинный ({len(text)} символов), обрезаем до {max_chars}")
            
            # Пытаемся найти естественные границы для обрезки
            # Ищем конец предложения ближе к лимиту
            cut_point = max_chars
            for i in range(max_chars - 100, max_chars):
                if i < len(text) and text[i] in '.!?':
                    cut_point = i + 1
                    break
                    
            text = text[:cut_point]
            print(f"Текст обрезан до {len(text)} символов")
        
        return text

    def _extract_key_info_manually(self, text):
        """
        Резервный метод извлечения ключевой информации с помощью регулярных выражений.
        """
        key_info = {}
        
        # Ищем номер ЕРДР (обычно 15 цифр)
        erdp_pattern = r'ЕРДР[^\d]*(\d{15})'
        erdp_match = re.search(erdp_pattern, text)
        if erdp_match:
            key_info['номер_ердр'] = erdp_match.group(1)
            
        # Ищем статью УК РК (без ссылок на УПК РК и статей Общей части)
        from core.qualification_guardrail import extract_uk_article_from_text
        article = extract_uk_article_from_text(text)
        if article:
            key_info['статья_ук_рк'] = article
            
        # Ищем дату в различных форматах
        date_patterns = [
            r'(\d{1,2}\s+[а-яё]+\s+\d{4}\s*г\.?)',  # 5 октября 2023 г.
            r'(\d{2}\.\d{2}\.\d{4})',  # 05.10.2023
        ]
        for pattern in date_patterns:
            date_match = re.search(pattern, text, re.IGNORECASE)
            if date_match:
                key_info['дата'] = date_match.group(1)
                break
        
        return key_info

    def _extract_ustanovil_fabula(self, raw_text):
        """
        Напрямую из текста вырезает блок после УСТАНОВИЛ: как фабулу.
        Возвращает строку или None если не найдено.
        """
        if not raw_text:
            return None
        # Ищем «УСТАНОВИЛ:» или «УСТАНОВИЛА:» и берём всё после до конца или до следующего раздела
        pattern = r'(?:УСТАНОВИЛ|УСТАНОВИЛА)\s*:?\s*(.+?)(?=\nПОСТАНОВИЛ|\nРАСПОРЯЖАЮСЬ|\nПРИКАЗЫВАЮ|\nПРОШУ|ПОСТАНОВИЛ|$)'
        match = re.search(pattern, raw_text, re.DOTALL | re.IGNORECASE)
        if match:
            fabula = match.group(1).strip()
            # Убираем лишние переносы строк
            fabula = ' '.join(fabula.split())
            if len(fabula) > 50:  # Минимальная длина чтобы быть настоящей фабулой
                return fabula
        return None

    def parse_raport_text_with_llm(self, raport_text, filename="raport.pdf"):
        """
        Главный метод парсинга текста рапорта ЕРДР (без необходимости читать PDF).
        """
        print(f"Начинаю парсинг текста рапорта ЕРДР: {filename}")
        
        if not raport_text:
            print("Текст рапорта пуст")
            return None
            
        # Предварительно обрабатываем текст
        processed_text = self._preprocess_text_for_llm(raport_text)
        
        # Пытаемся извлечь данные с помощью LLM
        print("Отправляю текст в LLM для структурирования...")
        case_data = self.llm_client.extract_case_data_with_retry(processed_text)
        
        if case_data:
            print("LLM успешно структурировал данные")
            
            # Проверяем качество извлеченных данных
            missing_fields = []
            for field in ['фио_следователя', 'рапорт_дата', 'статья_ук_рк', 'номер_ердр']:
                if not case_data.get(field) or case_data[field] == "Н/Д":
                    missing_fields.append(field)
                    
            if missing_fields:
                print(f"Внимание: Не удалось извлечь следующие поля: {missing_fields}")
                
                # Пытаемся дополнить данные с помощью регулярных выражений
                print("Пытаюсь восстановить данные с помощью резервного метода...")
                manual_info = self._extract_key_info_manually(raport_text)
                
                for field in missing_fields:
                    if field == 'номер_ердр' and 'номер_ердр' in manual_info:
                        case_data['номер_ердр'] = manual_info['номер_ердр']
                        print(f"Восстановлено поле {field}: {manual_info['номер_ердр']}")
                    elif field == 'статья_ук_рк' and 'статья_ук_рк' in manual_info:
                        case_data['статья_ук_рк'] = manual_info['статья_ук_рк']
                        print(f"Восстановлено поле {field}: {manual_info['статья_ук_рк']}")
                    elif field in ['рапорт_дата'] and 'дата' in manual_info:
                        case_data['рапорт_дата'] = manual_info['дата']
                        print(f"Восстановлено поле {field}: {manual_info['дата']}")
            
            # Полный текст рапорта — для сверки ФИО и статусов участников при генерации плана
            case_data['текст_рапорта'] = raport_text

            # ДОПОЛНИТЕЛЬНО: Python-извлечение блока УСТАНОВИЛ напрямую из текста
            raw_fabula = self._extract_ustanovil_fabula(raport_text)
            if raw_fabula:
                existing = case_data.get('суть_правонарушения', '')
                if not existing or existing == 'Н/Д' or len(raw_fabula) > len(existing):
                    case_data['суть_правонарушения'] = raw_fabula
                    print(f"✅ Фабула извлечена напрямую из УСТАНОВИЛ ('{raw_fabula[:80]}...')")
            
            return case_data
        else:
            print("LLM не смог структурировать данные")
            
            # В качестве последней попытки возвращаем то, что смогли извлечь вручную
            print("Пытаюсь извлечь хотя бы базовую информацию...")
            manual_info = self._extract_key_info_manually(raport_text)
            
            if manual_info:
                # Создаем базовую структуру с извлеченными данными
                case_data = {
                    "рапорт_дата": manual_info.get('дата', 'Н/Д'),
                    "фио_следователя": 'Н/Д',
                    "должность_следователя": 'Н/Д',
                    "дата_обнаружения": 'Н/Д',
                    "источник_сведений": 'Н/Д',
                    "суть_правонарушения": 'Н/Д',
                    "статья_ук_рк": manual_info.get('статья_ук_рк', 'Н/Д'),
                    "номер_ердр": manual_info.get('номер_ердр', 'Н/Д'),
                    "дата_регистрации_ердр": 'Н/Д',
                    "место_правонарушения": 'Н/Д',
                    "тип_правонарушения": 'Н/Д',
                    "фигуранты": 'Н/Д',
                    "дополнительные_сведения": 'Н/Д'
                }
                print("Создана базовая структура данных с частично извлеченной информацией")
                return case_data
            else:
                print("Не удалось извлечь даже базовую информацию")
                return None

    def parse_raport_pdf_with_llm(self, pdf_path):
        """
        Главный метод парсинга PDF с улучшенной обработкой ошибок.
        """
        print(f"Начинаю парсинг PDF файла: {pdf_path}")
        
        if not os.path.exists(pdf_path):
            print(f"Ошибка: PDF файл не найден по пути: {pdf_path}")
            return None
        
        # Извлекаем текст из PDF
        raport_text = self._extract_text_from_pdf(pdf_path)
        
        if not raport_text:
            print("Не удалось извлечь текст из PDF")
            return None
            
        print("Текст успешно извлечен из PDF")
        
        # Предварительно обрабатываем текст
        processed_text = self._preprocess_text_for_llm(raport_text)
        
        # Пытаемся извлечь данные с помощью LLM
        print("Отправляю текст в LLM для структурирования...")
        case_data = self.llm_client.extract_case_data_with_retry(processed_text)
        
        if case_data:
            print("LLM успешно структурировал данные")
            
            # Проверяем качество извлеченных данных
            missing_fields = []
            for field in ['фио_следователя', 'рапорт_дата', 'статья_ук_рк', 'номер_ердр']:
                if not case_data.get(field) or case_data[field] == "Н/Д":
                    missing_fields.append(field)
                    
            if missing_fields:
                print(f"Внимание: Не удалось извлечь следующие поля: {missing_fields}")
                
                # Пытаемся дополнить данные с помощью регулярных выражений
                print("Пытаюсь восстановить данные с помощью резервного метода...")
                manual_info = self._extract_key_info_manually(raport_text)
                
                for field in missing_fields:
                    if field == 'номер_ердр' and 'номер_ердр' in manual_info:
                        case_data['номер_ердр'] = manual_info['номер_ердр']
                        print(f"Восстановлено поле {field}: {manual_info['номер_ердр']}")
                    elif field == 'статья_ук_рк' and 'статья_ук_рк' in manual_info:
                        case_data['статья_ук_рк'] = manual_info['статья_ук_рк']
                        print(f"Восстановлено поле {field}: {manual_info['статья_ук_рк']}")
                    elif field in ['рапорт_дата'] and 'дата' in manual_info:
                        case_data['рапорт_дата'] = manual_info['дата']
                        print(f"Восстановлено поле {field}: {manual_info['дата']}")
            
            # Полный текст рапорта — для сверки ФИО и статусов участников при генерации плана
            case_data['текст_рапорта'] = raport_text

            # ДОПОЛНИТЕЛЬНО: Python-извлечение блока УСТАНОВИЛ напрямую из текста
            raw_fabula = self._extract_ustanovil_fabula(raport_text)
            if raw_fabula:
                existing = case_data.get('суть_правонарушения', '')
                # Используем Python-извлечение если оно длиннее или LLM вернул Н/Д
                if not existing or existing == 'Н/Д' or len(raw_fabula) > len(existing):
                    case_data['суть_правонарушения'] = raw_fabula
                    print(f"✅ Фабула извлечена напрямую из УСТАНОВИЛ ('{raw_fabula[:80]}...')")
            
            return case_data
        else:
            print("LLM не смог структурировать данные")
            
            # В качестве последней попытки возвращаем то, что смогли извлечь вручную
            print("Пытаюсь извлечь хотя бы базовую информацию...")
            manual_info = self._extract_key_info_manually(raport_text)
            
            if manual_info:
                # Создаем базовую структуру с извлеченными данными
                case_data = {
                    "рапорт_дата": manual_info.get('дата', 'Н/Д'),
                    "фио_следователя": 'Н/Д',
                    "должность_следователя": 'Н/Д',
                    "дата_обнаружения": 'Н/Д',
                    "источник_сведений": 'Н/Д',
                    "суть_правонарушения": 'Н/Д',
                    "статья_ук_рк": manual_info.get('статья_ук_рк', 'Н/Д'),
                    "номер_ердр": manual_info.get('номер_ердр', 'Н/Д'),
                    "дата_регистрации_ердр": 'Н/Д',
                    "место_правонарушения": 'Н/Д',
                    "тип_правонарушения": 'Н/Д',
                    "фигуранты": 'Н/Д',
                    "дополнительные_сведения": 'Н/Д'
                }
                print("Создана базовая структура данных с частично извлеченной информацией")
                return case_data
            else:
                print("Не удалось извлечь даже базовую информацию")
                return None
