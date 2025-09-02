# core/llm_utils.py

import ollama
import json
import re

class OllamaClient:
    def __init__(self, model_name="llama3"):
        self.model = model_name

    def _get_extraction_prompt(self, raport_text):
        """
        Генерирует продвинутый промпт, который учит LLM рассуждать на основе контекста.
        """
        prompt = f"""
    Ты — высококвалифицированный мультиязычный ИИ-ассистент для анализа юридических документов из Казахстана.
    Твоя задача — внимательно проанализировать следующий текст документа (он может быть на русском или казахском языке) и извлечь из него ключевую информацию в формате JSON.
    Если какое-либо поле отсутствует в тексте, используй значение "Н/Д".

    **Принципы извлечения данных:**

    1.  **"фио_следователя" и "должность_следователя":** Найди в тексте человека, который является **автором или составителем** данного рапорта. Его должность может быть разной ("Следователь по ОВД", "Следователь ДЭР", "Тергеуші" и т.д.). Ищи подписи в конце документа или фразы "Я, [должность] [ФИО], сообщаю..." в начале. Извлеки его полную должность и отдельно ФИО.

    2.  **"статья_ук_рк":** Найди упоминание **статьи Уголовного Кодекса** (УК РК или ҚК). Обычно она идет после фраз "признаки состава уголовного правонарушения, предусмотренного ст. ..." или "по статье...".

    3.  **"номер_ердр":** Найди номер, который следует за ключевым словом **"ЕРДР"** (на русском) или **"СДТБТ"** (на казахском). Обычно это длинное число.

    4.  **"рапорт_дата":** Найди дату, которая, по контексту, является **датой составления всего документа**. Чаще всего она находится вверху, под названием города.

    ТЕКСТ ДОКУМЕНТА:
    ТРЕБУЕМЫЕ ПОЛЯ JSON (названия полей использовать строго как указано):
    {{
        "рапорт_дата": "Дата рапорта",
        "фио_следователя": "Полное ФИО следователя",
        "должность_следователя": "Полная должность следователя",
        "статья_ук_рк": "Статья УК РК",
        "номер_ердр": "Номер ЕРДР",
        // ... и так далее для всех остальных полей
    }}

    Пожалуйста, убедись, что твой ответ — это ТОЛЬКО валидный JSON-объект.
    """
        return prompt

    def extract_case_data(self, raport_text):
        """
        Отправляет запрос в Ollama для извлечения данных из текста рапорта.
        """
        prompt = self._get_extraction_prompt(raport_text)
        try:
            response = ollama.chat(model=self.model, messages=[
                {'role': 'user', 'content': prompt},
            ])
            
            content = response['message']['content']
            
            # 1. Удаляем Markdown-блоки, если они есть
            if content.startswith("```json") and content.endswith("```"):
                content = content[7:-3].strip()
            elif content.startswith("```") and content.endswith("```"):
                content = content[3:-3].strip()
            
            cleaned_content = content.strip()

            # 2. Ищем первый '{' и последний '}'
            start_brace = cleaned_content.find('{')
            end_brace = cleaned_content.rfind('}')

            if start_brace != -1 and end_brace != -1 and end_brace > start_brace:
                # Берем только содержимое между ними, включая сами скобки
                json_string_candidate = cleaned_content[start_brace : end_brace + 1]
            else:
                # Если не нашли четкие скобки, это проблема.
                print("Внимание: Не удалось найти полный JSON-блок в ответе LLM. Попытка восстановления.")
                # Попытка восстановить JSON: если он оборван в конце
                if cleaned_content.startswith('{') and not cleaned_content.endswith('}'):
                    json_string_candidate = cleaned_content + '}'
                else:
                    json_string_candidate = cleaned_content # Оставляем как есть, пусть json.loads выдаст ошибку

            case_data = json.loads(json_string_candidate)
            return case_data
        except ollama.ResponseError as e:
            print(f"Ошибка Ollama API: {e}")
            print(f"Убедитесь, что Ollama запущен и модель {self.model} доступна.")
            return None
        except json.JSONDecodeError as e:
            print(f"Ошибка парсинга JSON ответа LLM: {e}")
            print(f"Попытка парсинга строки: {json_string_candidate}")
            print(f"Полный ответ LLM до очистки: {content}")
            print("Попробуйте скорректировать промпт или проверьте, что LLM генерирует валидный JSON.")
            return None
        except Exception as e:
            print(f"Неизвестная ошибка при взаимодействии с Ollama: {e}")
            return None
