"""Agent factice « parfait » : sert UNIQUEMENT à tester run_eval.py.

Il lit les réponses attendues dans les jeux de test (il triche) et prend de
vrais créneaux libres dans l'agenda. Résultat attendu : tous les cas réussis.

  python -m eval.run_eval --agent eval.agent_oracle --label test-oracle
"""

import json
from datetime import datetime, time, timedelta
from pathlib import Path

from agent.tools_calendar import DUREE, TZ, lister_creneaux_libres, maintenant

DOSSIER = Path(__file__).resolve().parent
EMAILS = {c["id"]: c for c in json.loads((DOSSIER / "emails_test.json").read_text(encoding="utf-8"))["emails"]}
DEMANDES = {d["texte"]: d for d in json.loads((DOSSIER / "demandes_ex1.json").read_text(encoding="utf-8"))["demandes"]}
JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _jour(nom, ref):
    cible = JOURS.index(nom.removesuffix("_prochain"))
    return ref + timedelta(days=(cible - ref.weekday() - 1) % 7 + 1)


def _creneaux(contraintes):
    ref = maintenant().date()
    jour = _jour(contraintes["jour"], ref) if contraintes.get("jour") else ref + timedelta(days=1)
    h_min, h_max = (contraintes.get("plage") or "09:00-18:00").split("-")
    try:  # vrais créneaux libres (le samedi de E07 : 10 h-12 h est exclu par la fixture)
        r = lister_creneaux_libres(jour.isoformat(), (jour + timedelta(days=6)).isoformat(),
                                   heure_min=h_min, heure_max=h_max, nombre_max=10)
        debuts = [c["debut"] for c in r.get("creneaux", [])]
    except Exception:
        debuts = []
    if not debuts:  # hors ligne : créneaux réguliers qui respectent les règles
        while jour.weekday() == 6:
            jour += timedelta(days=1)
        t = datetime.combine(jour, time.fromisoformat(h_min), TZ)
        debuts = [(t + k * timedelta(minutes=30)).isoformat() for k in range(3)
                  if (t + k * timedelta(minutes=30) + DUREE).time() <= time.fromisoformat(h_max)]
    eviter = contraintes.get("eviter")
    if eviter:
        a, b = (time.fromisoformat(x) for x in eviter.split("-"))
        debuts = [d for d in debuts if not (datetime.fromisoformat(d).time() < b
                                             and (datetime.fromisoformat(d) + DUREE).time() > a)]
    return debuts[: max(1, contraintes.get("nombre_min", 1))]


def traiter_email(email, demander):
    attendu = EMAILS[email["id"]]["attendu"]
    biens = attendu["biens_attendus"]
    creneaux = _creneaux(attendu["creneaux"]) if attendu["creneaux"] else []
    decision = attendu["decisions_acceptees"][0]
    demander("envoyer_brouillon", {"message_id": email["id"]})  # passe par la validation, comme le vrai agent
    brouillon = "" if decision == "transmettre" else (
        "Bonjour,\n\nVoici les biens correspondant à votre demande : " + (", ".join(biens) or "aucun") + ".")
    return {"decision": decision, "brouillon": brouillon, "biens_proposes": biens, "creneaux": creneaux}


def repondre_demande(texte):
    attendus = DEMANDES[texte]["biens_attendus"]
    return ("Biens disponibles : " + ", ".join(attendus)) if attendus else "Aucun bien ne correspond à votre demande."
