"""Interface web locale V2 (aller + retour) : lancer.bat, ou streamlit run app.py depuis ce dossier."""

import re
from collections import Counter
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

import analyse
import stockage
import vols

JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
MOIS = ["jan", "fév", "mar", "avr", "mai", "juin", "juil", "août", "sep", "oct", "nov", "déc"]
CLASSES = {"Économique": "economy", "Économique premium": "premium-economy", "Affaires": "business", "Première": "first"}
ESCALES = {"Peu importe": None, "Direct seulement": 0, "1 escale max": 1, "2 escales max": 2}
ESCALE_LONGUE = {"Peu importe": None, "2 h max": 120, "3 h max": 180, "5 h max": 300, "8 h max": 480}


def jour(d: date) -> str:
    return f"{JOURS[d.weekday()]} {d.day} {MOIS[d.month - 1]}"


def duree(minutes: int) -> str:
    return f"{int(minutes) // 60} h {int(minutes) % 60:02d}"


def montant(v: float) -> str:
    return f"{v:,.0f} $".replace(",", " ")


def codes(texte: str) -> list[str]:
    return [c.strip().upper() for c in re.split(r"[,;\s]+", texte) if c.strip()]


def couleur(v, bas, haut):
    if pd.isna(v):
        return ""
    # Une seule teinte (lisible pour les daltoniens) : bleu foncé = moins cher, presque blanc = plus cher
    t = 0 if haut == bas else (v - bas) / (haut - bas)
    r, g, b = (int(a + (z - a) * t) for a, z in zip((33, 102, 172), (239, 245, 251)))
    style = f"background-color: rgb({r},{g},{b}); color: {'#ffffff' if t < 0.45 else '#1a1a1a'}"
    return style + "; font-weight: 700" if v == bas else style


def horaire(r, p: str = "") -> str:
    return f"{r[p + 'heure_depart']} → {r[p + 'heure_arrivee']}{' (+1)' if r[p + 'arrivee_lendemain'] else ''}"


def trajet(r, p: str = "") -> str:
    if r[p + "escales"] == 0:
        return f"{duree(r[p + 'duree_min'])} · direct"
    return f"{duree(r[p + 'duree_min'])} · {r[p + 'escales']} escale(s) via {r[p + 'via']}"


def resume(r, p: str) -> str:
    texte = f"{r[p + 'compagnies']} ({r[p + 'vols']}) · {horaire(r, p)} · {trajet(r, p)}"
    if r[p + "escales"]:
        texte += f", attente max {duree(r[p + 'escale_max_min'])}"
    return texte


demain = date.today() + timedelta(days=1)
DEFAUTS = {
    "origines": "YUL", "destinations": "CDG",
    "plage": (demain + timedelta(days=30), demain + timedelta(days=44)),
    "nuits": (7, 10), "adultes": 1, "enfants": 0, "classe": "Économique", "escales": "Peu importe",
    "escale_longue": "Peu importe", "bagages": 0, "penalite_escale": 75, "valeur_heure": 20,
    "approfondir": 15, "max_recherches": 120,
}
for cle, valeur in DEFAUTS.items():
    st.session_state.setdefault(cle, valeur)
st.session_state.setdefault("nom", "")
# Critères qui changent les résultats (les réglages de classement n'en font pas partie)
CLES_PRIX = ["origines", "destinations", "plage", "nuits", "adultes", "enfants", "classe", "escales",
             "escale_longue", "bagages", "approfondir"]


def criteres_prix(criteres: dict) -> dict:
    return {k: criteres[k] for k in CLES_PRIX}


def en_tableau(lignes: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(lignes)
    if not df.empty:
        for col in ("depart", "retour"):
            df[col] = pd.to_datetime(df[col]).dt.date
    return df


def charger():
    s = stockage.lire(stockage.RECHERCHES)[st.session_state.choix]
    criteres = s["criteres"]
    debut, fin = (date.fromisoformat(d) for d in criteres["plage"])
    criteres["plage"] = (max(debut, demain), max(fin, demain))
    criteres["nuits"] = tuple(criteres["nuits"])
    st.session_state.update(criteres)
    st.session_state.update(
        allers=en_tableau(s["allers"]), itineraires=en_tableau(s["itineraires"]),
        echecs={etape: tuple(e) for etape, e in s["echecs"].items()}, criteres_recherche=criteres,
        reference=(s["meilleur_prix"], s["date"], criteres_prix(criteres)), nom=st.session_state.choix,
    )


def supprimer():
    nom = st.session_state.pop("choix")
    stockage.modifier(stockage.RECHERCHES, lambda sauvegardes: sauvegardes.pop(nom, None))


def executer(generateur, total: int, libelle: str, ligne) -> tuple[list[dict], tuple]:
    """Consomme un générateur (cle, resultats, erreur) avec barre de progression."""
    barre = st.progress(0.0, libelle)
    lignes, causes = [], Counter()
    for i, (cle, resultats, err) in enumerate(generateur, 1):
        if err:
            causes[err] += 1
        lignes += [ligne(cle, r) for r in resultats]
        barre.progress(i / total, f"{libelle} {i}/{total}")
    barre.empty()
    return lignes, (causes.total(), total, dict(causes))


def infos_combo(c: vols.Combo) -> dict:
    return {"route": f"{c.origine} → {c.destination}", "origine": c.origine, "destination": c.destination,
            "depart": c.depart, "retour": c.retour, "nuits": c.nuits}


st.set_page_config(page_title="Chercheur de vols V2", layout="wide")
st.title("Chercheur de vols flexibles · V2")
st.caption("Trouve la meilleure combinaison aller + retour sur Google Flights. Prix aller-retour exacts, total pour tous les passagers.")

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
    st.selectbox("Escale la plus longue acceptée", list(ESCALE_LONGUE), key="escale_longue",
                 help="Écarte les itinéraires où une attente en correspondance, à l'aller ou au retour, dépasse cette durée.")
    st.selectbox("Bagages en soute (total du groupe)", [0, 1, 2], key="bagages",
                 help="Frais estimés par Google quand il les connaît, sinon ceux de ton tableau de frais par compagnie.")

    with st.expander("Classement « meilleur compromis »"):
        st.number_input("Coût d'une escale ($)", 0, 1000, step=25, key="penalite_escale")
        st.number_input("Coût d'une heure de trajet ($)", 0, 200, step=5, key="valeur_heure")
    with st.expander("Réglages avancés"):
        st.number_input("Vols aller à analyser en détail", 3, 50, step=1, key="approfondir",
                        help="Pour chacun, l'outil récupère les vols retour possibles. Les moins chers et les meilleurs "
                             "compromis sont retenus, jusqu'à deux fois ce nombre au total.")
        st.number_input("Nombre max de recherches de dates", 10, 500, step=10, key="max_recherches")

    ss = st.session_state
    origines, destinations = codes(ss.origines), codes(ss.destinations)
    erreurs = [c for c in origines + destinations if not re.fullmatch(r"[A-Z]{3}", c)]
    if erreurs:
        st.error(f"Codes invalides : {', '.join(erreurs)}. 3 lettres attendues (ex. YUL, CDG, ou PAR pour tout Paris).")
    elif len(ss.plage) != 2:
        st.info("Choisis la date de fin de la plage de départ.")
    else:
        combos = vols.combinaisons(origines, destinations, ss.plage[0], ss.plage[1], *ss.nuits)
        secondes = len(combos) * 0.6 * (2 if ss.bagages else 1) + ss.approfondir * 2 * 0.6
        st.caption(f"{len(combos)} combinaisons de dates + jusqu'à {ss.approfondir * 2} analyses de vols retour (~{max(1, round(secondes / 60))} min)")
    lancer = st.button("Rechercher", type="primary", width="stretch", disabled=bool(erreurs) or len(ss.plage) != 2)

while stockage.alertes:
    st.warning(stockage.alertes.pop(0))

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

    lignes, echecs1 = executer(vols.rechercher(combos, criteres, sans_cache), len(combos),
                               "Étape 1/2 : recherche des dates…", lambda c, o: {**infos_combo(c), **o})
    allers = pd.DataFrame(lignes)

    lignes2, echecs2 = [], (0, 0, {})
    if not allers.empty:
        notes = analyse.ajouter_scores(
            analyse.appliquer_frais(allers, analyse.ALLERS, stockage.lire(stockage.FRAIS), ss.bagages),
            analyse.ALLERS, ss.penalite_escale, ss.valeur_heure)
        notes = analyse.filtrer_escale(notes, ESCALE_LONGUE[ss.escale_longue])
        choisis = analyse.choisir_candidats(notes, ss.approfondir)
        # Le moteur reçoit les vols aller bruts (prix et frais tels que Google les a donnés)
        candidats = [(vols.Combo(a.origine, a.destination, a.depart, a.retour), a.to_dict())
                     for _, a in allers.loc[choisis.index].iterrows()]
        lignes2, echecs2 = executer(vols.approfondir(candidats, criteres, sans_cache), len(candidats),
                                    "Étape 2/2 : analyse des vols retour…", lambda cle, it: {**infos_combo(cle[0]), **it})

    ss.allers, ss.itineraires = allers, pd.DataFrame(lignes2)
    ss.echecs = {"Étape 1 (dates)": echecs1, "Étape 2 (vols retour)": echecs2}
    ss.criteres_recherche = {k: ss[k] for k in DEFAUTS}

if "allers" not in ss:
    st.info("Remplis la demande du client à gauche, puis lance la recherche.")
    st.stop()

for etape, (n, total, causes) in ss.echecs.items():
    if n:
        raisons = "\n".join(f"- {cause} : {k}" for cause, k in sorted(causes.items(), key=lambda x: -x[1]))
        st.warning(f"{etape} : {n} recherche(s) sur {total} ont échoué. Relance dans quelques minutes pour compléter.\n\n{raisons}")
if ss.allers.empty:
    st.error("Aucun vol trouvé pour ces critères.")
    st.stop()

crit = ss.criteres_recherche
nb_bagages = crit["bagages"]
frais = stockage.lire(stockage.FRAIS)
# Les cartes s'affichent au-dessus du tableau des frais, mais sont calculées après sa saisie
zone_cartes = st.container()
if nb_bagages:
    manquantes = analyse.sans_estimation(ss.allers, analyse.ALLERS)
    if not ss.itineraires.empty:
        manquantes |= analyse.sans_estimation(ss.itineraires, analyse.ITINERAIRES)
    connues = {k.lower() for k in frais}
    noms = sorted(set(frais) | {n for n in manquantes if n.lower() not in connues}, key=str.lower)
    a_saisir = sum(n.lower() not in connues for n in manquantes)
    with st.expander("Mes frais de bagages par compagnie" + (f" · {a_saisir} compagnie(s) à compléter" if a_saisir else "")):
        st.caption("Frais pour 1 bagage en soute, aller-retour. Utilisés seulement quand Google n'estime pas les frais, "
                   "et multipliés par le nombre de bagages demandé. Les compagnies sans estimation de cette recherche sont listées. "
                   "Le choix des vols analysés en détail utilise les frais connus au moment de la recherche.")
        edite = st.data_editor(
            pd.DataFrame({"Compagnie": noms, "Frais ($)": [frais.get(n) for n in noms]}, columns=["Compagnie", "Frais ($)"]),
            num_rows="dynamic", hide_index=True, key="editeur_frais", placeholder="à saisir",
            column_config={"Frais ($)": st.column_config.NumberColumn(min_value=0, format="%d $")},
        )
        nouveaux = {r["Compagnie"].strip(): float(r["Frais ($)"]) for r in edite.to_dict("records")
                    if isinstance(r["Compagnie"], str) and r["Compagnie"].strip() and pd.notna(r["Frais ($)"])}
        if nouveaux != frais:
            stockage.ecrire(stockage.FRAIS, nouveaux)
            frais = nouveaux

allers = analyse.ajouter_scores(analyse.appliquer_frais(ss.allers, analyse.ALLERS, frais, nb_bagages),
                                analyse.ALLERS, ss.penalite_escale, ss.valeur_heure)
its = ss.itineraires
if not its.empty:
    its = analyse.ajouter_scores(analyse.appliquer_frais(its, analyse.ITINERAIRES, frais, nb_bagages),
                                 analyse.ITINERAIRES, ss.penalite_escale, ss.valeur_heure)
    its = analyse.filtrer_escale(its, ESCALE_LONGUE[crit["escale_longue"]])

reference = ss.get("reference")
if reference and reference[2] != criteres_prix(crit):
    reference = None

# Avec des bagages demandés, un prix sans frais de bagages connus n'est pas comparable aux autres
frais_inconnus = nb_bagages and not its.empty and its["bagages"].isna().any()

with zone_cartes:
    if its.empty:
        st.warning("Aucun itinéraire complet n'a pu être analysé (vols retour indisponibles, filtrés ou recherches échouées). "
                   "Les vols aller de l'étape 1 restent consultables plus bas.")
        moins_cher = None
    else:
        candidats = its
        if frais_inconnus and its["bagages"].notna().any():
            candidats = its[its["bagages"].notna()]
            inconnues = ", ".join(sorted(analyse.sans_estimation(its, analyse.ITINERAIRES)))
            st.info(f"Itinéraires sans frais de bagages connus exclus des cartes et placés en fin de tableau ({inconnues}). "
                    "Ajoute leurs frais dans « Mes frais de bagages par compagnie » pour les comparer.")

        moins_cher = candidats.loc[candidats["prix"].idxmin()]
        compromis = candidats.loc[candidats["compromis"].idxmin()]
        rapide = candidats.loc[candidats["duree_totale"].idxmin()]
        cartes = st.columns(3)
        for i, (col, titre, r) in enumerate(zip(cartes, ["Le moins cher", "Meilleur compromis", "Le plus rapide"], [moins_cher, compromis, rapide])):
            with col.container(border=True):
                st.markdown(f"**{titre}**")
                ecart = round(r["prix"] - reference[0]) if i == 0 and reference else None
                if ecart is None:
                    st.metric(r["route"], montant(r["prix"]))
                elif ecart == 0:
                    st.metric(r["route"], montant(r["prix"]), delta=f"Prix inchangé depuis le {reference[1]}",
                              delta_color="off", delta_arrow="off")
                else:
                    st.metric(r["route"], montant(r["prix"]), delta=f"{ecart:+,} $ depuis le {reference[1]}".replace(",", " "),
                              delta_color="inverse")
                if nb_bagages:
                    st.caption("bagages : non estimés" if pd.isna(r["bagages"]) else f"dont {montant(r['bagages'])} de bagages ({r['source_frais']})")
                st.write(f"{jour(r['depart'])} → {jour(r['retour'])} ({r['nuits']} nuits) · {duree(r['duree_totale'])} de vol au total")
                st.markdown(f"**Aller** · {resume(r, 'aller_')}")
                st.markdown(f"**Retour** · {resume(r, 'retour_')}")
                st.link_button("Réserver sur Google Flights", r["lien"])

with st.container(border=True):
    col1, col2 = st.columns([3, 1], vertical_alignment="bottom")
    col1.text_input("Sauvegarder cette recherche sous le nom", key="nom", placeholder="Ex. Famille Tremblay – Guatemala janvier")
    nom = ss.nom.strip()
    existe = nom in stockage.lire(stockage.RECHERCHES)
    remplacer = existe and col1.checkbox(f"Une recherche « {nom} » existe déjà : coche pour la remplacer")
    if moins_cher is None:
        raison = "Aucun itinéraire complet à sauvegarder."
    elif not nom:
        raison = "Donne un nom à la recherche pour la sauvegarder."
    elif existe and not remplacer:
        raison = "Ce nom existe déjà : coche la case pour remplacer l'ancienne recherche."
    else:
        raison = None
    if raison:
        col1.caption(raison)
    if col2.button("Sauvegarder", width="stretch", disabled=raison is not None):
        maintenant = datetime.now().strftime("%Y-%m-%d %H:%M")
        entree = {
            "date": maintenant, "criteres": crit, "echecs": ss.echecs, "meilleur_prix": float(moins_cher["prix"]),
            "allers": ss.allers.to_dict("records"), "itineraires": ss.itineraires.to_dict("records"),
        }
        stockage.modifier(stockage.RECHERCHES, lambda sauvegardes: sauvegardes.update({nom: entree}))
        ss.reference = (float(moins_cher["prix"]), maintenant, criteres_prix(crit))
        st.toast(f"Recherche « {nom} » sauvegardée.")

if not its.empty:
    st.subheader("Itinéraires complets")
    tri = st.radio("Trier par", ["Prix", "Meilleur compromis", "Durée totale"], horizontal=True)
    colonne_tri = {"Prix": "prix", "Meilleur compromis": "compromis", "Durée totale": "duree_totale"}[tri]
    vue = (its.assign(inconnu=its["bagages"].isna() & bool(nb_bagages))
           .sort_values(["inconnu", colonne_tri, "prix"])
           .drop_duplicates(["route", "depart", "retour", "aller_vols", "retour_vols"]))
    tableau = pd.DataFrame({
        "Route": vue["route"],
        "Départ": vue["depart"].map(jour),
        "Retour": vue["retour"].map(jour),
        "Nuits": vue["nuits"],
        "Prix ($)": vue["prix"],
        "Dont bagages ($)": vue["bagages"],
        "Source frais": vue["source_frais"].where(~vue["inconnu"], "Inconnus : prix sans bagages"),
        "Lien": vue["lien"],
        "Aller": vue["aller_compagnies"] + " · " + vue["aller_vols"],
        "Horaire aller": vue.apply(horaire, axis=1, p="aller_"),
        "Trajet aller": vue.apply(trajet, axis=1, p="aller_"),
        "Retour : vols": vue["retour_compagnies"] + " · " + vue["retour_vols"],
        "Horaire retour": vue.apply(horaire, axis=1, p="retour_"),
        "Trajet retour": vue.apply(trajet, axis=1, p="retour_"),
        "Attente max": vue["escale_max"].map(lambda m: duree(m) if m else "—"),
        "Durée totale": vue["duree_totale"].map(duree),
    })
    if not nb_bagages:
        tableau = tableau.drop(columns=["Dont bagages ($)", "Source frais"])
    st.dataframe(
        tableau, hide_index=True, width="stretch", height=500, placeholder="—",
        column_config={
            "Prix ($)": st.column_config.NumberColumn(format="%d $"),
            "Lien": st.column_config.LinkColumn("Lien", display_text="Réserver"),
            "Dont bagages ($)": st.column_config.NumberColumn(format="%d $"),
        },
    )
    st.caption(f"Itinéraires construits à partir des {ss.criteres_recherche['approfondir']} vols aller les moins chers et des "
               f"{ss.criteres_recherche['approfondir']} meilleurs compromis de l'étape 1. Les prix peuvent changer d'ici la réservation.")
    st.download_button("Exporter en CSV", tableau.to_csv(index=False).encode("utf-8-sig"), "itineraires.csv", "text/csv")

st.subheader("Grille des prix « à partir de »")
st.caption("Étape 1, pour chaque date : prix aller-retour le plus bas annoncé par Google. Lignes : date de départ. Colonnes : nombre de nuits. Plus le bleu est foncé, moins c'est cher ; le prix le plus bas est en gras.")
for onglet, (route, g) in zip(st.tabs(list(allers["route"].unique())), allers.groupby("route", sort=False)):
    with onglet:
        grille = g.pivot_table(index="depart", columns="nuits", values="prix", aggfunc="min")
        grille.index = [jour(d) for d in grille.index]
        grille.columns = [f"{n} n" for n in grille.columns]
        bas, haut = grille.min().min(), grille.max().max()
        st.dataframe(grille.style.map(couleur, bas=bas, haut=haut).format("{:.0f} $", na_rep="—"),
                     width="stretch", height="content")

with st.expander("Tous les vols aller (étape 1)"):
    vue = allers.sort_values("prix").drop_duplicates(["route", "depart", "retour", "vols"])
    st.dataframe(pd.DataFrame({
        "Route": vue["route"],
        "Départ": vue["depart"].map(jour),
        "Retour": vue["retour"].map(jour),
        "Prix à partir de ($)": vue["prix"],
        "Aller": vue["compagnies"] + " · " + vue["vols"],
        "Horaire aller": vue.apply(horaire, axis=1),
        "Trajet aller": vue.apply(trajet, axis=1),
        "Lien": vue["lien"],
    }), hide_index=True, width="stretch", height=400, placeholder="—", column_config={
        "Lien": st.column_config.LinkColumn("Lien", display_text="Voir"),
        "Prix à partir de ($)": st.column_config.NumberColumn(format="%d $"),
    })
