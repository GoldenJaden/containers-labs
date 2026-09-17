# Лаба 1

## Сервис API

Запуск:

```bash
cd lab1/api
go run .
```

По умолчанию сервис слушает порт `8080`. Его можно изменить переменной `PORT`.

```bash
curl http://localhost:8080/health       # проверка здоровья: ok
curl 'http://localhost:8080/eat?mb=100' # выделить и удерживать 100 MiB памяти
curl http://localhost:8080/burn         # бесконечно нагружать одно ядро CPU
```
