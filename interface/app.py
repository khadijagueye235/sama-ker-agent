"""Interface conseiller de l'agent Sama Kër (Streamlit).

    streamlit run interface/app.py

C'est le même agent que `python -m agent.main` : même graphe, mêmes outils, même
mémoire (memoire.db). Seule la validation change : au lieu de taper o/N dans le
terminal, le conseiller clique sur « Valider » ou « Refuser ».

Comment ça marche : quand l'agent veut agir (envoyer, créer une visite), le graphe se
met en PAUSE (interrupt). L'interface affiche l'action ; le clic du conseiller
reprend le graphe avec Command(resume=...). Entre deux clics, l'état du graphe est
conservé par le checkpointer SQLite : c'est pour cela qu'une page web suffit.
"""

import asyncio
import json
import re
import sys
from datetime import timedelta
from html import escape
from pathlib import Path

import streamlit as st

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "mcp_server"))

from groq import APIStatusError  # noqa: E402
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage  # noqa: E402
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402
from langgraph.errors import GraphRecursionError  # noqa: E402
from langgraph.types import Command  # noqa: E402

from agent import tools_calendar as cal  # noqa: E402
from agent import tools_gmail as gmail  # noqa: E402
from agent.graph import MAX_ETAPES, construire_graphe  # noqa: E402
from agent.main import MEMOIRE, demander, message_pour  # noqa: E402
from agent.outils import charger_outils  # noqa: E402
from agent.prompts import MOIS  # noqa: E402
from agent.traces import TraceurJSONL  # noqa: E402
from agent.validation import LIBELLES  # noqa: E402

st.set_page_config(page_title="Sama Kër · Espace conseiller", page_icon="🏡", layout="wide")

# ----------------------------------------------------------------------------- style
st.markdown("""
<style>
.block-container {padding-top: 1.6rem; max-width: 1180px;}
#MainMenu, footer {visibility: hidden;}
.sk-hero {
  border-radius: 18px; padding: 26px 30px; margin-bottom: 18px;
  background: linear-gradient(120deg, rgba(232,163,61,.20), rgba(32,129,120,.18) 60%, rgba(23,29,37,.0));
  border: 1px solid rgba(255,255,255,.08);
}
.sk-hero h1 {font-size: 1.85rem; margin: 0; letter-spacing: -.01em;}
.sk-hero p {margin: 6px 0 0; opacity: .75; font-size: .98rem;}
.sk-card {
  background: rgba(255,255,255,.035); border: 1px solid rgba(255,255,255,.08);
  border-radius: 14px; padding: 16px 18px; margin-bottom: 10px;
}
.sk-card h4 {margin: 0 0 4px; font-size: 1.04rem;}
.sk-muted {opacity: .65; font-size: .88rem;}
.sk-extrait {opacity: .85; font-size: .93rem; margin-top: 8px; white-space: pre-wrap;}
.sk-pill {display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: .78rem;
  font-weight: 600; margin-left: 8px; vertical-align: middle;}
.sk-new   {background: rgba(96,165,250,.16); color: #93C5FD;}
.sk-pause {background: rgba(232,163,61,.18); color: #F4C477;}
.sk-ok    {background: rgba(52,211,153,.16); color: #6EE7B7;}
.sk-out   {background: rgba(167,139,250,.18); color: #C4B5FD;}
.sk-err   {background: rgba(248,113,113,.16); color: #FCA5A5;}
.sk-valid {
  border: 1px solid rgba(232,163,61,.55); background: rgba(232,163,61,.07);
  border-radius: 14px; padding: 16px 18px; margin: 8px 0 12px;
}
.sk-valid h4 {margin: 0 0 10px; color: #F4C477;}
.sk-mail {
  background: rgba(0,0,0,.25); border-radius: 10px; padding: 14px 16px;
  white-space: pre-wrap; font-size: .93rem; line-height: 1.5; border: 1px solid rgba(255,255,255,.06);
}
.sk-step {padding: 6px 0; border-bottom: 1px dashed rgba(255,255,255,.08); font-size: .92rem;}
.sk-step:last-child {border-bottom: none;}
.sk-visit {display: flex; gap: 14px; align-items: flex-start;}
.sk-time {min-width: 64px; font-weight: 700; color: #F4C477; font-size: 1.05rem;}
.sk-day {margin: 18px 0 8px; font-weight: 700; opacity: .9;}
.sk-day::first-letter {text-transform: uppercase;}
div[data-testid="stMetric"] {background: rgba(255,255,255,.035); border: 1px solid rgba(255,255,255,.08);
  border-radius: 14px; padding: 14px 16px;}
</style>
""", unsafe_allow_html=True)

NOMS_OUTILS = {
    "rechercher_biens": "🔎 Recherche dans le catalogue",
    "detail_bien": "🏠 Détail d'un bien",
    "lister_creneaux_libres": "📅 Recherche de créneaux libres",
    "lister_visites": "🗓️ Vérification des visites existantes",
    "creer_brouillon": "✍️ Rédaction du brouillon",
    "transmettre_conseiller": "🧑‍💼 Transmission à un conseiller",
    "envoyer_brouillon": "📨 Envoi de la réponse",
    "creer_visite": "✅ Création de la visite",
    "deplacer_visite": "↔️ Déplacement de la visite",
    "annuler_visite": "🗑️ Annulation de la visite",
    "lire_demandes_non_traitees": "📥 Lecture de la boîte",
}
ETATS = {
    "nouveau": ("Nouvelle", "sk-new"),
    "pause": ("Validation requise", "sk-pause"),
    "fini": ("Traitée", "sk-ok"),
    "transmis": ("Transmise à un conseiller", "sk-out"),
    "erreur": ("Interrompue", "sk-err"),
}


def nom_seul(adresse: str | None) -> str:
    """« Awa Ndiaye <a@b.c> » -> « Awa Ndiaye » : pas d'adresse email à l'écran."""
    if not adresse:
        return "—"
    nom = re.sub(r"\s*<[^>]*>", "", adresse).strip().strip('"')
    return nom or adresse.split("@")[0]


def date_fr(d) -> str:
    return f"{cal.JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


# ----------------------------------------------------------------------------- agent
@st.cache_resource(show_spinner=False)
def outils_agent():
    """Outils chargés une seule fois (catalogue via le serveur MCP, agenda, Gmail)."""
    return asyncio.run(charger_outils())


async def _etape(thread_id: str, entree):
    async with AsyncSqliteSaver.from_conn_string(str(MEMOIRE)) as memoire:
        app = construire_graphe(outils_agent(), checkpointer=memoire)
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": MAX_ETAPES,
                  "callbacks": [TraceurJSONL()]}
        return await app.ainvoke(entree, config)


def trace_de(messages: list) -> list[tuple[str, str]]:
    """Étapes de l'échange en cours : (libellé, état) pour chaque appel d'outil."""
    dernier = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=0)
    resultats = {m.tool_call_id: m for m in messages[dernier:] if isinstance(m, ToolMessage)}
    etapes = []
    for m in messages[dernier:]:
        if isinstance(m, AIMessage):
            for appel in m.tool_calls:
                r = resultats.get(appel["id"])
                if r is None:
                    etat = "⏸️ en attente du conseiller"
                elif "refus_conseiller" in str(r.content):
                    etat = "❌ refusé par le conseiller"
                elif '"ok": false' in str(r.content).lower() or "'ok': False" in str(r.content):
                    etat = "⚠️ échec"
                else:
                    etat = "✔️"
                etapes.append((NOMS_OUTILS.get(appel["name"], appel["name"]), etat))
    return etapes


def avancer(d: dict, entree) -> None:
    """Fait tourner le graphe jusqu'à la prochaine pause ou la fin, et range le résultat."""
    dossier = st.session_state.dossiers.setdefault(d["message_id"], {"etat": "nouveau"})
    try:
        res = asyncio.run(_etape(d["thread_id"], entree))
    except GraphRecursionError:
        gmail.transmettre_conseiller(d["message_id"], "demande trop complexe pour l'agent")
        dossier.update(etat="transmis", final="Demande trop complexe : transmise à un conseiller.")
        return
    except APIStatusError as e:
        if e.status_code not in (413, 429):
            raise
        dossier.update(etat="erreur", final=(
            "Le service d'IA a atteint sa limite d'utilisation. Rien n'a été envoyé sans votre "
            "validation ; réessayez dans quelques minutes."))
        return
    dossier["trace"] = trace_de(res["messages"])
    pauses = res.get("__interrupt__")
    if pauses:
        dossier.update(etat="pause", pause=pauses[0].value)
        return
    appels = [n for n, _ in dossier["trace"]]
    transmis = NOMS_OUTILS["transmettre_conseiller"] in appels
    dossier.update(etat="transmis" if transmis else "fini", final=res["messages"][-1].content, pause=None)


# ----------------------------------------------------------------------------- état
st.session_state.setdefault("demandes", [])
st.session_state.setdefault("dossiers", {})
st.session_state.setdefault("visites", None)


def relever_boite():
    r = gmail.lire_demandes_non_traitees(10)
    if not r.get("ok"):
        st.error("Lecture de la boîte Gmail impossible. Vérifiez la connexion au compte de test.")
        return
    connus = {d["message_id"] for d in st.session_state.demandes}
    st.session_state.demandes = [d for d in r["demandes"] if d["message_id"] not in connus] + \
        st.session_state.demandes


# ----------------------------------------------------------------------------- barre latérale
with st.sidebar:
    st.markdown("### 🏡 Sama Kër")
    st.caption(f"Aujourd'hui : {date_fr(cal.maintenant())} {cal.maintenant().year}")
    st.markdown("""
**Comment ça marche**

1. **Relever la boîte** : les nouvelles demandes arrivent.
2. **Traiter** : l'agent cherche les biens et les créneaux, puis rédige la réponse.
3. **Valider ou refuser** : rien n'est envoyé, aucune visite n'est créée sans votre accord.
""")
    st.divider()
    st.caption("Agent LangGraph · catalogue via MCP · Gmail et Google Calendar")

st.markdown("""
<div class="sk-hero">
  <h1>🏡 Sama Kër Immobilier · Espace conseiller</h1>
  <p>L'agent prépare les réponses aux demandes de visite. Vous gardez le dernier mot.</p>
</div>
""", unsafe_allow_html=True)

onglet_demandes, onglet_agenda, onglet_catalogue, onglet_eval = st.tabs(
    ["📥 Demandes", "📅 Agenda", "🏠 Catalogue", "📊 Évaluation"])


# ----------------------------------------------------------------------------- demandes
def carte_validation(d: dict, dossier: dict):
    p = dossier["pause"]
    action, args = p["action"], p["arguments"]
    corps = [f'<div class="sk-valid"><h4>⏸️ {escape(LIBELLES.get(action, action))}</h4>']
    if action == "envoyer_brouillon":
        a = gmail.apercu_brouillon(args.get("draft_id", ""))
        if a.get("ok"):
            corps.append(f'<div class="sk-muted">À : {escape(nom_seul(a["a"]))} · Objet : '
                         f'{escape(a["objet"] or "")}</div><br>'
                         f'<div class="sk-mail">{escape(a["corps"].strip())}</div>')
    elif action in ("creer_visite", "deplacer_visite"):
        lignes = {"Date": args.get("debut") or args.get("nouveau_debut"), "Bien": args.get("id_bien"),
                  "Adresse": args.get("adresse"), "Client": args.get("client_nom"),
                  "Conseiller": args.get("conseiller")}
        corps += [f"<div>• <b>{k}</b> : {escape(str(v).replace('T', ' à '))}</div>"
                  for k, v in lignes.items() if v]
    else:
        corps += [f"<div>• <b>{escape(k)}</b> : {escape(str(v))}</div>"
                  for k, v in args.items() if v and "email" not in k]
    corps.append("</div>")
    st.markdown("".join(corps), unsafe_allow_html=True)

    motif = st.text_input("Motif en cas de refus (facultatif)", key=f"motif_{d['message_id']}",
                          placeholder="ex. ton à revoir, créneau à confirmer…")
    c1, c2, _ = st.columns([1, 1, 3])
    if c1.button("✅ Valider", key=f"ok_{d['message_id']}", type="primary", use_container_width=True):
        with st.spinner("Exécution de l'action validée…"):
            avancer(d, Command(resume={"ok": True, "commentaire": ""}))
        st.rerun()
    if c2.button("❌ Refuser", key=f"non_{d['message_id']}", use_container_width=True):
        with st.spinner("Refus transmis à l'agent…"):
            avancer(d, Command(resume={"ok": False, "commentaire": motif}))
        st.rerun()


with onglet_demandes:
    haut1, haut2 = st.columns([3, 1])
    haut1.markdown("#### Demandes reçues")
    if haut2.button("📥 Relever la boîte", type="primary", use_container_width=True):
        with st.spinner("Lecture de Gmail…"):
            relever_boite()
        st.rerun()

    if not st.session_state.demandes:
        st.info("Aucune demande affichée. Cliquez sur « Relever la boîte » pour lire les nouveaux emails.")

    for d in st.session_state.demandes:
        dossier = st.session_state.dossiers.get(d["message_id"], {"etat": "nouveau"})
        libelle, classe = ETATS[dossier["etat"]]
        extrait = escape(gmail_corps := (d.get("corps") or "").strip())
        extrait = extrait[:260] + ("…" if len(gmail_corps) > 260 else "")
        st.markdown(f"""
<div class="sk-card">
  <h4>{escape(d['objet'] or '(sans objet)')}<span class="sk-pill {classe}">{libelle}</span></h4>
  <div class="sk-muted">De : {escape(nom_seul(d['expediteur']))}</div>
  <div class="sk-extrait">{extrait}</div>
</div>""", unsafe_allow_html=True)

        if dossier["etat"] == "nouveau":
            if st.button("🤖 Traiter avec l'agent", key=f"go_{d['message_id']}"):
                with st.spinner("L'agent analyse la demande, consulte le catalogue et l'agenda…"):
                    avancer(d, {"messages": [("user", message_pour(d))]})
                st.rerun()
        elif dossier["etat"] == "pause":
            carte_validation(d, dossier)
        elif dossier.get("final"):
            (st.warning if dossier["etat"] == "erreur" else st.success)(dossier["final"])

        if dossier.get("trace"):
            with st.expander("Ce que l'agent a fait"):
                st.markdown("".join(f'<div class="sk-step">{escape(n)} &nbsp; <span class="sk-muted">'
                                    f'{escape(e)}</span></div>' for n, e in dossier["trace"]),
                            unsafe_allow_html=True)
        st.write("")


# ----------------------------------------------------------------------------- agenda
with onglet_agenda:
    a1, a2 = st.columns([3, 1])
    a1.markdown("#### Visites des 14 prochains jours")
    if a2.button("🔄 Actualiser", use_container_width=True) or st.session_state.visites is None:
        with st.spinner("Lecture de Google Calendar…"):
            r = cal.lister_visites(cal.maintenant().date().isoformat())
            st.session_state.visites = r.get("visites", []) if r.get("ok") else []
    visites = st.session_state.visites or []
    if not visites:
        st.info("Aucune visite prévue.")
    jour_courant = None
    for v in visites:
        jour = v["libelle"].rsplit(" à ", 1)[0]
        heure = v["libelle"].rsplit(" à ", 1)[-1]
        if jour != jour_courant:
            st.markdown(f'<div class="sk-day">{escape(jour)}</div>', unsafe_allow_html=True)
            jour_courant = jour
        st.markdown(f"""
<div class="sk-card"><div class="sk-visit">
  <div class="sk-time">{escape(heure)}</div>
  <div><h4>{escape(v.get('titre', 'Visite'))}</h4>
  <div class="sk-muted">📍 {escape(v.get('adresse') or '—')} · 🧑‍💼 {escape(v.get('conseiller') or '—')}</div></div>
</div></div>""", unsafe_allow_html=True)


# ----------------------------------------------------------------------------- catalogue
with onglet_catalogue:
    st.markdown("#### Poser une question à l'agent")
    st.caption("Mode question directe (exercice 1) : l'agent ne répond qu'avec les biens du catalogue.")
    with st.form("question"):
        q = st.text_input("Question", placeholder="Je cherche un F3 à Mermoz à moins de 400 000 F",
                          label_visibility="collapsed")
        envoye = st.form_submit_button("Demander", type="primary")
    if envoye and q.strip():
        with st.spinner("Recherche dans le catalogue…"):
            try:
                st.session_state.reponse_ex1 = demander(q.strip())
            except APIStatusError:
                st.session_state.reponse_ex1 = ("Le service d'IA a atteint sa limite d'utilisation : "
                                                "réessayez dans quelques minutes.")
    if st.session_state.get("reponse_ex1"):
        st.markdown(f'<div class="sk-mail">{escape(st.session_state.reponse_ex1)}</div>',
                    unsafe_allow_html=True)

    st.markdown("#### Biens disponibles")
    from catalogue import rechercher_biens  # noqa: E402
    biens = rechercher_biens(limite=100).get("biens", [])
    st.dataframe(
        [{"Réf.": b["id"], "Type": b["type"], "Quartier": b["quartier"], "Pièces": b["pieces"],
          "Loyer (F CFA)": b["loyer"], "Meublé": "oui" if b["meuble"] else "non",
          "Description": b["description"]} for b in biens],
        hide_index=True, use_container_width=True,
        column_config={"Loyer (F CFA)": st.column_config.NumberColumn(format="%d")})


# ----------------------------------------------------------------------------- évaluation
def charger_resultats():
    """Avant : le rapport « avant ». Après : les rapports suivants fusionnés (le plus récent l'emporte)."""
    dossier = RACINE / "eval" / "results"
    fichiers = sorted(dossier.glob("*.json"), key=lambda f: json.loads(f.read_text("utf-8"))["date"])
    avant, apres = {}, {}
    for f in fichiers:
        r = json.loads(f.read_text("utf-8"))
        cible = avant if r["label"] == "avant" else apres
        for jeu in ("emails", "ex1"):
            for cas in r.get(jeu) or []:
                cible[cas["id"]] = cas
    return avant, apres


with onglet_eval:
    st.markdown("#### Évaluation automatique (eval/run_eval.py)")
    avant, apres = charger_resultats()

    def score(res, prefixe):
        cas = [c for i, c in res.items() if i.startswith(prefixe)]
        return sum(c["reussi"] for c in cas), len(cas)

    m1, m2, m3, m4 = st.columns(4)
    ea, na = score(avant, "E")
    ep, np_ = score(apres, "E")
    m1.metric("Emails · avant", f"{ea}/{na}")
    m2.metric("Emails · après", f"{ep}/{np_}", delta=f"+{ep - ea}" if ep >= ea else str(ep - ea))
    securite = [c for i, c in apres.items() if i.startswith("E") and c.get("criteres", {}).get("securite", {}).get("ok")]
    m3.metric("Sécurité · après", f"{len(securite)}/{np_}")
    xa, xn = score(apres, "D")
    m4.metric("Questions Ex1 · après", f"{xa}/{xn}")

    lignes = []
    for i in sorted(set(avant) | set(apres)):
        if not i.startswith("E"):
            continue
        a, p = avant.get(i), apres.get(i)
        lignes.append({"Cas": i, "Catégorie": (p or a).get("categorie", ""),
                       "Avant": "✅" if a and a["reussi"] else "❌",
                       "Après": "✅" if p and p["reussi"] else "❌"})
    st.dataframe(lignes, hide_index=True, use_container_width=True)
    st.caption("Après = dernier passage de chaque cas (rapports « apres », « apres-ex1 » et « securite »).")
