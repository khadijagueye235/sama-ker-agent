"""Validation humaine avant toute écriture (exigence non négociable de l'agence).

Deux façons de demander l'accord d'un conseiller :
  - valider_en_console(action, arguments) : question posée dans le terminal
    (démonstrations, scripts) ;
  - outils_langgraph(...) : outils LangChain pour l'agent. Les outils d'écriture
    appellent interrupt() AVANT d'agir : le graphe se met en pause et ne reprend
    qu'avec Command(resume={"ok": True}) (ou {"ok": False, "commentaire": ...}).

Les outils d'écriture ne sont JAMAIS donnés au modèle sans cette enveloppe.
"""

import functools
import json

from langchain_core.tools import StructuredTool
from langgraph.types import interrupt

LIBELLES = {
    "creer_visite": "Créer une visite dans l'agenda",
    "deplacer_visite": "Déplacer une visite",
    "annuler_visite": "Annuler une visite",
}


def resume_action(action: str, arguments: dict) -> str:
    lignes = [f"Action proposée par l'agent : {LIBELLES.get(action, action)}"]
    lignes += [f"  - {cle} : {valeur}" for cle, valeur in arguments.items() if valeur not in (None, "")]
    return "\n".join(lignes)


def valider_en_console(action: str, arguments: dict) -> dict:
    """Pose la question au conseiller dans le terminal. Renvoie {"ok": bool, "commentaire": str}."""
    print("\n" + "=" * 60)
    print(resume_action(action, arguments))
    print("=" * 60)
    reponse = input("Conseiller, validez-vous cette action ? [o/N] ").strip().lower()
    commentaire = "" if reponse in ("o", "oui", "y") else input("Motif du refus (facultatif) : ").strip()
    return {"ok": reponse in ("o", "oui", "y"), "commentaire": commentaire}


def avec_validation(fn, demander=None):
    """Enveloppe un outil d'écriture : rien ne s'exécute sans accord explicite.

    demander : fonction (action, arguments) -> {"ok": bool, ...}. Par défaut,
    interrupt() de LangGraph (pause du graphe jusqu'à la décision du conseiller).
    """
    demander = demander or (lambda action, arguments: interrupt({"action": action, "arguments": arguments,
                                                                 "resume": resume_action(action, arguments)}))

    @functools.wraps(fn)
    def outil(**arguments):
        decision = demander(fn.__name__, arguments)
        if not (isinstance(decision, dict) and decision.get("ok")):
            commentaire = decision.get("commentaire", "") if isinstance(decision, dict) else ""
            return {"ok": False, "erreur": "refus_conseiller",
                    "conseil": "Le conseiller a refusé cette action : ne l'exécute pas et n'insiste pas.",
                    "commentaire": commentaire}
        return fn(**arguments)

    return outil


def outils_langgraph(outils_lecture, outils_ecriture, demander=None) -> list[StructuredTool]:
    """Outils prêts pour LangGraph : lecture directe, écriture derrière la validation."""
    outils = [StructuredTool.from_function(f, parse_docstring=True) for f in outils_lecture]
    outils += [StructuredTool.from_function(avec_validation(f, demander), name=f.__name__, parse_docstring=True)
               for f in outils_ecriture]
    return outils


if __name__ == "__main__":
    print(json.dumps(valider_en_console("creer_visite", {"id_bien": "SK-001", "debut": "2026-11-19T16:30"}),
                     ensure_ascii=False))
