"""Démonstration du critère d'acceptation de l'exercice 3 (Google Calendar).

  python -m agent.demo_calendar            # le conseiller valide chaque écriture au clavier
  python -m agent.demo_calendar --auto     # validations automatiques (tests)
  python -m agent.demo_calendar --garder   # ne supprime pas les visites créées à la fin

Déroulé :
  1. « jeudi prochain en fin d'après-midi » -> trois créneaux réellement libres ;
  2. création d'une visite pour SK-001, après validation, avec l'adresse du bien ;
  3. deux demandes sur le même créneau -> aucun doublon ;
  4. refus du conseiller -> rien n'est écrit ;
  5. déplacement puis annulation, chacun après validation.
"""

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path

from agent.tools_calendar import (annuler_visite, creer_visite, deplacer_visite, lister_creneaux_libres,
                                  maintenant)
from agent.validation import avec_validation, valider_en_console

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp_server"))
from catalogue import detail_bien  # noqa: E402  (démo : l'agent, lui, passe par le serveur MCP)


def titre(texte):
    print(f"\n### {texte}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto", action="store_true", help="valider automatiquement (tests)")
    parser.add_argument("--garder", action="store_true", help="garder les visites créées")
    args = parser.parse_args()

    oui = lambda action, arguments: {"ok": True}                      # noqa: E731
    non = lambda action, arguments: {"ok": False, "commentaire": "test de refus"}  # noqa: E731
    demander = oui if args.auto else valider_en_console
    creer = avec_validation(creer_visite, demander)
    deplacer = avec_validation(deplacer_visite, demander)
    annuler = avec_validation(annuler_visite, demander)
    a_nettoyer = []

    auj = maintenant().date()
    jeudi = auj + timedelta(days=(3 - auj.weekday()) % 7 or 7)

    titre(f"1. « Jeudi prochain en fin d'après-midi » ({jeudi.isoformat()})")
    r = lister_creneaux_libres(jeudi.isoformat(), heure_min="16:00", nombre_max=3)
    for c in r["creneaux"]:
        print(f"  - {c['libelle']}  (libres : {', '.join(c['conseillers_libres'])})")
    if r["nb_creneaux"] < 1:
        sys.exit("Aucun créneau libre : libérez les agendas de test et relancez.")
    creneau = r["creneaux"][0]["debut"]

    titre("2. Création de la visite de SK-001 (validation requise)")
    bien = detail_bien("SK-001")["bien"]
    v1 = creer(debut=creneau, id_bien=bien["id"], adresse=bien["adresse"],
               client_nom="Awa Sarr", client_email="awa.sarr@example.com")
    print(f"  -> {v1.get('message') or v1}")
    if v1.get("ok"):
        a_nettoyer.append(v1["event_id"])
        print(f"     conseiller : {v1['conseiller']} | adresse : {v1['adresse']}")

    titre("3. Deux demandes sur le même créneau")
    v2 = creer(debut=creneau, id_bien="SK-003", adresse="Rue MZ-45, Mermoz",
               client_nom="Client 2", conseiller=v1.get("conseiller"))
    print(f"  Même créneau, même conseiller  -> ok={v2['ok']} : {v2.get('erreur')} - {v2.get('conseil')}")
    v3 = creer(debut=creneau, id_bien="SK-003", adresse="Rue MZ-45, Mermoz", client_nom="Client 3")
    print(f"  Même créneau, conseiller libre -> ok={v3['ok']} : {v3.get('message') or v3.get('erreur')}")
    if v3.get("ok"):
        a_nettoyer.append(v3["event_id"])
    t = datetime.fromisoformat(creneau)
    r2 = lister_creneaux_libres(jeudi.isoformat(), heure_min=f"{t:%H:%M}",
                                heure_max=f"{t + timedelta(minutes=45):%H:%M}", nombre_max=1)
    libres = r2["creneaux"][0]["conseillers_libres"] if r2["nb_creneaux"] else []
    print(f"  Conseillers encore libres à ce créneau : {', '.join(libres) or 'aucun'}")

    titre("4. Refus du conseiller")
    refus = avec_validation(annuler_visite, non)(event_id=v1.get("event_id", ""))
    print(f"  -> ok={refus['ok']} : {refus['erreur']} (la visite reste en place)")

    if v1.get("ok"):
        titre("5. Déplacement puis annulation (validation requise)")
        nouveau = r["creneaux"][-1]["debut"]
        d = deplacer(event_id=v1["event_id"], nouveau_debut=nouveau)
        print(f"  -> {d.get('message') or d}")
        if d.get("ok"):
            a_nettoyer.remove(v1["event_id"])
            a_nettoyer.append(d["nouvelle_visite"]["event_id"])

    if not args.garder and a_nettoyer:
        titre("Nettoyage : annulation des visites de démonstration")
        for event_id in a_nettoyer:
            print(f"  -> {annuler(event_id=event_id).get('message')}")


if __name__ == "__main__":
    main()
