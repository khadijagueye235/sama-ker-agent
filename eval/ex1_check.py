import json
import re
import time

from agent.main import demander

with open("eval/demandes_ex1.json", encoding="utf-8") as f:
    demandes = json.load(f)["demandes"]

reussis = 0
for d in demandes:
    reponse = demander(d["texte"])
    cites = sorted(set(re.findall(r"SK-\d{3}", reponse)))
    ok = cites == sorted(d["biens_attendus"])
    if not d["biens_attendus"]:
        ok = ok and re.search(r"aucun|pas de bien|ne correspond", reponse, re.I) is not None
    reussis += ok
    print("OK " if ok else "KO ", d["id"], d["texte"], "->", cites)
    time.sleep(2)  # limite de débit Groq

print(f"{reussis}/{len(demandes)} demandes réussies")