# Скрипты lab5

- `chaos_metrics.py` - удаляет случайные поды апи/воркера и запускает нагрузку. Сам создаёт временный клиент, если его нет, и удаляет созданный под после теста.
- `load_client.py` - код нагрузочного клиента, который выполняется внутри пода. Отдельно запускать не нужно.
- `fault.py` - вносит случайную ошибку через Helm values, раскрывает выбор и восстанавливает рабочие настройки.
- `common.py` - общие функции для этих скриптов.

Команды ниже выполняются из корня репозитория. Нужны Python 3, kubectl и Helm с доступом к кластеру.

## Хаос-тест

```bash
helm upgrade --install shop lab5/helm/shop -n lab5 --create-namespace --reset-values \
  -f lab5/infrastructure/loadtest-values.yaml

python3 lab5/scripts/chaos_metrics.py --name "chaos-$(date +%Y%m%d-%H%M%S)"

helm upgrade shop lab5/helm/shop -n lab5 --reset-values
```

Имя запуска должно быть новым, чтобы не перезаписать предыдущие результаты. Логи сохраняются в `lab5/artifacts/<имя запуска>`. Для просмотра новых метрик в Grafana нужно указать это имя в поле Run.

## Скрытая поломка

```bash
python3 lab5/scripts/fault.py inject
# Найти причину через Hubble, затем раскрыть выбор:
python3 lab5/scripts/fault.py reveal
python3 lab5/scripts/fault.py restore
```

При повторном эксперименте сначала сохраните предыдущие файлы `fault-choice.json` и `selected-fault.json` из `lab5/artifacts/private` в отдельную папку.
