# FunOrk

FunOrk помогает собрать публичные данные FunPay/Kwork, импортировать ручные CSV/JSON выгрузки, оценить направления и выгрузить отчет для тестирования безопасных услуг на Kwork.

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

### Настройка авторизации Kwork

`collect-kwork` собирает публичные категории Kwork и реальные карточки услуг из подкатегорий. Услуги сохраняются в `scraped_items` с `source="kwork"` и затем используются как база спроса при анализе FunPay.

Для глубокого парсинга категорий Kwork может потребоваться залогиненная сессия. Без Cookie сайт иногда отдает пустую страницу без карточек или SmartCaptcha, и метрика спроса становится недостоверной.

Откройте Kwork в браузере под своим аккаунтом, затем DevTools (F12) -> Network. Выберите запрос к странице Kwork, скопируйте значение Request Header `cookie` и вставьте его в `.env`:

```env
KWORK_COOKIE="ваше_значение_cookie"
```

Файл `.env` хранит локальные секреты и не должен попадать в git.

Если Kwork возвращает SmartCaptcha, FunOrk не пытается обходить защиту. Добавьте актуальный `KWORK_COOKIE` или используйте ручной импорт CSV/JSON.

## Основные команды

```powershell
python -m app.main dry-run
python -m app.main collect-funpay
python -m app.main collect-kwork --force
python -m app.main import-funpay --file data/raw/funpay.csv
python -m app.main import-kwork --file data/raw/kwork.csv
python -m app.main analyze
python -m app.main export --format xlsx
python -m app.main run-all --format html
```

`--force` у команд сбора обновляет HTML без кеша. Лимит запросов и задержки задаются в `config.yaml`.

Если Kwork не отдал карточки услуг, диагностические HTML и summary сохраняются в:

```text
data/debug/kwork/
```

## Формат импорта FunPay

CSV/JSON должен содержать минимум:

```csv
url,title,price,currency,description,category,subcategory
https://funpay.com/lots/example/,Настройка профиля,100,RUB,Описание,Игры,Профили
```

## Формат импорта Kwork

Для категорий CSV/JSON должен содержать минимум:

```csv
url,category_name,subcategory_name,competitors_count,average_price,min_price,keywords
https://kwork.ru/categories/example,Разработка и IT,AI-услуги,25,1500,500,"ai,автоматизация"
```

Для услуг Kwork CSV/JSON должен содержать минимум:

```csv
url,title,price,currency,description,category,subcategory,keywords
https://kwork.ru/kwork/example,Помощь с Gemini,1500,RUB,Настройка промптов,AI-услуги,Нейросети,"gemini,ai"
```

`import-kwork` сам определяет формат: если в строке есть `title`, сохраняется услуга Kwork; если есть `category_name`, сохраняется категория.

## Отчеты

Поддерживаются форматы `csv`, `xlsx`, `html`. Основной отчет показывает opportunities. Дополнительно при наличии собранных услуг Kwork создается CSV:

```text
data/reports/kwork_services_YYYYMMDD_HHMMSS.csv
```

HTML-отчет также содержит блок «Собранные услуги Kwork». Основные блоки отчета:

- Лучшие направления для теста
- Можно тестировать осторожно
- Требуется ручной анализ
- Не брать

## Проверка

```powershell
pytest
python -m app.main dry-run
python -m app.main export --format xlsx
```
