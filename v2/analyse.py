"""Calculs sur les résultats (sans interface) : frais de bagages, score, choix des vols à approfondir."""

import pandas as pd

# Colonnes selon le tableau : vols aller seuls (étape 1) ou itinéraires complets (étape 2)
ALLERS = {"compagnies": ["compagnies"], "escales": ["escales"], "duree": ["duree_min"], "escale_max": ["escale_max_min"]}
ITINERAIRES = {
    "compagnies": ["aller_compagnies", "retour_compagnies"],
    "escales": ["aller_escales", "retour_escales"],
    "duree": ["aller_duree_min", "retour_duree_min"],
    "escale_max": ["aller_escale_max_min", "retour_escale_max_min"],
}


def noms_compagnies(ligne, colonnes) -> set[str]:
    return {n.strip() for col in colonnes for n in ligne[col].split(",") if n.strip()}


def sans_estimation(df: pd.DataFrame, cols: dict) -> set[str]:
    """Compagnies des options dont Google n'a pas estimé les frais de bagages."""
    manquants = df[df["bagages"].isna()]
    return set().union(*(noms_compagnies(l, cols["compagnies"]) for _, l in manquants.iterrows()))


def appliquer_frais(df: pd.DataFrame, cols: dict, frais: dict, nb_bagages: int) -> pd.DataFrame:
    """Estimation Google si disponible, sinon frais du tableau personnel (pour 1 bagage, × nombre de bagages).

    Ajoute la colonne source_frais et inclut les frais du tableau dans le prix.
    """
    df = df.copy()
    df[["prix", "bagages"]] = df[["prix", "bagages"]].astype(float)
    df["source_frais"] = ""
    if not nb_bagages or df.empty:
        return df
    tarifs = {k.lower(): v for k, v in frais.items()}

    def tarif(ligne):
        connus = [tarifs[n.lower()] for n in noms_compagnies(ligne, cols["compagnies"]) if n.lower() in tarifs]
        return max(connus) * nb_bagages if connus else None

    df.loc[df["bagages"].notna(), "source_frais"] = "Google"
    manquants = df["bagages"].isna()
    estimes = df[manquants].apply(tarif, axis=1).astype(float) if manquants.any() else pd.Series(dtype=float)
    df.loc[manquants, "bagages"] = estimes
    df.loc[manquants, "prix"] += estimes.fillna(0)
    df.loc[manquants & df["bagages"].notna(), "source_frais"] = "Mon tableau"
    return df


def ajouter_scores(df: pd.DataFrame, cols: dict, penalite_escale: float, valeur_heure: float) -> pd.DataFrame:
    df = df.copy()
    df["escales_total"] = df[cols["escales"]].sum(axis=1)
    df["duree_totale"] = df[cols["duree"]].sum(axis=1)
    df["escale_max"] = df[cols["escale_max"]].max(axis=1)
    df["compromis"] = df["prix"] + penalite_escale * df["escales_total"] + valeur_heure * df["duree_totale"] / 60
    return df


def filtrer_escale(df: pd.DataFrame, escale_max_min: int | None) -> pd.DataFrame:
    return df if escale_max_min is None else df[df["escale_max"] <= escale_max_min]


def choisir_candidats(allers: pd.DataFrame, nombre: int) -> pd.DataFrame:
    """Vols aller à approfondir : les moins chers et les meilleurs compromis (tableau déjà noté)."""
    possibles = allers[allers["segments"].notna()].drop_duplicates(["route", "depart", "retour", "vols"])
    choisis = pd.concat([possibles.nsmallest(nombre, "prix"), possibles.nsmallest(nombre, "compromis")])
    return choisis[~choisis.index.duplicated()]
