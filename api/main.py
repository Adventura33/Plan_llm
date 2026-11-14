import os
import sys

# Вывод в UTF-8: при запуске службой/с перенаправлением лога Windows берёт cp1251,
# и print с эмодзи-метками (✅, ⚠) падает с UnicodeEncodeError посреди генерации плана
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError):
        pass

# Force tiktoken to use the local offline cache directory
os.environ["TIKTOKEN_CACHE_DIR"] = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "tiktoken_cache"))

import shutil
import tempfile
import json
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.openapi.docs import get_swagger_ui_html
from pydantic import BaseModel

from core.parser import RaportParser
from core.plan_generator import generate_investigation_plan_with_llm
from utils.doc_formatter import create_investigation_plan_doc
from core.llm_utils import LLMClient, DEFAULT_API_URL, DEFAULT_MODEL

from contextlib import asynccontextmanager
import re

# ─────────────────────────────────────────────────────────────────────────────
# Конфигурация сервера. Адрес LLM и пути на сервере задаются ТОЛЬКО здесь
# (переменные окружения), а не клиентом: иначе клиент мог бы отправить материалы дела
# на свой сервер (api_url) или заставить сервис читать произвольные папки (methodology_dir, db_root).
# ─────────────────────────────────────────────────────────────────────────────
LLM_API_URL = os.environ.get("LLM_API_URL", DEFAULT_API_URL)
LLM_MODEL = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
METHODOLOGY_DIR = os.environ.get("METHODOLOGY_DIR", "data/methodology")
DB_ROOT = os.environ.get("DB_ROOT", "/root/database/workspaces")

# Номер дела / идентификатор задачи подставляется в пути — только буквы, цифры, «-», «_»
_CASE_ID_RE = re.compile(r'^[0-9A-Za-zА-Яа-яЁё_\-]{1,64}$')


def _safe_case_id(value: Optional[str], field: str = "номер дела") -> str:
    value = (value or "").strip()
    if not _CASE_ID_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимый {field}: разрешены буквы, цифры, «-» и «_» (до 64 символов)."
        )
    return value


def _safe_upload_name(filename: Optional[str]) -> str:
    """Имя загружаемого файла без каталогов: «../../x.pdf» -> «x.pdf»."""
    name = os.path.basename((filename or "").replace("\\", "/")).strip()
    if not name or name in (".", ".."):
        raise HTTPException(status_code=400, detail=f"Недопустимое имя файла: {filename!r}")
    return name


def _plan_response(plan: dict) -> dict:
    """
    JSON плана для клиента: статусы, пункты по блокам (у пункта — «блок_номер», «блок_код», «блок»),
    блоки со счётчиками (plan['blocks']) и статистика исполнения на сегодня (plan['statistics']).
    """
    from core.plan_blocks import attach_blocks
    from core.plan_generator import normalize_plan_statuses
    from core.plan_statistics import attach_statistics
    return attach_statistics(attach_blocks(normalize_plan_statuses(plan)))


def _load_saved_plan(case_number: str) -> Optional[dict]:
    """Сохранённый план дела: из PostgreSQL, иначе из data/plans/{номер}.json; None — плана нет."""
    from core.db import load_plan_from_db
    from core.plan_generator import PLANS_DIR, sanitize_filename
    plan = load_plan_from_db(case_number)
    if plan:
        return plan
    plan_path = os.path.join(PLANS_DIR, f"{sanitize_filename(case_number)}.json")
    if not os.path.exists(plan_path):
        return None
    try:
        with open(plan_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка чтения плана: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from core.db import init_db
    import asyncio
    asyncio.create_task(init_db())
    yield

app = FastAPI(
    title="Investigation Plan Generator API",
    description="API for generating investigation plans from Kazakhstani legal PDFs",
    version="1.0.0",
    docs_url=None,  # Disable default docs
    redoc_url=None, # Disable default redoc
    lifespan=lifespan,
)

# Mount static files to serve JS and CSS locally
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/docs", include_in_schema=False)
async def custom_swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=app.title + " - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="/static/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger-ui.css",
    )

# Enable CORS for the frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Recommend narrowing this down in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def cleanup_temp_dir(dir_path: str):
    """Background task to securely remove temporary files after response is sent."""
    if os.path.exists(dir_path):
        import shutil
        shutil.rmtree(dir_path, ignore_errors=True)

@app.post("/api/generate_plan")
def api_generate_plan(
    background_tasks: BackgroundTasks,
    documents: Optional[List[UploadFile]] = File(None),
    case_number: Optional[str] = Form(None),
    mode: str = Form("initial"), # "initial" или "append"
    response_format: str = Form("docx"), # "docx", "json", "text"
):
    """
    Generate an investigation plan docx based on a batch of uploaded documents
    OR directly from a pre-indexed local LightRAG database workspace for a case.
    """
    if not documents and not case_number:
        raise HTTPException(status_code=400, detail="Необходимо предоставить либо список документов (documents), либо номер дела (case_number).")
    if case_number:
        case_number = _safe_case_id(case_number)
    model, api_url, methodology_dir, db_root = LLM_MODEL, LLM_API_URL, METHODOLOGY_DIR, DB_ROOT

    session_dir = tempfile.mkdtemp(prefix="plan_llm_")
    background_tasks.add_task(cleanup_temp_dir, session_dir)
    output_docx_path = os.path.join(session_dir, "generated_investigation_plan.docx")

    try:
        if case_number:
            # === РЕЖИМ БАЗЫ ДАННЫХ ===
            from core.case_documents import load_case_documents_from_db
            
            all_db_docs = load_case_documents_from_db(case_number, db_root=db_root)
            if not all_db_docs:
                raise HTTPException(
                    status_code=404, 
                    detail=f"Не удалось найти документы дела №{case_number} в БД."
                )

            if mode == "append":
                # === РЕЖИМ ДОЗАГРУЗКИ (ПРОДОЛЖЕНИЕ ПЛАНА) ===
                from core.plan_generator import append_documents_to_plan, PLANS_DIR
                import json
                
                from core.db import load_plan_from_db
                plan = load_plan_from_db(case_number)
                plan_path = os.path.join(PLANS_DIR, f"{case_number}.json")
                if not plan:
                    if not os.path.exists(plan_path):
                        raise HTTPException(
                            status_code=404,
                            detail=f"План для уголовного дела №{case_number} не найден. Сначала выполните первичную генерацию (mode='initial')."
                        )
                    with open(plan_path, "r", encoding="utf-8") as f:
                        plan = json.load(f)
                    
                case_data = plan.get('case_data', {})
                processed_docs = plan.get('processed_docs', [])
                
                # Находим новые документы в БД, которых нет в processed_docs
                new_db_docs = [doc for doc in all_db_docs if doc.get("filename") not in processed_docs]
                
                if not new_db_docs:
                    print("  ℹ Новых документов в БД не обнаружено. Возвращаю текущий план.")
                    investigation_plan = plan
                else:
                    print(f"  📥 Обнаружено {len(new_db_docs)} новых документов в БД для дозагрузки: {[d.get('filename') for d in new_db_docs]}")
                    llm_client = LLMClient(api_url=api_url, model=model)
                    safe_methodology_dir = methodology_dir if os.path.exists(methodology_dir) else "data/methodology"
                    
                    investigation_plan = append_documents_to_plan(
                        plan_path=plan_path,
                        llm_client=llm_client,
                        methodology_dir=safe_methodology_dir,
                        new_db_docs=new_db_docs
                    )

            elif mode == "update_status":
                # === РЕЖИМ ОБНОВЛЕНИЯ СТАТУСОВ (без генерации новых действий) ===
                from core.plan_generator import update_plan_status, PLANS_DIR
                import json

                from core.db import load_plan_from_db
                plan = load_plan_from_db(case_number)
                plan_path = os.path.join(PLANS_DIR, f"{case_number}.json")
                if not plan:
                    if not os.path.exists(plan_path):
                        raise HTTPException(
                            status_code=404,
                            detail=f"План для уголовного дела №{case_number} не найден. Сначала выполните первичную генерацию (mode='initial')."
                        )
                    with open(plan_path, "r", encoding="utf-8") as f:
                        plan = json.load(f)

                processed_docs = plan.get('processed_docs', [])

                # Находим новые документы в БД
                new_db_docs = [doc for doc in all_db_docs if doc.get("filename") not in processed_docs]

                if not new_db_docs:
                    print("  ℹ Новых документов в БД не обнаружено. Возвращаю текущий план без изменений.")
                    investigation_plan = plan
                else:
                    print(f"  📥 Обнаружено {len(new_db_docs)} новых документов для обновления статусов: {[d.get('filename') for d in new_db_docs]}")
                    llm_client = LLMClient(api_url=api_url, model=model)

                    investigation_plan = update_plan_status(
                        plan_path=plan_path,
                        llm_client=llm_client,
                        new_db_docs=new_db_docs
                    )

            else:
                # === ПЕРВИЧНАЯ ГЕНЕРАЦИЯ ИЗ БД ===
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
                    raise HTTPException(status_code=400, detail="Не удалось определить главный рапорт ЕРДР в базе данных.")
                    
                main_pdf_path = raport_doc.get("file_path")
                raport_text = raport_doc["text"]
                raport_filename = raport_doc["filename"]
                
                # Парсинг рапорта ЕРДР из текста
                parser = RaportParser(api_url=api_url, model=model)
                case_data = parser.parse_raport_text_with_llm(raport_text, filename=raport_filename)
                
                if not case_data:
                    raise HTTPException(status_code=400, detail="Не удалось извлечь данные из главного рапорта в базе данных.")
                    
                # Заполняем номер дела, если его не удалось распознать
                if not case_data.get("номер_ердр") or case_data["номер_ердр"] == "Н/Д":
                    case_data["номер_ердр"] = case_number
                    
                llm_client = LLMClient(api_url=api_url, model=model)
                safe_methodology_dir = methodology_dir if os.path.exists(methodology_dir) else "data/methodology"
                
                # Генерация плана с использованием БД
                investigation_plan = generate_investigation_plan_with_llm(
                    case_data=case_data,
                    llm_client=llm_client,
                    methodology_dir=safe_methodology_dir,
                    main_pdf_path=main_pdf_path,
                    case_number=case_number,
                    db_root=db_root
                )
            
        else:
            # === ОРИГИНАЛЬНЫЙ РЕЖИМ ЗАГРУЗКИ ФАЙЛОВ ===
            input_dir = os.path.join(session_dir, "input")
            os.makedirs(input_dir, exist_ok=True)
            
            pdf_files = []
            for doc in documents:
                if doc.filename:
                    safe_name = _safe_upload_name(doc.filename)
                    doc_path = os.path.join(input_dir, safe_name)
                    with open(doc_path, "wb") as buffer:
                        shutil.copyfileobj(doc.file, buffer)
                    if safe_name.lower().endswith('.pdf'):
                        pdf_files.append(safe_name)

            if not pdf_files:
                raise HTTPException(status_code=400, detail="В загруженных документах нет файлов формата PDF.")

            from core.case_documents import detect_document_type
            from core.methodology_loader import load_pdf_file
                    
            raport_file = None
            try:
                from core.case_documents import score_raport_filename
                pdf_files_sorted = sorted(pdf_files, key=score_raport_filename, reverse=True)
            except ImportError:
                pdf_files_sorted = pdf_files
                
            for filename in pdf_files_sorted:
                score = 0
                try:
                    from core.case_documents import score_raport_filename
                    score = score_raport_filename(filename)
                except ImportError:
                    pass
                    
                if score > 0 or detect_document_type(filename, "") == "рапорт_ердр":
                    raport_file = filename
                    break
                    
            if not raport_file:
                for filename in pdf_files_sorted:
                    try:
                        from core.case_documents import score_raport_filename
                        if score_raport_filename(filename) < 0:
                            continue
                    except ImportError:
                        pass
                        
                    try:
                        file_path = os.path.join(input_dir, filename)
                        text = load_pdf_file(file_path)
                        if text and detect_document_type(filename, text[:2000]) == "рапорт_ердр":
                            raport_file = filename
                            break
                    except Exception:
                        continue
                        
            if raport_file:
                main_pdf_path = os.path.join(input_dir, raport_file)
                print(f"✅ Валидация API: Автоматически определен Главный Рапорт ЕРДР: {raport_file}")
            else:
                main_pdf_path = os.path.join(input_dir, pdf_files[0])
                print(f"⚠ Валидация API: Рапорт ЕРДР не найден среди ключей. Взят первый файл: {pdf_files[0]}")

            parser = RaportParser(api_url=api_url, model=model)
            case_data = parser.parse_raport_pdf_with_llm(main_pdf_path)
            
            if not case_data:
                raise HTTPException(status_code=400, detail="Не удалось извлечь данные из главного рапорта.")

            llm_client = LLMClient(api_url=api_url, model=model)
            safe_methodology_dir = methodology_dir if os.path.exists(methodology_dir) else "data/methodology"
            
            investigation_plan = generate_investigation_plan_with_llm(
                case_data=case_data,
                llm_client=llm_client,
                methodology_dir=safe_methodology_dir,
                input_dir=input_dir,
                main_pdf_path=main_pdf_path
            )

        # План, в котором модель не сгенерировала действий, не выдаём как успешный
        generation_error = investigation_plan.pop("ошибка_генерации", None) if mode == "initial" else None
        if generation_error:
            raise HTTPException(status_code=502, detail=f"План не сформирован: {generation_error}")

        # === ВЫБОР ФОРМАТА ОТВЕТА ===
        if response_format == "json":
            return _plan_response(investigation_plan)

        if response_format == "text":
            investigation_plan = _plan_response(investigation_plan)
            info = investigation_plan.get("plan_title_info", {})
            actions = investigation_plan.get("actions", [])
            lines = [
                "ПЛАН РАССЛЕДОВАНИЯ",
                f"Уголовное дело №: {info.get('номер_дела', 'Н/Д')}",
                f"Статья УК РК: {info.get('статья_ук_рк', 'Н/Д')}",
                f"Орган: ДЭР {info.get('регион_дэр', '')}",
                f"Город: {info.get('город', 'Н/Д')} ({info.get('год', '2025')})",
                f"Фабула (Факт): {info.get('факт', 'Н/Д')}",
                "\n" + "="*50 + "\n",
                "СЛЕДСТВЕННЫЕ ДЕЙСТВИЯ И МЕРОПРИЯТИЯ:\n"
            ]
            current_block = None
            for action in actions:
                if action.get("блок_номер") != current_block:
                    current_block = action.get("блок_номер")
                    lines.append(f"--- {action.get('блок', '')} ---\n")
                lines.append(f"{action.get('номер', '')}. Действие: {action.get('действие', '')}")
                lines.append(f"   Цель: {action.get('цель', '')}")
                lines.append(f"   Срок: {action.get('срок', '')}")
                lines.append(f"   Исполнитель: {action.get('исполнитель', '')}")
                lines.append("")
                
            text_content = "\n".join(lines)
            return PlainTextResponse(content=text_content, media_type="text/plain; charset=utf-8")

        # === СБОРКА И ОТПРАВКА ДОКУМЕНТА ===
        success = create_investigation_plan_doc(investigation_plan, output_docx_path)
        if not success or not os.path.exists(output_docx_path):
            raise HTTPException(status_code=500, detail="Ошибка при генерации документа Word.")
            
        return FileResponse(
            path=output_docx_path,
            filename=f"plan_{case_data.get('номер_ердр', 'Н_Д')}.docx",
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")

@app.get("/api/health")
def health_check():
    return {"status": "ok"}

def run_background_pipeline(case_id: str, pre_index_only: bool = False):
    model, api_url, methodology_dir = LLM_MODEL, LLM_API_URL, METHODOLOGY_DIR
    import json
    import os
    import traceback
    from datetime import datetime
    
    task_file = os.path.abspath(os.path.join("data", "tasks", f"{case_id}.json"))
    os.makedirs(os.path.dirname(task_file), exist_ok=True)
    
    def update_status(status: str, progress: str, error: Optional[str] = None, plan_path: Optional[str] = None, case_number: Optional[str] = None):
        data = {
            "case_id": case_id,
            "status": status,
            "progress": progress,
            "error": error,
            "result_plan_path": plan_path,
            "case_number": case_number,
            "updated_at": datetime.now().isoformat()
        }
        if os.path.exists(task_file):
            try:
                with open(task_file, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    if not case_number and old_data.get("case_number"):
                        data["case_number"] = old_data.get("case_number")
                    if not plan_path and old_data.get("result_plan_path"):
                        data["result_plan_path"] = old_data.get("result_plan_path")
            except Exception:
                pass
                
        with open(task_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    try:
        update_status("processing", "Определение главного рапорта...")
        input_dir = os.path.abspath(os.path.join("data", "cases", case_id, "input"))
        if not os.path.exists(input_dir):
            update_status("error", "Ошибка", error="Папка с документами не найдена.")
            return
            
        pdf_files = [f for f in os.listdir(input_dir) if f.lower().endswith('.pdf')]
        if not pdf_files:
            update_status("error", "Ошибка", error="В загруженных документах нет файлов формата PDF.")
            return
            
        from core.case_documents import detect_document_type
        from core.methodology_loader import load_pdf_file
                
        raport_file = None
                
        try:
            from core.case_documents import score_raport_filename
            pdf_files_sorted = sorted(pdf_files, key=score_raport_filename, reverse=True)
        except ImportError:
            pdf_files_sorted = pdf_files
            
        for filename in pdf_files_sorted:
            score = 0
            try:
                from core.case_documents import score_raport_filename
                score = score_raport_filename(filename)
            except ImportError:
                pass
                
            if score > 0 or detect_document_type(filename, "") == "рапорт_ердр":
                raport_file = filename
                break
                
        if not raport_file:
            for filename in pdf_files_sorted:
                try:
                    from core.case_documents import score_raport_filename
                    if score_raport_filename(filename) < 0:
                        continue
                except ImportError:
                    pass
                    
                try:
                    file_path = os.path.join(input_dir, filename)
                    text = load_pdf_file(file_path)
                    if text and detect_document_type(filename, text[:2000]) == "рапорт_ердр":
                        raport_file = filename
                        break
                except Exception:
                    continue
                    
        if raport_file:
            main_pdf_path = os.path.join(input_dir, raport_file)
            print(f"✅ Фоновая задача: Автоматически определен Главный Рапорт ЕРДР: {raport_file}")
        else:
            main_pdf_path = os.path.join(input_dir, pdf_files[0])
            print(f"⚠ Фоновая задача: Рапорт ЕРДР не найден. Взят первый файл: {pdf_files[0]}")

        update_status("processing", "Извлечение и структурирование данных рапорта через LLM...")
        parser = RaportParser(api_url=api_url, model=model)
        case_data = parser.parse_raport_pdf_with_llm(main_pdf_path)
        
        if not case_data:
            update_status("error", "Ошибка", error="Не удалось извлечь данные из главного рапорта.")
            return

        case_number = case_data.get("номер_ердр", "Н_Д")
        update_status("processing", f"Генерация следственных действий и RAG-индексация (Дело №{case_number})...", case_number=case_number)
        
        llm_client = LLMClient(api_url=api_url, model=model)
        safe_methodology_dir = methodology_dir if os.path.exists(methodology_dir) else "data/methodology"
        
        investigation_plan = generate_investigation_plan_with_llm(
            case_data=case_data,
            llm_client=llm_client,
            methodology_dir=safe_methodology_dir,
            input_dir=input_dir,
            main_pdf_path=main_pdf_path,
            pre_index_only=pre_index_only
        )
        
        case_plan_path = os.path.abspath(os.path.join("data", "cases", case_id, "plan.json"))
        with open(case_plan_path, "w", encoding="utf-8") as f:
            json.dump(investigation_plan, f, ensure_ascii=False, indent=2)
            
        if pre_index_only:
            update_status("pre_indexed", "Вариант А: Пре-индексация завершена. Ожидание запуска живой генерации.", plan_path=case_plan_path, case_number=case_number)
        elif investigation_plan.get("ошибка_генерации"):
            # План без сгенерированных действий — это ошибка, а не «успешно»; частичный план
            # (обязательные и уже проведённые действия) при этом сохранён и доступен
            update_status("error", "Следственные действия не сгенерированы", error=investigation_plan["ошибка_генерации"],
                          plan_path=case_plan_path, case_number=case_number)
        else:
            update_status("completed", "План успешно сгенерирован в фоновом режиме.", plan_path=case_plan_path, case_number=case_number)
        
    except Exception as e:
        traceback.print_exc()
        update_status("error", "Внутренняя ошибка", error=str(e))

@app.post("/api/upload_and_process")
def api_upload_and_process(
    background_tasks: BackgroundTasks,
    documents: List[UploadFile] = File(...),
    custom_case_id: Optional[str] = Form(None),
    pre_index_only: bool = Form(False),
):
    """
    Загружает документы и запускает полную фоновую генерацию плана.
    Идеально для подготовки перед презентацией.
    """
    if not documents:
        raise HTTPException(status_code=400, detail="Файлы не загружены.")

    import time
    if custom_case_id and custom_case_id.strip():
        case_id = _safe_case_id(custom_case_id, "идентификатор дела")
    else:
        case_id = f"case_{int(time.time())}"
    # Имена проверяются до записи первого файла, чтобы не оставлять частично загруженный набор
    safe_names = [_safe_upload_name(doc.filename) if doc.filename else None for doc in documents]

    input_dir = os.path.abspath(os.path.join("data", "cases", case_id, "input"))
    os.makedirs(input_dir, exist_ok=True)

    pdf_count = 0
    for doc, safe_name in zip(documents, safe_names):
        if safe_name:
            doc_path = os.path.join(input_dir, safe_name)
            with open(doc_path, "wb") as buffer:
                shutil.copyfileobj(doc.file, buffer)
            if safe_name.lower().endswith('.pdf'):
                pdf_count += 1
                
    if pdf_count == 0:
        raise HTTPException(status_code=400, detail="В загруженных документах нет файлов формата PDF.")
        
    task_file = os.path.abspath(os.path.join("data", "tasks", f"{case_id}.json"))
    os.makedirs(os.path.dirname(task_file), exist_ok=True)
    
    status_data = {
        "case_id": case_id,
        "status": "processing",
        "progress": "Файлы загружены. Ожидание запуска...",
        "error": None,
        "result_plan_path": None,
        "case_number": None,
        "updated_at": datetime.now().isoformat()
    }
    with open(task_file, "w", encoding="utf-8") as f:
        json.dump(status_data, f, ensure_ascii=False, indent=2)
        
    background_tasks.add_task(run_background_pipeline, case_id, pre_index_only)
    
    return {
        "status": "accepted",
        "case_id": case_id,
        "message": "Фоновая обработка документов и генерация плана запущена."
    }

@app.get("/api/status/{case_id}")
def api_get_status(case_id: str):
    """Возвращает текущий статус фоновой генерации плана."""
    case_id = _safe_case_id(case_id, "идентификатор дела")
    task_file = os.path.abspath(os.path.join("data", "tasks", f"{case_id}.json"))
    if not os.path.exists(task_file):
        return {
            "case_id": case_id,
            "status": "not_found",
            "progress": "Задача не найдена",
            "error": None
        }
    try:
        with open(task_file, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return {
            "case_id": case_id,
            "status": "error",
            "progress": "Ошибка чтения статуса",
            "error": str(e)
        }

@app.get("/api/download_plan_docx/{case_id}")
def api_download_plan_docx(case_id: str):
    """
    Мгновенно собирает и возвращает .docx документ на основе
    заранее сгенерированного в фоне JSON-плана.
    """
    case_id = _safe_case_id(case_id, "идентификатор дела")
    from core.db import load_plan_from_db
    plan_data = load_plan_from_db(case_id)
    
    case_plan_path = os.path.abspath(os.path.join("data", "cases", case_id, "plan.json"))
    
    if not plan_data and os.path.exists(case_plan_path):
        try:
            with open(case_plan_path, "r", encoding="utf-8") as f:
                plan_data = json.load(f)
        except Exception:
            pass
            
    if not plan_data:
        task_file = os.path.abspath(os.path.join("data", "tasks", f"{case_id}.json"))
        if os.path.exists(task_file):
            try:
                with open(task_file, "r", encoding="utf-8") as f:
                    task_info = json.load(f)
                    case_number = task_info.get("case_number")
                    if case_number:
                        plan_data = load_plan_from_db(case_number)
                        if not plan_data:
                            from core.plan_generator import sanitize_filename
                            номер = sanitize_filename(case_number)
                            shared_plan_path = os.path.abspath(os.path.join("data", "plans", f"{номер}.json"))
                            if os.path.exists(shared_plan_path):
                                with open(shared_plan_path, "r", encoding="utf-8") as pf:
                                    plan_data = json.load(pf)
            except Exception:
                pass
                
    if not plan_data:
        raise HTTPException(
            status_code=404, 
            detail="Сгенерированный план не найден. Убедитесь, что фоновая задача завершилась со статусом 'completed'."
        )
        
    session_dir = tempfile.mkdtemp(prefix="present_docx_")
    output_docx_path = os.path.join(session_dir, "investigation_plan_presentation.docx")
    
    success = create_investigation_plan_doc(plan_data, output_docx_path)
    if not success or not os.path.exists(output_docx_path):
        raise HTTPException(status_code=500, detail="Ошибка при сборке документа Word.")
        
    case_num = plan_data.get('plan_title_info', {}).get('номер_дела', case_id)
    return FileResponse(
        path=output_docx_path,
        filename=f"plan_{case_num}.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )

@app.get("/api/download_plan_by_number/{case_number}")
def api_download_plan_by_number(case_number: str):
    """Мгновенно собирает .docx по номеру дела из базы данных или папки data/plans/."""
    case_number = _safe_case_id(case_number)
    from core.db import load_plan_from_db
    try:
        plan_data = load_plan_from_db(case_number)
        
        if not plan_data:
            from core.plan_generator import sanitize_filename
            номер = sanitize_filename(case_number)
            plan_path = os.path.abspath(os.path.join("data", "plans", f"{номер}.json"))
            
            if not os.path.exists(plan_path):
                raise HTTPException(status_code=404, detail=f"План для дела №{case_number} не найден.")
                
            with open(plan_path, "r", encoding="utf-8") as f:
                plan_data = json.load(f)
        else:
            from core.plan_generator import sanitize_filename
            номер = sanitize_filename(case_number)
            
        session_dir = tempfile.mkdtemp(prefix="present_num_docx_")
        output_docx_path = os.path.join(session_dir, f"plan_{номер}.docx")
        
        success = create_investigation_plan_doc(plan_data, output_docx_path)
        if not success or not os.path.exists(output_docx_path):
            raise HTTPException(status_code=500, detail="Ошибка при сборке документа Word.")
            
        return FileResponse(
            path=output_docx_path,
            filename=f"plan_{номер}.docx",
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка: {str(e)}")


@app.post("/api/generate_live_plan/{case_number}")
def api_generate_live_plan(case_number: str):
    """
    Вариант А: Запускает генерацию пунктов плана через LLM 'в прямом эфире'
    на основе заранее подготовленного (пре-индексированного) контекста.
    Возвращает собранный .docx файл.
    """
    case_number = _safe_case_id(case_number)
    from core.plan_generator import generate_plan_live_from_context, sanitize_filename
    llm_client = LLMClient(api_url=LLM_API_URL, model=LLM_MODEL)
    
    try:
        plan_data = generate_plan_live_from_context(case_number, llm_client)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ошибка при генерации в прямом эфире: {str(e)}")

    generation_error = plan_data.pop("ошибка_генерации", None)
    if generation_error:
        raise HTTPException(status_code=502, detail=f"План не сформирован: {generation_error}")

    номер = sanitize_filename(case_number)
    session_dir = tempfile.mkdtemp(prefix="live_docx_")
    output_docx_path = os.path.join(session_dir, f"plan_{номер}_live.docx")
    
    success = create_investigation_plan_doc(plan_data, output_docx_path)
    if not success or not os.path.exists(output_docx_path):
        raise HTTPException(status_code=500, detail="Ошибка при сборке документа Word.")
        
    return FileResponse(
        path=output_docx_path,
        filename=f"plan_{номер}_live.docx",
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


@app.post("/api/update_status")
def api_update_status(
    background_tasks: BackgroundTasks,
    documents: Optional[List[UploadFile]] = File(None),
    case_number: Optional[str] = Form(None),
    response_format: str = Form("docx"),  # "docx", "json"
):
    """
    ОБНОВЛЕНИЕ СТАТУСОВ плана расследования на основе новых документов-подтверждений.

    В отличие от /api/generate_plan (mode='append'):
      ✅ Анализирует документы и обновляет статусы существующих пунктов на «Исполнен».
      ❌ НЕ генерирует новые следственные действия.
      ❌ НЕ изменяет тексты существующих пунктов.

    Параметры:
      - documents: загружаемые PDF-файлы (документы-подтверждения).
                   Если не переданы — используется БД дела (case_number).
      - case_number: номер уголовного дела (для поиска в БД и нахождения плана).
      - response_format: "docx" или "json".
    Адрес LLM и корень БД берутся из конфигурации сервера (LLM_API_URL, DB_ROOT).
    """
    from core.plan_generator import update_plan_status, PLANS_DIR, sanitize_filename

    if not case_number:
        raise HTTPException(
            status_code=400,
            detail="Необходимо указать номер дела (case_number) для обновления статусов."
        )
    case_number = _safe_case_id(case_number)
    model, api_url, db_root = LLM_MODEL, LLM_API_URL, DB_ROOT

    # Ищем план для этого дела
    from core.db import load_plan_from_db
    plan_snapshot = load_plan_from_db(case_number)
    plan_path = os.path.join(PLANS_DIR, f"{sanitize_filename(case_number)}.json")
    if not plan_snapshot and not os.path.exists(plan_path):
        raise HTTPException(
            status_code=404,
            detail=f"План для дела №{case_number} не найден. Сначала создайте план (mode='initial')."
        )

    session_dir = tempfile.mkdtemp(prefix="plan_update_status_")
    background_tasks.add_task(cleanup_temp_dir, session_dir)
    output_docx_path = os.path.join(session_dir, "updated_plan.docx")

    try:
        llm_client = LLMClient(api_url=api_url, model=model)
        new_db_docs = None
        new_docs_paths = None

        if documents:
            # === РЕЖИМ ЗАГРУЖЕННЫХ ФАЙЛОВ ===
            input_dir = os.path.join(session_dir, "input")
            os.makedirs(input_dir, exist_ok=True)

            from core.methodology_loader import load_pdf_file
            from core.case_documents import detect_document_type

            loaded_docs = []
            for doc in documents:
                if doc.filename:
                    safe_name = _safe_upload_name(doc.filename)
                    doc_path = os.path.join(input_dir, safe_name)
                    with open(doc_path, "wb") as buffer:
                        shutil.copyfileobj(doc.file, buffer)
                    # Читаем текст из документа
                    try:
                        text = load_pdf_file(doc_path) if safe_name.lower().endswith('.pdf') else ""
                        if not text:
                            # Пытаемся прочитать как текст
                            try:
                                with open(doc_path, "r", encoding="utf-8", errors="ignore") as tf:
                                    text = tf.read()
                            except Exception:
                                text = ""
                        if text:
                            doc_type = detect_document_type(safe_name, text[:2000])
                            loaded_docs.append({
                                "filename": safe_name,
                                "text": text,
                                "doc_type": doc_type,
                                "file_path": doc_path
                            })
                    except Exception as e:
                        print(f"  ⚠ Ошибка чтения файла {doc.filename}: {e}")

            if not loaded_docs:
                raise HTTPException(
                    status_code=400,
                    detail="Не удалось прочитать текст из загруженных документов."
                )
            new_db_docs = loaded_docs
            print(f"📄 Загружено {len(new_db_docs)} документов-подтверждений через форму")

        else:
            # === РЕЖИМ БД ДЕЛА ===
            from core.case_documents import load_case_documents_from_db

            all_db_docs = load_case_documents_from_db(case_number, db_root=db_root)
            if not all_db_docs:
                raise HTTPException(
                    status_code=404,
                    detail=f"Не удалось найти документы дела №{case_number} в БД."
                )
            # Фильтруем только новые документы
            if not plan_snapshot:
                with open(plan_path, "r", encoding="utf-8") as f:
                    plan_snapshot = json.load(f)
            processed_docs = plan_snapshot.get('processed_docs', [])
            new_db_docs = [d for d in all_db_docs if d.get("filename") not in processed_docs]

            if not new_db_docs:
                print("  ℹ В БД нет новых документов. Статусы не изменились.")
                investigation_plan = plan_snapshot
                if response_format == "json":
                    return _plan_response(investigation_plan)
                success = create_investigation_plan_doc(investigation_plan, output_docx_path)
                if not success or not os.path.exists(output_docx_path):
                    raise HTTPException(status_code=500, detail="Ошибка при генерации документа Word.")
                return FileResponse(
                    path=output_docx_path,
                    filename=f"plan_{sanitize_filename(case_number)}_statuses.docx",
                    media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
            print(f"📄 Найдено {len(new_db_docs)} новых документов в БД для анализа статусов")

        # === ВЫЗОВ ФУНКЦИИ ОБНОВЛЕНИЯ СТАТУСОВ ===
        investigation_plan = update_plan_status(
            plan_path=plan_path,
            llm_client=llm_client,
            new_db_docs=new_db_docs
        )

        if response_format == "json":
            return _plan_response(investigation_plan)

        success = create_investigation_plan_doc(investigation_plan, output_docx_path)
        if not success or not os.path.exists(output_docx_path):
            raise HTTPException(status_code=500, detail="Ошибка при генерации документа Word.")

        return FileResponse(
            path=output_docx_path,
            filename=f"plan_{sanitize_filename(case_number)}_statuses.docx",
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Внутренняя ошибка сервера: {str(e)}")


# ─────────────────────────────────────────────────────────────────────────────
# Ручное управление статусами (руководитель / следователь)
# ─────────────────────────────────────────────────────────────────────────────

class ManualStatusRequest(BaseModel):
    """Тело запроса для ручного обновления статуса пункта плана."""
    action_number: int
    """Номер пункта плана (поле 'номер' в JSON)."""

    new_status: str
    """Новый статус: 'Исполнен' или 'Не исполнен'."""

    locked: Optional[bool] = None
    """
    Управление блокировкой автоматического обновления ЛЛМ:
      True  — заблокировать: ЛЛМ НЕ сможет менять этот статус автоматически.
      False — разблокировать: ЛЛМ снова сможет обновлять статус.
      None  — не изменять текущий флаг блокировки.

    Логика по умолчанию (если locked не передан):
      - Если new_status = 'Не исполнен' → locked автоматически = True
      - Если new_status = 'Исполнен'    → locked автоматически = False
    """

    role: Optional[str] = "investigator"
    """
    Роль пользователя: 'manager' (руководитель) или 'investigator' (следователь). Пишется в историю статусов.
    Вручную статус меняет только руководитель, но это право проверяет бэк сайта (автор методики, 14.11.2026):
    сервис пользователей не знает и запрос по роли не отклоняет.
    """

    comment: Optional[str] = None
    """Опциональный комментарий к изменению статуса (сохраняется в плане)."""


@app.post("/api/manual_status/{case_number}")
def api_manual_status(case_number: str, req: ManualStatusRequest):
    """
    Ручное обновление статуса пункта плана. По правилу статус вручную меняет только руководитель;
    кто руководитель, проверяет бэк сайта до вызова — здесь роль только записывается в историю.

    Ключевое поведение:
      - Если new_status = 'Не исполнен' (и locked не указан) → автоматически
        устанавливает блокировку (статус_заблокирован = True).
        ЛЛМ НЕ сможет изменить этот статус до снятия блокировки.
      - Если new_status = 'Исполнен' (и locked не указан) → блокировка снимается.
      - Параметр locked=True/False позволяет явно управлять блокировкой.

    Только этот endpoint (ручное действие) может:
      1. Устанавливать блокировку на пункт плана.
      2. Снимать блокировку с заблокированного пункта.
    """
    from core.plan_generator import PLANS_DIR, sanitize_filename, save_plan_to_json
    case_number = _safe_case_id(case_number)

    # Допустимые значения статуса
    VALID_STATUSES = {"Исполнен", "Не исполнен"}
    if req.new_status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Недопустимый статус '{req.new_status}'. Разрешены: {VALID_STATUSES}"
        )

    # Ищем план
    from core.db import load_plan_from_db
    plan = load_plan_from_db(case_number)
    plan_path = os.path.join(PLANS_DIR, f"{sanitize_filename(case_number)}.json")
    
    if not plan:
        if not os.path.exists(plan_path):
            raise HTTPException(
                status_code=404,
                detail=f"План для дела №{case_number} не найден."
            )
        try:
            with open(plan_path, "r", encoding="utf-8") as f:
                plan = json.load(f)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Ошибка чтения плана: {e}")

    # Номер пункта клиент берёт из GET /api/plan, где план разложен по блокам и пронумерован заново.
    # План, сохранённый до появления блоков, хранит прежнюю нумерацию — приводим к той же раскладке
    from core.plan_generator import normalize_plan_statuses
    plan = normalize_plan_statuses(plan)

    # Ищем нужный пункт
    actions = plan.get("actions", [])
    target_action = None
    for action in actions:
        if action.get("номер") == req.action_number:
            target_action = action
            break

    if target_action is None:
        raise HTTPException(
            status_code=404,
            detail=f"Пункт №{req.action_number} не найден в плане дела №{case_number}."
        )

    old_status = target_action.get("статус", "Не исполнен")
    old_locked = target_action.get("статус_заблокирован", False)

    # Определяем новое значение блокировки
    if req.locked is not None:
        new_locked = req.locked
    else:
        # Автоматическая логика:
        # "Не исполнен" → блокируем (LLM не сможет переопределить)
        # "Исполнен"    → снимаем блокировку
        new_locked = (req.new_status == "Не исполнен")

    # Применяем изменения
    target_action["статус"] = req.new_status
    target_action["статус_заблокирован"] = new_locked

    # Сохраняем аудит-информацию
    audit_entry = {
        "статус": req.new_status,
        "заблокирован": new_locked,
        "роль": req.role or "investigator",
        "дата": datetime.now().isoformat(),
    }
    if req.comment:
        audit_entry["комментарий"] = req.comment

    if "история_статусов" not in target_action:
        target_action["история_статусов"] = []
    target_action["история_статусов"].append(audit_entry)

    # Сохраняем план
    plan["actions"] = actions
    save_plan_to_json(plan)

    lock_icon = "🔒" if new_locked else "🔓"
    print(
        f"{lock_icon} Ручное обновление [{req.role}]: "
        f"Дело №{case_number}, пункт №{req.action_number}: "
        f"'{old_status}' → '{req.new_status}' "
        f"(блокировка: {old_locked} → {new_locked})"
    )

    from core.plan_statistics import plan_statistics
    return {
        "success": True,
        "case_number": case_number,
        "action_number": req.action_number,
        "old_status": old_status,
        "new_status": req.new_status,
        "locked": new_locked,
        "statistics": plan_statistics(plan),
        "message": (
            f"Статус пункта №{req.action_number} изменён на '{req.new_status}'. "
            + ("ЛЛМ заблокирован от изменения этого пункта." if new_locked
               else "ЛЛМ может снова обновлять этот пункт автоматически.")
        )
    }


@app.get("/api/plan/{case_number}")
def api_get_plan(case_number: str):
    """
    Возвращает текущий план расследования в формате JSON по номеру дела.
    Включает статусы, флаги блокировки, историю изменений статусов
    и статистику исполнения на сегодня (поле statistics, см. GET /api/plan/{case_number}/statistics).

    План разложен по блокам (пункты в actions уже стоят в порядке блоков, нумерация сквозная):
      - у каждого пункта: «блок_номер» — номер блока, «блок_код» — постоянный код блока, «блок» — название;
      - blocks — блоки плана по порядку: number, code, title, total, executed, overdue,
        first_number, last_number, action_numbers (номера пунктов блока). Пустых блоков в списке нет.
    Коды блоков: analysis, evidence, requests, finance, assets, witnesses, expertise, suspects, arrest,
    verification, executed (исполненные пункты — последний блок, по дате документа).
    Заголовок блока выводится перед пунктом, у которого «блок_номер» отличается от предыдущего.
    """
    case_number = _safe_case_id(case_number)
    plan = _load_saved_plan(case_number)
    if not plan:
        raise HTTPException(
            status_code=404,
            detail=f"План для дела №{case_number} не найден."
        )
    return _plan_response(plan)


# ─────────────────────────────────────────────────────────────────────────────
# Статистика исполнения плана и уведомления руководству
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/api/plan/{case_number}/statistics")
def api_plan_statistics(case_number: str):
    """
    Статистика исполнения плана на сегодня (считается на сервере при каждом запросе):
      - total — всего пунктов; executed — «Исполнен»;
      - overdue — не исполнен и срок прошёл; in_progress — не исполнен, срок не наступил;
      - percent_executed — доля исполненных, %;
      - plan_update_required — true, если исполнено не меньше порога (PLAN_UPDATE_THRESHOLD, по умолчанию 70 %):
        руководству показывается уведомление «нужно обновить план»;
      - update_required_since — когда порог был достигнут (при сохранении плана);
      - overdue_numbers — номера просроченных пунктов.
    """
    case_number = _safe_case_id(case_number)
    plan = _load_saved_plan(case_number)
    if not plan:
        raise HTTPException(status_code=404, detail=f"План для дела №{case_number} не найден.")
    stats = _plan_response(plan)["statistics"]
    return {"case_number": case_number, **stats}


@app.get("/api/notifications/plan_update")
def api_plan_update_notifications():
    """
    Уведомления руководству: дела, план по которым исполнен на порог и более (по умолчанию 70 %)
    и должен быть обновлён. Дело уходит из списка, когда после дозагрузки новых пунктов
    доля исполненных стала ниже порога.
    Кто из руководителей курирует дело, сервис не знает — фильтрация по руководителю на стороне фронта.
    """
    import glob
    from core.db import list_plans_from_db
    from core.plan_generator import PLANS_DIR
    from core.plan_statistics import main_executor, update_threshold_percent

    plans = list_plans_from_db()
    for path in glob.glob(os.path.join(PLANS_DIR, "*.json")):
        if path.endswith(("_pre_context.json", "_backup.json")):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                plan = json.load(f)
        except Exception:
            continue
        case = (plan.get("plan_title_info") or {}).get("номер_дела") or os.path.splitext(os.path.basename(path))[0]
        plans.setdefault(case, plan)

    cases = []
    for case, plan in plans.items():
        stats = _plan_response(plan)["statistics"]
        if not stats["plan_update_required"]:
            continue
        title = plan.get("plan_title_info") or {}
        cases.append({
            "case_number": case,
            "article": title.get("статья_ук_рк", ""),
            "executor": main_executor(plan),
            "message": (f"План по делу №{case} исполнен на {stats['percent_executed']:g}% "
                        f"({stats['executed']} из {stats['total']}, просрочено {stats['overdue']}) — необходимо обновить план."),
            **stats,
        })
    cases.sort(key=lambda c: (c["update_required_since"] or "", c["case_number"]))
    return {"threshold_percent": update_threshold_percent(), "count": len(cases), "cases": cases}


# ─────────────────────────────────────────────────────────────────────────────
# Сохранение визуально отредактированного плана
# ─────────────────────────────────────────────────────────────────────────────

@app.put("/api/plan/{case_number}")
def api_save_edited_plan(case_number: str, plan_data: dict):
    """
    Сохраняет визуально отредактированный план обратно в PostgreSQL и data/plans/{номер}.json.
    """
    from core.db import load_plan_from_db
    from core.plan_generator import PLANS_DIR, sanitize_filename, save_plan_to_json

    case_number = _safe_case_id(case_number)
    existing_plan = load_plan_from_db(case_number)
    номер = sanitize_filename(case_number)
    plan_path = os.path.join(PLANS_DIR, f"{номер}.json")

    if not existing_plan and not os.path.exists(plan_path):
        raise HTTPException(
            status_code=404,
            detail=f"План для дела №{case_number} не найден. Сначала создайте план через /api/generate_plan."
        )

    if not plan_data:
        raise HTTPException(
            status_code=400,
            detail="Тело запроса (JSON план) не может быть пустым."
        )

    try:
        # Создаём резервную копию перед перезаписью, если файл существует
        backup_path = os.path.join(PLANS_DIR, f"{номер}_backup.json")
        if os.path.exists(plan_path):
            shutil.copy2(plan_path, backup_path)
            print(f"💾 Резервная копия файла создана: {backup_path}")

        # План сохраняется ТОЛЬКО под номером дела из URL и только в папку БД из конфигурации:
        # номер дела и db_root из тела запроса позволяли бы перезаписать план другого дела
        # (PUT /api/plan/A с номером B внутри) или записать plan.json в произвольную папку
        if not isinstance(plan_data.get("plan_title_info"), dict):
            plan_data["plan_title_info"] = {}
        if not isinstance(plan_data.get("case_data"), dict):
            plan_data["case_data"] = {}
        plan_data["plan_title_info"]["номер_дела"] = case_number
        plan_data["case_data"]["case_number"] = case_number
        plan_data["case_data"]["db_root"] = DB_ROOT

        # Добавляем метку времени последнего редактирования
        plan_data["last_edited_at"] = datetime.now().isoformat()
        plan_data["last_edited_by"] = "user_visual_edit"

        # Отметку о достижении порога исполнения ведёт сервер: фронт мог её не прислать —
        # берём из сохранённого плана, чтобы дата уведомления руководству не сбрасывалась
        from core.plan_statistics import NOTICE_KEY, plan_statistics
        previous = existing_plan or _load_saved_plan(case_number) or {}
        if previous.get(NOTICE_KEY):
            plan_data[NOTICE_KEY] = previous[NOTICE_KEY]
        else:
            plan_data.pop(NOTICE_KEY, None)

        # Сохраняем отредактированный план
        save_plan_to_json(plan_data)

        actions_count = len(plan_data.get("actions", []))
        print(f"✅ Визуальное редактирование: план дела №{case_number} сохранён в БД и файл ({actions_count} пунктов).")

        return {
            "success": True,
            "case_number": case_number,
            "saved_to": plan_path,
            "backup_created": backup_path,
            "actions_count": actions_count,
            "statistics": plan_statistics(plan_data),
            "last_edited_at": plan_data["last_edited_at"],
            "message": (
                f"План дела №{case_number} успешно сохранён ({actions_count} пунктов). "
                f"Резервная копия создана. "
                f"Для скачивания .docx используйте GET /api/download_plan_by_number/{case_number}."
            )
        }

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Ошибка при сохранении плана: {str(e)}")
