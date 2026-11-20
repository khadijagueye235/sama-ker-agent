"""Graphe LangGraph de l'agent : modèle <-> outils, en boucle.

    START -> agent --(appel d'outil)--> tools --> agent --(réponse finale)--> END

Les outils d'écriture arrivent déjà enveloppés par agent/validation.py : quand le
modèle en appelle un, l'outil exécute interrupt() avant d'agir. Le graphe se met
en pause et ne reprend qu'avec Command(resume=...) ; il faut donc un checkpointer.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_groq import ChatGroq
from langgraph.graph import START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from agent.prompts import prompt_systeme

RACINE = Path(__file__).resolve().parents[1]
load_dotenv(RACINE / ".env")

MAX_ETAPES = 16


def outils_catalogue_locaux() -> list[StructuredTool]:
    """Ex1 : le catalogue appelé directement en Python (l'agent complet passe par MCP)."""
    sys.path.insert(0, str(RACINE / "mcp_server"))
    from catalogue import detail_bien, rechercher_biens
    return [StructuredTool.from_function(f, parse_docstring=True) for f in (rechercher_biens, detail_bien)]


def historique_compact(messages: list) -> list:
    """Ce que le modèle relit à chaque appel.

    La mémoire garde tout le fil, mais Groq limite la taille d'une requête (8 000 tokens
    par minute en offre gratuite). Pour les échanges PRÉCÉDENTS du fil, on ne renvoie
    que les emails du client et les réponses finales de l'agent : les appels d'outils
    et leurs résultats (listes de biens, de créneaux...) sont résumés dans ces réponses.
    L'échange en cours (depuis le dernier email du client) est gardé en entier.
    """
    dernier = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=0)
    anciens = [m for m in messages[:dernier]
               if isinstance(m, HumanMessage) or (isinstance(m, AIMessage) and not m.tool_calls)]
    return anciens + messages[dernier:]


def construire_graphe(outils=None, checkpointer=None, consignes: str = ""):
    """Compile le graphe.

    outils : liste d'outils LangChain (défaut : catalogue local, pour l'Ex1).
    checkpointer : mémoire des conversations, obligatoire pour interrupt() et la reprise.
    consignes : texte ajouté au prompt système (utilisé par l'adaptateur d'évaluation).
    """
    outils = outils or outils_catalogue_locaux()
    llm = ChatGroq(model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"), temperature=0,
                   max_retries=5).bind_tools(outils)

    def appeler_modele(state: MessagesState):
        systeme = SystemMessage(prompt_systeme() + consignes)
        return {"messages": [llm.invoke([systeme] + historique_compact(state["messages"]))]}

    g = StateGraph(MessagesState)
    g.add_node("agent", appeler_modele)
    g.add_node("tools", ToolNode(outils, handle_tool_errors=True))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)
    g.add_edge("tools", "agent")
    return g.compile(checkpointer=checkpointer)
