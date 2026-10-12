"""Page « Chasse aux aubaines » : destinations nettement sous leur prix habituel, sur les prochains mois."""

import re
from pathlib import Path

import pandas as pd
import streamlit as st

import chasse
import stockage

JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
MOIS = ["jan", "fév", "mar", "avr", "mai", "juin", "juil", "août", "sep", "oct", "nov", "déc"]


def jour(d) -> str:
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]} {d.year}"


def codes(texte: str) -> list[str]:
    return [c.strip().upper() for c in re.split(r"[,;\s]+", texte) if c.strip()]


st.title("Chasse aux aubaines")
st.caption("Balaie une liste de destinations sur plusieurs mois et repère les prix nettement sous le prix habituel "
           "que Google observe pour ces dates. Prix aller-retour pour 1 adulte, classe économique, sans bagage en soute.")

r = chasse.reglages()
with st.sidebar:
    with st.form("reglages_chasse"):
        st.header("Réglages du balayage")
        origines = st.text_input("Départ", ", ".join(r["origines"]))
        destinations = st.text_area("Destinations", ", ".join(r["destinations"]), height=180,
                                    help="Codes d'aéroport (CUN) ou de ville (PAR, LON, TYO), séparés par des virgules.")
        nuits = st.number_input("Durée du séjour (nuits)", 1, 30, r["nuits"])
        debut = st.number_input("Premier départ dans (jours)", 1, 300, r["debut_jours"])
        horizon = st.number_input("Dernier départ dans (jours)", 7, 330, r["horizon_jours"])
        pas = st.number_input("Un départ tous les (jours)", 1, 30, r["pas_jours"])
        enregistrer = st.form_submit_button("Enregistrer les réglages", width="stretch")
    if enregistrer:
        nouveaux = {"origines": codes(origines), "destinations": codes(destinations), "nuits": nuits,
                    "debut_jours": debut, "horizon_jours": max(horizon, debut), "pas_jours": pas}
        invalides = [c for c in nouveaux["origines"] + nouveaux["destinations"] if not re.fullmatch(r"[A-Z]{3}", c)]
        if invalides or not nouveaux["origines"] or not nouveaux["destinations"]:
            st.error(f"Codes invalides : {', '.join(invalides) or 'liste vide'}. 3 lettres attendues.")
        else:
            stockage.ecrire(chasse.REGLAGES, nouveaux)
            st.rerun()
    combos = chasse.combinaisons(r)
    st.caption(f"{len(r['destinations'])} destinations · {len(combos)} recherches par balayage "
               f"(~{max(1, round(len(combos) * 0.6 / 60))} min)")
    lancer = st.button("Lancer un balayage maintenant", type="primary", width="stretch")

if lancer:
    barre = st.progress(0.0, "Balayage en cours…")
    ident, n_echecs, causes = chasse.balayer(lambda i, n: barre.progress(i / n, f"Balayage en cours… {i}/{n}"))
    barre.empty()
    if n_echecs:
        st.warning(f"{n_echecs} recherche(s) sur {len(combos)} ont échoué : "
                   + ", ".join(f"{c} ({k})" for c, k in causes.items()))
    st.session_state["balayage"] = ident

historique = chasse.balayages()
if historique.empty:
    st.info("Aucun balayage pour l'instant. Vérifie les réglages à gauche, puis lance un premier balayage.")
    st.stop()

ids = list(historique["id"])
if st.session_state.get("balayage") not in ids:
    st.session_state["balayage"] = ids[0]
st.session_state.setdefault("vue_aubaines", "Meilleure date par destination")
st.session_state.setdefault("seuil_aubaines", 15)
with st.container(horizontal=True, vertical_alignment="bottom"):
    st.selectbox("Balayage", ids, key="balayage", width=220,
                 format_func=lambda i: f"{i} (le plus récent)" if i == ids[0] else i)
    vue_choisie = st.segmented_control("Afficher", ["Meilleure date par destination", "Toutes les dates"], key="vue_aubaines")
    seuil = st.slider("Au moins … sous le prix habituel", 0, 60, step=5, format="%d %%", key="seuil_aubaines", width=260)

donnees = chasse.aubaines(st.session_state["balayage"]).dropna(subset=["ecart"])
if donnees.empty:
    st.warning("Ce balayage ne contient aucun prix exploitable (Google n'a pas donné de prix habituel).")
    st.stop()
retenues = donnees[donnees["ecart"] <= -seuil / 100]
if vue_choisie != "Toutes les dates":
    retenues = retenues.drop_duplicates("destination")

sous_fourchette = donnees[donnees["sous_fourchette"]]
with st.container(horizontal=True):
    st.metric("Destinations balayées", donnees["destination"].nunique(), border=True)
    st.metric("Prix sous la fourchette habituelle", f"{len(sous_fourchette)} (sur {sous_fourchette['destination'].nunique()} destinations)",
              border=True)
    meilleure = donnees.iloc[0]
    st.metric("Plus gros écart", f"{meilleure['ecart']:.0%}", border=True,
              delta=f"{meilleure['destination']} à {meilleure['prix']:.0f} \\$ au lieu de {meilleure['typique']:.0f} \\$",
              delta_color="off", delta_arrow="off")

if retenues.empty:
    st.info(f"Aucun prix à au moins {seuil} % sous le prix habituel dans ce balayage. Baisse le seuil pour voir les meilleurs écarts.")
else:
    st.dataframe(
        pd.DataFrame({
            "Route": retenues["origine"] + " → " + retenues["destination"],
            "Départ": retenues["depart"].map(jour),
            "Retour": retenues["retour"].map(jour),
            "Prix ($)": retenues["prix"],
            "vs habituel": retenues["ecart"] * 100,
            "Prix habituel ($)": retenues["typique"],
            "Fourchette habituelle": retenues["typique_bas"].map("{:.0f}".format) + "–" + retenues["typique_haut"].map("{:.0f} $".format),
            "Sous la fourchette": retenues["sous_fourchette"],
            "Plus bas de mes balayages ($)": retenues["plus_bas_observe"],
            "Compagnies": retenues["compagnies"],
            "Escales": retenues["escales"],
            "Durée aller": retenues["duree_min"].map(lambda m: f"{m // 60} h {m % 60:02d}"),
            "Lien": retenues["lien"],
        }),
        hide_index=True, width="stretch", placeholder="—",
        column_config={
            "Prix ($)": st.column_config.NumberColumn(format="%d $"),
            "vs habituel": st.column_config.NumberColumn(format="%+.0f %%"),
            "Prix habituel ($)": st.column_config.NumberColumn(format="%d $"),
            "Plus bas de mes balayages ($)": st.column_config.NumberColumn(
                format="%d $", help="Prix le plus bas relevé pour cette route et cette durée lors des balayages précédents"),
            "Lien": st.column_config.LinkColumn("Lien", display_text="Choisir le retour",
                                                help="Ouvre Google Flights avec ce vol aller déjà sélectionné"),
        },
    )
    st.caption("Le lien ouvre Google Flights avec le vol aller trouvé déjà sélectionné : il reste à choisir le retour. "
               "Les liens d'un ancien balayage peuvent mener à « itinéraire plus disponible ». "
               "Pour en faire une offre client, ouvre « Recherche client » avec la destination et une plage autour de la date : "
               "elle donnera les horaires, les bagages et le prix exact pour tout le groupe.")

with st.expander("Planifier un balayage automatique chaque jour"):
    bat = Path(chasse.__file__).with_name("chasse.bat")
    st.markdown("Le Planificateur de tâches Windows peut lancer `chasse.bat` chaque matin, même sans ouvrir l'application. "
                "Les résultats apparaissent ici au prochain affichage, et le journal s'écrit dans `data\\chasse.log`. "
                "Commande à coller dans une invite de commandes :")
    st.code(f'schtasks /Create /SC DAILY /ST 06:00 /TN "Chasse aux aubaines" /TR "\\"{bat}\\""', language="bat")
    st.caption("Pour l'arrêter : schtasks /Delete /TN \"Chasse aux aubaines\"")
