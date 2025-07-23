# cli/main.py

import click
import os
from dotenv import load_dotenv

from core.parser import RaportParser # Изменено на RaportParser
from core.plan_generator import generate_investigation_plan
from utils.doc_formatter import create_investigation_plan_doc

# Загружаем переменные окружения из .env файла
load_dotenv()

@click.command()
@click.option('--raport-pdf', '-r', type=click.Path(exists=True, readable=True, resolve_path=True),
              required=True, help='Путь к PDF-файлу рапорта ЕРДР.')
@click.option('--output', '-o', type=click.Path(),
              default='data/output/generated_investigation_plan.docx',
              help='Путь для сохранения сгенерированного документа Word.')
@click.option('--ollama-model', '-m', default='llama3', help='Название модели Ollama для использования (например, llama3).')
def generate_plan(raport_pdf, output, ollama_model):
    """
    Генерирует план досудебного расследования уголовного дела на основе PDF-файла рапорта ЕРДР,
    используя Ollama для извлечения данных.
    """
    parser = RaportParser(ollama_model=ollama_model)

    click.echo(f"Загрузка и парсинг рапорта из PDF: {raport_pdf} с использованием модели Ollama: {ollama_model}")
    try:
        case_data = parser.parse_raport_pdf_with_llm(raport_pdf)
        if case_data is None:
            click.echo("Произошла ошибка при извлечении или парсинге текста из PDF. Прерывание.", err=True)
            return

        click.echo("Данные из рапорта успешно извлечены и структурированы.")
        # Для отладки: click.echo(f"Извлеченные данные: {case_data}")
    except Exception as e:
        click.echo(f"Критическая ошибка при обработке PDF или парсинге: {e}", err=True)
        return

    click.echo("Генерация плана расследования...")
    investigation_plan = generate_investigation_plan(case_data)
    click.echo("План расследования сгенерирован.")

    # Создаем директорию для вывода, если ее нет
    output_dir = os.path.dirname(output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    try:
        create_investigation_plan_doc(investigation_plan, output)
        click.echo(f"Готовый документ сохранен в: {output}")
    except Exception as e:
        click.echo(f"Ошибка при сохранении документа Word: {e}", err=True)

if __name__ == '__main__':
    generate_plan()
