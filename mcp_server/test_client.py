"""Client MCP de vérification du serveur catalogue (sans LLM).

Liste les outils et la ressource, puis appelle chaque outil sur des cas de test.

Usage (depuis la racine du dépôt) :
  python mcp_server/test_client.py                       # lance le serveur en stdio
  python mcp_server/test_client.py --url http://127.0.0.1:8000/mcp   # serveur HTTP déjà lancé
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

SERVEUR = Path(__file__).parent / "server.py"

CAS = [
    ("rechercher_biens", {"quartier": "Mermoz", "pieces": 3, "loyer_max": 400000}, ["SK-001", "SK-003"]),
    ("rechercher_biens", {"quartier": "Saly"}, []),
    ("rechercher_biens", {"type_bien": "villa", "loyer_max": 500000}, []),
    ("rechercher_biens", {"quartier": "sacre coeur"}, ["SK-021", "SK-022"]),
    ("detail_bien", {"id_bien": "sk-3"}, ["SK-003"]),
    ("detail_bien", {"id_bien": "SK-099"}, []),
]


def ids(resultat: dict) -> list[str]:
    if "biens" in resultat:
        return [b["id"] for b in resultat["biens"]]
    if resultat.get("ok") and "bien" in resultat:
        return [resultat["bien"]["id"]]
    return []


async def verifier(session: ClientSession) -> bool:
    await session.initialize()

    outils = await session.list_tools()
    print("Outils :", ", ".join(t.name for t in outils.tools))
    ressources = await session.list_resources()
    print("Ressources :", ", ".join(str(r.uri) for r in ressources.resources))
    infos = await session.read_resource("agence://infos")
    print("Extrait de agence://infos :", infos.contents[0].text.splitlines()[0])

    tout_ok = True
    for outil, arguments, attendu in CAS:
        reponse = await session.call_tool(outil, arguments)
        resultat = json.loads(reponse.content[0].text)
        obtenu = ids(resultat)
        ok = obtenu == attendu
        tout_ok &= ok
        print(f"{'OK ' if ok else 'KO '} {outil}({arguments}) -> {obtenu}")
    return tout_ok


async def main(url: str | None) -> bool:
    if url:
        async with streamablehttp_client(url) as (lecture, ecriture, _):
            async with ClientSession(lecture, ecriture) as session:
                return await verifier(session)
    params = StdioServerParameters(command=sys.executable, args=[str(SERVEUR)])
    async with stdio_client(params) as (lecture, ecriture):
        async with ClientSession(lecture, ecriture) as session:
            return await verifier(session)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", help="URL d'un serveur HTTP déjà lancé (sinon stdio)")
    succes = asyncio.run(main(parser.parse_args().url))
    print("\nTous les cas passent." if succes else "\nCertains cas échouent.")
    sys.exit(0 if succes else 1)
