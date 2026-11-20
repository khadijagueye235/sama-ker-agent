"""Tous les outils de l'agent complet.

  - catalogue : via le SERVEUR MCP (mcp_server/server.py), comme n'importe quel client MCP ;
  - agenda et Gmail : outils Python ;
  - écritures (visites, envoi d'email) : derrière la validation du conseiller.
"""

import sys
from pathlib import Path

from langchain_mcp_adapters.client import MultiServerMCPClient

from agent import tools_calendar as cal
from agent import tools_gmail as gmail
from agent.validation import outils_langgraph

RACINE = Path(__file__).resolve().parents[1]


def client_mcp(url: str | None = None) -> MultiServerMCPClient:
    """Client du serveur catalogue : HTTP si une URL est donnée, sinon stdio (serveur lancé par le client)."""
    if url:
        return MultiServerMCPClient({"catalogue": {"transport": "streamable_http", "url": url}})
    return MultiServerMCPClient({"catalogue": {
        "transport": "stdio", "command": sys.executable,
        "args": [str(RACINE / "mcp_server" / "server.py")]}})


async def charger_outils(demander=None, url_mcp: str | None = None) -> list:
    """Outils de l'agent. demander=None : validation par interrupt() (pause du graphe)."""
    outils_mcp = await client_mcp(url_mcp).get_tools()
    return outils_mcp + outils_langgraph(
        cal.OUTILS_LECTURE + gmail.OUTILS_LECTURE,
        cal.OUTILS_ECRITURE + gmail.OUTILS_ECRITURE,
        demander=demander)
