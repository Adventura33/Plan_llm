# core/db.py
import os
import json
import asyncio
from typing import Optional, Dict, Any, List
import asyncpg
from dotenv import load_dotenv

# Load env variables
load_dotenv()

# Отдельные переменные: POSTGRES_* в .env заняты под БД LightRAG
POSTGRES_HOST = os.getenv("INVESTIGATOR_DB_HOST", "localhost")
POSTGRES_PORT = os.getenv("INVESTIGATOR_DB_PORT", "25432")
POSTGRES_DATABASE = os.getenv("INVESTIGATOR_DB_NAME", "investigator_prod")
POSTGRES_USER = os.getenv("INVESTIGATOR_DB_USER", "torekhan")
POSTGRES_PASSWORD = os.getenv("INVESTIGATOR_DB_PASSWORD", "")

async def get_db_connection() -> asyncpg.Connection:
    """Establishes and returns a direct connection to the PostgreSQL database."""
    return await asyncpg.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        database=POSTGRES_DATABASE,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        timeout=15
    )
async def init_db(raise_on_error: bool = False):
    """Checks the database connection without creating tables."""
    conn = None
    try:
        conn = await get_db_connection()
        await conn.execute("SELECT 1;")
        print("✅ Database connection check successful.")
    except Exception as e:
        print(f"⚠ Failed to connect to database: {e}")
        if raise_on_error:
            raise e
    finally:
        if conn:
            await conn.close()

def run_sync(coro):
    """Helper to run async coroutines in a synchronous context using a background thread."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, coro)
        return future.result()

# Async DB Operations

async def save_plan_to_db_async(case_number: str, plan_data: Dict[str, Any]):
    """Asynchronously saves or updates the investigation plan in the database."""
    conn = None
    try:
        conn = await get_db_connection()
        plan_json = json.dumps(plan_data, ensure_ascii=False)
        await conn.execute("""
            INSERT INTO investigation_plans (case_number, plan_data, updated_at)
            VALUES ($1, $2, CURRENT_TIMESTAMP)
            ON CONFLICT (case_number)
            DO UPDATE SET plan_data = EXCLUDED.plan_data, updated_at = CURRENT_TIMESTAMP;
        """, case_number, plan_json)
        print(f"💾 [DB] План дела №{case_number} успешно сохранен в PostgreSQL.")
    except Exception as e:
        print(f"❌ [DB] Ошибка сохранения плана дела №{case_number} в PostgreSQL: {e}")
    finally:
        if conn:
            await conn.close()

async def load_plan_from_db_async(case_number: str) -> Optional[Dict[str, Any]]:
    """Asynchronously loads the investigation plan from the database."""
    conn = None
    try:
        conn = await get_db_connection()
        row = await conn.fetchrow("""
            SELECT plan_data FROM investigation_plans WHERE case_number = $1;
        """, case_number)
        if row and row['plan_data']:
            # row['plan_data'] might be parsed as dict by asyncpg or returned as string
            data = row['plan_data']
            if isinstance(data, str):
                return json.loads(data)
            return dict(data)
        return None
    except Exception as e:
        print(f"❌ [DB] Ошибка загрузки плана дела №{case_number} из PostgreSQL: {e}")
        return None
    finally:
        if conn:
            await conn.close()

async def fetch_documents_from_db_async(case_number: str) -> List[Dict[str, Any]]:
    """Asynchronously fetches all full documents stored for a case from the LightRAG table."""
    conn = None
    documents = []
    try:
        conn = await get_db_connection()
        # Check if table exists first to avoid error
        table_exists = await conn.fetchval("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'lightrag_doc_full'
            );
        """)
        if not table_exists:
            print("⚠ Table 'lightrag_doc_full' does not exist in the database.")
            return documents

        rows = await conn.fetch("""
            SELECT doc_name, content FROM lightrag_doc_full WHERE workspace = $1;
        """, case_number)
        
        for row in rows:
            doc_name = row['doc_name'] or "unknown"
            content = row['content'] or ""
            documents.append({
                "doc_name": doc_name,
                "content": content
            })
    except Exception as e:
        print(f"❌ [DB] Ошибка получения документов дела №{case_number} из PostgreSQL: {e}")
    finally:
        if conn:
            await conn.close()
    return documents

# Synchronous DB Wrappers

def save_plan_to_db(case_number: str, plan_data: Dict[str, Any]):
    """Synchronous wrapper to save or update the investigation plan in the database."""
    run_sync(save_plan_to_db_async(case_number, plan_data))

def load_plan_from_db(case_number: str) -> Optional[Dict[str, Any]]:
    """Synchronous wrapper to load the investigation plan from the database."""
    return run_sync(load_plan_from_db_async(case_number))

def fetch_documents_from_db(case_number: str) -> List[Dict[str, Any]]:
    """Synchronous wrapper to fetch documents for a case from the LightRAG PostgreSQL table."""
    return run_sync(fetch_documents_from_db_async(case_number))

def test_db_connection():
    """Function to test database connectivity."""
    try:
        run_sync(init_db(raise_on_error=True))
        print("🎉 Connection test passed successfully!")
    except Exception as e:
        print(f"❌ Connection test failed: {e}")
