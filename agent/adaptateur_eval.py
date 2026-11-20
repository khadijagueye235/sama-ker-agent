"""Adaptateur d'évaluation : fait jouer à l'agent les emails FICTIFS du jeu de test.

  python -m eval.run_eval --agent agent.adaptateur_eval --label avant --pause 5

Pourquoi un adaptateur ? Les 15 emails de eval/emails_test.json n'existent pas
dans Gmail, et une évaluation ne doit rien écrire de réel. L'adaptateur donne
donc à l'agent :
  - les vrais outils de lecture (catalogue, créneaux libres de l'agenda) ;
  - les outils d'écriture de l'agenda derrière la validation `demander`
    (fournie par run_eval.py : elle enregistre la demande et REFUSE toujours) ;
  - des outils Gmail FACTICES, avec le même nom, les mêmes arguments et la même
    notice que les vrais : ils enregistrent le brouillon ou la transmission au
    lieu d'appeler Gmail.

Puis il traduit ce que l'agent a fait en résultat notable par run_eval.py
(décision, brouillon, biens proposés, créneaux proposés).

Le catalogue est appelé en Python, sans passer par le serveur MCP : c'est le
même code (mcp_server/catalogue.py), et l'équivalence est vérifiée par
mcp_server/test_client.py. On évite ainsi de lancer un serveur par appel.
"""

import json
import re
import sqlite3
from datetime import datetime

from langchain_core.messages import ToolMessage

from agent import tools_calendar as cal
from agent import tools_gmail as gmail
from agent.graph import MAX_ETAPES, RACINE, construire_graphe, outils_catalogue_locaux
from agent.validation import outils_langgraph

RE_BIEN = re.compile(r"\bSK[\s-]?0*(\d{1,3})\b", re.IGNORECASE)
RE_DECISION = re.compile(r"D[ÉEée]CISION\s*[:=]\s*\W*(repondre|demander_precisions|transmettre)", re.IGNORECASE)
# Seule consigne propre à l'évaluation : déclarer la décision (le reste = prompt de l'agent).
# Formulée comme une phrase de texte : sinon le modèle tente un appel d'outil nommé « DECISION ».
CONSIGNES = """
ÉVALUATION : quand tout est fini, écris un dernier message en TEXTE (ce n'est pas un outil)
qui se termine par la phrase « Décision : repondre », « Décision : demander_precisions »
ou « Décision : transmettre ».
"""


def _catalogue_disponibles() -> set[str]:
    with sqlite3.connect(RACINE / "mcp_server" / "catalogue.db") as cnx:
        return {ligne[0] for ligne in cnx.execute("SELECT id FROM biens WHERE disponible = 1")}


DISPONIBLES = _catalogue_disponibles()


def _gmail_factice(journal: dict):
    """Outils Gmail factices : même signature et même notice que les vrais."""

    def creer_brouillon(message_id: str, thread_id: str, expediteur: str, objet: str,
                        rfc_message_id: str, corps: str) -> dict:
        journal["brouillon"] = corps
        return {"ok": True, "draft_id": f"brouillon-{message_id}",
                "message": "Brouillon créé, en attente de validation."}

    def transmettre_conseiller(message_id: str, motif: str) -> dict:
        journal["transmis"] = motif
        # Transmettre APRÈS un brouillon = réaction au refus (forcé) de l'évaluation, pas une décision.
        journal.setdefault("transmis_d_emblee", "brouillon" not in journal)
        return {"ok": True, "message": f"Transmis à un conseiller : {motif}"}

    def envoyer_brouillon(draft_id: str, message_id: str) -> dict:  # jamais exécuté : la validation refuse
        journal["envoi_execute"] = True
        return {"ok": True, "message_envoye": "factice"}

    for f in (creer_brouillon, transmettre_conseiller, envoyer_brouillon):
        f.__doc__ = getattr(gmail, f.__name__).__doc__
    return [creer_brouillon, transmettre_conseiller], [envoyer_brouillon]


def _creneaux_cites(brouillon: str, messages) -> list[str]:
    """Créneaux renvoyés par lister_creneaux_libres ET repris dans le brouillon."""
    texte = brouillon.lower()
    proposes = []
    for m in messages:
        if not (isinstance(m, ToolMessage) and m.name == "lister_creneaux_libres"):
            continue
        try:
            creneaux = json.loads(m.content).get("creneaux", [])
        except (json.JSONDecodeError, AttributeError):
            continue
        for c in creneaux:
            d = datetime.fromisoformat(c["debut"])
            # « 16 h 30 », « 16h30 », « 16:30 » ; heure pile : « 16 h », « 16h00 », « 16:00 »
            heure = (rf"\b{d.hour}\s*[h:]\s*{d.minute:02d}\b" if d.minute
                     else rf"\b{d.hour}\s*(?:h\s*(?:00)?|:00)(?!\s*\d)")
            jour = rf"\b{d.day}\b|\b{cal.JOURS[d.weekday()]}\b"
            if re.search(heure, texte) and re.search(jour, texte) and c["debut"] not in proposes:
                proposes.append(c["debut"])
    return proposes


def traiter_email(email: dict, demander) -> dict:
    """Contrat de eval/run_eval.py : un email fictif -> ce que l'agent a décidé."""
    journal: dict = {}
    gmail_lecture, gmail_ecriture = _gmail_factice(journal)
    outils = outils_catalogue_locaux() + outils_langgraph(
        cal.OUTILS_LECTURE + gmail_lecture, cal.OUTILS_ECRITURE + gmail_ecriture, demander=demander)
    message = (gmail.presenter_email({"expediteur": email["expediteur"], "objet": email["objet"],
                                      "corps": email["corps"]})
               + f"\nIdentifiants : message_id={email['id']}, thread_id={email['id']}, "
                 f"rfc_message_id=<{email['id']}@eval.sama-ker>")

    resultat = construire_graphe(outils, consignes=CONSIGNES).invoke({"messages": [("user", message)]},
                                          config={"recursion_limit": MAX_ETAPES})
    messages = resultat["messages"]
    final = str(messages[-1].content or "")
    brouillon = journal.get("brouillon", "")

    declaree = RE_DECISION.search(final) or RE_DECISION.search(brouillon)
    declaree = declaree.group(1).lower() if declaree else None
    creneaux = _creneaux_cites(brouillon, messages)
    biens = sorted({f"SK-{int(n):03d}" for n in RE_BIEN.findall(brouillon)} & DISPONIBLES)
    if journal.get("transmis_d_emblee"):
        decision = "transmettre"
    elif brouillon:   # un brouillon a été rédigé : réponse ou questions, jamais « transmettre »
        decision = declaree if declaree in ("repondre", "demander_precisions") else (
            "repondre" if biens or creneaux else "demander_precisions")
    else:
        decision = declaree

    actions = ["envoyer_brouillon"] if journal.get("envoi_execute") else []
    return {"decision": decision, "brouillon": brouillon, "biens_proposes": biens,
            "creneaux": creneaux, "actions_sans_validation": actions}


def repondre_demande(texte: str) -> str:
    """Exercice 1 : on évalue directement l'agent de l'équipe (agent.main.demander)."""
    from agent.main import demander as demander_agent
    return demander_agent(texte)
