# core/llm_utils.py

import ollama
import json
import re

class OllamaClient:
    def __init__(self, model_name="llama3"):
        self.model = model_name

    def _get_extraction_prompt(self, raport_text):
        """
        Генерирует промпт для извлечения структурированных данных из текста рапорта.
        """
        prompt = f"""
Ты - очень точный и надежный специализированный ИИ-помощник для анализа юридических документов.
Твоя задача - извлечь ВСЮ ключевую информацию из следующего "Рапорта об обнаружении сведений об уголовном правонарушении" и представить ее в ФОРМАТЕ JSON.
Если какое-либо поле отсутствует в тексте, используй значение "Н/Д".
Пожалуйста, будь точным и извлекай данные как есть, без домысливания.
Твой ответ ДОЛЖЕН БЫТЬ ТОЛЬКО ЧИСТЫМ JSON-ОБЪЕКТОМ, без какого-либо дополнительного текста (таких как "Here is the JSON:", "```json", "```"), объяснений или форматирования.
ОБЯЗАТЕЛЬНО УБЕДИСЬ, ЧТО JSON-ОБЪЕКТ НАЧИНАЕТСЯ С '{{' И ЗАВЕРШАЕТСЯ НА '}}' И ЯВЛЯЕТСЯ ВАЛИДНЫМ.

ТЕКСТ РАПОРТА:

{raport_text}

ТРЕБУЕМЫЕ ПОЛЯ JSON (названия полей использовать СТРОГО как указано, порядок не важен):
{{
    "рапорт_дата": "Дата рапорта (пример: 5 октября 2023 г.)",
    "фио_следователя": "Полное ФИО следователя (пример: Сарсенбаев М.Р.)",
    "должность_следователя": "Полная должность следователя (пример: Следователь по ОВД 2-го Следственного управления Департамента экономических расследований по г. Астана Агентства по финансовому мониторингу РК)",
    "дата_обнаружения": "Дата и время обнаружения (пример: 05.10.2023 17:28)",
    "источник_сведений": "Источник сведений (пример: инициативный рапорт ОУ)",
    "суть_правонарушения": "Полное описание сути правонарушения (длинный текст)",
    "статья_ук_рк": "Статья УК РК (пример: 217 ч.2 п.1)",
    "номер_ердр": "Номер ЕРДР (пример: 237100121000075)",
    "дата_регистрации_ердр": "Дата и время регистрации в ЕРДР (пример: 05.10.2023г. в 17:28)",
    "место_правонарушения": "г. Астана",
    "тип_правонарушения": "финансовая пирамида",
    "фигуранты": "Краткое описание фигурантов (пример: руководство компании «Е»)",
    "дополнительные_сведения": "Любые другие важные сведения из рапорта (пример: Прилагаю подтверждающие документы об уголовном правонарушении.)"
}}
Отвечай ТОЛЬКО JSON-объектом. Ничего лишнего не добавляй.
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
