"""Génère les barres de progression (SVG) affichées dans le README.

Mettre à jour les pourcentages ci-dessous, puis :
    python docs/progression/generer_barres.py
"""

from pathlib import Path

DOSSIER = Path(__file__).resolve().parent

# Une couleur par membre de l'équipe
MEMBRES = {
    "role_a": ("Cheikh A. T. Ciss", 75, "#0969da"),
    "role_b": ("Khadidiatou Gueye", 12, "#8250df"),
    "role_c": ("Rokhaya C. Diouf", 33, "#1a7f37"),
    "equipe": ("Projet complet", 52, "#bc4c00"),
}

# Exercices : couleur selon l'avancement
EXERCICES = {
    "ex1": 85, "ex2": 75, "ex3": 100, "ex4": 10, "ex5": 10, "ex6": 60, "qualite": 70,
}


def couleur_etat(pct: int) -> str:
    if pct >= 100:
        return "#1a7f37"   # vert : terminé
    if pct >= 60:
        return "#2da44e"   # vert clair
    if pct >= 30:
        return "#d4a72c"   # jaune
    return "#cf222e"       # rouge


def barre(pct: int, couleur: str, largeur: int = 300, hauteur: int = 20) -> str:
    rempli = round(largeur * max(0, min(pct, 100)) / 100)
    texte_x = rempli - 6 if pct >= 18 else rempli + 6
    ancre = "end" if pct >= 18 else "start"
    couleur_texte = "#ffffff" if pct >= 18 else "#8c959f"
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{largeur}" height="{hauteur}" viewBox="0 0 {largeur} {hauteur}" role="img" aria-label="{pct} %">
  <title>{pct} %</title>
  <rect width="{largeur}" height="{hauteur}" rx="{hauteur // 2}" fill="#8c959f" fill-opacity="0.25"/>
  <rect width="{max(rempli, hauteur) if pct else 0}" height="{hauteur}" rx="{hauteur // 2}" fill="{couleur}"/>
  <text x="{texte_x}" y="{hauteur / 2 + 4.5:.1f}" text-anchor="{ancre}" fill="{couleur_texte}"
        font-family="Segoe UI, Helvetica, Arial, sans-serif" font-size="12" font-weight="600">{pct} %</text>
</svg>
"""


if __name__ == "__main__":
    for nom, (_, pct, couleur) in MEMBRES.items():
        (DOSSIER / f"{nom}.svg").write_text(barre(pct, couleur), encoding="utf-8")
    for nom, pct in EXERCICES.items():
        (DOSSIER / f"{nom}.svg").write_text(barre(pct, couleur_etat(pct), largeur=200, hauteur=16), encoding="utf-8")
    print(f"{len(MEMBRES) + len(EXERCICES)} barres écrites dans {DOSSIER}")
