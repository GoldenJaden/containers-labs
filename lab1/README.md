# Лаба 1

## Сервис API

Запуск:

```bash
cd lab1/api
go run .
```

```bash
curl http://localhost:8080/health       # проверка здоровья: ok
curl 'http://localhost:8080/eat?mb=100' # выделить и удерживать 100 MiB памяти
curl http://localhost:8080/burn         # бесконечно нагружать одно ядро CPU
```

# Что у нас там по процессу

Зафиксировали PID, PPID, UID. Так как у нас пока никакой изолияции нашего namespace нет, то наш процесс решил его разделить с неймспейсом shell. На этом все!

```bash
qerenny@containers:~/containers-labs/lab1/api$ pgrep -a -x api
23775 ./api

qerenny@containers:~/containers-labs/lab1/api$ ls -l /proc/$$/ns/
total 0
lrwxrwxrwx 1 qerenny qerenny 0 Sep 17 10:38 cgroup -> 'cgroup:[4026531835]'
lrwxrwxrwx 1 qerenny qerenny 0 Sep 17 10:36 ipc -> 'ipc:[4026531839]'
lrwxrwxrwx 1 qerenny qerenny 0 Sep 17 10:36 mnt -> 'mnt:[4026531841]'
lrwxrwxrwx 1 qerenny qerenny 0 Sep 17 10:36 net -> 'net:[4026531840]'
lrwxrwxrwx 1 qerenny qerenny 0 Sep 17 10:36 pid -> 'pid:[4026531836]'  
  
qerenny@containers:~/containers-labs/lab1/api$ ls -l /proc/23775/ns/
total 0
lrwxrwxrwx 1 qerenny qerenny 0 Sep 17 10:47 cgroup -> 'cgroup:[4026531835]'
lrwxrwxrwx 1 qerenny qerenny 0 Sep 17 10:36 ipc -> 'ipc:[4026531839]'
lrwxrwxrwx 1 qerenny qerenny 0 Sep 17 10:36 mnt -> 'mnt:[4026531841]'
lrwxrwxrwx 1 qerenny qerenny 0 Sep 17 10:36 net -> 'net:[4026531840]'
lrwxrwxrwx 1 qerenny qerenny 0 Sep 17 10:36 pid -> 'pid:[4026531836]'
```

# Поехали пляски -- свой неймспейс

Так, первым делом меня послали и сказали что нельзя мапиться:

```bash
qerenny@containers:~/containers-labs/lab1/api$ unshare -Ur bash
unshare: write failed /proc/self/uid_map: Operation not permitted
```

Так как машинка учебная могу себе позволить снять защиту и говорю, что не надо мне тут запрещать ничего!

```bash
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0
```

Успешно договорившись с машинкой о наших отношениях, могу с гордостью заявить, что мы сделали неймспейс, где свой pid, mount, net, uts, ipc и user.

```bash
#родительский
qerenny@containers:~/containers-labs$ ps -o pid,ppid,user,uid,stat,comm,args -p 37506
    PID    PPID USER       UID STAT COMMAND         COMMAND
  37506   37505 qerenny   1000 Sl+  api             ./api

qerenny@containers:~/containers-labs$ hostname
containers

qerenny@containers:~/containers-labs$ readlink /proc/self/ns/*
cgroup:[4026531835]
ipc:[4026531839]
mnt:[4026531841]
net:[4026531840]
pid:[4026531836]
pid:[4026531836]
time:[4026531834]
time:[4026531834]
user:[4026531837]
uts:[4026531838]

qerenny@containers:~/containers-labs$ sudo nsenter -t 37506   --user   --mount   --net   --uts   --ipc   --pid   bash

#дочерний
root@containers:~/containers-labs/lab1/api# id && echo $$
uid=0(root) gid=0(root) groups=0(root),65534(nogroup)
1

root@containers:~/containers-labs/lab1/api# hostname help-pls
root@containers:~/containers-labs/lab1/api# hostname
help-pls

root@containers:~/containers-labs/lab1/api# ip addr
1: lo: <LOOPBACK> mtu 65536 qdisc noop state DOWN group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
root@containers:~/containers-labs/lab1/api# ip route
root@containers:~/containers-labs/lab1/api# ip link
1: lo: <LOOPBACK> mtu 65536 qdisc noop state DOWN mode DEFAULT group default qlen 1000
    link/loopback 00:00:00:00:00:00 brd 00:00:00:00:00:00
    
root@containers:~/containers-labs/lab1/api# exec ./api 
2026/09/17 12:06:17 api listening on :8080

root@help-pls:/# echo $$ && \
readlink /proc/self/ns/pid && \
readlink /proc/1/ns/pid && \
ps -ef && \
19
pid:[4026532323]
pid:[4026532323]
UID          PID    PPID  C STIME TTY          TIME CMD
root           1       0  0 12:04 pts/0    00:00:00 ./api
root          19       0  0 12:11 pts/5    00:00:00 bash
root          24      19  0 12:12 pts/5    00:00:00 ps -ef


root@help-pls:/# readlink /proc/self/ns/*
cgroup:[4026531835]
ipc:[4026532322]
mnt:[4026532318]
net:[4026532324]
pid:[4026532323]
pid:[4026532323]
time:[4026531834]
time:[4026531834]
user:[4026532317]
uts:[4026532321]
```

# Пляски это мягко сказано -- cgroups

В общем здесь мы сначала для души посмотрим в каком скоупе находится наш shell.

```bash
qerenny@containers:~/containers-labs/lab1/api$ mount | grep 'cgroup'
cgroup2 on /sys/fs/cgroup type cgroup2 (rw,nosuid,nodev,noexec,relatime,nsdelegate,memory_recursiveprot)

qerenny@containers:~/containers-labs/lab1/api$ cat /proc/self/cgroup 
0::/user.slice/user-1000.slice/session-125.scope
```

Потом поймем, что, чтобы не нарушать правило no internal processes и не ломать дерево бедного systemd, мы создадим свой скоуп под владением systemd, но управление заберем в свои ручки.

```bash
qerenny@containers:~/containers-labs/lab1/api$ systemd-run --user --scope \
> --unit=lab1.scope \
> --property=Delegate=yes \
> --property=DelegateSubgroup=manager \
> bash
Running as unit: lab1.scope; invocation ID: 22ca4469e21f4a2abcd4cf299bc60291

qerenny@containers:~/containers-labs/lab1/api$ cat /proc/self/cgroup 
0::/user.slice/user-1000.slice/user@1000.service/app.slice/lab1.scope
qerenny@containers:~/containers-labs/lab1/api$ BASE=/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice/lab1.scope
```

Так как план не всегда работает как швейцарские часы и у нас subgroup отработал не так как нужно, мы сами создадим руками manager, но зато мы убедимся что мы имеем права что либо делать, ну и переместим наш bash процесс туда, где он должен был быть.

```bash
qerenny@containers:~/containers-labs/lab1/api$ mkdir "$BASE/manager"
qerenny@containers:~/containers-labs/lab1/api$ echo $$ > "$BASE/manager/cgroup.procs"
qerenny@containers:~/containers-labs/lab1/api$ cat /proc/self/cgroup
0::/user.slice/user-1000.slice/user@1000.service/app.slice/lab1.scope/manager 
#это победа друзья, мы могли с этим справиться еще на предыдущем шаге, но чего же унывать то
```

Теперь дадим нашим дочерним процессам создаваться с нашими контроллерами cpu, memory и pids. И создадим нашу целевую api со своими правилами.

```bash
qerenny@containers:~/containers-labs/lab1/api$ cat $BASE/cgroup.controllers
cpu memory pids
qerenny@containers:~/containers-labs/lab1/api$ echo '+cpu +memory +pidse' > "$BASE/cgroup.subtree_control"
bash: echo: write error: Invalid argument #ну не без урода в семье.
qerenny@containers:~/containers-labs/lab1/api$ echo '+cpu +memory +pids' > "$BASE/cgroup.subtree_control"
#так лучше

qerenny@containers:~/containers-labs/lab1/api$ mkdir $BASE/api
qerenny@containers:~/containers-labs/lab1/api$ ls $BASE/api
cgroup.controllers      cgroup.stat             cpu.stat         memory.events        memory.peak          memory.swap.peak
cgroup.events           cgroup.subtree_control  cpu.stat.local   memory.events.local  memory.pressure      memory.zswap.current
cgroup.freeze           cgroup.threads          cpu.uclamp.max   memory.high          memory.reclaim       memory.zswap.max
cgroup.kill             cgroup.type             cpu.uclamp.min   memory.low           memory.stat          memory.zswap.writeback
cgroup.max.depth        cpu.idle                cpu.weight       memory.max           memory.swap.current  pids.current
cgroup.max.descendants  cpu.max                 cpu.weight.nice  memory.min           memory.swap.events   pids.events
cgroup.pressure         cpu.max.burst           io.pressure      memory.numa_stat     memory.swap.high     pids.max
cgroup.procs            cpu.pressure            memory.current   memory.oom.group     memory.swap.max      pids.peak

qerenny@containers:~/containers-labs/lab1/api$ echo 32M > "$BASE/api/memory.max"
qerenny@containers:~/containers-labs/lab1/api$ echo 0   > "$BASE/api/memory.swap.max"  # деремся за свап
qerenny@containers:~/containers-labs/lab1/api$ echo '50000 100000' > "$BASE/api/cpu.max" # половина цпу (вроде)
qerenny@containers:~/containers-labs/lab1/api$ echo 20 > "$BASE/api/pids.max" # да, захотелось 20
```

Откроем дочерку bash, перекинем его в api/cgroup.procs, ну и запустим ./api.

```bash
qerenny@containers:~/containers-labs/lab1/api$ bash
qerenny@containers:~/containers-labs/lab1/api$ echo $$ > $BASE/api/cgroup.procs
qerenny@containers:~/containers-labs/lab1/api$ cat $BASE/api/cgroup.procs
54509
55310
qerenny@containers:~/containers-labs/lab1/api$ echo $$
54509
qerenny@containers:~/containers-labs/lab1/api$ exec ./api
2026/09/17 14:58:26 api listening on :8080

# на этом моменте я потерял где я и в каком процессе нахожусь и все слома😭. теперь все заново..........
# сделаем так на всякий nohup sleep infinity >/dev/null 2>&1 &

qerenny@containers:~/containers-labs/lab1/api$ echo $$
58306
qerenny@containers:~/containers-labs/lab1/api$ exec ./api
2026/09/17 15:19:18 api listening on :8080  
...  
  
qerenny@containers:~/containers-labs$ ps -o pid,ppid,comm,args -p 58306
    PID    PPID COMMAND         COMMAND
  58306   57050 api             ./api
  
#держимся🫠
```

Ну давайте теперь попробуем загасить его намеренно через память.

```bash
qerenny@containers:~/containers-labs$ curl http://localhost:8080/health
ok
qerenny@containers:~/containers-labs$ curl http://localhost:8080/eat?mb=100
curl: (52) Empty reply from server

    # тем временем в другой вселенной 
    qerenny@containers:~/containers-labs/lab1/api$ exec ./api
    2026/09/17 15:19:18 api listening on :8080
    Killed
    
qerenny@containers:~/containers-labs/lab1/api$ cat $BASE/api/memory.peak
33554432
qerenny@containers:~/containers-labs/lab1/api$ cat $BASE/api/memory.events
low 0
high 0
max 37
oom 1 # поймали гада
oom_kill 1
oom_group_kill 0
qerenny@containers:~/containers-labs/lab1/api$ cat $BASE/api/memory.current
0
qerenny@containers:~/containers-labs/lab1/api$ cat $BASE/api/cgroup.events
populated 0
frozen 0
```

Теперь через цпу троттлинг.
```bash
# на всякий уберем с памяти ограничение
qerenny@containers:~/containers-labs/lab1/api$ echo max > "$BASE/api/memory.max"
qerenny@containers:~/containers-labs/lab1/api$ cat "$BASE/api/cpu.stat"
usage_usec 53095
user_usec 12744
system_usec 40350
core_sched.force_idle_usec 0
nr_periods 23
nr_throttled 0
throttled_usec 0
nr_bursts 0
burst_usec 0

    # пошла жара
    watch -n 1 cat "/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice/lab1.sco
pe/api/cpu.stat"
    Every 1.0s: cat /sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice/lab1....  containers: Thu Sep 17 15:46:54 2026
    usage_usec 40002110
    user_usec 39669298
    system_usec 332812
    core_sched.force_idle_usec 0
    nr_periods 859
    nr_throttled 796
    throttled_usec 42318778
    nr_bursts 0
    burst_usec 0

    
```

А теперь через бомбу.

```bash
qerenny@containers:~/containers-labs$ echo max > "$BASE/api/cpu.max" # на всякий
qerenny@containers:~/containers-labs$ cat "$BASE/api/pids.current" \
cat "$BASE/api/pids.max" \
cat "$BASE/api/pids.events"
6
20
max 0

# чтобы ограничение работало нам нужно сделать новый шелл в это же api cgroup
qerenny@containers:~/containers-labs$ echo $$ | sudo tee "$BASE/api/cgroup.procs" 
64305
qerenny@containers:~/containers-labs$ stress-ng --fork 100 --timeout 10s
stress-ng: info:  [65727] setting to a 10 secs run per stressor
stress-ng: info:  [65727] dispatching hogs: 100 fork

    #наблюдаем
    qerenny@containers:~/containers-labs$ watch -n 0.5 cat "$BASE/api/pids.current"
    Every 0.5s: cat /sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice/lab1....  
    containers: Thu Sep 17 16:00:02 2026
    20
    
    Every 0.5s: cat /sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice/lab1....
    containers: Thu Sep 17 16:00:25 2026
    20
    
    qerenny@containers:~/containers-labs$ cat "$BASE/api/pids.events"
    max 290290
    qerenny@containers:~/containers-labs$ cat "$BASE/api/pids.peak"
    20
    # в общем можем наблюдать ограничение
    

```