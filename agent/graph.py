import sys

from dotenv import dotenv_values, load_dotenv
from langchain_core.messages import SystemMessage
from langchain_core.tools import StructuredTool
from langchain_groq import ChatGroq
from langgraph.graph import StateGraph, MessagesState, START
from langgraph.prebuilt import ToolNode, tools_condition

from agent.prompts import SYSTEM_PROMPT

load_dotenv(".env")  # à lancer depuis la racine du dépôt
config = dotenv_values(".env")

sys.path.insert(0, "mcp_server")
from catalogue import rechercher_biens, detail_bien  # Ex1 : outils locaux

MAX_ETAPES = 12

TOOLS = [StructuredTool.from_function(f, parse_docstring=True)
         for f in (rechercher_biens, detail_bien)]

llm = ChatGroq(model=config.get("GROQ_MODEL", "openai/gpt-oss-120b"),
               temperature=0).bind_tools(TOOLS)


def appeler_modele(state: MessagesState):
    messages = [SystemMessage(SYSTEM_PROMPT)] + state["messages"]
    return {"messages": [llm.invoke(messages)]}


def construire_graphe(checkpointer=None):
    g = StateGraph(MessagesState)
    g.add_node("agent", appeler_modele)
    g.add_node("tools", ToolNode(TOOLS, handle_tool_errors=True))
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", tools_condition)
    g.add_edge("tools", "agent")
    return g.compile(checkpointer=checkpointer)