#!/usr/bin/env python3
# diagnose.py

"""
Скрипт диагностики для выявления проблем с извлечением данных из PDF рапортов.
"""

import click
import os
import sys

# Добавляем корневую папку проекта в путь
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.diagnostic import diagnose_pdf_file, analyze_extracted_text, test_llm_model, full_diagnostic

@click.group()
def cli():
    """🔧 Диагностика системы извлечения данных из PDF рапортов"""
    pass

@cli.command()
@click.argument('pdf_path', type=click.Path(exists=True))
def pdf(pdf_path):
    """Диагностика PDF файла"""
    diagnose_pdf_file(pdf_path)

@cli.command()
@click.argument('pdf_path', type=click.Path(exists=True))
def text(pdf_path):
    """Извлечение и анализ текста из PDF"""
    try:
        from core.parser import RaportParser
        parser = RaportParser()
        
        print("📄 Извлекаю текст из PDF...")
        extracted_text = parser._extract_text_from_pdf(pdf_path)
        
        if extracted_text:
            analyze_extracted_text(extracted_text)
            
            # Сохраняем извлеченный текст для дальнейшего анализа
            output_path = "extracted_text.txt"
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(extracted_text)
            print(f"\n💾 Извлеченный текст сохранен в: {output_path}")
        else:
            print("❌ Не удалось извлечь текст из PDF")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")

@cli.command()
@click.option('--model', '-m', default='llama3', help='Модель для тестирования')
def llm(model):
    """Тестирование LLM модели"""
    test_llm_model(model)

@cli.command()
@click.argument('pdf_path', type=click.Path(exists=True))
@click.option('--model', '-m', default='llama3', help='Модель LLM для тестирования')
def full(pdf_path, model):
    """Полная диагностика системы"""
    success = full_diagnostic(pdf_path, model)
    
    if success:
        print(f"\n🚀 Попробуйте теперь запустить основную команду:")
        print(f"   python -m cli.main generate-plan --raport-pdf {pdf_path} --model {model} --debug")
    else:
        print(f"\n🔧 Сначала устраните обнаруженные проблемы")

@cli.command()
@click.option('--input-dir', '-i', default='data/input', help='Папка с PDF файлами')
def scan(input_dir):
    """Сканирование папки input на предмет PDF файлов"""
    print(f"🔍 Сканирую папку: {input_dir}")
    
    if not os.path.exists(input_dir):
        print(f"❌ Папка {input_dir} не существует")
        print(f"   Создайте папку и поместите туда PDF файлы рапортов")
        return
    
    files = os.listdir(input_dir)
    pdf_files = [f for f in files if f.lower().endswith('.pdf')]
    
    if not pdf_files:
        print(f"❌ PDF файлы не найдены в папке {input_dir}")
        print(f"   Поместите PDF файлы рапортов в эту папку")
        return
    
    print(f"✅ Найдено {len(pdf_files)} PDF файлов:")
    
    for i, filename in enumerate(pdf_files, 1):
        filepath = os.path.join(input_dir, filename)
        file_size = os.path.getsize(filepath)
        
        print(f"\n   {i}. {filename}")
        print(f"      Размер: {file_size:,} байт ({file_size/1024:.1f} KB)")
        
        # Быстрая проверка
        try:
            from pypdf import PdfReader
            reader = PdfReader(filepath)
            pages_count = len(reader.pages)
            
            # Проверяем первую страницу на наличие текста
            first_page_text = reader.pages[0].extract_text() if pages_count > 0 else ""
            text_length = len(first_page_text.strip()) if first_page_text else 0
            
            print(f"      Страниц: {pages_count}")
            print(f"      Текст на 1-й стр.: {text_length} символов {'✓' if text_length > 50 else '⚠️' if text_length > 0 else '❌'}")
            
        except Exception as e:
            print(f"      ❌ Ошибка чтения: {e}")
    
    print(f"\n💡 Для диагностики конкретного файла используйте:")
    print(f"   python diagnose.py full {input_dir}/{pdf_files[0]}")

@cli.command()
@click.argument('text_file', type=click.Path(exists=True))
@click.option('--model', '-m', default='llama3', help='Модель LLM')
def test_extraction(text_file, model):
    """Тестирование извлечения данных из текстового файла"""
    print(f"🧪 Тестирую извлечение данных из файла: {text_file}")
    
    try:
        with open(text_file, 'r', encoding='utf-8') as f:
            text_content = f.read()
        
        if not text_content.strip():
            print("❌ Файл пуст")
            return
            
        print(f"📊 Размер текста: {len(text_content)} символов")
        
        # Анализируем текст
        analyze_extracted_text(text_content)
        
        # Тестируем LLM извлечение
        print(f"\n🤖 Тестирую извлечение данных с помощью {model}...")
        
        from core.llm_utils import LLMClient
        client = LLMClient(model=model)
        
        result = client.extract_case_data_with_retry(text_content)
        
        if result:
            print("✅ Извлечение успешно!")
            
            from cli.main import print_extraction_summary
            print_extraction_summary(result)
        else:
            print("❌ Извлечение неудачно")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")

if __name__ == '__main__':
    cli()