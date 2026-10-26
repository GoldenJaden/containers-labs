| Источник | Назначение | Протокол | Ожидание | Факт | Результат |
|---|---|---|---|---|---|
| client | api (10.0.1.163:8080) | tcp | Разрешено | Доступно | PASS |
| client | worker (10.0.1.81:8081) | tcp | Запрещено | Недоступно | PASS |
| client | postgres (10.0.1.55:5432) | tcp | Запрещено | Недоступно | PASS |
| client | stray (10.0.1.204:8082) | tcp | Запрещено | Недоступно | PASS |
| client | api DNS (shop-api.lab5.svc.cluster.local:8080) | tcp | Разрешено | Доступно | PASS |
| client | postgres DNS (shop-db-rw.lab5.svc.cluster.local:5432) | tcp | Запрещено | Недоступно | PASS |
| client | CoreDNS (10.96.0.10:53) | dns-udp | Разрешено | Доступно | PASS |
| client | CoreDNS (10.96.0.10:53) | dns-tcp | Разрешено | Доступно | PASS |
| api | api (10.0.1.4:8080) | tcp | Запрещено | Недоступно | PASS |
| api | worker (10.0.1.81:8081) | tcp | Запрещено | Недоступно | PASS |
| api | postgres (10.0.1.55:5432) | tcp | Разрешено | Доступно | PASS |
| api | stray (10.0.1.204:8082) | tcp | Запрещено | Недоступно | PASS |
| api | postgres DNS (shop-db-rw.lab5.svc.cluster.local:5432) | tcp | Разрешено | Доступно | PASS |
| api | CoreDNS (10.96.0.10:53) | dns-udp | Разрешено | Доступно | PASS |
| api | CoreDNS (10.96.0.10:53) | dns-tcp | Разрешено | Доступно | PASS |
| worker | api (10.0.1.163:8080) | tcp | Запрещено | Недоступно | PASS |
| worker | worker (10.0.1.73:8081) | tcp | Запрещено | Недоступно | PASS |
| worker | postgres (10.0.1.55:5432) | tcp | Разрешено | Доступно | PASS |
| worker | stray (10.0.1.204:8082) | tcp | Запрещено | Недоступно | PASS |
| worker | api DNS (shop-api.lab5.svc.cluster.local:8080) | tcp | Запрещено | Недоступно | PASS |
| worker | postgres DNS (shop-db-rw.lab5.svc.cluster.local:5432) | tcp | Разрешено | Доступно | PASS |
| worker | CoreDNS (10.96.0.10:53) | dns-udp | Разрешено | Доступно | PASS |
| worker | CoreDNS (10.96.0.10:53) | dns-tcp | Разрешено | Доступно | PASS |
| stray | api (10.0.1.163:8080) | tcp | Запрещено | Недоступно | PASS |
| stray | worker (10.0.1.81:8081) | tcp | Запрещено | Недоступно | PASS |
| stray | postgres (10.0.1.55:5432) | tcp | Запрещено | Недоступно | PASS |
| stray | api DNS (shop-api.lab5.svc.cluster.local:8080) | tcp | Запрещено | Недоступно | PASS |
| stray | postgres DNS (shop-db-rw.lab5.svc.cluster.local:5432) | tcp | Запрещено | Недоступно | PASS |
| stray | CoreDNS (10.96.0.10:53) | dns-udp | Запрещено | Недоступно | PASS |
| stray | CoreDNS (10.96.0.10:53) | dns-tcp | Запрещено | Недоступно | PASS |
