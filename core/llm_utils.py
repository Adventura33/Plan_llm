# core/llm_utils.py

import json
import re
from datetime import datetime
from typing import Tuple, Optional

try:
    import ollama  # type: ignore
except Exception:
    ollama = None  # graceful handling if not installed

# MONTHS should be provided by your project.
# Example structure (ensure your core.localization provides this):
# MONTHS = {
#   'ru': {'января':1, 'февраля':2, 'марта':3, 'апреля':4, 'мая':5, 'июня':6,
#          'июля':7, 'августа':8, 'сентября':9, 'октября':10, 'ноября':11, 'декабря':12},
#   'kk': {'қаңтар':1, 'ақпан':2, 'наурыз':3, 'сәуір':4, 'мамыр':5, 'маусым':6,
#          'шілде':7, 'тамыз':8, 'қыркүйек':9, 'қазан':10, 'қараша':11, 'желтоқсан':12}
# }
from core.localization import MONTHS


class OllamaClient:
    def __init__(self, model_name: str = "llama3"):
        self.model = model_name

    # ----------------------------- ДАТЫ: НОРМАЛИЗАЦИЯ -----------------------------
    def _normalize_dates(self, data: dict) -> dict:
        """
        Приводит распознанные даты к ISO-формам:
          - *_iso: YYYY-MM-DD
          - *_iso для дат+времени: YYYY-MM-DD HH:MM
        Оригинальные значения не трогаются.
        """
        ALL_MONTHS = {**MONTHS.get("ru", {}), **MONTHS.get("kk", {})}

        def to_iso_date(value: str):
            if not isinstance(value, str) or not value.strip():
                return value
            s = value.strip().replace("\\u00A0", " ")

            # DD.MM.YYYY
            m = re.match(r"\\b(\\d{1,2})\\.(\\d{1,2})\\.(\\d{4})\\b", s)
            if m:
                d, mo, y = map(int, m.groups())
                try:
                    return datetime(y, mo, d).strftime("%Y-%m-%d")
                except Exception:
                    return value

            # 5 октября 2023 / 5 қазан 2023 / 5 Қазан 2023
            m = re.match(
                r"\\b(\\d{1,2})\\s+([А-Яа-яӘәІіҢңҒғҮүҰұҚқӨө]+)\\s+(\\d{4})",
                s,
                re.IGNORECASE,
            )
            if m:
                d, mon_str, y = m.groups()
                mon_num = ALL_MONTHS.get(mon_str.lower())
                if mon_num:
                    try:
                        return datetime(int(y), mon_num, int(d)).strftime("%Y-%m-%d")
                    except Exception:
                        return value
            return value

        def to_iso_dt(value: str):
            if not isinstance(value, str) or not value.strip():
                return value
            s = value.strip().replace("\\u00A0", " ")

            # a) 05.10.2023 (г.) в 17:28
            m = re.search(
                r"(\\d{1,2}\\.\\d{1,2}\\.\\d{4})\\s*(?:г\\.?|года)?\\s*(?:в)?\\s*(\\d{1,2}:\\d{2})",
                s,
                re.IGNORECASE,
            )
            if m:
                dstr, tstr = m.groups()
                try:
                    dt = datetime.strptime(f"{dstr} {tstr}", "%d.%m.%Y %H:%M")
                    return dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass

            # b) 5 октября 2023 (г./ж./жылы) в 17:28
            m = re.search(
                r"(\\d{1,2}\\s+[А-Яа-яӘәІіҢңҒғҮүҰұҚқӨө]+\\s+\\d{4})\\s*(?:г\\.?|года|ж\\.?|жылы)?\\s*(?:в)?\\s*(\\d{1,2}:\\d{2})",
                s,
                re.IGNORECASE,
            )
            if m:
                dstr, tstr = m.groups()
                d_iso = to_iso_date(dstr)
                if re.match(r"\\d{4}-\\d{2}-\\d{2}", d_iso or ""):
                    return f"{d_iso} {tstr}"

            # c) 05.10.2023 года 17 часов 28 минут
            m = re.search(
                r"(\\d{1,2}\\.\\d{1,2}\\.\\d{4})\\s*(?:г\\.?|года)?\\D{0,10}(\\d{1,2})\\s*час\\w*\\s*(\\d{1,2})\\s*мин",
                s,
                re.IGNORECASE,
            )
            if m:
                dstr, hh, mm = m.groups()
                try:
                    dt = datetime.strptime(
                        f"{dstr} {int(hh):02d}:{int(mm):02d}", "%d.%m.%Y %H:%M"
                    )
                    return dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass

            return value

        if "рапорт_дата" in data:
            data["рапорт_дата_iso"] = to_iso_date(data.get("рапорт_дата", ""))
        if "дата_регистрации_ердр" in data:
            data["дата_регистрации_ердр_iso"] = to_iso_dt(
                data.get("дата_регистрации_ердр", "")
            )

        return data

    # ----------------------------- FALLBACK: ИСПОЛНИТЕЛЬ -----------------------------
    def _fallback_executor(self, text: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Возвращает (должность_строка, ФИО) или (None, None).
        Ищет И.О. и Полное ФИО, казахские и русские маркеры подписи/должности.
        Включает кейсы "Орындаушы/Исполнитель ... ФИО".
        """
        if not isinstance(text, str) or not text.strip():
            return None, None

        # Разрешим казахские символы
        KZ = "А-ЯӘІҢҒҮҰҚӨа-яәіңғүұқө"
        FIO_INITIALS = rf"[{KZ}][{KZ}\\-’ʼ']+\\s+[A-ZА-Я]\\.[A-ZА-Я]\\."
        FIO_THREE = rf"[{KZ}][{KZ}\\-’ʼ']+\\s+[{KZ}][{KZ}\\-’ʼ']+\\s+[{KZ}][{KZ}\\-’ʼ']+"
        FIO_ANY = rf"(?:{FIO_INITIALS}|{FIO_THREE})"

        s = re.sub(r"[ \\t\\u00A0]+", " ", text)

        # 0) Линия "Орындаушы/Исполнитель ... ФИО" (часто в планах)
        m = re.search(
            rf"(Орындаушы|Исполнитель)[^\\n]{{0,200}}?({FIO_ANY})",
            s,
            flags=re.IGNORECASE,
        )
        if m:
            label = m.group(1)
            fio = m.group(2).strip()
            duty_fragment = m.group(0)
            duty = re.sub(rf"{label}\\s*[:\\-–—]?", "", duty_fragment, flags=re.IGNORECASE)
            duty = duty.replace(fio, "").strip(" :—–-")
            if not duty:
                duty = label
            return duty, fio

        # 1) Подпись: "Құжатты дайындады және қол қойды: ФИО"
        m = re.search(
            rf"Құжатты\\s+дайындады\\s+және\\s+қол\\s+қойды\\s*:?\\s*({FIO_ANY})",
            s,
            flags=re.IGNORECASE,
        )
        if m:
            cand = m.group(1).strip()
            cand = re.split(r"[,|\\n]|@\\w+|\\d{{2}}\\.\\d{{2}}\\.\\d{{4}}", cand)[0].strip()
            if 1 <= len(cand) <= 80:
                return "қол қойды", cand

        # 2) "қол қойды"/"подписал(а)" рядом с ФИО
        m = re.search(
            rf"(қол\\s+қойды|подпис[аы]л[аи]?)\\s*[:\\-–—]?\\s*({FIO_ANY})",
            s,
            flags=re.IGNORECASE,
        )
        if m:
            return m.group(1).strip(), m.group(2).strip()

        # 3) Должности + ФИО (на той же строке)
        roles = [
            r"аға\\s+тергеуші",
            r"бас\\s+тергеуші",
            r"ерекше\\s+маңызды\\s+істер\\s+жөніндегі\\s+аға\\s+тергеуші",
            r"тергеуші",
            r"Следователь",
            r"старший\\s+следователь",
        ]
        m = re.search(
            rf"(?:{'|'.join(roles)})[^\\.\\n]{{0,200}}[:\\-–—]?\\s*({FIO_ANY})",
            s,
            flags=re.IGNORECASE,
        )
        if m:
            # краткая должность — первые несколько слов роли
            role_snippet = " ".join(m.group(0).split()[:8])
            return role_snippet, m.group(1).strip()

        # 4) Окно строк вокруг должности
        lines = [ln.strip() for ln in s.splitlines()]
        role_re = re.compile(rf"({'|'.join(roles)})", re.IGNORECASE)
        fio_re = re.compile(rf"\\b{FIO_ANY}\\b")
        for i, ln in enumerate(lines):
            if role_re.search(ln):
                m = fio_re.search(ln)
                if m:
                    return ln, m.group(0).strip()
                for j in range(1, 3):
                    if i + j < len(lines):
                        m = fio_re.search(lines[i + j])
                        if m:
                            return lines[i + j], m.group(0).strip()
                if i - 1 >= 0:
                    m = fio_re.search(lines[i - 1])
                    if m:
                        return lines[i - 1], m.group(0).strip()

        # 5) Последние строки (подпись внизу)
        pat_footer = rf"[:\\-–—]\\s*({FIO_ANY})\\s*$"
        for ln in lines[-6:]:
            m = re.search(pat_footer, ln)
            if m:
                return ln, m.group(1).strip()

        return None, None

    # ----------------------------- FALLBACK: ДАТА -----------------------------
    def _fallback_date(self, text: str) -> Optional[str]:
        if not isinstance(text, str) or not text.strip():
            return None

        s = text.replace("\\u00A0", " ")

        # 1) DD.MM.YYYY (+ необязательное "г.", "года", + время "в HH:MM")
        m = re.search(
            r"(\\b\\d{1,2}\\.\\d{1,2}\\.\\d{4}\\b)\\s*(?:г\\.?|года)?(?:\\s*(?:в)?\\s*\\d{1,2}:\\d{2})?",
            s,
            flags=re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()

        # 2) DD month YYYY (+ "г.", "года", "ж.", "жылы")
        m = re.search(
            r"(\\b\\d{1,2}\\s+[А-Яа-яӘәІіҢңҒғҮүҰұҚқӨө]+\\s+\\d{4}\\b)\\s*(?:г\\.?|года|ж\\.?|жылы)?",
            s,
            flags=re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()

        # 3) "DD.MM.YYYY ... HH часов MM минут"
        m = re.search(
            r"(\\b\\d{1,2}\\.\\d{1,2}\\.\\d{4}\\b)[^0-9]{0,10}(\\d{1,2})\\s*час\\w*\\s*(\\d{1,2})\\s*мин",
            s,
            flags=re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()

        return None

    # ----------------------------- FALLBACK: СТАТЬЯ -----------------------------
    def _fallback_article(self, text: str) -> Optional[str]:
        if not isinstance(text, str) or not text.strip():
            return None
        m = re.search(
            r"((?:ст\\.|статьи|бабы)\\s*[\\d\\.\\s\\w\\-\\(\\)]+\\s*(?:УК РК|ҚР ҚК))",
            text,
            re.IGNORECASE,
        )
        return m.group(1).strip() if m else None

    # ----------------------------- ПРОМПТ -----------------------------
    def _get_extraction_prompt(self, raport_text: str) -> str:
        """
        Строгий промпт: извлекаем ТОЛЬКО из текста.
        """
        prompt = f"""
Ты — высокоточный и честный ИИ-ассистент для анализа юридических документов.
Твоя единственная задача — извлечь информацию ИСКЛЮЧИТЕЛЬНО из предоставленного текста.

**КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО:**
- ПРИДУМЫВАТЬ ИНФОРМАЦИЮ. Если данных нет в тексте, используй "Н/Д".
- ИСПОЛЬЗОВАТЬ СВОИ ОБЩИЕ ЗНАНИЯ.
- ДОБАВЛЯТЬ ЛЮБОЙ ТЕКСТ КРОМЕ JSON.

**ГЛАВНОЕ ПРАВИЛО:** Если не можешь найти точный ответ на какой-либо ключ — верни строку "Н/Д".

**ТЕКСТ ДЛЯ АНАЛИЗА:**
<<<DOC_START
{raport_text}
DOC_END>>>

ТРЕБУЕМЫЕ ПОЛЯ JSON (ключи строго как ниже):
{{
  "рапорт_дата": "Дата рапорта (например, '5 октября 2023 г.' или '5 қазан 2023 ж.')",
  "фио_следователя": "Полное ФИО следователя (например, 'Сарсенбаев М.Р.')",
  "должность_следователя": "Полная должность следователя",
  "статья_ук_рк": "Строка со статьей УК РК из текста (например, 'ст. 217 ч.2 п.1 УК РК')",
  "номер_ердр": "Номер ЕРДР",
  "дата_регистрации_ердр": "Дата и время регистрации в ЕРДР",
  "суть_правонарушения": "Короткое описание сути правонарушения дословно из текста",
  "фигуранты": "Краткое описание фигурантов"
}}
- Верни ТОЛЬКО один валидный JSON-объект. Если каких-то значений нет — "Н/Д".
"""
        return prompt

    # ----------------------------- ОСНОВНОЙ МЕТОД -----------------------------
    def extract_case_data(self, raport_text: str):
        if ollama is None:
            raise RuntimeError(
                "Модуль 'ollama' не найден. Установите: pip install ollama, и убедитесь, что Ollama CLI работает."
            )

        prompt = self._get_extraction_prompt(raport_text)
        content = ""  # на случай исключения до присвоения
        try:
            response = ollama.chat(
                model=self.model, messages=[{"role": "user", "content": prompt}]
            )
            content = response["message"]["content"]

            # Вырезаем JSON из ответа
            start_brace = content.find("{")
            end_brace = content.rfind("}")
            json_string_candidate = (
                content[start_brace : end_brace + 1]
                if start_brace != -1 and end_brace != -1 and end_brace > start_brace
                else content
            )
            case_data = json.loads(json_string_candidate)

            # --- Fallback'и: дата, статья, ФИО/должность ---
            if case_data.get("рапорт_дата") in (None, "", "Н/Д"):
                fb_date = self._fallback_date(raport_text)
                if fb_date:
                    case_data["рапорт_дата"] = fb_date

            if case_data.get("статья_ук_рк") in (None, "", "Н/Д"):
                fb_article = self._fallback_article(raport_text)
                if fb_article:
                    case_data["статья_ук_рк"] = fb_article

            if case_data.get("фио_следователя") in (None, "", "Н/Д"):
                fb_duty, fb_fio = self._fallback_executor(raport_text)
                if fb_fio:
                    case_data["фио_следователя"] = fb_fio
                if case_data.get("должность_следователя") in (None, "", "Н/Д") and fb_duty:
                    case_data["должность_следователя"] = fb_duty

            # --- Нормализация дат (добавит *_iso) ---
            case_data = self._normalize_dates(case_data)

            return case_data

        except Exception as e:
            print(f"Критическая ошибка: {e}. Ответ LLM: '{content}'")
            return None
