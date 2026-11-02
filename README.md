# Plan_llm
# Plan_llm - Генератор планов расследования уголовных дел

Система автоматической генерации планов досудебного расследования на основе PDF рапортов ЕРДР с использованием LLM (Ollama).

## 🚀 Быстрый старт

### 1. Установка зависимостей
```bash
pip install -r requirements.txt
```

### 2. Установка и запуск Ollama
```bash
# Скачайте Ollama с https://ollama.ai/
# Запустите сервер
ollama serve

# Установите модель (в другом терминале)
ollama pull llama3
```

### 3. Подготовка данных
```bash
# Создайте папку и поместите PDF рапорты
mkdir -p data/input
# Поместите ваши PDF файлы в data/input/
```

### 4. Генерация плана
```bash
python -m cli.main
```

## 🔧 Диагностика проблем

Если система работает некорректно или дает неточные данные, используйте диагностические инструменты:

### Сканирование папки с PDF файлами
```bash
python diagnose.py scan --input-dir data/input
```

### Полная диагностика файла
```bash
python diagnose.py full data/input/your_raport.pdf --model llama3
```

### Диагностика только PDF
```bash
python diagnose.py pdf data/input/your_raport.pdf
```

### Тестирование LLM модели
```bash
python diagnose.py llm --model llama3
```

### Анализ извлеченного текста
```bash
python diagnose.py text data/input/your_raport.pdf
```

## 🎯 Решение частых проблем

### Проблема: "Галлюцинации" - неправильные данные

**Возможные причины:**
- PDF файл содержит артефакты сканирования
- Модель LLM слишком "творческая"  
- Текст документа плохого качества

**Решения:**
1. **Диагностируйте PDF файл:**
   ```bash
   python diagnose.py pdf your_file.pdf
   ```

2. **Попробуйте другую модель:**
   ```bash
   # Установите более точную модель
   ollama pull mistral
   python -m cli.main --ollama-model mistral
   ```

3. **Используйте режим отладки:**
   ```bash
   python -m cli.main --debug
   ```

### Проблема: "Не удалось извлечь текст из PDF"

**Возможные причины:**
- PDF создан из сканированных изображений
- PDF защищен паролем
- Поврежденный файл

**Решения:**
1. **Проверьте PDF файл:**
   ```bash
   python diagnose.py pdf your_file.pdf
   ```

2. **Используйте OCR для сканированных PDF** (требует установки Tesseract)

3. **Пересохраните PDF** в текстовом формате

### Проблема: "Ollama недоступна"

**Решения:**
1. **Запустите Ollama сервер:**
   ```bash
   ollama serve
   ```

2. **Установите нужную модель:**
   ```bash
   ollama pull llama3
   ```

3. **Проверьте статус:**
   ```bash
   python diagnose.py llm
   ```

## 📋 Использование

### Основные команды

```bash
# Обработка конкретного файла
python -m cli.main --raport-pdf path/to/raport.pdf

# Обработка из папки input
python -m cli.main --input-dir data/input

# Указание выходного файла
python -m cli.main --output my_plan.docx

# Выбор модели LLM
python -m cli.main --ollama-model mistral

# Режим отладки
python -m cli.main --debug
```

### Тестирование извлечения данных

```bash
# Быстрый тест на примере
python -m cli.main test-extraction --model llama3

# Тест на реальном файле
python diagnose.py test-extraction extracted_text.txt --model llama3
```

## 🔍 Понимание извлекаемых данных

Система извлекает следующие поля из рапорта:

| Поле | Описание | Пример |
|---