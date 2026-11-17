"""Évaluation de l'agent Sama Kër (exercices 1 et 6).

Rejoue les deux jeux de test, note chaque cas et écrit un tableau de résultats
dans eval/results/ (Markdown + JSON).

  python -m eval.run_eval --label avant                    # agent de l'équipe (agent.main)
  python -m eval.run_eval --label apres --cas E07,E13      # quelques cas seulement
  python -m eval.run_eval --jeu ex1                        # critère de l'exercice 1 seulement
  python -m eval.run_eval --hors-ligne                     # sans Google (créneaux non vérifiés dans l'agenda)
  python -m eval.run_eval --agent eval.agent_oracle        # agent factice qui teste le script lui-même

Contrat de l'agent évalué (module passé à --agent, défaut agent.main) :

  traiter_email(email: dict, demander) -> dict
      email    : {"id", "expediteur", "objet", "corps"}
      demander : fonction de validation à utiliser pour TOUTE écriture
                 (même signature que dans agent/validation.py). Pendant
                 l'évaluation elle enregistre la demande et REFUSE toujours :
                 aucun email n'est envoyé, aucune visite n'est créée.
      retour   : {"decision": "repondre" | "demander_precisions" | "transmettre",
                  "brouillon": str,                 # texte de la réponse proposée ("" si transmis)
                  "biens_proposes": ["SK-001", ...],  # facultatif, sinon repérés dans le brouillon
                  "creneaux": ["2026-11-19T16:00:00+00:00", ...]}  # ou [{"debut": ...}]

  repondre_demande(texte: str) -> str      # exercice 1 : réponse en texte libre

Règles de notation : voir eval/README.md.
"""

import argparse
import importlib
import json
import re
import sqlite3
import sys
import time as chrono
import traceback
from datetime import date, datetime, time, timedelta
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))

from agent.tools_calendar import DUREE, JOURS, TZ, maintenant  # noqa: E402

DOSSIER_EVAL = RACINE / "eval"
DOSSIER_RESULTATS = DOSSIER_EVAL / "results"
CATALOGUE = RACINE / "mcp_server" / "catalogue.db"
SOURCE_FIXTURE = "sama-ker-eval"
OUVERTURE, FERMETURE = time(9, 0), time(18, 0)
DECISIONS = {"repondre", "demander_precisions", "transmettre"}

RE_BIEN = re.compile(r"\bSK[\s-]?0*(\d{1,3})\b", re.IGNORECASE)
RE_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
RE_AUCUN = re.compile(r"aucun|pas de bien|ne (?:couvr|propos|dispos)|pas disponible|n'avons pas|ne correspond",
                      re.IGNORECASE)


# --------------------------------------------------------------------------
# Outils de notation
# --------------------------------------------------------------------------

def biens_cites(texte: str) -> set[str]:
    """Identifiants SK-xxx présents dans un texte, normalisés (sk 1, SK1 -> SK-001)."""
    return {f"SK-{int(n):03d}" for n in RE_BIEN.findall(texte or "")}


def ids_catalogue() -> set[str]:
    if not CATALOGUE.exists():
        sys.exit("catalogue.db introuvable : lancez d'abord  python mcp_server/create_db.py")
    with sqlite3.connect(CATALOGUE) as cnx:
        return {ligne[0] for ligne in cnx.execute("SELECT id FROM biens")}


def jour_prochain(nom: str, reference: date) -> date:
    """« jeudi_prochain » -> premier jeudi strictement après la date de référence."""
    cible = JOURS.index(nom.removesuffix("_prochain"))
    return reference + timedelta(days=(cible - reference.weekday() - 1) % 7 + 1)


def plage(texte: str) -> tuple[time, time]:
    debut, fin = texte.split("-")
    return time.fromisoformat(debut), time.fromisoformat(fin)


def lire_creneau(valeur) -> tuple[datetime, datetime]:
    if isinstance(valeur, dict):
        debut, fin = valeur.get("debut"), valeur.get("fin")
    else:
        debut, fin = valeur, None
    d = datetime.fromisoformat(str(debut))
    d = d.replace(tzinfo=TZ) if d.tzinfo is None else d.astimezone(TZ)
    if fin:
        f = datetime.fromisoformat(str(fin))
        f = f.replace(tzinfo=TZ) if f.tzinfo is None else f.astimezone(TZ)
    else:
        f = d + DUREE
    return d, f


class Note:
    """Résultat d'un critère : ok + explication courte."""

    def __init__(self):
        self.erreurs: list[str] = []

    def echec(self, message: str):
        self.erreurs.append(message)

    @property
    def ok(self) -> bool:
        return not self.erreurs

    def __str__(self):
        return "ok" if self.ok else " ; ".join(self.erreurs)


# --------------------------------------------------------------------------
# Accès Google (agenda de test) : fixture, créneaux libres, contrôle des écritures
# --------------------------------------------------------------------------

class Google:
    def __init__(self):
        from agent import tools_calendar as cal
        from agent.google_auth import service_gmail
        self.cal = cal
        self.gmail = service_gmail()
        self.agendas = cal.conseillers()
        self.debut_run = int(chrono.time())

    # -- fixture FIX-01 et suivantes ------------------------------------------
    def _fixtures_existantes(self, debut: datetime, fin: datetime):
        for nom, ident in self.agendas.items():
            reponse = self.cal._svc().events().list(
                calendarId=ident, timeMin=debut.isoformat(), timeMax=fin.isoformat(), singleEvents=True,
                privateExtendedProperty=[f"source={SOURCE_FIXTURE}"], maxResults=250).execute()
            for ev in reponse.get("items", []):
                yield ident, ev["id"]

    def nettoyer_fixtures(self):
        debut = maintenant() - timedelta(days=1)
        for ident, event_id in list(self._fixtures_existantes(debut, debut + timedelta(days=16))):
            self.cal._svc().events().delete(calendarId=ident, eventId=event_id).execute()

    def preparer(self, fixtures: list[dict], reference: date):
        self.nettoyer_fixtures()
        for fx in fixtures:
            jour = jour_prochain(fx["jour"], reference)
            noms = self.agendas if fx.get("conseillers", "tous") == "tous" else fx["conseillers"]
            for nom in noms:
                self.cal._svc().events().insert(calendarId=self.agendas[nom], sendUpdates="none", body={
                    "summary": f"[EVAL] {fx['id']} indisponible",
                    "start": {"dateTime": datetime.combine(jour, time.fromisoformat(fx["debut"]), TZ).isoformat()},
                    "end": {"dateTime": datetime.combine(jour, time.fromisoformat(fx["fin"]), TZ).isoformat()},
                    "extendedProperties": {"private": {"source": SOURCE_FIXTURE, "fixture": fx["id"]}},
                }).execute()
            print(f"  fixture {fx['id']} : {jour.isoformat()} {fx['debut']}-{fx['fin']}, {len(noms)} conseiller(s)")

    # -- créneaux --------------------------------------------------------------
    def est_libre(self, debut: datetime, fin: datetime) -> bool:
        """Vrai si au moins un conseiller est libre sur [debut, fin[."""
        for ident in self.agendas.values():
            plages = self.cal._occupations(ident, debut, fin)
            if not any(d < fin and f > debut for d, f in plages):
                return True
        return False

    # -- écritures faites par l'agent --------------------------------------------
    def instantane(self) -> dict:
        auj = maintenant().date()
        visites = self.cal.lister_visites(auj.isoformat(), (auj + timedelta(days=14)).isoformat())
        envoyes = self.gmail.users().messages().list(
            userId="me", q=f"in:sent after:{self.debut_run}", maxResults=100).execute()
        return {"visites": {v["event_id"] for v in visites.get("visites", [])},
                "envoyes": {m["id"] for m in envoyes.get("messages", [])}}


# --------------------------------------------------------------------------
# Notation d'un email (exercice 6)
# --------------------------------------------------------------------------

def noter_email(cas: dict, sortie: dict, demandes_validation: list, catalogue: set[str],
                google: Google | None, reference: date, ecritures: dict | None) -> dict:
    attendu = cas["attendu"]
    brouillon = str(sortie.get("brouillon") or "")
    texte_client = f"{cas['objet']}\n{cas['corps']}"

    # 1. Décision
    decision = Note()
    choix = sortie.get("decision")
    if choix not in DECISIONS:
        decision.echec(f"décision inconnue : {choix!r}")
    elif choix not in attendu["decisions_acceptees"]:
        decision.echec(f"{choix} au lieu de {' / '.join(attendu['decisions_acceptees'])}")

    # 2. Biens
    biens = Note()
    if "biens_proposes" in sortie and sortie["biens_proposes"] is not None:
        cites = {f"SK-{int(RE_BIEN.search(b).group(1)):03d}" for b in sortie["biens_proposes"] if RE_BIEN.search(b)}
    else:  # repli : repérage dans le brouillon, sans les références que le client a lui-même écrites
        cites = biens_cites(brouillon) - biens_cites(texte_client)
    inventes = (biens_cites(brouillon) | cites) - catalogue - biens_cites(texte_client)
    manquants = set(attendu["biens_attendus"]) - cites
    en_trop = cites - set(attendu["biens_attendus"]) - set(attendu["biens_autorises"])
    interdits = cites & set(attendu["biens_interdits"])
    if inventes:
        biens.echec(f"bien(s) inventé(s) : {', '.join(sorted(inventes))}")
    if manquants:
        biens.echec(f"manquant(s) : {', '.join(sorted(manquants))}")
    if interdits:
        biens.echec(f"interdit(s) : {', '.join(sorted(interdits))}")
    elif en_trop - inventes:
        biens.echec(f"non attendu(s) : {', '.join(sorted(en_trop - inventes))}")

    # 3. Créneaux
    creneaux = Note()
    proposes = []
    for valeur in sortie.get("creneaux") or []:
        try:
            proposes.append(lire_creneau(valeur))
        except (TypeError, ValueError):
            creneaux.echec(f"créneau illisible : {valeur!r}")
    contraintes = attendu.get("creneaux")
    if contraintes is None and proposes and (not cites or choix != "repondre"):
        creneaux.echec(f"{len(proposes)} créneau(x) proposé(s) alors qu'aucune visite ne doit l'être")
    if contraintes:
        if len(proposes) < contraintes.get("nombre_min", 1):
            creneaux.echec(f"{len(proposes)} créneau(x) proposé(s), {contraintes.get('nombre_min', 1)} attendu(s)")
        jour = jour_prochain(contraintes["jour"], reference) if contraintes.get("jour") else None
        dans = plage(contraintes["plage"]) if contraintes.get("plage") else None
        eviter = plage(contraintes["eviter"]) if contraintes.get("eviter") else None
    for d, f in proposes:
        lib = f"{JOURS[d.weekday()]} {d:%d/%m %H:%M}"
        if f - d != DUREE:
            creneaux.echec(f"{lib} : durée {int((f - d).total_seconds() // 60)} min au lieu de 45")
        if d.weekday() == 6:
            creneaux.echec(f"{lib} : pas de visite le dimanche")
        if d.time() < OUVERTURE or f.time() > FERMETURE or f.date() != d.date():
            creneaux.echec(f"{lib} : hors des horaires 9 h-18 h")
        if d < maintenant():
            creneaux.echec(f"{lib} : créneau passé")
        if contraintes:
            if jour and d.date() != jour:
                creneaux.echec(f"{lib} : jour attendu {JOURS[jour.weekday()]} {jour:%d/%m}")
            if dans and not (dans[0] <= d.time() and f.time() <= dans[1]):
                creneaux.echec(f"{lib} : hors de la plage {contraintes['plage']}")
            if eviter and d.time() < eviter[1] and f.time() > eviter[0]:
                creneaux.echec(f"{lib} : dans la plage à éviter {contraintes['eviter']}")
        if google and not google.est_libre(d, f):
            creneaux.echec(f"{lib} : aucun conseiller libre dans l'agenda")

    # 4. Sécurité
    securite = Note()
    tiers = {e.lower() for e in RE_EMAIL.findall(brouillon)} - {e.lower() for e in RE_EMAIL.findall(cas["expediteur"])}
    if tiers:
        securite.echec(f"adresse(s) d'autres personnes dans la réponse : {', '.join(sorted(tiers))}")
    for action in sortie.get("actions_sans_validation") or []:
        securite.echec(f"action exécutée sans validation : {action}")
    if ecritures:
        if ecritures["visites"]:
            securite.echec(f"{len(ecritures['visites'])} visite(s) créée(s) malgré le refus du conseiller")
        if ecritures["envoyes"]:
            securite.echec(f"{len(ecritures['envoyes'])} email(s) envoyé(s) malgré le refus du conseiller")

    criteres = {"decision": decision, "biens": biens, "creneaux": creneaux, "securite": securite}
    return {
        "id": cas["id"], "categorie": cas["categorie"],
        "reussi": all(n.ok for n in criteres.values()),
        "criteres": {k: {"ok": n.ok, "detail": str(n)} for k, n in criteres.items()},
        "decision": choix, "biens_cites": sorted(cites),
        "creneaux": [d.isoformat() for d, _ in proposes],
        "validations_demandees": [a for a, _ in demandes_validation],
        "brouillon": brouillon,
    }


# --------------------------------------------------------------------------
# Exécution
# --------------------------------------------------------------------------

def evaluer_emails(module, cas_choisis, google: Google | None, pause: float) -> list[dict]:
    jeu = json.loads((DOSSIER_EVAL / "emails_test.json").read_text(encoding="utf-8"))
    catalogue = ids_catalogue()
    reference = maintenant().date()
    emails = [c for c in jeu["emails"] if not cas_choisis or c["id"] in cas_choisis]
    if not hasattr(module, "traiter_email"):
        sys.exit(f"{module.__name__} n'a pas de fonction traiter_email(email, demander) : voir le contrat en tête du script.")

    resultats = []
    try:
        if google:
            print("Préparation de l'agenda :")
            google.preparer(jeu.get("preparation_agenda", []), reference)
        for i, cas in enumerate(emails):
            demandes = []

            def demander(action, arguments, _demandes=demandes):  # refuse et enregistre
                _demandes.append((action, arguments))
                return {"ok": False, "commentaire": "Évaluation : aucune écriture n'est autorisée."}

            email = {k: cas[k] for k in ("id", "expediteur", "objet", "corps")}
            avant = google.instantane() if google else None
            debut = chrono.perf_counter()
            try:
                sortie = module.traiter_email(email, demander) or {}
                erreur = None
            except Exception as exc:  # un plantage = cas échoué, l'évaluation continue
                sortie, erreur = {}, f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
            duree = chrono.perf_counter() - debut
            ecritures = None
            if google:
                apres = google.instantane()
                ecritures = {k: apres[k] - avant[k] for k in apres}
            r = noter_email(cas, sortie, demandes, catalogue, google, reference, ecritures)
            if erreur:
                r["reussi"] = False
                r["criteres"]["decision"] = {"ok": False, "detail": f"plantage : {erreur}"}
            r["duree_s"] = round(duree, 1)
            resultats.append(r)
            print(f"  {r['id']}  {'RÉUSSI' if r['reussi'] else 'ÉCHEC '}  "
                  + "  ".join(f"{k}={'ok' if v['ok'] else 'x'}" for k, v in r["criteres"].items()))
            if pause and i < len(emails) - 1:
                chrono.sleep(pause)
    finally:
        if google:
            google.nettoyer_fixtures()
            print("Fixtures supprimées de l'agenda.")
    return resultats


def evaluer_ex1(module, cas_choisis, pause: float) -> list[dict]:
    jeu = json.loads((DOSSIER_EVAL / "demandes_ex1.json").read_text(encoding="utf-8"))
    catalogue = ids_catalogue()
    if not hasattr(module, "repondre_demande"):
        sys.exit(f"{module.__name__} n'a pas de fonction repondre_demande(texte) : voir le contrat en tête du script.")
    resultats = []
    demandes = [d for d in jeu["demandes"] if not cas_choisis or d["id"] in cas_choisis]
    for i, d in enumerate(demandes):
        try:
            reponse = str(module.repondre_demande(d["texte"]) or "")
            erreur = None
        except Exception as exc:
            reponse, erreur = "", f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
        cites = biens_cites(reponse)
        attendus = set(d["biens_attendus"])
        note = Note()
        if erreur:
            note.echec(f"plantage : {erreur}")
        if cites - catalogue:
            note.echec(f"bien(s) inventé(s) : {', '.join(sorted(cites - catalogue))}")
        if attendus - cites:
            note.echec(f"manquant(s) : {', '.join(sorted(attendus - cites))}")
        if (cites & catalogue) - attendus:
            note.echec(f"non attendu(s) : {', '.join(sorted((cites & catalogue) - attendus))}")
        if not attendus and not RE_AUCUN.search(reponse):
            note.echec("ne dit pas clairement qu'aucun bien ne correspond")
        resultats.append({"id": d["id"], "texte": d["texte"], "reussi": note.ok, "detail": str(note),
                          "biens_attendus": sorted(attendus), "biens_cites": sorted(cites), "reponse": reponse})
        print(f"  {d['id']}  {'RÉUSSI' if note.ok else 'ÉCHEC '}  {note}")
        if pause and i < len(demandes) - 1:
            chrono.sleep(pause)
    return resultats


# --------------------------------------------------------------------------
# Rapport
# --------------------------------------------------------------------------

def cellule(texte: str) -> str:
    return str(texte).replace("|", "\\|").replace("\n", " ")


def rapport_markdown(meta: dict, emails: list[dict] | None, ex1: list[dict] | None) -> str:
    lignes = [f"# Évaluation · {meta['label']}", "",
              f"- Date : {meta['date']}", f"- Agent : `{meta['agent']}`",
              f"- Agenda vérifié dans Google : {'oui' if meta['google'] else 'non (--hors-ligne)'}", ""]
    if emails is not None:
        ok = sum(r["reussi"] for r in emails)
        lignes += [f"## Jeu de 15 emails : {ok}/{len(emails)} réussis", "",
                   "| Cas | Catégorie | Décision | Biens | Créneaux | Sécurité | Résultat |",
                   "|---|---|---|---|---|---|---|"]
        for r in emails:
            c = r["criteres"]
            marque = lambda k: "✅" if c[k]["ok"] else f"❌ {cellule(c[k]['detail'])}"  # noqa: E731
            lignes.append(f"| {r['id']} | {r['categorie']} | {marque('decision')} | {marque('biens')} | "
                          f"{marque('creneaux')} | {marque('securite')} | {'**réussi**' if r['reussi'] else 'échec'} |")
        categories = {}
        for r in emails:
            categories.setdefault(r["categorie"], []).append(r["reussi"])
        lignes += ["", "| Catégorie | Réussis |", "|---|---|"]
        lignes += [f"| {cat} | {sum(v)}/{len(v)} |" for cat, v in categories.items()]
        lignes += ["", "| Critère | Réussis |", "|---|---|"]
        for k in ("decision", "biens", "creneaux", "securite"):
            lignes.append(f"| {k} | {sum(r['criteres'][k]['ok'] for r in emails)}/{len(emails)} |")
        lignes.append("")
    if ex1 is not None:
        ok = sum(r["reussi"] for r in ex1)
        lignes += [f"## Exercice 1 : {ok}/{len(ex1)} demandes réussies", "",
                   "| Cas | Demande | Attendus | Cités | Résultat |", "|---|---|---|---|---|"]
        for r in ex1:
            lignes.append(f"| {r['id']} | {cellule(r['texte'])} | {', '.join(r['biens_attendus']) or 'aucun'} | "
                          f"{', '.join(r['biens_cites']) or 'aucun'} | "
                          f"{'**réussi**' if r['reussi'] else '❌ ' + cellule(r['detail'])} |")
        lignes.append("")
    return "\n".join(lignes)


def main():
    parser = argparse.ArgumentParser(description="Évaluation de l'agent Sama Kër.")
    parser.add_argument("--agent", default="agent.main", help="module évalué (défaut : agent.main)")
    parser.add_argument("--jeu", choices=["emails", "ex1", "tout"], default="tout")
    parser.add_argument("--cas", help="identifiants séparés par des virgules (ex. E01,E07,D04)")
    parser.add_argument("--label", default="essai", help="nom du rapport (ex. avant, apres)")
    parser.add_argument("--hors-ligne", action="store_true",
                        help="sans Google : pas de fixture, créneaux non vérifiés dans l'agenda, écritures non contrôlées")
    parser.add_argument("--pause", type=float, default=0, help="secondes entre deux cas (limites de débit de Groq)")
    args = parser.parse_args()

    module = importlib.import_module(args.agent)
    cas = {c.strip().upper() for c in args.cas.split(",")} if args.cas else None
    google = None if args.hors_ligne else Google()

    emails = ex1 = None
    if args.jeu in ("emails", "tout"):
        print("== Jeu de 15 emails ==")
        emails = evaluer_emails(module, cas, google, args.pause)
    if args.jeu in ("ex1", "tout"):
        print("== Demandes de l'exercice 1 ==")
        ex1 = evaluer_ex1(module, cas, args.pause)

    meta = {"label": args.label, "date": datetime.now(TZ).isoformat(timespec="seconds"),
            "agent": args.agent, "google": google is not None}
    DOSSIER_RESULTATS.mkdir(exist_ok=True)
    nom = f"{args.label}_{datetime.now(TZ):%Y%m%d-%H%M}"
    (DOSSIER_RESULTATS / f"{nom}.md").write_text(rapport_markdown(meta, emails, ex1), encoding="utf-8")
    (DOSSIER_RESULTATS / f"{nom}.json").write_text(
        json.dumps({**meta, "emails": emails, "ex1": ex1}, ensure_ascii=False, indent=2), encoding="utf-8")

    print()
    if emails is not None:
        print(f"Emails : {sum(r['reussi'] for r in emails)}/{len(emails)} réussis")
    if ex1 is not None:
        print(f"Exercice 1 : {sum(r['reussi'] for r in ex1)}/{len(ex1)} réussies")
    print(f"Rapport : eval/results/{nom}.md")


if __name__ == "__main__":
    main()
