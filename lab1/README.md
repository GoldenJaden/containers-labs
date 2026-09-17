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