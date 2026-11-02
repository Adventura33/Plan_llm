# core/doc_facts.py
"""
Выжимки документов дела для планирования.

Каждый документ разбирается отдельным запросом к модели по строгой схеме (лица, организации, платежи,
имущество, интернет-ресурсы, отказ от показаний, защитник, пробелы). План дозагрузки строится по выжимкам:
модель видит факты всех документов, а не обрезок склейки. Выжимки кэшируются по содержимому текста
(рядом с OCR-кэшем), поэтому повторный запуск не тратит запросы.
"""
import hashlib
import json
import os
import re
from typing import Dict, List

from core.doc_compress import compress_document

_MAX_DOC_CHARS = 12000
_FACT_LISTS = (('лица', 'Лица'), ('организации', 'Организации'), ('платежи_и_счета', 'Платежи и счета'),
               ('имущество', 'Имущество'), ('интернет_ресурсы', 'Интернет-ресурсы'),
               ('ключевые_факты', 'По существу'), ('пробелы', 'Не установлено / противоречия'))


def _cache_path(text: str, model: str) -> str:
    h = hashlib.sha256((model + '\n' + text).encode('utf-8')).hexdigest()
    cache_dir = os.environ.get("FACTS_CACHE_DIR", os.path.join("data", "facts_cache"))
    return os.path.join(cache_dir, f"{h}.json")


def _verify_persons(facts: Dict, source: str) -> Dict:
    """Лица, которых нет в тексте документа, из выжимки убираются (модель не должна добавлять людей)."""
    from core.case_participants import surname_stem
    low = (source or '').lower()
    kept = []
    for p in facts.get('лица') or []:
        fio = (p.get('фио') if isinstance(p, dict) else str(p)) or ''
        surname = fio.split()[0] if fio.split() else ''
        if len(surname) >= 3 and surname_stem(surname).lower()[:5] not in low:
            print(f"    ⛔ [Выжимка] Лица нет в документе, убрано: {fio}")
            continue
        kept.append(p)
    facts['лица'] = kept
    return facts


def extract_facts(llm_client, doc: Dict) -> Dict:
    """Выжимка документа (из кэша или новым запросом к модели). Пустой dict — если модель не ответила."""
    text = compress_document(doc)[:_MAX_DOC_CHARS]
    if len(text) < 200:
        return {}
    model = getattr(llm_client, 'model', '') or ''
    path = _cache_path(text, model)
    if os.path.exists(path):
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    facts = llm_client.extract_document_facts(doc.get('filename', 'документ'), text)
    if not facts:
        return {}
    facts = _verify_persons(facts, doc.get('text') or '')
    facts['файл'] = doc.get('filename', '')
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(facts, f, ensure_ascii=False, indent=1)
    except OSError as e:
        print(f"    ⚠ Не удалось сохранить выжимку: {e}")
    return facts


def _item_text(item) -> str:
    if isinstance(item, dict):
        return ' — '.join(str(v) for v in item.values() if v)
    return str(item)


def facts_text(facts: Dict) -> str:
    """Выжимка одного документа в компактном виде для запроса к модели."""
    if not facts:
        return ''
    lines = [f"[ФАЙЛ: {facts.get('файл', '')}] {facts.get('вид', '')} {facts.get('дата', '')}".rstrip()]
    who = facts.get('допрошенный') or {}
    if isinstance(who, dict) and who.get('фио'):
        lines.append(f"Допрошен: {who.get('фио')} ({who.get('статус', '')})")
    if facts.get('отказ_от_показаний'):
        lines.append("ОТКАЗАЛСЯ ОТ ДАЧИ ПОКАЗАНИЙ")
    if facts.get('защитник'):
        lines.append(f"Защитник: {facts['защитник']}")
    for key, label in _FACT_LISTS:
        items = [_item_text(x) for x in facts.get(key) or [] if _item_text(x).strip()]
        if items:
            lines.append(f"{label}: " + '; '.join(items))
    return re.sub(r'[ \t]+', ' ', '\n'.join(lines))


def build_facts_context(llm_client, docs: List[Dict]) -> List[Dict]:
    """Выжимки всех документов; печатает сводку."""
    result = []
    for doc in docs or []:
        facts = extract_facts(llm_client, doc)
        if facts:
            result.append(facts)
    print(f"  🧾 Выжимки документов: {len(result)} из {len(docs or [])}")
    return result
