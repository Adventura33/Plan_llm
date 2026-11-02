# cli/main.py

import click
import os
import sys

# Вывод всегда в UTF-8: при перенаправлении в файл (`> log.txt`) Windows берёт cp1251,
# в которой нет эмодзи-меток лога (✅, ⚠), и print падает с UnicodeEncodeError.
# line_buffering — каждая строка выводится сразу, а не порциями (ход работы виден в реальном времени)
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace', line_buffering=True)
    except (AttributeError, ValueError):
        pass


class _Tee:
    """Дублирует вывод в консоль и в файл лога (параметр --log)."""

    def __init__(self, stream, log_file):
        self._stream, self._log = stream, log_file

    def write(self, data):
        self._stream.write(data)
        self._log.write(data)
        return len(data)

    def flush(self):
        self._stream.flush()
        self._log.flush()

    def __getattr__(self, name):
        return getattr(self._stream, name)

# Добавляем корневую директорию проекта в sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import json
import requests
from dotenv import load_dotenv

from core.parser import RaportParser
from core.plan_generator import generate_investigation_plan, generate_investigation_plan_with_llm
from utils.doc_formatter import create_investigation_plan_doc
from core.llm_utils import LLMClient, DEFAULT_API_URL, DEFAULT_MODEL

# Загружаем переменные окружения из .env файла
load_dotenv()

def print_extraction_summary(case_data):
    """
    Выводит сводку извлеченных данных для проверки пользователем.
    """
    print("\n" + "="*60)
    print("СВОДКА ИЗВЛЕЧЕННЫХ ДАННЫХ")
    print("="*60)
    
    key_fields = [
        ('ФИО следователя', 'фио_следователя'),
        ('Должность следователя', 'должность_следователя'), 
        ('Дата рапорта', 'рапорт_дата'),
        ('Номер ЕРДР', 'номер_ердр'),
        ('Статья УК РК', 'статья_ук_рк'),
        ('Регион ДЭР', 'регион_дэр'),
        ('Дата регистрации ЕРДР', 'дата_регистрации_ердр'),
        ('Суть правонарушения', 'суть_правонарушения')
    ]
    
    all_missing = True
    for label, field_name in key_fields:
        value = case_data.get(field_name, 'Н/Д')
        status = "НЕ ИЗВЛЕЧЕНО" if value == 'Н/Д' else "ИЗВЛЕЧЕНО"
        
        if value != 'Н/Д':
            all_missing = False
            
        # Обрезаем длинные значения для читаемости
        display_value = value if len(str(value)) <= 80 else str(value)[:80] + "..."
        
        print(f"{label:.<25} {status}")
        if value != 'Н/Д':
            print(f"{'Значение:':>25} {display_value}")
        print()
    
    if all_missing:
        print("ВНИМАНИЕ: Не удалось извлечь ключевую информацию!")
        print("   Проверьте качество PDF файла и попробуйте другую модель LLM")
    else:
        print("Извлечение данных завершено успешно")
    
    print("="*60)

@click.command()
@click.option('--raport-pdf', '-r', type=click.Path(exists=True, readable=True, resolve_path=True),
              help='Путь к PDF-файлу рапорта ЕРДР.')
@click.option('--input-dir', '-i', type=click.Path(exists=True, file_okay=False, dir_okay=True),
              default='data/input',
              help='Папка с PDF файлами для обработки (по умолчанию: data/input).')
@click.option('--case-number', '-n', default=None,
              help='Номер уголовного дела для загрузки документов из БД.')
@click.option('--db-root', default='/root/database/workspaces',
              help='Путь к корню базы данных workspaces.')
@click.option('--output', '-o', type=click.Path(),
              default='data/output/generated_investigation_plan.docx',
              help='Путь для сохранения сгенерированного документа Word.')
@click.option('--model', '-m', default=DEFAULT_MODEL,
              help='Название модели LLM для использования.')
@click.option('--api-url', default=DEFAULT_API_URL,
              help='URL LLM API (OpenAI-совместимый endpoint).')
@click.option('--methodology-dir', '-md', type=click.Path(file_okay=False, dir_okay=True),
              default='data/methodology',
              help='Папка с файлами методик расследования (PDF/DOCX/XLSX/TXT).')
@click.option('--no-extra-actions', is_flag=True, default=False,
              help='Отключить генерацию дополнительных действий через LLM (использовать только шаблоны).')
@click.option('--pre-index-only', is_flag=True, default=False,
              help='Вариант А: Выполнить только пре-индексацию (парсинг, OCR, RAG), отложив живую генерацию действий.')
@click.option('--debug', is_flag=True, help='Включить подробный вывод для отладки.')
def generate_plan(raport_pdf, input_dir, case_number, db_root, output, model, api_url, methodology_dir, no_extra_actions, pre_index_only, debug):
    """
    Генерирует план досудебного расследования уголовного дела на основе PDF-файла рапорта ЕРДР или из БД,
    используя LLM API для извлечения данных.
    """
    
    if debug:
        print("Режим отладки включен")

    # Проверка доступности LLM API
    try:
        print(f"Проверяю доступность LLM API ({api_url})...")
        test_data = {
            "model": model,
            "messages": [{"role": "user", "content": "тест"}],
            "temperature": 0.0
        }
        test_endpoint = f"{api_url}/chat/completions" if not api_url.endswith('/chat/completions') else api_url
        test_response = requests.post(test_endpoint, json=test_data, timeout=30)
        
        if test_response.status_code == 200:
            print(f"LLM API доступен, модель: {model}")
        else:
            click.echo(f"LLM API вернул ошибку (HTTP {test_response.status_code}): {test_response.text}", err=True)
            return
            
    except requests.exceptions.ConnectionError as e:
        click.echo(f"Не удалось подключиться к LLM API: {e}", err=True)
        click.echo(f"   Проверьте доступность сервера: {api_url}", err=True)
        return
    except requests.exceptions.Timeout:
        click.echo("Таймаут при проверке LLM API", err=True)
        return
    except Exception as e:
        click.echo(f"Ошибка подключения к LLM API: {e}", err=True)
        return

    # Определяем какой файл обрабатывать или берем из БД
    pdf_file_to_process = None
    case_data = None
    main_pdf_path = None
    
    if case_number:
        print(f"Загружаю документы дела №{case_number} из БД ({db_root})...")
        from core.case_documents import load_case_documents_from_db
        try:
            all_db_docs = load_case_documents_from_db(case_number, db_root=db_root)
        except Exception as e:
            click.echo(f"❌ Ошибка загрузки документов дела из БД: {e}", err=True)
            return
            
        if not all_db_docs:
            click.echo(f"❌ В базе данных для дела №{case_number} не найдено документов.", err=True)
            return
            
        # Поиск главного рапорта в загруженных документах БД
        raport_doc = None
        try:
            from core.case_documents import score_raport_filename
            db_docs_sorted = sorted(all_db_docs, key=lambda x: score_raport_filename(x["filename"]), reverse=True)
        except Exception:
            db_docs_sorted = all_db_docs
            
        for doc in db_docs_sorted:
            if doc.get("doc_type") == "рапорт_ердр":
                raport_doc = doc
                break
                
        if not raport_doc and db_docs_sorted:
            raport_doc = db_docs_sorted[0]
            
        if not raport_doc:
            click.echo("❌ Не удалось определить главный рапорт ЕРДР в базе данных.", err=True)
            return
            
        main_pdf_path = raport_doc.get("file_path")
        raport_text = raport_doc["text"]
        raport_filename = raport_doc["filename"]
        
        # Парсинг рапорта ЕРДР из текста
        parser = RaportParser(api_url=api_url, model=model)
        case_data = parser.parse_raport_text_with_llm(raport_text, filename=raport_filename)
        
        if not case_data:
            click.echo("❌ Не удалось извлечь данные из главного рапорта в базе данных.", err=True)
            return
            
        # Заполняем номер дела, если его не удалось распознать
        if not case_data.get("номер_ердр") or case_data["номер_ердр"] == "Н/Д":
            case_data["номер_ердр"] = case_number
            
        print_extraction_summary(case_data)
        
    else:
        if raport_pdf:
            pdf_file_to_process = raport_pdf
            print(f"Обрабатываю указанный PDF файл: {raport_pdf}")
        else:
            # Ищем PDF файлы в папке input
            if not os.path.exists(input_dir):
                click.echo(f"Папка {input_dir} не существует", err=True)
                return
                
            pdf_files = [f for f in os.listdir(input_dir) if f.lower().endswith('.pdf')]
            
            if not pdf_files:
                click.echo(f"В папке {input_dir} не найдено PDF файлов", err=True)
                return
            elif len(pdf_files) == 1:
                pdf_file_to_process = os.path.join(input_dir, pdf_files[0])
                print(f"Найден единичный PDF файл в папке input: {pdf_files[0]}")
            else:
                print(f"Найдено {len(pdf_files)} PDF файлов в папке {input_dir}. Выполняю автопоиск Рапорта ЕРДР...")
                from core.case_documents import detect_document_type, score_raport_filename
                from core.methodology_loader import load_pdf_file
                
                raport_file = None
                
                # Сортируем файлы по приоритету (Уведомления < 0, КУИ = 5, ЕРДР = 20, Рапорт = 50)
                pdf_files_sorted = sorted(pdf_files, key=score_raport_filename, reverse=True)
                
                # Сначала ищем файл с положительным рейтингом
                for filename in pdf_files_sorted:
                    score = score_raport_filename(filename)
                    if score > 0 or detect_document_type(filename, "") == "рапорт_ердр":
                        raport_file = filename
                        break
                
                # Если по имени не нашли, углубляемся в чтение текста
                if not raport_file:
                    for filename in pdf_files_sorted:
                        if score_raport_filename(filename) < 0:
                            continue # точно пропускаем уведомления и талоны
                        try:
                            file_path = os.path.join(input_dir, filename)
                            text = load_pdf_file(file_path)
                            if text and detect_document_type(filename, text[:2000]) == "рапорт_ердр":
                                raport_file = filename
                                break
                        except Exception:
                            continue
                            
                if raport_file:
                    pdf_file_to_process = os.path.join(input_dir, raport_file)
                    print(f"✅ Автоматически найден Главный Рапорт ЕРДР: {raport_file}")
                else:
                    click.echo("⚠ Не удалось точно определить Рапорт ЕРДР среди файлов (отсутствуют ключевые слова). Беру первый файл по списку.", err=True)
                    pdf_file_to_process = os.path.join(input_dir, pdf_files[0])

        # Создаем парсер и обрабатываем файл
        parser = RaportParser(api_url=api_url, model=model)

        print(f"\nНачинаю извлечение данных из PDF...")
        print(f"   Файл: {pdf_file_to_process}")
        print(f"   Модель: {model}")
        
        try:
            case_data = parser.parse_raport_pdf_with_llm(pdf_file_to_process)
            
            if case_data is None:
                click.echo("Не удалось извлечь данные из PDF файла", err=True)
                click.echo("   Возможные причины:")
                click.echo("   - PDF файл поврежден или защищен паролем")
                click.echo("   - PDF содержит только изображения (нужно OCR)")
                click.echo("   - Проблемы с моделью LLM")
                return

            # Выводим сводку извлеченных данных
            print_extraction_summary(case_data)
            
            if debug:
                click.echo("\nОТЛАДОЧНАЯ ИНФОРМАЦИЯ:")
                click.echo("   Полные извлеченные данные:")
                for key, value in case_data.items():
                    click.echo(f"     {key}: {value}")
            
        except Exception as e:
            click.echo(f"Критическая ошибка при обработке PDF: {e}", err=True)
            if debug:
                import traceback
                click.echo("Полная трассировка ошибки:")
                click.echo(traceback.format_exc())
            return

    # Генерируем план расследования
    print("\nГенерирую план расследования...")
    try:
        if no_extra_actions:
            print("Режим: только базовые действия из шаблонов")
            investigation_plan = generate_investigation_plan(case_data)
        else:
            print("Режим: базовые действия + дополнительные из методик и документов дела")
            llm_client = LLMClient(api_url=api_url, model=model)
            if case_number:
                investigation_plan = generate_investigation_plan_with_llm(
                    case_data=case_data,
                    llm_client=llm_client,
                    methodology_dir=methodology_dir,
                    main_pdf_path=main_pdf_path,
                    pre_index_only=pre_index_only,
                    case_number=case_number,
                    db_root=db_root
                )
            else:
                print(f"Папка методик: {methodology_dir}")
                print(f"Папка документов дела: {input_dir}")
                investigation_plan = generate_investigation_plan_with_llm(
                    case_data=case_data,
                    llm_client=llm_client,
                    methodology_dir=methodology_dir,
                    input_dir=input_dir,
                    main_pdf_path=pdf_file_to_process,
                    pre_index_only=pre_index_only
                )
            
        if pre_index_only:
            print("\n🏁 Вариант А: Пре-индексация успешно завершена! Документы готовы к показу 'в прямом эфире'.")
            return
        
        print(f"План сгенерирован ({len(investigation_plan.get('actions', []))} действий)")
        
    except Exception as e:
        click.echo(f"Ошибка при генерации плана: {e}", err=True)
        if debug:
            import traceback
            click.echo(traceback.format_exc())
        return

    # Создаем директорию для вывода, если ее нет
    output_dir = os.path.dirname(output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)
        print(f"Создана папка: {output_dir}")

    # Сохраняем документ Word
    print(f"\nСохраняю документ: {output}")
    try:
        if create_investigation_plan_doc(investigation_plan, output):
            print("Документ успешно сохранен!")
        else:
            click.echo(f"❌ Документ НЕ сохранён: {output}. Если файл открыт в Word — закройте его. "
                       f"План сохранён в data/plans/ (JSON).", err=True)
        
        # Выводим краткую статистику
        actions_count = len(investigation_plan.get('actions', []))
        print(f"\nСТАТИСТИКА:")
        print(f"   Количество действий в плане: {actions_count}")
        print(f"   Номер дела: {investigation_plan['plan_title_info'].get('номер_дела', 'Н/Д')}")
        print(f"   Статья УК РК: {investigation_plan['plan_title_info'].get('статья_ук_рк', 'Н/Д')}")
        if investigation_plan.get('ошибка_генерации'):
            click.echo(f"\n❌ ПЛАН НЕ СФОРМИРОВАН: {investigation_plan['ошибка_генерации']} "
                       f"В документе только обязательные и уже проведённые действия.", err=True)
            sys.exit(1)

    except Exception as e:
        click.echo(f"Ошибка при сохранении документа: {e}", err=True)
        if debug:
            import traceback
            click.echo("Полная трассировка ошибки:")
            click.echo(traceback.format_exc())

@click.command()
@click.option('--model', '-m', default=DEFAULT_MODEL, help='Модель LLM для тестирования.')
@click.option('--api-url', default=DEFAULT_API_URL, help='URL LLM API.')
def test_extraction(model, api_url):
    """
    Тестирует извлечение данных на примере текста рапорта.
    """
    test_text = """
    РАПОРТ об обнаружении сведений об уголовном правонарушении
    
    5 октября 2023 г.                                г. Астана
    
    Я, Сарсенбаев М.Р., следователь по ОВД 2-го Следственного управления 
    Департамента экономических расследований по г. Астана Агентства по 
    финансовому мониторингу РК, докладываю об обнаружении сведений об 
    уголовном правонарушении.
    
    Данные действия содержат признаки уголовного правонарушения, 
    предусмотренного ст. 217 ч.2 п.1 УК РК.
    
    Сведения зарегистрированы в ЕРДР за № 237100121000075 
    05.10.2023г. в 17:28.
    """
    
    print(f"Тестирую извлечение данных с моделью: {model}")
    
    from core.llm_utils import LLMClient
    
    client = LLMClient(api_url=api_url, model=model)
    result = client.extract_case_data_with_retry(test_text)
    
    if result:
        print("Тест прошел успешно!")
        print_extraction_summary(result)
    else:
        print("Тест не пройден - не удалось извлечь данные")

# Добавляем группу команд
@click.group()
@click.option('--log', 'log_path', default=None, type=click.Path(dir_okay=False),
              help='Файл лога: вывод показывается в терминале и одновременно сохраняется в файл (UTF-8).')
def cli(log_path):
    """Генератор планов расследования уголовных дел"""
    if log_path:
        log_dir = os.path.dirname(log_path)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        log_file = open(log_path, 'w', encoding='utf-8', buffering=1)
        sys.stdout = _Tee(sys.stdout, log_file)
        sys.stderr = _Tee(sys.stderr, log_file)
        print(f"📝 Лог пишется в файл: {log_path}")


@click.command()
@click.option('--plan', '-p', default=None,
              help='Путь к JSON-файлу плана. Если не указан — ищется автоматически.')
@click.option('--case-number', '-n', default=None,
              help='Номер дела (ЕРДР). Ищет data/plans/{номер}.json автоматически.')
@click.option('--new-docs', '-d', multiple=True,
              help='Путь к новым документам (файл или папка). Можно указывать несколько раз.')
@click.option('--db-root', default='/root/database/workspaces',
              help='Путь к корню базы данных workspaces.')
@click.option('--output', '-o', default=None,
              help='Путь для сохранения обновлённого .docx.')
@click.option('--model', '-m', default=DEFAULT_MODEL, help='Модель LLM.')
@click.option('--api-url', default=DEFAULT_API_URL, help='URL LLM API.')
@click.option('--methodology-dir', default='data/methodology', help='Папка с методиками.')
@click.option('--pre-index-only', is_flag=True, default=False, help='Вариант А: Выполнить только пре-индексацию дозагрузки.')
def append_to_plan(plan, case_number, new_docs, db_root, output, model, api_url, methodology_dir, pre_index_only):
    """
    Дозагружает документы и добавляет новые пункты в существующий план (из локальных файлов или БД).

    Примеры:
        # По номеру дела из БД автоматически:
        python -m cli.main append_to_plan -n 245510010000060

        # По номеру дела из локальной папки:
        python -m cli.main append_to_plan -n 252700121000021 -d data/input/new/
    """
    from core.plan_generator import append_documents_to_plan
    from utils.doc_formatter import create_investigation_plan_doc

    plans_dir = 'data/plans'

    # --- Авто-поиск файла плана ---
    if plan is None:
        if case_number:
            # По номеру дела
            plan = os.path.join(plans_dir, f"{case_number}.json")
            if not os.path.exists(plan):
                click.echo(f"❌ План для дела №{case_number} не найден: {plan}", err=True)
                return
        else:
            # Берём самый свежий план в папке
            if not os.path.exists(plans_dir):
                click.echo(f"❌ Папка планов не найдена: {plans_dir}", err=True)
                return
            json_files = [
                os.path.join(plans_dir, f)
                for f in os.listdir(plans_dir)
                if f.endswith('.json')
            ]
            if not json_files:
                click.echo(f"❌ В папке {plans_dir} нет сохранённых планов. Сначала сгенерируйте план.", err=True)
                return
            plan = max(json_files, key=os.path.getmtime)
            print(f"🔍 Автоматически выбран план: {plan}")

    if not os.path.exists(plan):
        click.echo(f"❌ Файл плана не найден: {plan}", err=True)
        return

    # Загружаем текущий план
    with open(plan, "r", encoding="utf-8") as f:
        plan_data = json.load(f)

    if not case_number:
        case_number = plan_data.get('plan_title_info', {}).get('номер_дела') or plan_data.get('case_data', {}).get('номер_ердр')
        if not case_number:
            case_number = os.path.splitext(os.path.basename(plan))[0]

    if not new_docs and not case_number:
        click.echo("❌ Ошибка: Укажите либо --new-docs, либо --case-number для автоматической дозагрузки из БД", err=True)
        return

    print(f"\n{'='*60}")
    print("ДОЗАГРУЗКА ДОКУМЕНТОВ В ПЛАН")
    print(f"{'='*60}")
    print(f"  Текущий план: {plan}")
    if new_docs:
        print(f"  Новые документы (локальные пути): {list(new_docs)}")
    else:
        print(f"  Режим автоматической дозагрузки из БД для дела №{case_number}")

    llm_client = LLMClient(api_url=api_url, model=model)

    if not new_docs:
        # Читаем документы из БД
        from core.case_documents import load_case_documents_from_db
        try:
            all_db_docs = load_case_documents_from_db(case_number, db_root=db_root)
        except Exception as e:
            click.echo(f"❌ Ошибка загрузки документов дела из БД: {e}", err=True)
            return

        processed_docs = plan_data.get('processed_docs', [])
        new_db_docs = [doc for doc in all_db_docs if doc.get("filename") not in processed_docs]

        if not new_db_docs:
            print("  ℹ Новых документов в БД не обнаружено. План актуален.")
            updated_plan = plan_data
        else:
            print(f"  📥 Обнаружено {len(new_db_docs)} новых документов в БД для дозагрузки: {[d.get('filename') for d in new_db_docs]}")
            updated_plan = append_documents_to_plan(
                plan_path=plan,
                llm_client=llm_client,
                methodology_dir=methodology_dir,
                new_db_docs=new_db_docs,
                pre_index_only=pre_index_only
            )
    else:
        for doc_path in new_docs:
            if not os.path.exists(doc_path):
                click.echo(f"❌ Путь к документам не найден: {doc_path}", err=True)
                return

        updated_plan = append_documents_to_plan(
            plan_path=plan,
            new_docs_paths=list(new_docs),
            llm_client=llm_client,
            methodology_dir=methodology_dir,
            pre_index_only=pre_index_only
        )

    if pre_index_only:
        return

    # Сохраняем обновлённый .docx
    номер = updated_plan.get('plan_title_info', {}).get('номер_дела', 'unknown')
    if output is None:
        output = os.path.join('data', 'output', f"plan_{номер}_updated.docx")

    output_dir = os.path.dirname(output)
    if output_dir and not os.path.exists(output_dir):
        os.makedirs(output_dir)

    success = create_investigation_plan_doc(updated_plan, output)
    if success:
        print(f"\n✅ Обновлённый документ сохранён: {output}")
        print(f"   Всего пунктов в плане: {len(updated_plan.get('actions', []))}")
    else:
        click.echo("❌ Ошибка при сохранении документа", err=True)


@click.command(name="build-plan")
@click.option('--case-number', '-n', required=True,
              help='Номер дела (ЕРДР).')
@click.option('--append', is_flag=True, default=False,
              help='Запустить генерацию дозагрузки (продолжение плана).')
@click.option('--db-root', default='/root/database/workspaces',
              help='Путь к корню базы данных workspaces.')
@click.option('--output', '-o', default=None,
              help='Путь для сохранения итогового .docx.')
@click.option('--model', '-m', default=DEFAULT_MODEL, help='Модель LLM.')
@click.option('--api-url', default=DEFAULT_API_URL, help='URL LLM API.')
@click.option('--methodology-dir', default='data/methodology', help='Папка с методиками.')
@click.option('--no-extra-actions', is_flag=True, default=False,
              help='Отключить генерацию дополнительных действий через LLM (использовать только шаблоны).')
def build_plan(case_number, append, db_root, output, model, api_url, methodology_dir, no_extra_actions):
    """
    Генерирует план досудебного расследования уголовного дела из БД (первичный или продолжение).
    """
    import os
    import json
    from core.plan_generator import (
        generate_investigation_plan,
        generate_investigation_plan_with_llm,
        append_documents_to_plan,
        sanitize_filename
    )
    from utils.doc_formatter import create_investigation_plan_doc
    from core.llm_utils import LLMClient
    from core.parser import RaportParser

    # Проверка доступности LLM API
    try:
        print(f"Проверяю доступность LLM API ({api_url})...")
        test_data = {
            "model": model,
            "messages": [{"role": "user", "content": "тест"}],
            "temperature": 0.0
        }
        test_endpoint = f"{api_url}/chat/completions" if not api_url.endswith('/chat/completions') else api_url
        test_response = requests.post(test_endpoint, json=test_data, timeout=30)
        
        if test_response.status_code == 200:
            print(f"LLM API доступен, модель: {model}")
        else:
            click.echo(f"LLM API вернул ошибку (HTTP {test_response.status_code}): {test_response.text}", err=True)
            return
            
    except Exception as e:
        click.echo(f"Ошибка подключения к LLM API: {e}", err=True)
        return

    llm_client = LLMClient(api_url=api_url, model=model)

    if append:
        # === РЕЖИМ ПРОДОЛЖЕНИЯ ПЛАНА (APPEND) ===
        db_plan_path = os.path.join(db_root, case_number, "plan.json")
        local_plan_path = os.path.join('data/plans', f"{case_number}.json")
        
        if os.path.exists(db_plan_path):
            plan_path = db_plan_path
        elif os.path.exists(local_plan_path):
            plan_path = local_plan_path
        else:
            click.echo(f"❌ Существующий план для дела №{case_number} не найден (ни в БД {db_plan_path}, ни локально {local_plan_path}). Сначала создайте изначальный план.", err=True)
            return
            
        print(f"\n{'='*60}")
        print(f"ПРОДОЛЖЕНИЕ ПЛАНА ДЛЯ ДЕЛА №{case_number} (ДОЗАГРУЗКА)")
        print(f"{'='*60}")
        print(f"  Существующий план: {plan_path}")
        
        with open(plan_path, "r", encoding="utf-8") as f:
            plan_data = json.load(f)
            
        # Читаем документы из БД
        from core.case_documents import load_case_documents_from_db
        try:
            all_db_docs = load_case_documents_from_db(case_number, db_root=db_root)
        except Exception as e:
            click.echo(f"❌ Ошибка загрузки документов дела из БД: {e}", err=True)
            return
            
        processed_docs = plan_data.get('processed_docs', [])
        new_db_docs = [doc for doc in all_db_docs if doc.get("filename") not in processed_docs]
        
        if not new_db_docs:
            print("  ℹ Новых документов в БД не обнаружено. План актуален.")
            updated_plan = plan_data
        else:
            print(f"  📥 Обнаружено {len(new_db_docs)} новых документов в БД для дозагрузки: {[d.get('filename') for d in new_db_docs]}")
            updated_plan = append_documents_to_plan(
                plan_path=plan_path,
                llm_client=llm_client,
                methodology_dir=methodology_dir,
                new_db_docs=new_db_docs
            )
            
        номер = sanitize_filename(case_number)
        if output is None:
            output = os.path.join('data', 'output', f"plan_{номер}_updated.docx")
            
        output_dir = os.path.dirname(output)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
            
        success = create_investigation_plan_doc(updated_plan, output)
        if success:
            print(f"\n✅ Обновлённый документ сохранён: {output}")
            print(f"   Всего пунктов в плане: {len(updated_plan.get('actions', []))}")
        else:
            click.echo("❌ Ошибка при сохранении документа", err=True)

    else:
        # === РЕЖИМ НАЧАЛЬНОГО ПЛАНА (INITIAL) ===
        print(f"\n{'='*60}")
        print(f"ГЕНЕРАЦИЯ НАЧАЛЬНОГО ПЛАНА ДЛЯ ДЕЛА №{case_number}")
        print(f"{'='*60}")
        
        from core.case_documents import load_case_documents_from_db
        try:
            all_db_docs = load_case_documents_from_db(case_number, db_root=db_root)
        except Exception as e:
            click.echo(f"❌ Ошибка загрузки документов дела из БД: {e}", err=True)
            return
            
        if not all_db_docs:
            click.echo(f"❌ В базе данных для дела №{case_number} не найдено документов.", err=True)
            return
            
        # Поиск главного рапорта в загруженных документах БД
        raport_doc = None
        try:
            from core.case_documents import score_raport_filename
            db_docs_sorted = sorted(all_db_docs, key=lambda x: score_raport_filename(x["filename"]), reverse=True)
        except Exception:
            db_docs_sorted = all_db_docs
            
        for doc in db_docs_sorted:
            if doc.get("doc_type") == "рапорт_ердр":
                raport_doc = doc
                break
                
        if not raport_doc and db_docs_sorted:
            raport_doc = db_docs_sorted[0]
            
        if not raport_doc:
            click.echo("❌ Не удалось определить главный рапорт ЕРДР в базе данных.", err=True)
            return
            
        main_pdf_path = raport_doc.get("file_path")
        raport_text = raport_doc["text"]
        raport_filename = raport_doc["filename"]
        
        # Парсинг рапорта ЕРДР из текста
        parser = RaportParser(api_url=api_url, model=model)
        case_data = parser.parse_raport_text_with_llm(raport_text, filename=raport_filename)
        
        if not case_data:
            click.echo("❌ Не удалось извлечь данные из главного рапорта в базе данных.", err=True)
            return
            
        # Заполняем номер дела, если его не удалось распознать
        if not case_data.get("номер_ердр") or case_data["номер_ердр"] == "Н/Д":
            case_data["номер_ердр"] = case_number
            
        print_extraction_summary(case_data)

        print("\nГенерирую план расследования...")
        try:
            if no_extra_actions:
                print("Режим: только базовые действия из шаблонов")
                investigation_plan = generate_investigation_plan(case_data)
            else:
                print("Режим: базовые действия + дополнительные из методик и документов дела")
                investigation_plan = generate_investigation_plan_with_llm(
                    case_data=case_data,
                    llm_client=llm_client,
                    methodology_dir=methodology_dir,
                    main_pdf_path=main_pdf_path,
                    case_number=case_number,
                    db_root=db_root
                )
                
            print(f"План сгенерирован ({len(investigation_plan.get('actions', []))} действий)")
            
        except Exception as e:
            click.echo(f"Ошибка при генерации плана: {e}", err=True)
            return

        номер = sanitize_filename(case_number)
        if output is None:
            output = os.path.join('data', 'output', f"plan_{номер}_live.docx")

        output_dir = os.path.dirname(output)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)

        # Сохраняем документ Word
        print(f"\nСохраняю документ: {output}")
        try:
            if create_investigation_plan_doc(investigation_plan, output):
                print("Документ успешно сохранен!")
            else:
                click.echo(f"❌ Документ НЕ сохранён: {output}. Если файл открыт в Word — закройте его. "
                           f"План сохранён в data/plans/ (JSON).", err=True)
            
            # Выводим краткую статистику
            actions_count = len(investigation_plan.get('actions', []))
            print(f"\nСТАТИСТИКА:")
            print(f"   Количество действий в плане: {actions_count}")
            print(f"   Номер дела: {investigation_plan['plan_title_info'].get('номер_дела', 'Н/Д')}")
            print(f"   Статья УК РК: {investigation_plan['plan_title_info'].get('статья_ук_рк', 'Н/Д')}")
            if investigation_plan.get('ошибка_генерации'):
                click.echo(f"\n❌ ПЛАН НЕ СФОРМИРОВАН: {investigation_plan['ошибка_генерации']} "
                           f"В документе только обязательные и уже проведённые действия.", err=True)
                sys.exit(1)

        except Exception as e:
            click.echo(f"Ошибка при сохранении документа: {e}", err=True)


cli.add_command(generate_plan)
cli.add_command(test_extraction)
cli.add_command(append_to_plan)

cli.add_command(build_plan)

if __name__ == '__main__':
    cli()

