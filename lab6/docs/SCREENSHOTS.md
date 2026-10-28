# Скриншоты из Bash и Grafana

Команды показывают результаты реального прогона 28.10.2026. Логи и метрики сохранены в `docs/evidence`. Веса релиза для этих снимков менять не нужно.

Открой Git Bash на этом ПК:

```bash
cd /c/Users/GoldenJaden/repos/containers-labs/lab6
export PATH="$PWD/.tools:$PATH"
mkdir -p docs/images
```

В Linux используй путь к своей копии `lab6` и `python3` вместо `python`.

## 00-baseline.png

```bash
clear
python scripts/show_results.py baseline
```

Сними команду и результат: RPS, ошибки, p95, CPU и RAM без mesh.

## 01-mtls.png

Получим свежий публичный сертификат v2 и проверим его доверенным CA:

```bash
clear
linkerd -n lab6 identity -l lab_version=v2 > /tmp/lab6-v2-identity.txt
openssl x509 -in /tmp/lab6-v2-identity.txt -out /tmp/lab6-v2.pem
openssl x509 -in /tmp/lab6-v2.pem -noout -subject -issuer -ext subjectAltName
openssl verify -CAfile docs/evidence/roots.pem \
  -untrusted docs/evidence/issuer.pem /tmp/lab6-v2.pem

kubectl -n lab6 exec deploy/shop-api-v1 -c api -- python -c \
  'from urllib.request import urlopen; print(urlopen("http://shop-api-v2:8080/version").read().decode())'
kubectl -n lab6 logs plain-check
```

На снимке нужны identity ServiceAccount, `OK`, ответ `v2` и отказ HTTP 403 у клиента без proxy.

## 02-retries.png

```bash
clear
python scripts/show_results.py reliability
```

Основной снимок сделай в Grafana: дашборд **Shop / Lab 6 / Experiments**, диапазон **Reliability**, панель **Backend and client errors**. На одном графике видно, как после включения retry ошибки клиента падают, а backend продолжает ошибаться. Панель **Mesh backend attempts per logical request** подтверждает рост числа попыток.

## 03-split.png

```bash
clear
python scripts/show_results.py split
```

Это сохранённая выборка из 1000 настоящих запросов к `/version` при весах 90/10.

## 04-rollback.png

```bash
clear
python scripts/show_results.py bad
```

В Grafana открой диапазон **Bad release**, панель **Canary: actual v2 share and errors**. На снимке должны быть ошибки v2 и падение её доли до нуля. Терминал показывает решение гейта `rollback`.

## 05-promote.png

```bash
clear
python scripts/show_results.py good
```

В Grafana открой диапазон **Good release**, ту же панель **Canary: actual v2 share and errors**. Нужны рост доли до 100% и отсутствие ошибок. В терминале видны шаги `promote` и итоговый `complete`.

## 06-cost.png

```bash
clear
python scripts/show_results.py cost
```

Сними таблицу сравнения baseline и mesh. В Grafana для дополнительных снимков есть диапазоны **Baseline** и **Mesh cost**, панели p95, CPU и RAM.

## 07-dashboard.png

Открой ссылку **Main dashboard**, чтобы снять все панели основного дашборда. При необходимости уменьши масштаб браузера, чтобы панели поместились на экран.

## Как открыть Grafana

В отдельном терминале Bash оставь работающий port-forward:

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3006:80
```

Если порт уже занят нашим работающим port-forward, используй его. Открой `http://localhost:3006` и войди в Grafana. В переключателе Prometheus выбери существующий источник данных.

Ссылки с точными историческими диапазонами:

```bash
python scripts/screenshot_links.py
```

Открой нужную ссылку из вывода. Чтобы снять один график крупно, в меню панели выбери **View**. Названия диапазонов и панелей указаны выше.

Готовые снимки положи в `docs/images` под указанными именами. После этого обнови ссылки в README и перечень в `INVENTORY.md`.

## Ссылки записанного прогона

Время московское, UTC+3.

| Опыт | Диапазон | Grafana |
|---|---|---|
| Baseline | 2026-10-28 14:03:36 -> 14:06:36 | [Открыть](http://localhost:3006/d/shop-lab6-experiments?from=1793185416873&to=1793185596873&timezone=browser) |
| Mesh | 2026-10-28 14:49:09 -> 14:52:09 | [Открыть](http://localhost:3006/d/shop-lab6-experiments?from=1793188149042&to=1793188329042&timezone=browser) |
| Retry | 2026-10-28 14:24:19 -> 14:29:50 | [Открыть](http://localhost:3006/d/shop-lab6-experiments?from=1793186659044&to=1793186990525&timezone=browser) |
| Rollback | 2026-10-28 14:30:50 -> 14:33:36 | [Открыть](http://localhost:3006/d/shop-lab6-experiments?from=1793187050117&to=1793187216241&timezone=browser) |
| Promotion | 2026-10-28 14:35:41 -> 14:46:18 | [Открыть](http://localhost:3006/d/shop-lab6-experiments?from=1793187341376&to=1793187978148&timezone=browser) |
| Dashboard | 2026-10-28 14:35:41 -> 14:46:18 | [Открыть](http://localhost:3006/d/shop-lab6-mesh?from=1793187341376&to=1793187978148&timezone=browser) |
