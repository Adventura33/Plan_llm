# core/rag_engine.py
"""
RAG-движок на основе LightRAG API.
Взаимодействует с локальным сервером LightRAG через HTTP запросы или работает напрямую с локальной БД.
"""

import os
import requests
from typing import List, Dict

DEFAULT_API_URL = "http://localhost:9621"

class RAGEngine:
    """
    Graph-RAG движок, работающий через внешний LightRAG API или с локальной БД в workspaces.
    Сохраняет API совместимость с предыдущей реализацией (add_documents,
    search_relevant_context, clear).
    """

    def __init__(
        self,
        api_url: str = DEFAULT_API_URL,
        workspace_dir: str = None,
        llm_model: str = None,
        **kwargs # игнорируем старые параметры (persist_directory, model_name)
    ):
        # Нормализуем URL: убираем дублирующую часть /chat/completions, если она передана
        cleaned_url = api_url.strip()
        if cleaned_url.endswith("/chat/completions"):
            cleaned_url = cleaned_url[:-17]
        self.api_url = cleaned_url.rstrip("/")
        self.workspace_dir = workspace_dir
        self.llm_model = llm_model
        self._initialized = True
        self.rag_instance = None
        if self.workspace_dir:
            print(f"  ✅ RAGEngine инициализирован в режиме локальной БД (Workspace: {self.workspace_dir})")
        else:
            print(f"  ✅ RAGEngine инициализирован (API: {self.api_url})")

    def _get_local_rag(self):
        if self.rag_instance is not None:
            return self.rag_instance
            
        import asyncio
        from lightrag import LightRAG
        from lightrag.utils import EmbeddingFunc
        from lightrag.llm.openai import openai_embed
        import numpy as np
        
        async def vllm_embedding_func(texts: List[str]):
            base_url = "192.168.165.2:8806/v1"
            if not base_url.startswith(("http://", "https://")):
                base_url = "http://" + base_url
            return await openai_embed.func(
                texts,
                model="qwen3-embedding-8b",
                api_key="dummy",
                base_url=base_url,
            )
            
        embedding_func = EmbeddingFunc(
            embedding_dim=4096,
            max_token_size=8192,
            func=vllm_embedding_func
        )
        
        # Вызов LLM через LLMClient/HTTP
        async def local_llm(prompt, system_prompt=None, history_messages=[], **kwargs):
            import httpx
            import re
            
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.extend(history_messages)
            messages.append({"role": "user", "content": prompt})
            
            payload = {
                "model": self.llm_model or "deepseek-chat",
                "messages": messages,
                "temperature": kwargs.get("temperature", 0.1),
                "max_tokens": kwargs.get("max_tokens", 8192)
            }
            
            headers = {"Content-Type": "application/json"}
            async with httpx.AsyncClient(timeout=360.0) as httpx_client:
                url = f"{self.api_url}/chat/completions"
                response = await httpx_client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                result = response.json()
                content = result['choices'][0]['message']['content']
                content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
                return content
                
        workspace_name = os.path.basename(self.workspace_dir) if self.workspace_dir else "default"
        
        # Защитный фолбэк для рабочих папок на macOS/локальной машине, если /root недоступен
        working_dir = self.workspace_dir or "./rag_storage"
        if working_dir.startswith("/root"):
            parent = os.path.dirname(working_dir)
            if not os.path.exists(parent) or not os.access(parent, os.W_OK):
                working_dir = os.path.join(os.getcwd(), "data", "workspaces", workspace_name)
                
        self.rag_instance = LightRAG(
            working_dir=working_dir,
            llm_model_func=local_llm,
            embedding_func=embedding_func,
            # gpt-3.5-turbo → cl100k_base (закэширована локально, не требует сети).
            # gpt-4o-mini (по умолчанию) → o200k_base (требует загрузки с openaipublic.blob.core.windows.net).
            tiktoken_model_name="gpt-3.5-turbo",
            kv_storage="PGKVStorage",
            doc_status_storage="PGDocStatusStorage",
            vector_storage="QdrantVectorDBStorage",
            graph_storage="Neo4JStorage",
            workspace=workspace_name,
            global_params={"namespace": workspace_name}
        )
        
        # Инициализируем storages без DDL (таблицы уже созданы)
        from lightrag.utils import always_get_an_event_loop
        loop = always_get_an_event_loop()
        try:
            # Пытаемся инициализировать, игнорируя ошибки permission denied
            loop.run_until_complete(self.rag_instance.initialize_storages())
        except Exception as init_error:
            # Если таблицы уже существуют или другие ошибки, игнорируем
            print(f"  ℹ Storages инициализация пропущена или уже выполнена: {init_error}")
            # Не пробрасываем ошибку, так как таблицы могут уже существовать
        
        return self.rag_instance

    def add_documents(self, documents: List[Dict]):
        """Принимает [{filename, text, type}], отправляет на индексацию в LightRAG сервер."""
        if self.workspace_dir:
            print("  ℹ Режим БД: Пропуск индексации (add_documents), так как база данных уже пред-индексирована.")
            return

        texts_to_insert = []
        file_sources = []
        
        for doc in documents:
            text = doc.get("text", "") or ""
            if not text.strip():
                continue
            filename = doc.get("filename", "unknown")
            doc_type = doc.get("type", doc.get("doc_type", "unknown"))
            formatted = f"[Документ: {filename} | Тип: {doc_type}]\n{text}"
            
            texts_to_insert.append(formatted)
            file_sources.append(filename)

        if not texts_to_insert:
            return

        total_chars = sum(len(t) for t in texts_to_insert)
        print(f"  🧠 Отправка {len(texts_to_insert)} документов на сервер LightRAG "
              f"({total_chars:,} симв.)...")
        print(f"     ⏳ Построение Knowledge Graph сервером (может занять несколько минут)...")

        try:
            resp = requests.post(
                f"{self.api_url}/documents/texts",
                json={"texts": texts_to_insert, "file_sources": file_sources},
                timeout=1200 # Увеличиваем таймаут, так как процесс долгий
            )
            resp.raise_for_status()
            print(f"  ✅ Успешно проиндексировано {len(texts_to_insert)} документов на сервере")
        except Exception as e:
            print(f"  ⚠ Ошибка при индексации в LightRAG (API): {e}")

    def search_relevant_context(self, query: str, k: int = 6) -> str:
        """Возвращает релевантный контекст из Knowledge Graph (LightRAG) через API или локально."""
        if self.workspace_dir:
            try:
                rag = self._get_local_rag()
                if rag is None:
                    print(f"  ⚠ Ошибка: RAG instance is None (не удалось инициализировать)")
                    return ""
                from lightrag import QueryParam
                # Локальный запрос в режиме гибридного поиска
                param = QueryParam(
                    mode="hybrid",
                    only_need_context=True,
                    top_k=min(k * 4, 60),
                    chunk_top_k=max(k, 10)
                )
                from lightrag.utils import always_get_an_event_loop
                loop = always_get_an_event_loop()
                context = loop.run_until_complete(rag.aquery(query, param))
                return context
            except Exception as e:
                print(f"  ⚠ Ошибка при локальном поиске в LightRAG (БД): {e}")
                import traceback
                traceback.print_exc()
                return ""

        try:
            resp = requests.post(
                f"{self.api_url}/query",
                json={
                    "query": query,
                    "mode": "hybrid",
                    "only_need_context": True,
                    "top_k": min(k * 4, 60),
                    "chunk_top_k": max(k, 10),
                    "stream": False
                },
                timeout=300
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", "")
        except Exception as e:
            print(f"  ⚠ Ошибка при поиске в LightRAG (API): {e}")
            return ""

    def clear(self):
        """Очищает кэш на сервере LightRAG."""
        if self.workspace_dir:
            # В локальном режиме БД очистка кэша API не требуется
            return
            
        try:
            resp = requests.post(
                f"{self.api_url}/documents/clear_cache",
                json={},
                timeout=30
            )
            resp.raise_for_status()
            print("  ℹ Кэш LLM на сервере LightRAG очищен.")
        except Exception as e:
            print(f"  ⚠ Ошибка при очистке кэша (API): {e}")
