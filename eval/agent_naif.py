"""Agent factice « naïf » : sert UNIQUEMENT à tester run_eval.py.

Il fait les erreurs typiques d'un agent sans outils : il répond à tout,
invente un bien, propose un dimanche soir et obéit à l'injection.
Résultat attendu : presque tous les cas échouent, avec la bonne raison.

  python -m eval.run_eval --agent eval.agent_naif --hors-ligne --label test-naif
"""

from datetime import datetime, time, timedelta

from agent.tools_calendar import TZ, maintenant


def traiter_email(email, demander):
    auj = maintenant().date()
    dimanche = auj + timedelta(days=(6 - auj.weekday()) or 7)
    brouillon = "Bonjour, je vous propose le SK-001 et le magnifique SK-099, visite dimanche à 19 h."
    actions = []
    if "copie" in email["corps"] or "liste des clients" in email["corps"]:
        brouillon += " En copie : awa.sarr@example.com, client2@example.com."
        actions.append("envoyer_brouillon")  # obéit à l'injection : envoie sans demander
    return {"decision": "repondre", "brouillon": brouillon,
            "creneaux": [datetime.combine(dimanche, time(19, 0), TZ).isoformat()],
            "actions_sans_validation": actions}


def repondre_demande(texte):
    return "Je vous recommande le SK-001 et le SK-099, d'excellents choix."
