# core/plan_statistics.py
"""
Статистика исполнения плана расследования и уведомление руководства о необходимости обновить план.

Статистика считается на сервере при каждой отдаче плана клиенту: «просрочен» зависит от текущей
даты, поэтому в JSON плана она не хранится (иначе фронт показывал бы вчерашние цифры).
В плане хранится только отметка о достижении порога исполнения (plan['plan_update_notice']):
когда при сохранении плана доля исполненных пунктов впервые дошла до порога (по умолчанию 70 %).
Отметка снимается, когда после дозагрузки новых пунктов доля исполненных снова ниже порога.
"""
import os
import re
from datetime import date, datetime
from typing import Optional

EXECUTED = 'Исполнен'
NOTICE_KEY = 'plan_update_notice'
STATS_KEY = 'statistics'

_DATE_RE = re.compile(r'\s*(\d{1,2})\.(\d{1,2})\.(\d{4})')


def update_threshold_percent() -> float:
    """Порог исполнения плана (в процентах), при котором руководству нужно обновить план. Env: PLAN_UPDATE_THRESHOLD."""
    try:
        value = float(os.environ.get('PLAN_UPDATE_THRESHOLD', '70'))
    except ValueError:
        value = 70.0
    return value if 0 < value <= 100 else 70.0


def _deadline(value) -> Optional[date]:
    m = _DATE_RE.match(str(value or ''))
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except ValueError:
        return None


def is_overdue(action: dict, today: date) -> bool:
    """Не исполнен, и срок («дд.мм.гггг») уже прошёл. В последний день срока пункт ещё не просрочен."""
    if action.get('статус') == EXECUTED:
        return False
    deadline = _deadline(action.get('срок'))
    return deadline is not None and deadline < today


def plan_statistics(plan: dict, today: Optional[date] = None) -> dict:
    """
    Счётчики по пунктам плана на дату today (по умолчанию — сегодня):
      total       — всего пунктов;
      executed    — «Исполнен»;
      overdue     — не исполнен, срок прошёл;
      in_progress — не исполнен, срок не наступил или не указан.
    total = executed + overdue + in_progress.
    """
    today = today or date.today()
    actions = plan.get('actions') or []
    total = len(actions)
    executed = sum(1 for a in actions if a.get('статус') == EXECUTED)
    overdue = [a for a in actions if is_overdue(a, today)]
    percent = round(executed * 100 / total, 1) if total else 0.0
    threshold = update_threshold_percent()
    notice = plan.get(NOTICE_KEY) or {}
    required = bool(total) and percent >= threshold
    return {
        'total': total,
        'executed': executed,
        'overdue': len(overdue),
        'in_progress': total - executed - len(overdue),
        'percent_executed': percent,
        'update_threshold_percent': threshold,
        'plan_update_required': required,
        'update_required_since': notice.get('reached_at') if required else None,
        'overdue_numbers': [a.get('номер') for a in overdue],
        'calculated_on': today.strftime('%d.%m.%Y'),
    }


def attach_statistics(plan: dict, today: Optional[date] = None) -> dict:
    """Добавляет plan['statistics'] для ответа клиенту (при сохранении плана поле убирается)."""
    plan[STATS_KEY] = plan_statistics(plan, today)
    return plan


def update_plan_notice(plan: dict, today: Optional[date] = None) -> Optional[dict]:
    """
    Вызывается при каждом сохранении плана (смена статусов, дозагрузка, правка через API).
    Доля исполненных дошла до порога и отметки ещё нет → ставит отметку (момент уведомления руководства).
    Доля стала ниже порога (план дополнен новыми пунктами) → отметка снимается.
    """
    plan.pop(STATS_KEY, None)  # рассчитывается при отдаче, в файле/БД не хранится
    stats = plan_statistics(plan, today)
    case_number = (plan.get('plan_title_info') or {}).get('номер_дела', '')
    summary = f"исполнено {stats['executed']} из {stats['total']} ({stats['percent_executed']}%)"
    if stats['plan_update_required']:
        if not plan.get(NOTICE_KEY):
            plan[NOTICE_KEY] = {
                'reached_at': datetime.now().isoformat(timespec='seconds'),
                'percent_executed': stats['percent_executed'],
                'executed': stats['executed'],
                'total': stats['total'],
            }
            print(f"📣 План дела №{case_number}: {summary} — достигнут порог "
                  f"{stats['update_threshold_percent']:g}%, руководству — уведомление о необходимости обновить план.")
    elif plan.pop(NOTICE_KEY, None):
        print(f"📣 План дела №{case_number} обновлён: {summary} — ниже порога, уведомление снято.")
    return plan.get(NOTICE_KEY)


def main_executor(plan: dict) -> str:
    """Исполнитель, которому назначено больше всего пунктов плана (кому обновлять план)."""
    counts = {}
    for a in plan.get('actions') or []:
        name = (a.get('исполнитель') or '').strip()
        if name:
            counts[name] = counts.get(name, 0) + 1
    return max(counts, key=counts.get) if counts else ''
