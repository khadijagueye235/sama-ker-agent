from langgraph.errors import GraphRecursionError

from agent.graph import construire_graphe, MAX_ETAPES
from agent.traces import TraceurJSONL

app = construire_graphe()


def demander(texte: str) -> str:
    traceur = TraceurJSONL()
    try:
        res = app.invoke({"messages": [("user", texte)]},
                         config={"recursion_limit": MAX_ETAPES, "callbacks": [traceur]})
        return res["messages"][-1].content
    except GraphRecursionError:
        return "Demande trop complexe : elle est transmise à un conseiller."


if __name__ == "__main__":
    print(demander("Un F3 à Mermoz à moins de 400 000 F"))