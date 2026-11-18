# AIIP content

Документы, вложения и PDF-экспорт. `content` обслуживает HTTP и события;
`content-pdf` запускается отдельным Deployment из того же образа с
`CONTENT_ROLE=pdf`. Данные лежат в Postgres, объекты — в SeaweedFS S3.

## Запуск

Нужны Python 3.14, `uv`, Postgres, RabbitMQ, Dragonfly, SeaweedFS, а также
доступ к `auth` и `projects`. Скопируйте значения из `.env.example` в
локальное окружение и выполните:

```bash
uv sync
make upgrade
make run
```

`make check` запускает формат, lint и mypy; `make test` — unit-тесты.
Миграция `0001_content` создаёт все таблицы, включая общие inbox/outbox.
Откат начальной миграции запрещён, поскольку уничтожил бы документы и файлы.

## Контракты

- [HTTP API](docs/endpoints.md) — маршруты и схемы.
- `content.events`, `content.live`, `content.commands` — конверт `MessageV1`;
  запись событий вместе с данными через outbox из `aiip-kit`.
- Очереди `content.projects-events` и `content.pdf-render` из
  `backend/messaging.md`; консьюмеры используют inbox из `aiip-kit`.
- Права читаются через `DragonflyAccessChecker` из `aiip-kit`. Проверка
  пользовательского JWT — `aud=content`.

## Выкатка

Соберите образ по `Dockerfile`, создайте Secret `content` с переменными из
`.env.example`, примените `k8s/migrate.yaml`, затем `k8s/`. Для `content-pdf`
KEDA держит 0–1 реплику по длине очереди `content.pdf-render`; обработка
идёт с `prefetch=1` и пулом Postgres в одно соединение.

Для браузерной загрузки задайте `S3_PUBLIC_ENDPOINT` как достижимый с
клиента адрес SeaweedFS S3. Он должен вести к тому же бакету; URL подписи
включает host. Прокси или ingress для этого адреса настраивается в infra.

## Известные внешние зависимости

Проект должен выдать `document.write` (PRJ-4), а `auth` — служебные токены
(AUTH-4). Для экспорта отчёта `incidents` предоставляет
`GET /internal/v1/investigations/{id}/report` со scope
`incidents.reports.read`. Ответ содержит `report` и `incident`;
`content-pdf` использует данные отчёта и заголовок инцидента. Экспорт
документа не требует этого endpoint.
