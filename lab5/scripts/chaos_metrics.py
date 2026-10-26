"""Run pod chaos with real client and controller counters scraped by Prometheus."""
import argparse
import json
import os
import random
import subprocess
import time
import urllib.parse
import urllib.request
from common import ROOT,kube

def query(expr):
    url="http://prometheus.homelab.internal/api/v1/query?"+urllib.parse.urlencode({"query":expr})
    return json.load(urllib.request.urlopen(url,timeout=15))

def ensure_client():
    existing=kube("get","pod","lab5-client","-n","lab5","--ignore-not-found","-o","json").stdout.strip()
    if existing:
        if json.loads(existing)["metadata"].get("labels",{}).get("lab5-role")!="client":
            raise RuntimeError("lab5-client exists without the expected client label")
        return False
    manifest={"apiVersion":"v1","kind":"Pod",
              "metadata":{"name":"lab5-client","namespace":"lab5","labels":{"lab5-role":"client"}},
              "spec":{"restartPolicy":"Never","automountServiceAccountToken":False,
                      "nodeSelector":{"kubernetes.io/hostname":"k8s-ctrlr"},
                      "tolerations":[{"key":"node-role.kubernetes.io/control-plane","operator":"Exists","effect":"NoSchedule"}],
                      "securityContext":{"runAsNonRoot":True,"runAsUser":10001},
                      "containers":[{"name":"client","image":"192.168.0.242:3000/gjaden/lab4-api:1.0.1",
                                     "command":["python","-m","http.server","8082"],
                                     "resources":{"requests":{"cpu":"1m","memory":"32Mi"},"limits":{"memory":"128Mi"}},
                                     "securityContext":{"allowPrivilegeEscalation":False,"capabilities":{"drop":["ALL"]}}}]}}
    kube("create","-f","-",input=json.dumps(manifest))
    return True

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--seconds",type=int,default=600)
    parser.add_argument("--rps",type=float,default=5)
    parser.add_argument("--interval",type=float,default=5)
    parser.add_argument("--name",default="chaos-03")
    args=parser.parse_args()
    directory=ROOT/"artifacts"/args.name
    directory.mkdir(parents=True,exist_ok=False)
    (ROOT/".tools").mkdir(exist_ok=True)
    created_client=ensure_client()
    try:
        kube("wait","-n","lab5","--for=condition=Ready","pod/lab5-client","--timeout=90s",timeout=100)
    except BaseException:
        if created_client:
            kube("delete","pod","lab5-client","-n","lab5","--ignore-not-found","--wait=false",check=False)
        raise
    config={"seconds":args.seconds,"rps":args.rps,"name":args.name,"warmup":0,"metrics":True,"start_gate":True}
    code="CONFIG="+repr(config)+"\n"+(ROOT/"scripts/load_client.py").read_text(encoding="utf-8")
    output=open(directory/"client.log","w",encoding="utf-8")
    errors=open(directory/"client-errors.log","w",encoding="utf-8")
    client=subprocess.Popen(["kubectl","exec","-n","lab5","lab5-client","--","python","-c",code],stdout=output,stderr=errors)
    tunnel_log=open(ROOT/".tools/client-portforward.log","w")
    tunnel=subprocess.Popen(["kubectl","port-forward","-n","lab5","pod/lab5-client","19090:9090","--address=127.0.0.1"],stdout=tunnel_log,stderr=tunnel_log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
    events=[]; seen={}; pending=[]
    def persist():
        (directory/"events.json").write_text(json.dumps(events,indent=2),encoding="utf-8")
    def publish():
        while pending:
            body=json.dumps(pending[0]).encode()
            try:
                request=urllib.request.Request("http://127.0.0.1:19090/events",data=body,headers={"Content-Type":"application/json"})
                with urllib.request.urlopen(request,timeout=2) as response:
                    if response.status!=204: raise RuntimeError("Unexpected event collector response")
                pending.pop(0)
            except Exception: return
    try:
        for _ in range(40):
            if client.poll() is not None: raise RuntimeError("Client exited during warmup; inspect client-errors.log")
            try:
                urllib.request.urlopen("http://127.0.0.1:19090/metrics",timeout=1).close()
                break
            except Exception: time.sleep(.5)
        else: raise RuntimeError("Client metrics endpoint is unreachable")
        print("Client metrics available; waiting for the first Prometheus scrape before load",flush=True)
        deadline=time.monotonic()+300
        while time.monotonic()<deadline:
            samples=query('lab5_client_requests_total{run="'+args.name+'",result="failed"}')["data"]["result"]
            if samples and float(samples[0]["value"][1])==0: break
            time.sleep(2)
        else: raise RuntimeError("Prometheus did not scrape the client; load was not started")
        with urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:19090/start",data=b"{}",headers={"Content-Type":"application/json"}),timeout=5): pass
        started=time.monotonic()
        next_delete=started+args.interval; next_status=started
        while time.monotonic()<started+args.seconds:
            try:
                pods=json.loads(kube("get","pods","-n","lab5","-l","app.kubernetes.io/instance=shop","-o","json","--request-timeout=10s",timeout=15).stdout)["items"]
            except (RuntimeError,subprocess.TimeoutExpired) as error:
                events.append({"event":"control_error","time":time.time(),"detail":str(error)}); persist(); time.sleep(1); continue
            changed=False
            for pod in pods:
                uid=pod["metadata"]["uid"]
                if uid not in seen and pod["status"].get("podIP"):
                    event={"event":"observed","time":time.time(),"pod":pod["metadata"]["name"],"uid":uid,"ip":pod["status"]["podIP"],"component":pod["metadata"]["labels"].get("app.kubernetes.io/component")}
                    seen[uid]=event; events.append(event); changed=True
            if time.monotonic()>=next_delete:
                eligible=[pod for pod in pods if not pod["metadata"].get("deletionTimestamp") and pod["metadata"]["labels"].get("app.kubernetes.io/component") in ["api","worker"] and pod["status"].get("containerStatuses") and all(c.get("ready") for c in pod["status"]["containerStatuses"])]
                if eligible:
                    pod=random.SystemRandom().choice(eligible)
                    result=kube("delete","pod","-n","lab5",pod["metadata"]["name"],"--wait=false","--request-timeout=10s",timeout=15,check=False)
                    if result.returncode:
                        events.append({"event":"delete_error","time":time.time(),"detail":result.stderr})
                    else:
                        event={"event":"delete","time":time.time(),"pod":pod["metadata"]["name"],"uid":pod["metadata"]["uid"],"ip":pod["status"]["podIP"],"component":pod["metadata"]["labels"]["app.kubernetes.io/component"]}
                        events.append(event); pending.append(dict(event,unique_ips=len({e["ip"] for e in seen.values()})))
                next_delete=time.monotonic()+args.interval
            if changed: pending.append({"event":"snapshot","unique_ips":len({e["ip"] for e in seen.values()})})
            publish(); persist()
            if time.monotonic()>=next_status:
                print("Elapsed",round(time.monotonic()-started),"s; deleted",sum(e["event"]=="delete" for e in events),"pods",flush=True)
                next_status+=30
            time.sleep(1)
        publish(); persist()
        if pending: raise RuntimeError("Unpublished controller events remain; do not claim Grafana counters match")
        client.wait(timeout=60)
        output.close(); errors.close()
        if client.returncode: raise RuntimeError((directory/"client-errors.log").read_text(encoding="utf-8"))
        records=kube("exec","-n","lab5","lab5-client","--","cat","/tmp/lab5-"+args.name+".jsonl").stdout
        (directory/"requests.jsonl").write_text(records,encoding="utf-8")
        summary=json.loads(kube("exec","-n","lab5","lab5-client","--","cat","/tmp/lab5-"+args.name+"-summary.json").stdout)
        summary.update({"deletions":sum(e["event"]=="delete" for e in events),"unique_pod_ips":len({e["ip"] for e in seen.values()}),"control_errors":sum(e["event"]=="control_error" for e in events),"metrics_run":args.name})
        (directory/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
        print(json.dumps(summary,indent=2),flush=True)
        if summary["failed"] or summary["pending_orders"]: raise SystemExit(1)
    finally:
        persist()
        tunnel.terminate()
        tunnel_log.close()
        if not output.closed: output.close()
        if not errors.closed: errors.close()
        if created_client:
            kube("delete","pod","lab5-client","-n","lab5","--ignore-not-found","--wait=false",check=False)

if __name__=="__main__": main()
