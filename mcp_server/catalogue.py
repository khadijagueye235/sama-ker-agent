"""Outils métier du catalogue Sama Kër Immobilier.

Ce module contient la logique pure des outils `rechercher_biens` et
`detail_bien`. Il est utilisé :
  - à l'exercice 1, directement par l'agent (outils locaux) ;
  - à l'exercice 2, par le serveur MCP (server.py), qui l'expose à tout client.

Règles de conception :
  - les docstrings sont écrites POUR LE MODÈLE : c'est ce qu'il lit pour
    décider quand et comment appeler l'outil ;
  - aucune exception ne remonte jusqu'au modèle : chaque erreur est renvoyée
    sous forme de dictionnaire {"ok": False, "erreur": ..., "conseil": ...}
    pour qu'il puisse corriger son appel ;
  - seuls les biens disponibles sont proposés par la recherche.
"""

import re
from contextlib import closing
import sqlite3
import unicodedata
from pathlib import Path

DB_PATH = Path(__file__).parent / "catalogue.db"

TYPES_VALIDES = ("appartement", "studio", "villa", "chambre")
LIMITE_MAX = 30


# --------------------------------------------------------------------------
# Utilitaires internes
# --------------------------------------------------------------------------

def _normaliser(texte: str) -> str:
    """'Sacré-Cœur' -> 'sacrecoeur', 'liberte 6' -> 'liberte6'."""
    texte = texte.lower().replace("œ", "oe").replace("æ", "ae")
    texte = unicodedata.normalize("NFKD", texte)
    texte = "".join(c for c in texte if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", texte)


def _connexion() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(
            "Catalogue introuvable. Lancez d'abord : python mcp_server/create_db.py"
        )
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.create_function("norm", 1, _normaliser, deterministic=True)
    return con


def _vers_dict(ligne: sqlite3.Row) -> dict:
    bien = dict(ligne)
    bien["disponible"] = bool(bien["disponible"])
    bien["meuble"] = bool(bien["meuble"])
    return bien


def _erreur(message: str, conseil: str, **details) -> dict:
    return {"ok": False, "erreur": message, "conseil": conseil, **details}


def quartiers_couverts() -> list[str]:
    """Liste des quartiers présents dans le catalogue (ordre alphabétique)."""
    with closing(_connexion()) as con:
        return [r[0] for r in con.execute("SELECT DISTINCT quartier FROM biens ORDER BY quartier")]


# --------------------------------------------------------------------------
# Outils exposés au modèle
# --------------------------------------------------------------------------

def rechercher_biens(
    quartier: str | None = None,
    type_bien: str | None = None,
    pieces: int | None = None,
    loyer_max: int | None = None,
    meuble: bool | None = None,
    limite: int = 10,
) -> dict:
    """Recherche les biens DISPONIBLES à la location dans le catalogue de l'agence.

    Utilise cet outil dès qu'un client décrit le logement qu'il cherche.
    C'est la seule source fiable : ne cite jamais un bien qui n'a pas été
    renvoyé par cet outil. Tous les critères sont optionnels ; n'indique que
    ceux que le client a exprimés.

    Correspondances utiles :
      - « F2 », « F3 », « F4 »… = nombre de pièces (F3 -> pieces=3).
      - « 400 000 F », « 400k », « 400 mille » -> loyer_max=400000 (F CFA, entier).

    Args:
        quartier: Quartier de Dakar, ex. "Mermoz", "Sacré-Cœur", "Liberté 6".
            Accents, majuscules et tirets sont ignorés.
        type_bien: Un parmi "appartement", "studio", "villa", "chambre".
        pieces: Nombre exact de pièces (F3 = 3). Entier >= 1.
        loyer_max: Loyer mensuel maximal en F CFA (entier, ex. 400000).
        meuble: True pour meublé uniquement, False pour non meublé uniquement.
        limite: Nombre maximal de biens renvoyés (1 à 30, défaut 10).

    Returns:
        {"ok": True, "nb_resultats": int, "biens": [...], "message": str}
        Si nb_resultats vaut 0, aucun bien ne correspond : dis-le clairement
        au client sans inventer d'alternative. En cas de paramètre invalide :
        {"ok": False, "erreur": str, "conseil": str}.
    """
    # --- Validation des arguments (erreurs exploitables par le modèle) ---
    if type_bien is not None:
        type_bien = type_bien.strip().lower()
        if type_bien not in TYPES_VALIDES:
            return _erreur(
                f"type_bien '{type_bien}' inconnu.",
                "Utilise l'une des valeurs acceptées, ou n'indique pas de type.",
                valeurs_acceptees=list(TYPES_VALIDES),
            )
    if pieces is not None and pieces < 1:
        return _erreur("pieces doit être un entier >= 1.", "Pour un F3, indique pieces=3.")
    if loyer_max is not None and loyer_max <= 0:
        return _erreur(
            "loyer_max doit être un entier positif en F CFA.",
            "Pour « 400 000 F », indique loyer_max=400000.",
        )
    limite = max(1, min(int(limite), LIMITE_MAX))

    try:
        con = _connexion()
    except FileNotFoundError as e:
        return _erreur(str(e), "Préviens un conseiller : le catalogue est indisponible.")

    with closing(con):
        # --- Quartier hors zone : réponse explicite, pas une erreur ---
        if quartier:
            couverts = [r[0] for r in con.execute("SELECT DISTINCT quartier FROM biens ORDER BY quartier")]
            if _normaliser(quartier) not in {_normaliser(q) for q in couverts}:
                return {
                    "ok": True,
                    "nb_resultats": 0,
                    "biens": [],
                    "message": f"L'agence n'a aucun bien dans le quartier '{quartier}'.",
                    "quartiers_couverts": couverts,
                }

        # --- Requête paramétrée (jamais de concaténation de texte client) ---
        conditions, params = ["disponible = 1"], []
        if quartier:
            conditions.append("norm(quartier) = ?")
            params.append(_normaliser(quartier))
        if type_bien:
            conditions.append("type = ?")
            params.append(type_bien)
        if pieces is not None:
            conditions.append("pieces = ?")
            params.append(pieces)
        if loyer_max is not None:
            conditions.append("loyer <= ?")
            params.append(loyer_max)
        if meuble is not None:
            conditions.append("meuble = ?")
            params.append(int(meuble))

        sql = f"SELECT * FROM biens WHERE {' AND '.join(conditions)} ORDER BY loyer, id LIMIT ?"
        biens = [_vers_dict(r) for r in con.execute(sql, [*params, limite])]

    criteres = {k: v for k, v in {
        "quartier": quartier, "type_bien": type_bien, "pieces": pieces,
        "loyer_max": loyer_max, "meuble": meuble,
    }.items() if v is not None}

    if not biens:
        message = (
            "Aucun bien disponible ne correspond à ces critères. Dis-le clairement "
            "au client ; tu peux lui proposer d'élargir un critère (budget, quartier, pièces)."
        )
    else:
        message = f"{len(biens)} bien(s) disponible(s) trouvé(s), triés par loyer croissant."

    return {"ok": True, "nb_resultats": len(biens), "criteres": criteres, "biens": biens, "message": message}


def detail_bien(id_bien: str) -> dict:
    """Renvoie la fiche complète d'un bien du catalogue à partir de son identifiant.

    Utilise cet outil pour obtenir l'adresse exacte, la description ou la
    disponibilité d'un bien précis (par exemple avant de proposer une visite),
    ou quand le client cite une référence comme « SK-003 ».

    Args:
        id_bien: Identifiant du bien au format "SK-001" (les formes "sk-1",
            "SK1" ou "1" sont aussi acceptées).

    Returns:
        {"ok": True, "bien": {...}} avec tous les champs du bien, y compris
        "disponible" (un bien non disponible ne doit pas être proposé).
        Si l'identifiant est inconnu : {"ok": False, "erreur": str, "conseil": str}.
    """
    brut = (id_bien or "").strip().upper()
    m = re.fullmatch(r"(?:SK)?-?\s*(\d{1,3})", brut)
    if not m:
        return _erreur(
            f"Identifiant '{id_bien}' mal formé.",
            "Le format attendu est 'SK-001'. Utilise rechercher_biens pour trouver des identifiants valides.",
        )
    ident = f"SK-{int(m.group(1)):03d}"

    try:
        con = _connexion()
    except FileNotFoundError as e:
        return _erreur(str(e), "Préviens un conseiller : le catalogue est indisponible.")

    with closing(con):
        ligne = con.execute("SELECT * FROM biens WHERE id = ?", (ident,)).fetchone()

    if ligne is None:
        return _erreur(
            f"Aucun bien avec l'identifiant '{ident}' dans le catalogue.",
            "N'invente pas de bien. Utilise rechercher_biens pour trouver des biens existants.",
        )
    return {"ok": True, "bien": _vers_dict(ligne)}


# --------------------------------------------------------------------------
# Démonstration rapide : python mcp_server/catalogue.py
# --------------------------------------------------------------------------
if __name__ == "__main__":
    import json

    exemples = [
        ("F3 à Mermoz <= 400 000 F", rechercher_biens(quartier="Mermoz", pieces=3, loyer_max=400000)),
        ("Quartier hors zone (Saly)", rechercher_biens(quartier="Saly")),
        ("Aucun résultat (villa < 500 000 F)", rechercher_biens(type_bien="villa", loyer_max=500000)),
        ("Type invalide", rechercher_biens(type_bien="duplex")),
        ("Détail sk-3", detail_bien("sk-3")),
        ("Détail inexistant", detail_bien("SK-099")),
    ]
    for titre, resultat in exemples:
        print(f"\n### {titre}")
        print(json.dumps(resultat, ensure_ascii=False, indent=2))
