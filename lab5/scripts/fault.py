"""Inject one randomly selected values overlay. Reveal only after Hubble diagnosis."""
import argparse
import json
import random
import time
from common import ROOT, HELM, run, save

p=argparse.ArgumentParser()
p.add_argument("action",choices=["inject","restore","reveal"])
args=p.parse_args()
choice=ROOT/"artifacts/private/fault-choice.json"
if args.action=="inject":
    if choice.exists(): raise SystemExit("An experiment already exists; archive it before injecting another")
    candidates=[
        ("wrong-database-selector",{"networkPolicy":{"databaseLabels":{"cnpg.io/cluster":"shop-db-typo"}}}),
        ("wrong-database-port",{"networkPolicy":{"databasePort":5433}}),
        ("missing-api-dns",{"networkPolicy":{"dnsComponents":["worker","database","client"]}}),
        ("missing-worker-database",{"networkPolicy":{"allowWorkerDatabase":False}}),
    ]
    name,values=random.SystemRandom().choice(candidates)
    overlay=save("private/selected-fault.json",values)
    save("private/fault-choice.json",{"name":name,"injected_at":time.time()})
    print(run(HELM,"upgrade","shop",ROOT/"helm/shop","-n","lab5","--reset-values","-f",overlay).stdout)
    print("A hidden fault is active. Diagnose the flows before using reveal.")
elif args.action=="restore":
    print(run(HELM,"upgrade","shop",ROOT/"helm/shop","-n","lab5","--reset-values").stdout)
    save("fault-restored.json",{"time":time.time()})
else:
    save("fault/revealed.json",json.loads(choice.read_text(encoding="utf-8")))
    print(choice.read_text(encoding="utf-8"))
