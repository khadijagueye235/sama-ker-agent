"""Outils Google Calendar de l'agent (exercice 3).

Quatre actions métier + une aide :
  - lister_creneaux_libres : créneaux de visite réellement libres sur une période ;
  - creer_visite           : inscrit une visite (ÉCRITURE, validation humaine requise) ;
  - deplacer_visite        : déplace une visite (ÉCRITURE, validation humaine requise) ;
  - annuler_visite         : annule une visite  (ÉCRITURE, validation humaine requise) ;
  - lister_visites         : retrouve les visites existantes (pour déplacer/annuler).

Règles de l'agence appliquées ici, dans le code (pas seulement dans le prompt) :
  - visites de 45 minutes, du lundi au samedi, entre 9 h et 18 h ;
  - fuseau Africa/Dakar ; adresse du bien dans l'événement ;
  - un créneau n'est proposé que s'il est libre chez au moins un conseiller ;
  - aucun doublon : le créneau est revérifié juste avant l'écriture, et chaque
    visite reçoit un identifiant calculé (agenda + heure) que Google refuse de
    créer deux fois.

Comme les outils du catalogue, ces fonctions ne lèvent jamais d'exception :
elles renvoient {"ok": False, "erreur": ..., "conseil": ...} en cas de problème.
La validation humaine est ajoutée au-dessus, dans agent/validation.py.
"""

import functools
import hashlib
import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from googleapiclient.errors import HttpError

from agent.google_auth import service_calendar

TZ = ZoneInfo(os.getenv("AGENCY_TIMEZONE", "Africa/Dakar"))
DUREE = timedelta(minutes=45)
OUVERTURE, FERMETURE = time(9, 0), time(18, 0)
PAS = timedelta(minutes=30)            # créneaux proposés toutes les 30 min : 9 h, 9 h 30… 17 h
JOURS_OUVRES = range(0, 6)             # lundi (0) à samedi (5)
DELAI_MIN = timedelta(hours=2)         # pas de visite dans les 2 prochaines heures
PERIODE_MAX = timedelta(days=14)
SOURCE = "sama-ker-agent"

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
        "septembre", "octobre", "novembre", "décembre"]
REGLES = "Visites de 45 min, du lundi au samedi, de 9 h à 18 h (heure de Dakar)."


# --------------------------------------------------------------------------
# Utilitaires
# --------------------------------------------------------------------------

class ErreurMetier(Exception):
    """Erreur prévue, renvoyée telle quelle au modèle."""

    def __init__(self, erreur: str, conseil: str, **details):
        super().__init__(erreur)
        self.reponse = {"ok": False, "erreur": erreur, "conseil": conseil, **details}


def _sans_exception(fn):
    """Transforme toute exception en réponse exploitable par le modèle."""
    @functools.wraps(fn)
    def enveloppe(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ErreurMetier as e:
            return e.reponse
        except HttpError as e:
            return {"ok": False, "erreur": f"Google Calendar a répondu {e.resp.status}.",
                    "conseil": "Réessaie une fois ; si l'erreur persiste, transmets à un conseiller."}
        except Exception as e:  # noqa: BLE001 - l'agent ne doit jamais planter
            return {"ok": False, "erreur": f"{type(e).__name__}: {e}",
                    "conseil": "Transmets la demande à un conseiller."}
    return enveloppe


def conseillers() -> dict[str, str]:
    """{nom: id_agenda} lus dans la variable CONSEILLERS du fichier .env."""
    brut = os.getenv("CONSEILLERS", "")
    resultat = {}
    for morceau in filter(None, (m.strip() for m in brut.split(","))):
        nom, _, ident = morceau.partition(":")
        if nom and ident:
            resultat[nom.strip()] = ident.strip()
    if not resultat:
        raise ErreurMetier("Aucun agenda de conseiller configuré (CONSEILLERS dans .env).",
                           "Préviens l'équipe technique.")
    return resultat


_service = None


def _svc():
    global _service
    if _service is None:
        _service = service_calendar()
    return _service


def maintenant() -> datetime:
    return datetime.now(TZ)


def _lire_date(texte: str, nom: str) -> date:
    try:
        return date.fromisoformat(texte.strip()[:10])
    except (ValueError, AttributeError):
        raise ErreurMetier(f"{nom} '{texte}' invalide.", "Utilise le format AAAA-MM-JJ, ex. 2026-11-19.")


def _lire_heure(texte: str, nom: str) -> time:
    try:
        return time.fromisoformat(texte.strip().replace("h", ":").rstrip(":").zfill(5))
    except (ValueError, AttributeError):
        raise ErreurMetier(f"{nom} '{texte}' invalide.", "Utilise le format HH:MM, ex. 16:00.")


def _lire_datetime(texte: str) -> datetime:
    try:
        dt = datetime.fromisoformat(texte.strip())
    except (ValueError, AttributeError):
        raise ErreurMetier(f"Date/heure '{texte}' invalide.",
                           "Utilise le format AAAA-MM-JJTHH:MM, ex. 2026-11-19T16:30.")
    return dt.replace(tzinfo=TZ) if dt.tzinfo is None else dt.astimezone(TZ)


def libelle(dt: datetime) -> str:
    """'jeudi 19 novembre 2026 à 16 h 30'."""
    minutes = f" {dt.minute:02d}" if dt.minute else ""
    return f"{JOURS[dt.weekday()]} {dt.day} {MOIS[dt.month - 1]} {dt.year} à {dt.hour} h{minutes}"


def raison_hors_regles(debut: datetime) -> str | None:
    """Pourquoi ce début de visite enfreint les règles de l'agence (ou None)."""
    fin = debut + DUREE
    if debut.weekday() not in JOURS_OUVRES:
        return "L'agence ne fait pas de visite le dimanche."
    if debut.time() < OUVERTURE or fin.time() > FERMETURE or fin.date() != debut.date():
        return "Les visites ont lieu entre 9 h et 18 h (dernière visite à 17 h 15)."
    if debut < maintenant() + DELAI_MIN:
        return "Ce créneau est passé ou trop proche (au moins 2 h à l'avance)."
    return None


def _occupations(id_agenda: str, debut: datetime, fin: datetime) -> list[tuple[datetime, datetime]]:
    """Plages occupées d'un agenda entre debut et fin (événements annulés ou « disponible » exclus)."""
    plages, page = [], None
    while True:
        reponse = _svc().events().list(
            calendarId=id_agenda, timeMin=debut.isoformat(), timeMax=fin.isoformat(),
            singleEvents=True, orderBy="startTime", maxResults=250, pageToken=page,
        ).execute()
        for ev in reponse.get("items", []):
            if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
                continue
            if "dateTime" in ev["start"]:
                d = datetime.fromisoformat(ev["start"]["dateTime"]).astimezone(TZ)
                f = datetime.fromisoformat(ev["end"]["dateTime"]).astimezone(TZ)
            else:  # événement « toute la journée »
                d = datetime.combine(date.fromisoformat(ev["start"]["date"]), time(0), TZ)
                f = datetime.combine(date.fromisoformat(ev["end"]["date"]), time(0), TZ)
            plages.append((d, f))
        page = reponse.get("nextPageToken")
        if not page:
            return plages


def _libre(debut: datetime, plages) -> bool:
    fin = debut + DUREE
    return not any(d < fin and f > debut for d, f in plages)


def _id_evenement(id_agenda: str, debut: datetime) -> str:
    """Identifiant déterministe (base32hex) : même agenda + même heure = même id."""
    cle = f"{id_agenda}|{debut.astimezone(ZoneInfo('UTC')).isoformat()}"
    return "sk" + hashlib.sha1(cle.encode()).hexdigest()[:30]


def _trouver_evenement(event_id: str) -> tuple[str, str, dict]:
    """(nom_conseiller, id_agenda, événement) ou ErreurMetier si introuvable/annulé."""
    for nom, id_agenda in conseillers().items():
        try:
            ev = _svc().events().get(calendarId=id_agenda, eventId=event_id).execute()
        except HttpError as e:
            if e.resp.status in (404, 410):
                continue
            raise
        if ev.get("status") != "cancelled":
            return nom, id_agenda, ev
    raise ErreurMetier(f"Aucune visite active avec l'identifiant '{event_id}'.",
                       "Utilise lister_visites pour retrouver l'identifiant de la visite.")


def _resume_evenement(nom: str, ev: dict) -> dict:
    debut = datetime.fromisoformat(ev["start"]["dateTime"]).astimezone(TZ)
    prive = ev.get("extendedProperties", {}).get("private", {})
    return {
        "event_id": ev["id"], "conseiller": nom, "debut": debut.isoformat(),
        "fin": (debut + DUREE).isoformat(), "libelle": libelle(debut),
        "titre": ev.get("summary"), "adresse": ev.get("location"),
        "id_bien": prive.get("id_bien"), "client_nom": prive.get("client_nom"),
        "client_email": prive.get("client_email"),
    }


# --------------------------------------------------------------------------
# Outils exposés au modèle
# --------------------------------------------------------------------------

@_sans_exception
def lister_creneaux_libres(
    date_debut: str,
    date_fin: str | None = None,
    heure_min: str | None = None,
    heure_max: str | None = None,
    conseiller: str | None = None,
    nombre_max: int = 5,
) -> dict:
    """Cherche des créneaux de visite RÉELLEMENT libres dans les agendas des conseillers.

    Utilise cet outil avant de proposer une visite : ne propose jamais un
    créneau qui n'a pas été renvoyé par cet outil. Les règles de l'agence sont
    appliquées automatiquement : visites de 45 min, du lundi au samedi, de 9 h
    à 18 h, heure de Dakar, au moins 2 h à l'avance.

    Repères : « matin » -> heure_min="09:00", heure_max="12:00" ;
    « après-midi » -> heure_min="14:00" ; « fin d'après-midi » -> heure_min="16:00".

    Args:
        date_debut: Premier jour de recherche, format AAAA-MM-JJ (ex. "2026-11-19").
        date_fin: Dernier jour inclus, format AAAA-MM-JJ. Par défaut, date_debut. 14 jours au plus.
        heure_min: Heure de début la plus tôt, format HH:MM (défaut "09:00").
        heure_max: Heure de FIN la plus tardive, format HH:MM (défaut "18:00").
        conseiller: Nom d'un conseiller précis ; par défaut, tous les conseillers.
        nombre_max: Nombre maximal de créneaux renvoyés (1 à 20, défaut 5).

    Returns:
        {"ok": True, "nb_creneaux": int, "creneaux": [{"debut", "fin", "libelle",
        "conseillers_libres"}], "message": str}. Propose les créneaux avec leur
        libellé ; le conseiller sera choisi à la création. En cas de paramètre
        invalide : {"ok": False, "erreur": str, "conseil": str}.
    """
    j1 = _lire_date(date_debut, "date_debut")
    j2 = _lire_date(date_fin, "date_fin") if date_fin else j1
    if j2 < j1:
        raise ErreurMetier("date_fin est avant date_debut.", "Inverse les deux dates.")
    if j2 - j1 > PERIODE_MAX:
        raise ErreurMetier("Période trop longue (14 jours au plus).", "Réduis la période de recherche.")
    h_min = max(_lire_heure(heure_min, "heure_min") if heure_min else OUVERTURE, OUVERTURE)
    h_max = min(_lire_heure(heure_max, "heure_max") if heure_max else FERMETURE, FERMETURE)
    nombre_max = max(1, min(int(nombre_max), 20))

    agendas = conseillers()
    if conseiller:
        if conseiller not in agendas:
            raise ErreurMetier(f"Conseiller '{conseiller}' inconnu.", "Laisse ce champ vide ou choisis un nom de la liste.",
                               conseillers=list(agendas))
        agendas = {conseiller: agendas[conseiller]}

    debut_periode = datetime.combine(j1, time(0), TZ)
    fin_periode = datetime.combine(j2 + timedelta(days=1), time(0), TZ)
    occupations = {nom: _occupations(ident, debut_periode, fin_periode) for nom, ident in agendas.items()}

    creneaux, remarques = [], []
    jour = j1
    while jour <= j2 and len(creneaux) < nombre_max:
        if jour.weekday() not in JOURS_OUVRES:
            remarques.append(f"{JOURS[jour.weekday()]} {jour.day} {MOIS[jour.month - 1]} : pas de visite le dimanche.")
        else:
            t = datetime.combine(jour, OUVERTURE, TZ)
            while t + DUREE <= datetime.combine(jour, FERMETURE, TZ) and len(creneaux) < nombre_max:
                if t.time() >= h_min and (t + DUREE).time() <= h_max and raison_hors_regles(t) is None:
                    libres = [nom for nom, plages in occupations.items() if _libre(t, plages)]
                    if libres:
                        creneaux.append({"debut": t.isoformat(), "fin": (t + DUREE).isoformat(),
                                         "libelle": libelle(t), "conseillers_libres": libres})
                t += PAS
        jour += timedelta(days=1)

    message = (f"{len(creneaux)} créneau(x) libre(s) trouvé(s). {REGLES}" if creneaux else
               "Aucun créneau libre sur cette période et cette plage horaire. "
               "Propose au client un autre jour ou une autre plage horaire.")
    reponse = {"ok": True, "nb_creneaux": len(creneaux), "creneaux": creneaux, "message": message}
    if remarques:
        reponse["remarques"] = remarques
    return reponse


@_sans_exception
def creer_visite(
    debut: str,
    id_bien: str,
    adresse: str,
    client_nom: str,
    client_email: str | None = None,
    conseiller: str | None = None,
) -> dict:
    """Inscrit une visite de 45 minutes dans l'agenda d'un conseiller.

    Action d'ÉCRITURE : elle n'est exécutée qu'après l'accord d'un conseiller.
    À utiliser uniquement quand le client a CONFIRMÉ un créneau proposé.
    Le créneau est revérifié juste avant l'écriture : s'il a été pris entre-temps,
    l'outil renvoie l'erreur « creneau_pris » et il faut proposer d'autres créneaux.

    Args:
        debut: Début de la visite, format AAAA-MM-JJTHH:MM (heure de Dakar), ex. "2026-11-19T16:30".
        id_bien: Référence du bien visité, ex. "SK-001".
        adresse: Adresse exacte du bien, telle que renvoyée par detail_bien.
        client_nom: Nom du client.
        client_email: Adresse email du client (facultatif).
        conseiller: Nom du conseiller ; par défaut, le premier conseiller libre.

    Returns:
        {"ok": True, "event_id": str, "conseiller": str, "libelle": str, ...} ou
        {"ok": False, "erreur": "creneau_pris" | "doublon" | ..., "conseil": str}.
    """
    t = _lire_datetime(debut)
    if (raison := raison_hors_regles(t)) is not None:
        raise ErreurMetier(raison, "Propose un créneau renvoyé par lister_creneaux_libres.")
    if not adresse or not adresse.strip():
        raise ErreurMetier("L'adresse du bien est obligatoire.", "Récupère-la avec detail_bien.")

    agendas = conseillers()
    if conseiller and conseiller not in agendas:
        raise ErreurMetier(f"Conseiller '{conseiller}' inconnu.", "Laisse ce champ vide ou choisis un nom de la liste.",
                           conseillers=list(agendas))
    candidats = {conseiller: agendas[conseiller]} if conseiller else agendas

    # Revérification juste avant l'écriture (le créneau a pu être pris entre-temps)
    choisi = next((nom for nom, ident in candidats.items()
                   if _libre(t, _occupations(ident, t, t + DUREE))), None)
    if choisi is None:
        raise ErreurMetier("creneau_pris",
                           f"Le créneau du {libelle(t)} n'est plus libre. Propose d'autres créneaux "
                           "avec lister_creneaux_libres.")

    id_agenda = agendas[choisi]
    event_id = _id_evenement(id_agenda, t)
    corps = {
        "id": event_id,
        "summary": f"Visite {id_bien} · {client_nom}",
        "location": adresse.strip(),
        "description": (f"Visite du bien {id_bien}\nAdresse : {adresse.strip()}\n"
                        f"Client : {client_nom}" + (f" <{client_email}>" if client_email else "") +
                        f"\nConseiller : {choisi}\nCréée par l'agent Sama Kër après validation d'un conseiller."),
        "start": {"dateTime": t.isoformat(), "timeZone": str(TZ)},
        "end": {"dateTime": (t + DUREE).isoformat(), "timeZone": str(TZ)},
        "extendedProperties": {"private": {
            "source": SOURCE, "id_bien": id_bien, "client_nom": client_nom,
            "client_email": client_email or "",
        }},
    }
    try:
        ev = _svc().events().insert(calendarId=id_agenda, body=corps, sendUpdates="none").execute()
    except HttpError as e:
        if e.resp.status != 409:
            raise
        # Même identifiant déjà connu de Google : soit un doublon, soit une ancienne visite annulée.
        existant = _svc().events().get(calendarId=id_agenda, eventId=event_id).execute()
        if existant.get("status") != "cancelled":
            raise ErreurMetier("doublon", f"Une visite existe déjà chez {choisi} le {libelle(t)}. "
                                          "Propose un autre créneau.")
        corps["status"] = "confirmed"
        ev = _svc().events().update(calendarId=id_agenda, eventId=event_id, body=corps,
                                    sendUpdates="none").execute()

    return {"ok": True, **_resume_evenement(choisi, ev), "lien": ev.get("htmlLink"),
            "message": f"Visite inscrite chez {choisi} le {libelle(t)}."}


@_sans_exception
def deplacer_visite(event_id: str, nouveau_debut: str, conseiller: str | None = None) -> dict:
    """Déplace une visite existante vers un nouveau créneau.

    Action d'ÉCRITURE : elle n'est exécutée qu'après l'accord d'un conseiller.
    La nouvelle visite est créée d'abord (avec les mêmes règles et la même
    protection contre les doublons), puis l'ancienne est annulée : en cas
    d'échec, l'ancienne visite reste en place.

    Args:
        event_id: Identifiant de la visite (renvoyé par creer_visite ou lister_visites).
        nouveau_debut: Nouveau début, format AAAA-MM-JJTHH:MM (heure de Dakar).
        conseiller: Nom du conseiller pour le nouveau créneau ; par défaut, le premier libre.

    Returns:
        {"ok": True, "ancienne_visite": {...}, "nouvelle_visite": {...}} ou
        {"ok": False, "erreur": str, "conseil": str}.
    """
    nom, id_agenda, ev = _trouver_evenement(event_id)
    ancienne = _resume_evenement(nom, ev)
    nouvelle = creer_visite.__wrapped__(
        debut=nouveau_debut, id_bien=ancienne["id_bien"] or "", adresse=ancienne["adresse"] or "",
        client_nom=ancienne["client_nom"] or ev.get("summary", "Client"),
        client_email=ancienne["client_email"] or None, conseiller=conseiller,
    )
    _svc().events().delete(calendarId=id_agenda, eventId=event_id, sendUpdates="none").execute()
    return {"ok": True, "ancienne_visite": ancienne, "nouvelle_visite": nouvelle,
            "message": f"Visite déplacée du {ancienne['libelle']} au {nouvelle['libelle']}."}


@_sans_exception
def annuler_visite(event_id: str) -> dict:
    """Annule une visite existante.

    Action d'ÉCRITURE : elle n'est exécutée qu'après l'accord d'un conseiller.

    Args:
        event_id: Identifiant de la visite (renvoyé par creer_visite ou lister_visites).

    Returns:
        {"ok": True, "visite_annulee": {...}} ou {"ok": False, "erreur": str, "conseil": str}.
    """
    nom, id_agenda, ev = _trouver_evenement(event_id)
    resume = _resume_evenement(nom, ev)
    _svc().events().delete(calendarId=id_agenda, eventId=event_id, sendUpdates="none").execute()
    return {"ok": True, "visite_annulee": resume, "message": f"Visite du {resume['libelle']} annulée."}


@_sans_exception
def lister_visites(date_debut: str, date_fin: str | None = None, client_email: str | None = None) -> dict:
    """Liste les visites inscrites par l'agent sur une période (pour retrouver un event_id).

    Args:
        date_debut: Premier jour, format AAAA-MM-JJ.
        date_fin: Dernier jour inclus, format AAAA-MM-JJ. Par défaut, date_debut + 14 jours.
        client_email: Ne garder que les visites de ce client (facultatif).

    Returns:
        {"ok": True, "nb_visites": int, "visites": [{"event_id", "conseiller", "libelle", ...}]}.
    """
    j1 = _lire_date(date_debut, "date_debut")
    j2 = _lire_date(date_fin, "date_fin") if date_fin else j1 + PERIODE_MAX
    debut, fin = datetime.combine(j1, time(0), TZ), datetime.combine(j2 + timedelta(days=1), time(0), TZ)
    visites = []
    for nom, ident in conseillers().items():
        filtres = [f"source={SOURCE}"] + ([f"client_email={client_email}"] if client_email else [])
        reponse = _svc().events().list(
            calendarId=ident, timeMin=debut.isoformat(), timeMax=fin.isoformat(), singleEvents=True,
            orderBy="startTime", privateExtendedProperty=filtres, maxResults=250,
        ).execute()
        visites += [_resume_evenement(nom, ev) for ev in reponse.get("items", [])
                    if ev.get("status") != "cancelled" and "dateTime" in ev["start"]]
    visites.sort(key=lambda v: v["debut"])
    return {"ok": True, "nb_visites": len(visites), "visites": visites}


OUTILS_LECTURE = [lister_creneaux_libres, lister_visites]
OUTILS_ECRITURE = [creer_visite, deplacer_visite, annuler_visite]
