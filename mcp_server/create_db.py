"""Crée le catalogue SQLite de Sama Kër Immobilier (30 biens fictifs).

Usage : python mcp_server/create_db.py

Le script est idempotent : il supprime la base existante et la recrée à
l'identique, pour que les tests et l'évaluation soient reproductibles.
Au Sénégal, « F3 » désigne un logement de 3 pièces (salon + 2 chambres) :
la colonne `pieces` porte ce chiffre.
"""

import sqlite3
from collections import Counter
from pathlib import Path

DB_PATH = Path(__file__).parent / "catalogue.db"

SCHEMA = """
CREATE TABLE biens (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL CHECK (type IN ('appartement', 'studio', 'villa', 'chambre')),
    quartier    TEXT NOT NULL,
    loyer       INTEGER NOT NULL CHECK (loyer > 0),          -- F CFA par mois
    pieces      INTEGER NOT NULL CHECK (pieces >= 1),        -- F3 = 3 pièces
    adresse     TEXT NOT NULL,
    disponible  INTEGER NOT NULL DEFAULT 1 CHECK (disponible IN (0, 1)),
    meuble      INTEGER NOT NULL DEFAULT 0 CHECK (meuble IN (0, 1)),
    description TEXT
);
"""

# Données fixes (pas d'aléatoire) : l'évaluation de l'exercice 6 en dépend.
# Cas de test prévus :
#   - F3 à Mermoz <= 400 000 F disponibles : SK-001, SK-003
#   - F3 à Mermoz au-dessus du budget      : SK-004 (450 000 F)
#   - biens non disponibles                : SK-005, SK-013, SK-023
#   - quartiers absents (ex. Saly, Thiès)  : aucun bien -> « aucun résultat »
BIENS = [
    # (id, type, quartier, loyer, pieces, adresse, disponible, meuble, description)
    ("SK-001", "appartement", "Mermoz", 350000, 3, "Rue MZ-12, Mermoz", 1, 0, "F3 lumineux au 2e étage, proche VDN"),
    ("SK-002", "studio", "Point E", 150000, 1, "Rue PE-8, Point E", 1, 1, "Studio meublé près de l'UCAD"),
    ("SK-003", "appartement", "Mermoz", 380000, 3, "Rue MZ-45, Mermoz", 1, 1, "F3 meublé avec balcon, résidence sécurisée"),
    ("SK-004", "appartement", "Mermoz", 450000, 3, "Rue MZ-3, Mermoz", 1, 0, "F3 standing avec parking, proche de la corniche"),
    ("SK-005", "appartement", "Mermoz", 320000, 3, "Rue MZ-27, Mermoz", 0, 0, "F3 en rez-de-chaussée avec petite cour"),
    ("SK-006", "appartement", "Mermoz", 550000, 4, "Rue MZ-60, Mermoz", 1, 0, "F4 familial, 3 chambres dont une suite parentale"),
    ("SK-007", "studio", "Mermoz", 180000, 1, "Rue MZ-18, Mermoz", 1, 1, "Studio meublé, idéal pour un jeune actif"),
    ("SK-008", "villa", "Almadies", 2500000, 6, "Route des Almadies, lot 14", 1, 0, "Villa avec piscine et grand jardin"),
    ("SK-009", "appartement", "Almadies", 900000, 4, "Rue AL-5, Almadies", 1, 1, "F4 meublé avec vue sur la mer"),
    ("SK-010", "appartement", "Almadies", 700000, 3, "Rue AL-22, Almadies", 1, 0, "F3 neuf dans une résidence avec gardien"),
    ("SK-011", "studio", "Ngor", 200000, 1, "Rue NG-9, Ngor Village", 1, 1, "Studio meublé à cinq minutes de la plage"),
    ("SK-012", "appartement", "Ngor", 500000, 3, "Rue NG-31, Ngor", 1, 0, "F3 avec grande terrasse"),
    ("SK-013", "villa", "Ngor", 1800000, 5, "Rue NG-2, Ngor", 0, 0, "Villa de 4 chambres avec jardin"),
    ("SK-014", "appartement", "Ouakam", 220000, 2, "Rue OK-14, Ouakam", 1, 0, "F2 proche de la mosquée de la Divinité"),
    ("SK-015", "appartement", "Ouakam", 300000, 3, "Rue OK-40, Ouakam", 1, 0, "F3 au 1er étage dans un quartier calme"),
    ("SK-016", "chambre", "Ouakam", 75000, 1, "Rue OK-7, Ouakam", 1, 1, "Chambre meublée avec salle d'eau, en colocation"),
    ("SK-017", "appartement", "Point E", 280000, 2, "Rue PE-21, Point E", 1, 0, "F2 proche des commerces"),
    ("SK-018", "appartement", "Point E", 420000, 3, "Rue PE-3, Point E", 1, 0, "F3 rénové avec cuisine équipée"),
    ("SK-019", "appartement", "Plateau", 600000, 3, "Rue PL-11, Plateau", 1, 0, "F3 en centre-ville, proche des ministères"),
    ("SK-020", "appartement", "Plateau", 350000, 2, "Rue PL-30, Plateau", 1, 1, "F2 meublé au cœur du Plateau"),
    ("SK-021", "appartement", "Sacré-Cœur", 280000, 3, "Sacré-Cœur 3, villa 112", 1, 0, "F3 avec salon spacieux"),
    ("SK-022", "appartement", "Sacré-Cœur", 380000, 4, "Sacré-Cœur 2, villa 45", 1, 0, "F4 au 2e étage, bien ventilé"),
    ("SK-023", "appartement", "Sacré-Cœur", 200000, 2, "Sacré-Cœur 1, villa 8", 0, 0, "F2 simple et bien situé"),
    ("SK-024", "appartement", "Liberté 6", 250000, 3, "Liberté 6 Extension, villa 230", 1, 0, "F3 proche du rond-point"),
    ("SK-025", "chambre", "Liberté 6", 60000, 1, "Liberté 6, villa 77", 1, 1, "Chambre meublée pour étudiant"),
    ("SK-026", "appartement", "Liberté 6", 320000, 4, "Liberté 6, villa 150", 1, 0, "F4 avec deux salles de bain"),
    ("SK-027", "appartement", "Yoff", 230000, 3, "Rue YF-5, Yoff Tonghor", 1, 0, "F3 à dix minutes de la plage de Yoff"),
    ("SK-028", "villa", "Yoff", 900000, 5, "Cité YF-12, Yoff", 1, 0, "Villa avec garage et cour"),
    ("SK-029", "appartement", "Fann", 750000, 4, "Rue FN-4, Fann Résidence", 1, 0, "F4 standing dans un quartier résidentiel"),
    ("SK-030", "studio", "Fann", 170000, 1, "Rue FN-19, Fann Hock", 1, 1, "Studio meublé proche de l'hôpital"),
]


def main():
    DB_PATH.unlink(missing_ok=True)          # on repart de zéro à chaque lancement
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    con.executemany("INSERT INTO biens VALUES (?,?,?,?,?,?,?,?,?)", BIENS)
    con.commit()
    con.close()

    print(f"{len(BIENS)} biens créés dans {DB_PATH}")
    for quartier, n in sorted(Counter(b[2] for b in BIENS).items()):
        print(f"  {quartier:<12} {n}")

if __name__ == "__main__":
    main()