"""Connexion OAuth au compte Google de test (partagée par Calendar et Gmail).

Un seul jeton (token.json) couvre les deux API, avec les scopes strictement
nécessaires :
  - gmail.modify    : lire les messages, gérer les libellés, créer des
                      brouillons et les envoyer (pas de suppression définitive) ;
  - calendar.events : lire, créer, déplacer et annuler des événements
                      (pas de gestion des agendas eux-mêmes ni du partage).

Premier lancement : python -m agent.google_auth
Un navigateur s'ouvre ; on se connecte avec le compte de TEST et on accepte.
Le jeton est ensuite rafraîchi automatiquement. credentials.json et token.json
restent hors de Git (.gitignore).
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

RACINE = Path(__file__).resolve().parents[1]
load_dotenv(RACINE / ".env")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.events",
]

FICHIER_CREDENTIALS = RACINE / os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
FICHIER_TOKEN = RACINE / os.getenv("GOOGLE_TOKEN_FILE", "token.json")


def obtenir_credentials() -> Credentials:
    """Renvoie des identifiants valides, en lançant la connexion si besoin."""
    creds = None
    if FICHIER_TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(FICHIER_TOKEN), SCOPES)

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    else:
        if not FICHIER_CREDENTIALS.exists():
            raise FileNotFoundError(
                f"{FICHIER_CREDENTIALS.name} introuvable à la racine du dépôt. "
                "Téléchargez-le depuis Google Cloud (client OAuth « Desktop app »)."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(FICHIER_CREDENTIALS), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
    FICHIER_TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return creds


def service_calendar():
    return build("calendar", "v3", credentials=obtenir_credentials(), cache_discovery=False)


def service_gmail():
    return build("gmail", "v1", credentials=obtenir_credentials(), cache_discovery=False)


if __name__ == "__main__":
    obtenir_credentials()
    profil = service_gmail().users().getProfile(userId="me").execute()
    print(f"Connexion réussie au compte {profil['emailAddress']}")
    print(f"Jeton enregistré dans {FICHIER_TOKEN.name} (ignoré par Git).")
