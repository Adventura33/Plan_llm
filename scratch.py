import re

def detect_gender(fio: str) -> str:
    if not fio or fio == 'Н/Д':
        return 'неизвестно'
    
    results = []
    # Разделяем фигурантов по запятой
    persons = [p.strip() for p in fio.split(',')]
    
    for person in persons:
        words = [w.strip() for w in person.split() if w.strip()]
        if not words:
            continue
            
        gender = 'неизвестно'
        # Проверяем отчество
        for word in words:
            word_lower = word.lower()
            if word_lower.endswith(('ович', 'евич', 'ич', 'ұлы', 'улы')):
                gender = 'мужской'
                break
            if word_lower.endswith(('овна', 'евна', 'ична', 'инична', 'қызы', 'кызы')):
                gender = 'женский'
                break
                
        # Если по отчеству не нашли, проверяем фамилию
        if gender == 'неизвестно':
            last_names = [words[0], words[-1]] if len(words) > 1 else [words[0]]
            for ln in last_names:
                ln_lower = ln.lower()
                if ln_lower.endswith(('ов', 'ев', 'ин', 'ый', 'ий', 'ский', 'ской')):
                    gender = 'мужской'
                    break
                if ln_lower.endswith(('ова', 'ева', 'ина', 'ая', 'яя', 'ская')):
                    gender = 'женский'
                    break
                    
        if gender != 'неизвестно':
            results.append(f"{person} ({gender} пол)")
        else:
            results.append(person)
            
    return ', '.join(results)

names = ["Кубжасаров Равиль Жанатович", "Кубжасаров Равиль Жанатович, Инкербаева Алия Бекболатовна", "Толегенова Э.Д.", "Болатов Б.Б.", "Кайрат", "Ахметов"]
for n in names:
    print(f"{n}  =>  {detect_gender(n)}")

