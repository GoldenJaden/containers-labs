# Запуск на Kubernetes

Стенд запущен на кластере 28.10.2026; результаты находятся в `docs/evidence`.
Команды ниже позволяют повторить запуск. Чарт создаёт приложение и PostgreSQL
`shop-postgres` в namespace `lab6`.

## Предварительные условия

Нужны Kubernetes, kubectl, Helm 3, Python 3.12+ и Docker/совместимый builder.
Для данных нужен StorageClass с возможностью выделить 2 GiB. PostgreSQL
разворачивается самим чартом как одиночный StatefulSet.
Мониторинг требует Prometheus Operator, Prometheus с cAdvisor-метриками и Grafana;
ниже описана установка этого стека на новом кластере.

```bash
kubectl config current-context
kubectl get nodes
kubectl get storageclass
helm lint helm/shop
python -m pip install -r requirements-dev.txt
```

Для отдельного локального стенда при уже установленном Docker можно создать
одноузловой kind-кластер: `kind create cluster --name lab6`. Затем установите
Linkerd и мониторинг по разделам ниже, после чего запускайте сценарии из README.

## Образы и values

```bash
docker build -t shop-api:6.0.0 api
docker build -t shop-gate:6.0.0 gate
```

Для kind без registry:

```bash
kind load docker-image shop-api:6.0.0 shop-gate:6.0.0 --name lab6
```

Для другого кластера образы должны быть доступны его нодам. Можно импортировать архивы
`docker save` в containerd каждой ноды либо самостоятельно использовать свой
registry. Здесь отправка образов или кода куда-либо не выполнялась.

```bash
cp values/local.example.yaml values-local.yaml
```

Для этого homelab вместо примера используется `cp values/homelab.yaml values-local.yaml`.

При необходимости в этой копии укажите image repository, storageClass и
`monitoring.labels.release`, который выбирает ваш kube-prometheus-stack.
Для изолированного учебного стенда в values задан демонстрационный пароль.
Его можно заменить через `database.password` в локальной копии до первого запуска.
Чарт создаёт Secret `shop-postgres`; API получает отдельные `PGHOST`,
`PGDATABASE`, `PGUSER` и `PGPASSWORD`, поэтому спецсимволы пароля не нужно
вручную кодировать в URL. После инициализации тома изменение Secret само по
себе не меняет пароль внутри PostgreSQL — между сценариями сохраняйте его.
Образ и каталог PGDATA настроены по [документации официального PostgreSQL image](https://github.com/docker-library/docs/blob/master/postgres/README.md).

## Linkerd

Чарт использует HTTPRoute v1 и новые retry-аннотации: требуется Linkerd 2.18+
или соответствующий edge, совместимый с версией Gateway API кластера.
Публичный пример закреплённой edge-версии — [edge-26.6.3](https://github.com/linkerd/linkerd2/releases/tag/edge-26.6.3).
Это ориентир воспроизводимости, а не утверждение о самой новой версии.
CLI и control plane должны соответствовать друг другу.

Проверяем, установлен ли Gateway API:

```bash
kubectl get crd httproutes.gateway.networking.k8s.io
linkerd version --client
linkerd check --pre
```

Если Gateway API уже есть, сверьте его с
[таблицей совместимости](https://linkerd.io/2.18/features/gateway-api/)
**именно вашей версии Linkerd**. Для Linkerd 2.18 документированы Gateway API
1.1.1–1.2.1. Для более нового edge используйте документацию соответствующего релиза.

На новом стенде без Gateway API можно установить комплект CRD, поставляемый
с выбранным Linkerd CLI:

```bash
linkerd install --crds --set installGatewayAPI=true | kubectl apply -f -
linkerd install | kubectl apply -f -
linkerd check
```

Если совместимый Gateway API уже установлен, в первой команде вместо `true`
задайте `false`. Не понижайте общекластерные CRD ради этого эксперимента.
Если Linkerd уже работает — повторная установка не нужна.

Инжектор обычно добавляет init-контейнер с `NET_ADMIN`/`NET_RAW` для настройки
перенаправления трафика. Если admission-политики вашего кластера запрещают это,
проверьте admission errors. Для такого окружения предусмотрен Linkerd CNI;
либо отдельное узкое исключение для проверенного init-контейнера.
Правила всего кластера ради лабы отключать не нужно.

Стек `linkerd-viz` для основного решения не обязателен: метрики напрямую
попадают в существующий Prometheus через PodMonitor.

### Cilium с заменой kube-proxy

При включённом socket LB внутри pod Linkerd может видеть IP backend вместо
адреса Service: mTLS работает, но HTTPRoute и retry не применяются. На этом
стенде это проявилось выборкой из 1000 ответов только v1 при весах 90/10.
Для совместимости нужен `socketLB.hostNamespaceOnly=true` в Cilium.
Проверять нужно фактический вывод `cilium-dbg status --verbose` на рабочей
ноде: до изменения он показывал `Socket LB Coverage: Full`.
Изменение Cilium общекластерное и требует перезапуска агентов; здесь оно
выполнялось после явного согласования с владельцем кластера.
[Описание настройки в документации Cilium](https://docs.cilium.io/en/stable/network/servicemesh/istio/).

## Prometheus и Grafana

Если стек мониторинга ещё не установлен, разверните его отдельно от приложения:

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update prometheus-community
helm upgrade --install monitoring prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false \
  --set prometheus.prometheusSpec.ruleSelectorNilUsesHelmValues=false \
  --set grafana.sidecar.dashboards.searchNamespace=ALL \
  --wait --timeout 10m
```

Это готовая инфраструктурная зависимость для метрик, правил и графиков lab6.
Вариант с уже установленным совместимым стеком также подходит.
Перед установкой приложения проверьте наличие нужных CRD, затем найдите
сервис Prometheus и пробросьте его порт в отдельном терминале:

```bash
kubectl get crd servicemonitors.monitoring.coreos.com podmonitors.monitoring.coreos.com prometheusrules.monitoring.coreos.com
kubectl -n monitoring get svc
# Подставьте существующее имя сервиса вашего Prometheus:
kubectl -n monitoring port-forward svc/PROMETHEUS_SERVICE 9090:9090
```

Если стек выбирает только определённые labels у мониторов и rules,
задайте их в `monitoring.labels`. Если используются селекторы namespace,
разрешите `lab6` в конфигурации вашего стека. Это не меняется автоматически.
При NetworkPolicy должен быть доступ Prometheus → pod:9090 и proxy:4191,
mesh-подам нужны DNS, control plane и межподовые соединения Linkerd.

Проверка targets:

```promql
up{namespace="lab6",lab_release="shop"}
```

Должны быть targets API/client и их proxy после подключения mesh.
Гейт фильтрует `up` с `lab_version="v2"`; нездоровый target остановит продвижение.
Дашборд можно импортировать вручную из `helm/shop/dashboard.json` либо загрузить
Grafana sidecar через ConfigMap с label `grafana_dashboard=1`.
Для sidecar нужен поиск ConfigMap в namespace `lab6`, например
`sidecar.dashboards.searchNamespace=ALL` в уже используемом Grafana chart.

## Проверка mTLS

1. Развернуть `bash scripts/deploy.sh good`.
2. Сохранить `linkerd check --proxy -n lab6` и
   `linkerd -n lab6 identity -l lab_component=api`.
3. Проверить SAN сертификата: identity должен соответствовать ServiceAccount
   `shop-api-v1.lab6.serviceaccount.identity.linkerd.cluster.local` или v2.
   Сверить issuer, срок действия и доверенный корень. У новой реализации
   формат SAN может быть URI: ориентироваться нужно на фактический вывод CLI.
4. Выполнить запрос из `shop-api-v1` в `shop-api-v2`, как в README, и увидеть
   `response_total{namespace="lab6",lab_component="api",lab_version="v2",direction="inbound",tls="true"}`.
   Для нового соединения используйте свежий клиентский процесс.

Команда `linkerd identity` показывает сертификат, но сама использует
`InsecureSkipVerify` для диагностики; её вывод не заменяет проверку цепочки.
Это проверено по [исходному коду CLI](https://github.com/linkerd/linkerd2/blob/edge-26.6.3/cli/cmd/identity.go).
Для сохранённого PEM и независимой проверки пробросьте admin-порт прокси
**конкретного pod v2** на локальную машину — этот же порт использует CLI:

```bash
POD=$(kubectl -n lab6 get pod -l lab_component=api,lab_version=v2 -o jsonpath='{.items[0].metadata.name}')
kubectl -n lab6 port-forward "pod/$POD" 14191:4191
```

В другом терминале сохраните **публичные** trust anchors/issuer:

```bash
mkdir -p docs/evidence
kubectl -n linkerd get configmap linkerd-identity-trust-roots \
  -o jsonpath='{.data.ca-bundle\.crt}' > docs/evidence/roots.pem
kubectl -n linkerd get secret linkerd-identity-issuer \
  -o jsonpath='{.data.crt\.pem}' | base64 -d > docs/evidence/issuer.pem
# При внешнем issuer ключ сертификата может называться tls.crt, не crt.pem.
openssl s_client -connect localhost:14191 \
  -servername shop-api-v2.lab6.serviceaccount.identity.linkerd.cluster.local \
  -showcerts </dev/null \
  > docs/evidence/handshake.txt 2>&1
openssl x509 -in docs/evidence/handshake.txt -out docs/evidence/peer.pem
openssl x509 -in docs/evidence/peer.pem -noout -subject -issuer -dates -ext subjectAltName
openssl verify -CAfile docs/evidence/roots.pem -untrusted docs/evidence/issuer.pem docs/evidence/peer.pem
```

Проверяйте содержимое PEM и код возврата `openssl verify`; пустой экспорт — ошибка.
У Linkerd с внешним CA имена ресурсов/ключей могут отличаться.
Этот шаг получает публичный сертификат на диагностическом порту, а успешный
**взаимный** TLS для бизнес-трафика подтверждают запрос двух mesh-под,
strict-политика и proxy-телеметрия.

Отрицательный тест — вызов бизнес-ручки из явно немешенного pod:

```bash
kubectl -n lab6 run plain-check --image=curlimages/curl:8.12.1 \
  --annotations=linkerd.io/inject=disabled --restart=Never -- \
  curl -sS -o /dev/null -w '%{http_code}\n' --max-time 5 http://shop-api-v2:8080/version
kubectl -n lab6 logs plain-check
# После сохранения вывода:
kubectl -n lab6 delete pod plain-check
```

Ожидается отказ, например 403. Успешный 200 для `/version` означает, что
strict-политика не доказана. `/health` намеренно разрешён без identity.
При запрещённых внешних image registry используйте разрешённый эквивалент образа curl.

## Сохранение и воспроизводимость

Каждый сценарий начинайте через `deploy.sh`, который делает `--reset-values`.
Не запускайте Helm upgrade одновременно с гейтом.
Скрипт также явно сбрасывает веса HTTPRoute из values установленного релиза:
одного Helm upgrade недостаточно, если гейт изменил веса, а желаемые values
между сценариями остались прежними.

```bash
bash scripts/collect.sh
python -m pytest -q -ra
```

`collect.sh` читает версии компонентов, pod, HTTPRoute, сертификаты и распределение
`/version`; он не экспортирует kubeconfig или приватные ключи.
Для теста реальной БД задайте `TEST_DATABASE_URL` на **отдельную тестовую базу**.
Тест создаёт таблицу и тестовый заказ. Его нельзя направлять на рабочую БД.

Исходные результаты хранятся в `docs/evidence`. Скриншоты отчёта и назначение
сохранённых скриптов перечислены в `docs/INVENTORY.md`.
