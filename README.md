# social-poster

CLI для очереди постов в Buffer (Threads, X, LinkedIn). Только стандартная
библиотека Python, без зависимостей.

Ключи берутся из переменных окружения или из `/root/.hermes/.env`:
`BUFFER_API_KEY` (аккаунт 1, по умолчанию) и `BUFFER_API_KEY_2` (аккаунт 2).
В вывод ключи не попадают.

## Команды

```bash
# что подключено (без --account — оба аккаунта)
python3 buffer_post.py channels
python3 buffer_post.py channels --account 2

# очередь и последние посты
python3 buffer_post.py queue --account 2 --limit 20

# положить пост в очередь
python3 buffer_post.py add --service threads --text-file post.txt
python3 buffer_post.py add --service threads --text-file post-en.txt --due-at 2026-10-07T09:00:00Z
python3 buffer_post.py add --service x --text-file post.txt --dry-run

# удалить посты из очереди по id (опубликованные Buffer не отдаёт)
python3 buffer_post.py delete --account 2 <id1> <id2>
```

## Сервисы в CLI

`threads`, `x`, `bluesky`, `linkedin`. Для X внутрь подставляется имя сервиса
Buffer `twitter`.

## Проверки до отправки

Лимиты символов: Threads 500, Bluesky 300 графем, X 280, LinkedIn 3000.
Для Threads считается UTF-8 байтами (кириллица ≈ 2 байта на символ).
Скрипт требует наличия ссылки на бот в тексте; обойти — `--allow-no-link`.
`--dry-run` печатает payload и ничего не отправляет.

## Кто этим пользуется

Фоновые крон-задачи продвижения (Smart Favorite, IntelgramAI, ezodoc).
Тексты и банк тем лежат отдельно — см. `docs/SOCIAL-AUTO.md` в репозитории
navsegda или промпты задач.
