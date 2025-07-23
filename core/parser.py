# core/parser.py

import os
from core.llm_utils import OllamaClient
from pypdf import PdfReader # Импортируем pypdf

# Класс RaportParser теперь будет использовать pypdf для извлечения текста
class RaportParser:
    def __init__(self, ollama_model="llama3"):
        self.ollama_client = OllamaClient(model_name=ollama_model)

    def _extract_text_from_pdf(self, pdf_path):
        """
        Извлекает текст из PDF-файла с помощью pypdf.
        Это работает только для текстовых PDF, не для сканированных изображений.
        """
        text_content = ""
        try:
            reader = PdfReader(pdf_path)
            # Проверяем, есть ли текст на страницах
            has_text = False
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text_content += page_text + "\n"
                    has_text = True
            
            if has_text:
                print("Текст успешно извлечен из PDF с помощью pypdf.")
                return text_content
            else:
                print("Не удалось извлечь текст из PDF с помощью pypdf. Возможно, PDF является сканированным изображением или не содержит текстового слоя.")
                return None

        except Exception as e:
            print(f"Ошибка при извлечении текста из PDF с помощью pypdf: {e}")
            print("Убедитесь, что файл PDF не поврежден и не защищен.")
            return None

    def parse_raport_pdf_with_llm(self, pdf_path):
        """
        Извлекает текст из PDF с помощью pypdf,
        а затем использует LLM для парсинга данных.
        """
        raport_text = self._extract_text_from_pdf(pdf_path)
        
        if raport_text is None:
            # Если pypdf не смог извлечь текст, это критично для текущей логики
            print("Прерывание: Не удалось извлечь читаемый текст из PDF.")
            return None

        print("Текст из PDF успешно извлечен. Передаю в LLM для структурирования...")
        case_data = self.ollama_client.extract_case_data(raport_text)
        
        if case_data:
            print("Данные успешно структурированы LLM.")
        else:
            print("LLM не смог структурировать данные или произошла ошибка.")

        return case_data
