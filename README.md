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

Для глубокого парсинга категорий Kwork может потребоваться залогиненная сессия. Без Cookie сайт иногда отдает пустую страницу без карточек, и метрика конкурентов становится недостоверной.

Откройте Kwork в браузере под своим аккаунтом, затем DevTools (F12) -> Network. Выберите запрос к странице Kwork, скопируйте значение Request Header `cookie` и вставьте его в `.env`:

```env
KWORK_COOKIE="ваше_значение_cookie"
```

Файл `.env` хранит локальные секреты и не должен попадать в git.

## Основные команды

```powershell
python -m app.main dry-run
python -m app.main collect-funpay
python -m app.main collect-kwork
python -m app.main import-funpay --file data/raw/funpay.csv
python -m app.main import-kwork --file data/raw/kwork.csv
python -m app.main analyze
python -m app.main export --format xlsx
python -m app.main run-all --format html
```

`--force` у команд сбора обновляет HTML без кеша. Лимит запросов и задержки задаются в `config.yaml`.

## Формат импорта FunPay

CSV/JSON должен содержать минимум:

```csv
url,title,price,currency,description,category,subcategory
https://funpay.com/lots/example/,Настройка профиля,100,RUB,Описание,Игры,Профили
```

## Формат импорта Kwork

CSV/JSON должен содержать минимум:

```csv
url,category_name,subcategory_name,competitors_count,average_price,min_price,keywords
https://kwork.ru/categories/example,Разработка и IT,AI-услуги,25,1500,500,"ai,автоматизация"
```

## Отчеты

Поддерживаются форматы `csv`, `xlsx`, `html`. HTML-отчет делится на блоки:

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
