# cli/main.py

import click
import os
from dotenv import load_dotenv

from core.parser import RaportParser
from core.plan_generator import generate_investigation_plan
from utils.doc_formatter import create_investigation_plan_doc
from core.llm_utils import OllamaClient # <-- Добавил импорт для лучшей организации

load_dotenv()

@click.command()
@click.option('--raport-pdf', '-r', type=click.Path(exists=True, readable=True, resolve_path=True),
              required=True, help='Путь к PDF-файлу рапорта ЕРДР.')
@click.option('--output', '-o', type=click.Path(),
              default='data/output/generated_plan.docx',
              help='Путь для сохранения сгенерированного документа Word.')
@click.option('--ollama-model', '-m', default='llama3', help='Название модели Ollama для использования.')
@click.option('--lang', '-l', type=click.Choice(['ru', 'kk'], case_sensitive=False), 
              default='ru', help='Язык для генерируемого документа (ru или kk).')
def generate_plan(raport_pdf, output, ollama_model, lang): # <-- ИСПРАВЛЕНИЕ: raport-pdf -> raport_pdf
    """
    Генерирует план досудебного расследования на основе PDF-файла рапорта ЕРДР.
    """
    # Создаем парсер, который внутри себя содержит OllamaClient
    parser = RaportParser(ollama_model=ollama_model)

    click.echo(f"Загрузка и парсинг рапорта из PDF: {raport_pdf}")
    case_data = parser.parse_raport_pdf_with_llm(raport_pdf)
    if case_data is None:
        click.echo("Произошла ошибка при извлечении данных. Прерывание.", err=True)
        return
    click.echo("Данные из рапорта успешно извлечены и структурированы.")

    click.echo(f"Генерация плана расследования на языке: {lang.upper()}")
    investigation_plan = generate_investigation_plan(case_data, lang)
    click.echo("План расследования сгенерирован.")

    output_dir = os.path.dirname(output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    try:
        create_investigation_plan_doc(investigation_plan, output, lang)
        click.echo(f"Готовый документ сохранен в: {output}")
    except Exception as e:
        click.echo(f"Ошибка при сохранении документа Word: {e}", err=True)

if __name__ == '__main__':
    generate_plan()

