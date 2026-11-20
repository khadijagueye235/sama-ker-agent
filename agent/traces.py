import json
import time
import uuid

from langchain_core.callbacks import BaseCallbackHandler


class TraceurJSONL(BaseCallbackHandler):
    def __init__(self, dossier="traces"):
        nom = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}.jsonl"
        self.chemin = f"{dossier}/{nom}"
        self.nb_outils = 0
        self.tokens = 0

    def _ecrire(self, **evt):
        evt["t"] = round(time.time(), 3)
        with open(self.chemin, "a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False, default=str) + "\n")

    def on_tool_start(self, serialized, input_str, **kw):
        self.nb_outils += 1
        self._ecrire(type="outil_appel", outil=serialized.get("name"),
                     arguments=kw.get("inputs") or input_str)

    def on_tool_end(self, output, **kw):
        self._ecrire(type="outil_resultat", resultat=getattr(output, "content", output))

    def on_llm_end(self, response, **kw):
        msg = response.generations[0][0].message
        usage = getattr(msg, "usage_metadata", None) or {}
        self.tokens += usage.get("total_tokens", 0)
        self._ecrire(type="modele", contenu=msg.content,
                     appels_outils=getattr(msg, "tool_calls", []), tokens=usage)