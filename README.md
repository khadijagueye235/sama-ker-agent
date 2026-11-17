# Sama Kër Immobilier · Agent de visites

Programme Data & IA · Orange Digital Center

Équipe : Nom 1, Nom 2, Nom 3

## Objectif
Agent qui traite les demandes de visite reçues par Gmail, avec validation d'un conseiller avant tout envoi ou écriture dans l'agenda.

## Installation

Prérequis : Python 3.11 ou plus récent, Git. Node.js (facultatif) pour l'inspecteur MCP.

```bash
git clone https://github.com/khadijagueye235/sama-ker-agent.git
cd sama-ker-agent
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS : source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env            # puis renseigner les vraies valeurs dans .env
python mcp_server/create_db.py  # crée le catalogue SQLite (30 biens)
```

> `mcp` est fixé en version 1.x : la version 2 renomme `FastMCP` et n'est pas
> encore compatible avec `langchain-mcp-adapters`.

## Serveur MCP du catalogue (`mcp_server/`)

Le catalogue des biens est exposé par un serveur MCP écrit avec FastMCP. Tout
client MCP (notre agent, l'agent d'une autre équipe, MCP Inspector, Claude
Desktop…) peut s'y brancher sans connaître notre code.

| Fichier | Rôle |
|---|---|
| `create_db.py` | crée `catalogue.db` : 30 biens fictifs, données fixes |
| `catalogue.py` | logique des outils (requêtes SQL paramétrées, validation) |
| `server.py` | serveur MCP : expose les outils et la ressource |
| `test_client.py` | client de vérification sans LLM (6 cas de test) |

### Lancer le serveur

```bash
# Transport stdio (local) : c'est le client qui lance le serveur
python mcp_server/server.py

# Transport HTTP (streamable HTTP) : http://127.0.0.1:8000/mcp
python mcp_server/server.py --transport http
python mcp_server/server.py --transport http --host 0.0.0.0 --port 8000   # accessible sur le réseau local
```

### Outils

#### `rechercher_biens`
Recherche les biens **disponibles**, triés par loyer croissant. Tous les arguments sont optionnels.

| Argument | Type | Description |
|---|---|---|
| `quartier` | string | Quartier de Dakar (`"Mermoz"`, `"Sacré-Cœur"`…). Accents, casse et tirets ignorés. |
| `type_bien` | string | `"appartement"`, `"studio"`, `"villa"` ou `"chambre"` |
| `pieces` | integer | Nombre exact de pièces (F3 = 3) |
| `loyer_max` | integer | Loyer mensuel maximal en F CFA (ex. `400000`) |
| `meuble` | boolean | `true` meublé uniquement, `false` non meublé uniquement |
| `limite` | integer | 1 à 30, défaut 10 |

Exemple d'appel et de réponse :
```json
// arguments
{"quartier": "Mermoz", "pieces": 3, "loyer_max": 400000}
// résultat
{
  "ok": true,
  "nb_resultats": 2,
  "criteres": {"quartier": "Mermoz", "pieces": 3, "loyer_max": 400000},
  "biens": [
    {"id": "SK-001", "type": "appartement", "quartier": "Mermoz", "loyer": 350000, "pieces": 3,
     "adresse": "Rue MZ-12, Mermoz", "disponible": true, "meuble": false,
     "description": "F3 lumineux au 2e étage, proche VDN"},
    {"id": "SK-003", "...": "..."}
  ],
  "message": "2 bien(s) disponible(s) trouvé(s), triés par loyer croissant."
}
```
Quartier non couvert (`{"quartier": "Saly"}`) : `nb_resultats: 0` et la liste `quartiers_couverts`.

#### `detail_bien`
Fiche complète d'un bien, y compris son champ `disponible`.

| Argument | Type | Description |
|---|---|---|
| `id_bien` | string | Identifiant `"SK-001"` (`"sk-1"`, `"SK1"`, `"1"` acceptés) |

```json
// arguments
{"id_bien": "SK-099"}
// résultat
{"ok": false, "erreur": "Aucun bien avec l'identifiant 'SK-099' dans le catalogue.",
 "conseil": "N'invente pas de bien. Utilise rechercher_biens pour trouver des biens existants."}
```

**Format des erreurs.** Les outils ne lèvent jamais d'exception : une erreur est
renvoyée comme `{"ok": false, "erreur": "...", "conseil": "..."}` pour que le
modèle puisse corriger son appel.

### Ressource

| URI | Contenu |
|---|---|
| `agence://infos` | Markdown : horaires de visite (lun.–sam., 9 h–18 h, 45 min, Africa/Dakar), zones couvertes, conditions de location, demandes hors périmètre |

### Se brancher sur le serveur

**Vérification rapide (sans LLM)** :
```bash
python mcp_server/test_client.py                                   # stdio
python mcp_server/test_client.py --url http://127.0.0.1:8000/mcp   # HTTP (serveur lancé à part)
```

**MCP Inspector** (interface web de test, nécessite Node.js) :
```bash
npx @modelcontextprotocol/inspector .venv/Scripts/python.exe mcp_server/server.py   # stdio
```
En HTTP (serveur lancé à part avec `--transport http`) :
1. `npx @modelcontextprotocol/inspector`, puis ouvrir http://localhost:6274 ;
2. **Add Servers → + Add manually** : Server ID `sama-ker-http`, Transport `streamable-http`,
   URL `http://127.0.0.1:8000/mcp`, puis **Add** ;
3. activer l'interrupteur de la carte `sama-ker-http` (Connected) ;
4. onglet **Tools** en haut : `rechercher_biens` et `detail_bien` apparaissent ; onglet **Resources** : `agence://infos`.

> Le serveur HTTP envoie les en-têtes CORS (dont `Access-Control-Expose-Headers: Mcp-Session-Id`)
> nécessaires aux clients qui tournent dans un navigateur, comme l'Inspector.

**Depuis un agent LangChain / LangGraph** (`langchain-mcp-adapters`) :
```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({
    "catalogue": {"transport": "streamable_http", "url": "http://127.0.0.1:8000/mcp"},
    # ou en local : {"transport": "stdio", "command": "python", "args": ["mcp_server/server.py"]}
})
tools = await client.get_tools()   # -> rechercher_biens, detail_bien
```

**Depuis un client MCP configuré en JSON** (Claude Desktop, Cursor…) :
```json
{
  "mcpServers": {
    "sama-ker-catalogue": {
      "command": "python",
      "args": ["C:/chemin/vers/sama-ker-agent/mcp_server/server.py"]
    }
  }
}
```
