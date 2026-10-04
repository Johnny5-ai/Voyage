"""Chasse aux aubaines : balaie une liste de destinations et repère les prix nettement sous le prix habituel.

Lancement depuis la page « Chasse aux aubaines », ou planifié avec chasse.bat (Planificateur de tâches Windows).
Chaque balayage est conservé dans data/historique.sqlite pour comparer avec les prix observés auparavant.
"""

import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta

import pandas as pd

import stockage
import vols

REGLAGES = stockage.DOSSIER / "chasse.json"
HISTORIQUE = stockage.DOSSIER / "historique.sqlite"
DEFAUTS = {
    "origines": ["YUL"],
    "destinations": [
        # Soleil
        "CUN", "PUJ", "VRA", "HAV", "SJU", "MBJ", "PVR", "SJD", "SJO", "LIR", "GUA", "CTG",
        # États-Unis
        "FLL", "MCO", "MIA", "TPA", "LAS", "LAX", "SFO", "NYC",
        # Europe et Afrique du Nord
        "PAR", "LON", "LIS", "OPO", "MAD", "BCN", "ROM", "ATH", "DUB", "AMS", "BRU", "NCE", "CMN",
        # Plus loin
        "TYO", "BKK", "LIM",
    ],
    "nuits": 7,
    "debut_jours": 21,
    "horizon_jours": 150,
    "pas_jours": 10,
}


def reglages() -> dict:
    return {**DEFAUTS, **stockage.lire(REGLAGES)}


def combinaisons(r: dict) -> list[vols.Combo]:
    premier = date.today() + timedelta(days=r["debut_jours"])
    departs = [premier + timedelta(days=i) for i in range(0, r["horizon_jours"] - r["debut_jours"] + 1, r["pas_jours"])]
    return [vols.Combo(o, d, j, j + timedelta(days=r["nuits"]))
            for o in r["origines"] for d in r["destinations"] for j in departs]


def _connexion() -> sqlite3.Connection:
    stockage.DOSSIER.mkdir(exist_ok=True)
    cnx = sqlite3.connect(HISTORIQUE)
    cnx.executescript("""
        CREATE TABLE IF NOT EXISTS balayages (id TEXT PRIMARY KEY, fin TEXT, total INTEGER, echecs INTEGER);
        CREATE TABLE IF NOT EXISTS observations (
            balayage TEXT, origine TEXT, destination TEXT, depart TEXT, retour TEXT, nuits INTEGER,
            prix REAL, typique REAL, typique_bas REAL, typique_haut REAL,
            compagnies TEXT, escales INTEGER, duree_min INTEGER, lien TEXT);
        CREATE INDEX IF NOT EXISTS obs_route ON observations (origine, destination, nuits);
    """)
    return cnx


def balayer(progression=None) -> tuple[str, int, dict]:
    """Lance un balayage complet. `progression(fait, total)` est appelé après chaque recherche.

    Renvoie (identifiant du balayage, nombre d'échecs, causes d'échec).
    """
    combos = combinaisons(reglages())
    identifiant = datetime.now().strftime("%Y-%m-%d %H:%M")
    lignes, causes = [], {}
    for i, (c, options, err) in enumerate(vols.rechercher(combos, vols.Criteres(), sans_cache=True), 1):
        if err:
            causes[err] = causes.get(err, 0) + 1
        if options:
            o = min(options, key=lambda o: o["prix"])
            lignes.append((identifiant, c.origine, c.destination, c.depart.isoformat(), c.retour.isoformat(), c.nuits,
                           o["prix"], o["typique"], o["typique_bas"], o["typique_haut"],
                           o["compagnies"], o["escales"], o["duree_min"], o["lien"]))
        if progression:
            progression(i, len(combos))
    echecs = sum(causes.values())
    with closing(_connexion()) as cnx, cnx:
        cnx.execute("INSERT OR REPLACE INTO balayages VALUES (?, ?, ?, ?)",
                    (identifiant, datetime.now().strftime("%Y-%m-%d %H:%M"), len(combos), echecs))
        cnx.executemany("INSERT INTO observations VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", lignes)
    return identifiant, echecs, causes


def balayages() -> pd.DataFrame:
    if not HISTORIQUE.exists():
        return pd.DataFrame(columns=["id", "fin", "total", "echecs"])
    with closing(_connexion()) as cnx:
        return pd.read_sql("SELECT * FROM balayages ORDER BY id DESC", cnx)


def aubaines(balayage: str) -> pd.DataFrame:
    """Observations d'un balayage, avec leur écart au prix habituel de Google et à nos propres relevés passés."""
    with closing(_connexion()) as cnx:
        obs = pd.read_sql("SELECT * FROM observations WHERE balayage = ?", cnx, params=(balayage,))
        passe = pd.read_sql("""
            SELECT origine, destination, nuits, MIN(prix) AS plus_bas_observe, COUNT(DISTINCT balayage) AS releves
            FROM observations WHERE balayage < ? GROUP BY origine, destination, nuits""", cnx, params=(balayage,))
    if obs.empty:
        return obs
    obs = obs.merge(passe, on=["origine", "destination", "nuits"], how="left")
    obs["ecart"] = (obs["prix"] - obs["typique"]) / obs["typique"]
    obs["sous_fourchette"] = obs["prix"] < obs["typique_bas"]
    for col in ("depart", "retour"):
        obs[col] = pd.to_datetime(obs[col]).dt.date
    return obs.sort_values("ecart")


if __name__ == "__main__":
    ident, n_echecs, raisons = balayer()
    print(f"Balayage {ident} terminé, {n_echecs} échec(s). {raisons or ''}")
    meilleures = aubaines(ident).dropna(subset=["ecart"]).drop_duplicates("destination").head(10)
    for _, a in meilleures.iterrows():
        print(f"{a['origine']} → {a['destination']} {a['depart']} → {a['retour']} : {a['prix']:.0f} $ "
              f"({a['ecart']:+.0%} vs habituel {a['typique']:.0f} $)")
