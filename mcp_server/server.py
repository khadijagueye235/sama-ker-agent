"""Serveur MCP du catalogue Sama Kër Immobilier.

Expose :
  - l'outil  `rechercher_biens`  : recherche des biens disponibles ;
  - l'outil  `detail_bien`       : fiche complète d'un bien ;
  - la ressource `agence://infos` : horaires de visite, zones couvertes,
    conditions de location.

Lancement (depuis la racine du dépôt) :
  python mcp_server/server.py                      # transport stdio (local)
  python mcp_server/server.py --transport http     # HTTP sur http://127.0.0.1:8000/mcp
"""

import argparse
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

# Permet `python mcp_server/server.py` comme `python -m` depuis n'importe où.
sys.path.insert(0, str(Path(__file__).parent))
from catalogue import detail_bien, quartiers_couverts, rechercher_biens  # noqa: E402

INSTRUCTIONS = (
    "Serveur du catalogue de l'agence de location Sama Kër Immobilier (Dakar). "
    "Utilise rechercher_biens pour trouver des biens disponibles et detail_bien pour "
    "la fiche d'un bien. Ne cite jamais un bien absent des résultats de ces outils. "
    "Lis la ressource agence://infos pour les horaires de visite, les zones couvertes "
    "et les conditions de location."
)


def creer_serveur(host: str = "127.0.0.1", port: int = 8000) -> FastMCP:
    mcp = FastMCP("sama-ker-catalogue", instructions=INSTRUCTIONS, host=host, port=port)

    # Les deux outils sont des lectures seules : on le signale aux clients MCP.
    lecture_seule = ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False)
    mcp.tool(annotations=lecture_seule)(rechercher_biens)
    mcp.tool(annotations=lecture_seule)(detail_bien)

    @mcp.resource(
        "agence://infos",
        name="infos_agence",
        description="Horaires de visite, zones couvertes et conditions de location de l'agence.",
        mime_type="text/markdown",
    )
    def infos_agence() -> str:
        zones = ", ".join(quartiers_couverts())
        return f"""# Sama Kër Immobilier (agence fictive, Dakar)

## Horaires de visite
- Du lundi au samedi, de 9 h à 18 h (fuseau Africa/Dakar).
- Pas de visite le dimanche ni les jours fériés.
- Durée d'une visite : 45 minutes, avec un conseiller de l'agence.

## Zones couvertes
{zones}.
Aucun bien n'est proposé en dehors de ces quartiers.

## Conditions de location
- Bail d'un an renouvelable.
- Caution : 2 mois de loyer ; avance : 1 mois de loyer ; frais d'agence : 1 mois de loyer.
- Pièces demandées : pièce d'identité et justificatif de revenus.
- Loyers exprimés en F CFA, charges non comprises sauf mention contraire.

## Hors périmètre de l'assistant
Réclamations, négociation de loyer et litiges sont traités par un conseiller.
"""

    return mcp


def main():
    parser = argparse.ArgumentParser(description="Serveur MCP du catalogue Sama Kër Immobilier")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio",
                        help="stdio (local, par défaut) ou http (streamable HTTP sur /mcp)")
    parser.add_argument("--host", default="127.0.0.1", help="adresse d'écoute en HTTP")
    parser.add_argument("--port", type=int, default=8000, help="port d'écoute en HTTP")
    args = parser.parse_args()

    serveur = creer_serveur(args.host, args.port)
    if args.transport == "http":
        import uvicorn
        from starlette.middleware.cors import CORSMiddleware

        # CORS : nécessaire pour les clients qui tournent dans un navigateur
        # (MCP Inspector en connexion directe). Sans `expose_headers`, le
        # navigateur ne peut pas lire l'identifiant de session renvoyé à la
        # connexion, et les requêtes suivantes (liste des outils) échouent.
        app = CORSMiddleware(
            serveur.streamable_http_app(),
            allow_origins=["*"],
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=["*"],
            expose_headers=["Mcp-Session-Id"],
        )
        print(f"Serveur MCP sur http://{args.host}:{args.port}/mcp", file=sys.stderr)
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    else:
        serveur.run(transport="stdio")


if __name__ == "__main__":
    main()
