"""Démonstration Gmail (exercice 4), à lancer avec : python -m agent.demo_gmail

Avant : envoie un ou plusieurs emails de test à l'adresse du compte de test
(variable AGENCY_EMAIL dans .env) depuis une AUTRE adresse (copie l'objet et le corps de eval/emails_test.json).

Options :
  --envoyer     propose d'envoyer le brouillon (validation au clavier o/N)
  --transmettre transmet le premier message à un conseiller au lieu de répondre
"""
import sys

from agent import tools_gmail as g


def main():
    envoyer = "--envoyer" in sys.argv
    transmettre = "--transmettre" in sys.argv

    print("\n### 1. Lecture des demandes non traitées")
    r = g.lire_demandes_non_traitees(5)
    if not r["ok"]:
        print("Erreur :", r)
        return
    print(f"  {r['nb_demandes']} demande(s) (libellé « À traiter » posé)")
    for d in r["demandes"]:
        print(f"  - {d['message_id']} | {d['expediteur']} | {d['objet']}")
        print(f"    corps : {d['corps'][:80]!r}")
    if not r["demandes"]:
        print("  Rien à traiter : envoie d'abord un email de test.")
        return

    d = r["demandes"][0]

    if transmettre:
        print("\n### 2. Transmission à un conseiller (hors périmètre / injection)")
        print(" ", g.transmettre_conseiller(d["message_id"], "test hors périmètre"))
        print("  Vérifie dans Gmail : libellé « Transmis à un conseiller », aucun brouillon.")
        return

    print("\n### 2. Brouillon dans le fil")
    b = g.creer_brouillon(
        d["message_id"], d["thread_id"], d["expediteur"], d["objet"],
        d["rfc_message_id"],
        "Bonjour,\n\nMerci pour votre message. Ceci est un brouillon de test.\n\n"
        "Cordialement,\nL'équipe Sama Kër Immobilier")
    print(" ", b)
    if not b["ok"]:
        return
    a = g.apercu_brouillon(b["draft_id"])
    print(f"  Aperçu -> à : {a.get('a')} | objet : {a.get('objet')}")
    print(f"  Même fil que le message d'origine : {a.get('thread_id') == d['thread_id']}")
    print("  Vérifie dans Gmail : brouillon dans le fil, libellé « En attente de validation ».")

    if envoyer:
        print("\n### 3. Envoi après validation")
        print(a.get("corps"))
        rep = input("Envoyer ce brouillon ? (o/N) ").strip().lower()
        if rep == "o":
            print(" ", g.envoyer_brouillon(b["draft_id"], d["message_id"]))
        else:
            print("  Refus : rien n'est envoyé, le brouillon reste en attente.")


if __name__ == "__main__":
    main()
