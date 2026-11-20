"""Point d'entrée de l'agent Sama Kër.

  python -m agent.main                    # traite les nouvelles demandes de la boîte Gmail
  python -m agent.main --max 3            # au plus 3 emails
  python -m agent.main --mcp-url http://127.0.0.1:8000/mcp   # catalogue via un serveur MCP HTTP

Pour chaque email :
  1. l'agent cherche les biens (serveur MCP) et les créneaux (agenda), rédige un brouillon ;
  2. quand il veut agir pour de vrai (envoyer, créer une visite), le graphe se met en
     PAUSE (interrupt) et le conseiller décide au clavier ;
  3. le graphe REPREND avec la décision (Command(resume=...)).
La mémoire (checkpointer SQLite, une conversation par fil Gmail) permet de reprendre
un fil plus tard : quand le client répond « je prends jeudi 16 h », l'agent se souvient
des biens et créneaux proposés.
"""

import argparse
import asyncio
import re

from groq import APIStatusError
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from agent import tools_gmail as gmail
from agent.graph import MAX_ETAPES, RACINE, construire_graphe
from agent.traces import TraceurJSONL
from agent.validation import resume_action

MEMOIRE = RACINE / "memoire.db"


# --------------------------------------------------------------------------
# Exercice 1 : une demande courte, catalogue local, sans mémoire
# --------------------------------------------------------------------------
_app_ex1 = None
CONSIGNES_EX1 = """
MODE QUESTION DIRECTE (exercice 1) : il n'y a ni email ni agenda. Utilise seulement
rechercher_biens et detail_bien, puis réponds directement en texte (pas de brouillon,
pas de créneau, pas de signature).
"""


def demander(texte: str) -> str:
    global _app_ex1
    _app_ex1 = _app_ex1 or construire_graphe(consignes=CONSIGNES_EX1)
    try:
        res = _app_ex1.invoke({"messages": [("user", texte)]},
                              config={"recursion_limit": MAX_ETAPES, "callbacks": [TraceurJSONL()]})
        return res["messages"][-1].content
    except GraphRecursionError:
        return "Demande trop complexe : elle est transmise à un conseiller."


# --------------------------------------------------------------------------
# Agent complet : boîte Gmail, validation humaine, reprise
# --------------------------------------------------------------------------
CITATION = re.compile(r"^\s*(Le .{5,120} a écrit\s*:|On .{5,120} wrote\s*:)", re.M)


def sans_citation(corps: str) -> str:
    """Retire l'ancien message cité sous une réponse (« Le ven. ... a écrit : » et les lignes « > »).
    L'agent connaît déjà ce message grâce à la mémoire du fil : inutile de le renvoyer au modèle."""
    m = CITATION.search(corps)
    corps = corps[:m.start()] if m else corps
    return "\n".join(l for l in corps.splitlines() if not l.lstrip().startswith(">")).strip()


def message_pour(d: dict) -> str:
    d = {**d, "corps": sans_citation(d["corps"])}
    return (gmail.presenter_email(d)
            + f"\nIdentifiants : message_id={d['message_id']}, thread_id={d['thread_id']}, "
              f"rfc_message_id={d['rfc_message_id']}")


def decision_conseiller(demande: dict) -> dict:
    """Montre l'action proposée (et le brouillon s'il s'agit d'un envoi), puis demande o/N."""
    print("\n" + "=" * 64)
    print(resume_action(demande["action"], {k: v for k, v in demande["arguments"].items()}))
    if demande["action"] == "envoyer_brouillon":
        a = gmail.apercu_brouillon(demande["arguments"].get("draft_id", ""))
        if a.get("ok"):
            print(f"\n  À : {a['a']}\n  Objet : {a['objet']}\n" + "-" * 64 + f"\n{a['corps'].strip()}")
    print("=" * 64)
    oui = input("Conseiller, validez-vous ? [o/N] ").strip().lower() in ("o", "oui", "y")
    commentaire = "" if oui else input("Motif du refus (facultatif) : ").strip()
    return {"ok": oui, "commentaire": commentaire}


async def traiter_email(app, d: dict) -> str:
    config = {"configurable": {"thread_id": d["thread_id"]}, "recursion_limit": MAX_ETAPES,
              "callbacks": [TraceurJSONL()]}
    entree = {"messages": [("user", message_pour(d))]}
    try:
        while True:
            resultat = await app.ainvoke(entree, config)
            pauses = resultat.get("__interrupt__")
            if not pauses:
                return resultat["messages"][-1].content
            entree = Command(resume=decision_conseiller(pauses[0].value))   # reprise du graphe
    except GraphRecursionError:
        gmail.transmettre_conseiller(d["message_id"], "demande trop complexe pour l'agent")
        return "Demande trop complexe : transmise à un conseiller."


async def traiter_boite(max_emails: int = 5, url_mcp: str | None = None):
    from agent.outils import charger_outils
    outils = await charger_outils(url_mcp=url_mcp)
    print(f"{len(outils)} outils chargés : {', '.join(o.name for o in outils)}")
    async with AsyncSqliteSaver.from_conn_string(str(MEMOIRE)) as memoire:
        app = construire_graphe(outils, checkpointer=memoire)
        r = gmail.lire_demandes_non_traitees(max_emails)
        if not r.get("ok"):
            print("Lecture Gmail impossible :", r)
            return
        print(f"{r['nb_demandes']} demande(s) à traiter.")
        for d in r["demandes"]:
            print(f"\n### {d['expediteur']} · {d['objet']}")
            try:
                print(await traiter_email(app, d))
            except APIStatusError as e:
                if e.status_code not in (413, 429):
                    raise
                attente = re.search(r"try again in ([\dhms.]+)", str(e))
                print("\nLimite Groq atteinte (" + ("requête trop longue" if e.status_code == 413 else
                      "quota de tokens") + ") : traitement interrompu.\n"
                      "Seules les actions déjà validées par le conseiller ont été faites ; l'email garde "
                      "le libellé « À traiter » et sera repris au prochain lancement"
                      + (f" (réessayer dans {attente.group(1)})." if attente else "."))
                return


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Agent Sama Kër : traite les demandes de la boîte Gmail.")
    p.add_argument("--max", type=int, default=5, help="nombre maximal d'emails à traiter")
    p.add_argument("--mcp-url", help="URL d'un serveur MCP HTTP (défaut : serveur lancé en stdio)")
    args = p.parse_args()
    asyncio.run(traiter_boite(args.max, args.mcp_url))
