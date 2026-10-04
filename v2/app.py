"""Interface web locale V2 (aller + retour) : lancerv2.bat, ou streamlit run app.py depuis ce dossier."""

import streamlit as st

st.set_page_config(page_title="Chercheur de vols V2", layout="wide")

# Streamlit efface les champs d'une page quand on en affiche une autre : on les réaffecte pour les garder
# (les boutons de formulaire et l'éditeur de tableau refusent qu'on leur assigne une valeur)
for cle in list(st.session_state):
    if cle != "editeur_frais" and not cle.startswith("FormSubmitter:"):
        st.session_state[cle] = st.session_state[cle]

st.navigation([
    st.Page("app_pages/recherche.py", title="Recherche client", icon=":material/flight_takeoff:", default=True),
    st.Page("app_pages/aubaines.py", title="Chasse aux aubaines", icon=":material/local_offer:"),
]).run()
