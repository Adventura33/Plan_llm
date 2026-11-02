import sys
import os

from utils.doc_formatter import create_investigation_plan_doc

dummy_plan_data = {
    "plan_title_info": {
        "номер_дела": "123456789012345",
        "факт": "выписки фиктивных счет фактур ТОО «Рога и Копыта»",
        "статья_ук_рк": "216 ч.1 УК РК",
        "город": "г. Алматы",
        "год": "2026"
    },
    "actions": [
        {
            "номер": 1,
            "действие": "Выемка документов",
            "цель": "Изъятие фиктивных счетов-фактур",
            "исполнитель": "Следователь Иванов",
            "срок": "Дни 1-10"
        },
        {
            "номер": 2,
            "действие": "Допрос свидетелей",
            "цель": "Установить обстоятельства",
            "исполнитель": "Следователь Петров",
            "срок": "Дни 10-20"
        }
    ]
}

output_path = "test_generated_plan.docx"
success = create_investigation_plan_doc(dummy_plan_data, output_path)

if success and os.path.exists(output_path):
    print("Test document generated successfully.")
else:
    print("Failed to generate test document.")
    sys.exit(1)
