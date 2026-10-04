"""Interface web locale : streamlit run app.py"""

import re
from collections import Counter
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import stockage
import vols

JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
MOIS = ["jan", "fév", "mar", "avr", "mai", "juin", "juil", "août", "sep", "oct", "nov", "déc"]
CLASSES = {"Économique": "economy", "Économique premium": "premium-economy", "Affaires": "business", "Première": "first"}
ESCALES = {"Peu importe": None, "Direct seulement": 0, "1 escale max": 1, "2 escales max": 2}


def jour(d: date) -> str:
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


def duree(minutes: int) -> str:
    return f"{minutes // 60} h {minutes % 60:02d}"


def montant(v: float) -> str:
    return f"{v:,.0f} $".replace(",", " ")


def codes(texte: str) -> list[str]:
    return [c.strip().upper() for c in re.split(r"[,;\s]+", texte) if c.strip()]


def couleur(v, bas, haut):
    if pd.isna(v):
        return ""
    t = 0 if haut == bas else (v - bas) / (haut - bas)
    r, g, b = (int(a + (z - a) * t) for a, z in zip((198, 239, 206), (255, 199, 206)))
    return f"background-color: rgb({r},{g},{b}); color: #1a1a1a"


demain = date.today() + timedelta(days=1)
DEFAUTS = {
    "origines": "YUL", "destinations": "CDG",
    "plage": (demain + timedelta(days=30), demain + timedelta(days=44)),
    "nuits": (7, 10), "adultes": 1, "enfants": 0, "classe": "Économique", "escales": "Peu importe",
    "bagages": 0, "penalite_escale": 75, "valeur_heure": 20, "max_recherches": 120,
}
for cle, valeur in DEFAUTS.items():
    st.session_state.setdefault(cle, valeur)
st.session_state.setdefault("nom", "")
# Critères qui influencent les prix (les réglages de classement n'en font pas partie)
CLES_PRIX = ["origines", "destinations", "plage", "nuits", "adultes", "enfants", "classe", "escales", "bagages"]


def criteres_prix(criteres: dict) -> dict:
    return {k: criteres[k] for k in CLES_PRIX}


def charger():
    s = stockage.lire(stockage.RECHERCHES)[st.session_state.choix]
    criteres = s["criteres"]
    debut, fin = (date.fromisoformat(d) for d in criteres["plage"])
    criteres["plage"] = (max(debut, demain), max(fin, demain))
    criteres["nuits"] = tuple(criteres["nuits"])
    st.session_state.update(criteres)
    resultats = pd.DataFrame(s["resultats"])
    if not resultats.empty:
        for col in ("depart", "retour"):
            resultats[col] = pd.to_datetime(resultats[col]).dt.date
    st.session_state.update(
        resultats=resultats, echecs=tuple(s["echecs"]), criteres_recherche=criteres,
        reference=(s["meilleur_prix"], s["date"], criteres_prix(criteres)), nom=st.session_state.choix,
    )


def supprimer():
    nom = st.session_state.pop("choix")
    stockage.modifier(stockage.RECHERCHES, lambda sauvegardes: sauvegardes.pop(nom, None))


st.set_page_config(page_title="Chercheur de vols", layout="wide")
st.title("Chercheur de vols flexibles")
st.caption("Compare automatiquement toutes les combinaisons de dates sur Google Flights. Prix aller-retour, total pour tous les passagers.")

with st.sidebar:
    sauvegardes = stockage.lire(stockage.RECHERCHES)
    if sauvegardes:
        with st.expander("Recherches sauvegardées", expanded=True):
            st.selectbox("Recherche", list(sauvegardes), key="choix", label_visibility="collapsed",
                         format_func=lambda n: f"{n} ({sauvegardes[n]['date']})")
            col1, col2 = st.columns(2)
            col1.button("Charger", on_click=charger, width="stretch")
            col2.button("Supprimer", on_click=supprimer, width="stretch")

    st.header("Demande du client")
    st.text_input("Départ (codes IATA)", key="origines", help="Ex. YUL, ou plusieurs : YUL, YQB")
    st.text_input("Destination(s)", key="destinations", help="Aéroport (CDG) ou ville (PAR). Plusieurs : LIS, OPO, MAD")
    st.date_input("Départ possible entre", key="plage", min_value=demain, format="YYYY-MM-DD")
    st.slider("Durée du séjour (nuits)", 1, 30, key="nuits")

    col1, col2 = st.columns(2)
    col1.number_input("Adultes", 1, 9, key="adultes")
    col2.number_input("Enfants", 0, 8, key="enfants")
    st.selectbox("Classe", list(CLASSES), key="classe")
    st.selectbox("Escales", list(ESCALES), key="escales")
    st.selectbox("Bagages en soute (total du groupe)", [0, 1, 2], key="bagages",
                 help="Frais estimés par Google quand il les connaît, sinon ceux de ton tableau de frais par compagnie.")

    with st.expander("Classement « meilleur compromis »"):
        st.number_input("Coût d'une escale ($)", 0, 1000, step=25, key="penalite_escale")
        st.number_input("Coût d'une heure de trajet ($)", 0, 200, step=5, key="valeur_heure")
    st.number_input("Nombre max de recherches", 10, 500, step=10, key="max_recherches")

    lancer = st.button("Rechercher", type="primary", width="stretch")

while stockage.alertes:
    st.warning(stockage.alertes.pop(0))

ss = st.session_state
origines, destinations = codes(ss.origines), codes(ss.destinations)
erreurs = [c for c in origines + destinations if not re.fullmatch(r"[A-Z]{3}", c)]
if erreurs:
    st.error(f"Codes invalides : {', '.join(erreurs)} (3 lettres attendues, ex. YUL).")
    st.stop()
if len(ss.plage) != 2:
    st.info("Choisis la date de fin de la plage de départ.")
    st.stop()

combos = vols.combinaisons(origines, destinations, ss.plage[0], ss.plage[1], *ss.nuits)
st.sidebar.caption(f"{len(combos)} combinaisons à vérifier (~{max(1, round(len(combos) * 0.6 * (2 if ss.bagages else 1) / 60))} min)")

if lancer:
    if len(combos) > ss.max_recherches:
        st.error(f"{len(combos)} combinaisons dépassent la limite de {ss.max_recherches}. Réduis la plage de dates, la fourchette de durée ou le nombre de destinations.")
        st.stop()
    criteres = vols.Criteres(
        adultes=ss.adultes, enfants=ss.enfants, classe=CLASSES[ss.classe],
        escales_max=ESCALES[ss.escales], bagages_soute=ss.bagages,
    )
    # Relancer une recherche sauvegardée sert à suivre les prix : on ignore alors le cache
    reference = ss.get("reference")
    sans_cache = bool(reference) and reference[2] == criteres_prix(ss)
    barre = st.progress(0.0, "Recherche en cours…")
    lignes, causes = [], Counter()
    for i, (c, options, err) in enumerate(vols.rechercher(combos, criteres, sans_cache), 1):
        if err:
            causes[err] += 1
        for o in options:
            lignes.append({"route": f"{c.origine} → {c.destination}", "depart": c.depart, "retour": c.retour, "nuits": c.nuits, **o})
        barre.progress(i / len(combos), f"Recherche en cours… {i}/{len(combos)}")
    barre.empty()
    ss.resultats = pd.DataFrame(lignes)
    ss.echecs = (causes.total(), len(combos), dict(causes))
    ss.criteres_recherche = {k: ss[k] for k in DEFAUTS}

if "resultats" not in ss:
    st.info("Remplis la demande du client à gauche, puis lance la recherche.")
    st.stop()

df = ss.resultats.copy()
echecs, total, *detail = ss.echecs
if echecs:
    raisons = "\n".join(f"- {cause} : {n}" for cause, n in sorted((detail[0] if detail else {}).items(), key=lambda x: -x[1]))
    st.warning(f"{echecs} recherche(s) sur {total} ont échoué. Relance dans quelques minutes pour compléter.\n\n{raisons}")
if df.empty:
    st.error("Aucun vol trouvé pour ces critères.")
    st.stop()

# Frais de bagages : estimation Google si disponible, sinon tableau personnel par compagnie
nb_bagages = ss.criteres_recherche["bagages"]
df[["prix", "bagages"]] = df[["prix", "bagages"]].astype(float)
df["source_frais"] = ""
frais = stockage.lire(stockage.FRAIS)
if nb_bagages:
    sans_google = {n.strip() for comps in df.loc[df["bagages"].isna(), "compagnies"] for n in comps.split(",")}
    connues = {k.lower() for k in frais}
    noms = sorted(set(frais) | {n for n in sans_google if n.lower() not in connues}, key=str.lower)
    with st.expander("Mes frais de bagages par compagnie", expanded=bool(sans_google - set(frais))):
        st.caption("Frais pour 1 bagage en soute, aller-retour. Utilisés seulement quand Google n'estime pas les frais, "
                   "et multipliés par le nombre de bagages demandé. Les compagnies sans estimation de cette recherche sont listées.")
        edite = st.data_editor(
            pd.DataFrame({"Compagnie": noms, "Frais ($)": [frais.get(n) for n in noms]}, columns=["Compagnie", "Frais ($)"]),
            num_rows="dynamic", hide_index=True, key="editeur_frais",
            column_config={"Frais ($)": st.column_config.NumberColumn(min_value=0, format="%d $")},
        )
        nouveaux = {r["Compagnie"].strip(): float(r["Frais ($)"]) for r in edite.to_dict("records")
                    if isinstance(r["Compagnie"], str) and r["Compagnie"].strip() and pd.notna(r["Frais ($)"])}
        if nouveaux != frais:
            stockage.ecrire(stockage.FRAIS, nouveaux)
            frais = nouveaux

    tarifs = {k.lower(): v for k, v in frais.items()}

    def tarif(compagnies: str):
        connus = [tarifs[n.strip().lower()] for n in compagnies.split(",") if n.strip().lower() in tarifs]
        return max(connus) * nb_bagages if connus else None

    df.loc[df["bagages"].notna(), "source_frais"] = "Google"
    manquants = df["bagages"].isna()
    estimes = df.loc[manquants, "compagnies"].map(tarif).astype(float)
    df.loc[manquants, "bagages"] = estimes
    df.loc[manquants, "prix"] += estimes.fillna(0)
    df.loc[manquants & df["bagages"].notna(), "source_frais"] = "Mon tableau"

df["compromis"] = df["prix"] + ss.penalite_escale * df["escales"] + ss.valeur_heure * df["duree_min"] / 60

candidats = df
if nb_bagages and df["bagages"].notna().any() and df["bagages"].isna().any():
    candidats = df[df["bagages"].notna()]
    inconnues = ", ".join(sorted({n.strip() for comps in df.loc[df["bagages"].isna(), "compagnies"] for n in comps.split(",")}))
    st.info(f"Options sans frais de bagages connus exclues des cartes ({inconnues}). Ajoute leurs frais dans le tableau pour les comparer.")

moins_cher = candidats.loc[candidats["prix"].idxmin()]
compromis = candidats.loc[candidats["compromis"].idxmin()]
rapide = candidats.loc[candidats["duree_min"].idxmin()]
reference = ss.get("reference")
if reference and reference[2] != criteres_prix(ss.criteres_recherche):
    reference = None
cartes = st.columns(3)
for i, (col, titre, r) in enumerate(zip(cartes, ["Le moins cher", "Meilleur compromis", "Le plus rapide"], [moins_cher, compromis, rapide])):
    with col.container(border=True):
        st.markdown(f"**{titre}**")
        delta = f"{r['prix'] - reference[0]:+,.0f} $ depuis le {reference[1]}".replace(",", " ") if i == 0 and reference else None
        st.metric(r["route"], montant(r["prix"]), delta=delta, delta_color="inverse")
        if nb_bagages:
            st.caption("bagages : non estimés" if pd.isna(r["bagages"]) else f"dont {montant(r['bagages'])} de bagages ({r['source_frais']})")
        st.write(f"{jour(r['depart'])} → {jour(r['retour'])} ({r['nuits']} nuits)")
        st.write(f"{r['compagnies']} · {'direct' if r['escales'] == 0 else str(r['escales']) + ' escale(s) via ' + r['via']} · {duree(r['duree_min'])}")
        st.link_button("Voir sur Google Flights", r["lien"])

with st.container(border=True):
    col1, col2 = st.columns([3, 1], vertical_alignment="bottom")
    col1.text_input("Sauvegarder cette recherche sous le nom", key="nom", placeholder="Ex. Famille Tremblay – Guatemala janvier")
    nom = ss.nom.strip()
    existe = nom in stockage.lire(stockage.RECHERCHES)
    remplacer = existe and st.checkbox(f"Une recherche « {nom} » existe déjà : la remplacer")
    if col2.button("Sauvegarder", width="stretch", disabled=not nom or (existe and not remplacer)):
        maintenant = datetime.now().strftime("%Y-%m-%d %H:%M")
        entree = {
            "date": maintenant, "criteres": ss.criteres_recherche, "echecs": ss.echecs,
            "meilleur_prix": float(moins_cher["prix"]), "resultats": ss.resultats.to_dict("records"),
        }
        stockage.modifier(stockage.RECHERCHES, lambda sauvegardes: sauvegardes.update({nom: entree}))
        ss.reference = (float(moins_cher["prix"]), maintenant, criteres_prix(ss.criteres_recherche))
        st.toast(f"Recherche « {nom} » sauvegardée.")

st.subheader("Grille des prix les plus bas")
st.caption("Lignes : date de départ. Colonnes : nombre de nuits. Vert = moins cher.")
for onglet, (route, g) in zip(st.tabs(list(df["route"].unique())), df.groupby("route", sort=False)):
    with onglet:
        grille = g.pivot_table(index="depart", columns="nuits", values="prix", aggfunc="min")
        grille.index = [jour(d) for d in grille.index]
        grille.columns = [f"{n} n" for n in grille.columns]
        bas, haut = grille.min().min(), grille.max().max()
        st.dataframe(grille.style.map(couleur, bas=bas, haut=haut).format("{:.0f} $", na_rep="—"), width="stretch")

st.subheader("Toutes les options")
tri = st.radio("Trier par", ["Prix", "Meilleur compromis", "Durée"], horizontal=True)
colonne_tri = {"Prix": "prix", "Meilleur compromis": "compromis", "Durée": "duree_min"}[tri]
vue = df.sort_values([colonne_tri, "prix"]).drop_duplicates(["route", "depart", "retour", "vols"])
tableau = pd.DataFrame({
    "Route": vue["route"],
    "Départ": vue["depart"].map(jour),
    "Retour": vue["retour"].map(jour),
    "Nuits": vue["nuits"],
    "Prix ($)": vue["prix"],
    "Dont bagages ($)": vue["bagages"],
    "Source frais": vue["source_frais"],
    "Compagnie(s)": vue["compagnies"],
    "Vols (aller)": vue["vols"],
    "Escales": vue["escales"],
    "Via": vue["via"],
    "Horaire aller": vue["heure_depart"] + " → " + vue["heure_arrivee"] + vue["arrivee_lendemain"].map({True: " (+1)", False: ""}),
    "Durée aller": vue["duree_min"].map(duree),
    "Lien": vue["lien"],
})
if not nb_bagages:
    tableau = tableau.drop(columns=["Dont bagages ($)", "Source frais"])
st.dataframe(
    tableau, hide_index=True, width="stretch", height=500,
    column_config={
        "Lien": st.column_config.LinkColumn("Lien", display_text="Voir"),
        "Prix ($)": st.column_config.NumberColumn(format="%d $"),
        "Dont bagages ($)": st.column_config.NumberColumn(format="%d $"),
    },
)
st.caption("Le détail affiché est celui du vol aller ; le vol retour se choisit sur Google Flights via le lien. Les prix peuvent changer d'ici la réservation.")
st.download_button("Exporter en CSV", tableau.to_csv(index=False).encode("utf-8-sig"), "vols.csv", "text/csv")
