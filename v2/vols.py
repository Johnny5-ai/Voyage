"""Moteur V2 : trouve la meilleure combinaison aller + retour sur des dates flexibles.

Étape 1 : une recherche aller-retour par combinaison de dates donne les vols aller
et leur prix « à partir de ».
Étape 2 : pour les vols aller les plus prometteurs, on redemande la page Google
avec ce vol sélectionné, ce qui donne les vols retour et le prix exact de chaque paire.

Source : Google Flights (gratuit), via l'encodeur de requêtes de fast-flights.
Le HTML est analysé ici, car fast-flights ignore le bloc « meilleurs vols »
et ne sait pas sélectionner un vol aller.
"""

import base64
import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import date, timedelta

from fast_flights import FlightQuery, Passengers, create_query
from fast_flights.pb.flights_pb2 import FlightData, Info
from primp import Client
from selectolax.lexbor import LexborHTMLParser

URL = "https://www.google.com/travel/flights"
URL_RESERVATION = "https://www.google.com/travel/flights/booking"
DUREE_CACHE = 10 * 60
INTERVALLE_MIN = 0.5  # secondes entre deux requêtes, tous fils confondus
ECHECS_AVANT_ARRET = 5
CHAMPS_VOL = ("compagnies", "vols", "escales", "via", "heure_depart", "heure_arrivee",
              "arrivee_lendemain", "duree_min", "escale_max_min")
_cache: dict[str, tuple[float, list[dict]]] = {}
_verrou_rythme = threading.Lock()
_derniere_requete = 0.0


class Blocage(Exception):
    """Google refuse temporairement de répondre (trop de requêtes)."""


class FormatInconnu(Exception):
    """La page de Google n'a plus la structure attendue."""


@dataclass(frozen=True)
class Combo:
    """Aller-retour, ou aller simple si `retour` est None."""

    origine: str
    destination: str
    depart: date
    retour: date | None = None

    @property
    def nuits(self) -> int:
        return (self.retour - self.depart).days


@dataclass(frozen=True)
class Criteres:
    adultes: int = 1
    enfants: int = 0
    classe: str = "economy"
    escales_max: int | None = None
    bagages_soute: int = 0
    devise: str = "CAD"


def combinaisons(origines, destinations, debut, fin, nuits_min, nuits_max) -> list[Combo]:
    combos = []
    for o in origines:
        for dest in destinations:
            d = debut
            while d <= fin:
                for n in range(nuits_min, nuits_max + 1):
                    combos.append(Combo(o, dest, d, d + timedelta(days=n)))
                d += timedelta(days=1)
    return combos


def allers_simples(origines, destinations, debut, fin, nuits_min, nuits_max) -> list[Combo]:
    """Allers simples couvrant les mêmes dates que `combinaisons` : vers la destination, puis retour."""
    simples = set()
    for o in origines:
        for dest in destinations:
            for i in range((fin - debut).days + 1):
                simples.add(Combo(o, dest, debut + timedelta(days=i)))
            for i in range((fin - debut).days + nuits_max - nuits_min + 1):
                simples.add(Combo(dest, o, debut + timedelta(days=nuits_min + i)))
    return sorted(simples, key=lambda c: (c.origine, c.destination, c.depart))


def requete(c: Combo, cr: Criteres):
    trajets = [FlightQuery(date=c.depart.isoformat(), from_airport=c.origine, to_airport=c.destination)]
    if c.retour:
        trajets.append(FlightQuery(date=c.retour.isoformat(), from_airport=c.destination, to_airport=c.origine))
    return create_query(
        flights=trajets,
        trip="round-trip" if c.retour else "one-way",
        seat=cr.classe,
        passengers=Passengers(adults=cr.adultes, children=cr.enfants),
        max_stops=cr.escales_max,
        checked_bags=cr.bagages_soute,
        language="fr",
        currency=cr.devise,
    )


def _champ(numero: int, contenu: bytes) -> bytes:
    """Encode un champ protobuf de type « length-delimited »."""
    taille, n = b"", len(contenu)
    while True:
        octet, n = n & 0x7F, n >> 7
        taille += bytes([octet | (0x80 if n else 0)])
        if not n:
            return bytes([numero << 3 | 2]) + taille + contenu


def _selection(segments) -> bytes:
    # Champ 4 de FlightData, absent du .proto de fast-flights : les vols déjà choisis, un par tronçon
    return b"".join(
        _champ(4, _champ(1, o.encode()) + _champ(2, d.encode()) + _champ(3, a.encode())
               + _champ(5, cie.encode()) + _champ(6, num.encode()))
        for o, d, a, cie, num in segments
    )


def tfs(c: Combo, cr: Criteres, aller=(), retour=()) -> str:
    info = Info.FromString(requete(c, cr).to_bytes())
    trajets = [FlightData.FromString(fd.SerializeToString() + _selection(sel)) for fd, sel in zip(info.data, (aller, retour))]
    del info.data[:]
    info.data.extend(trajets)
    return base64.b64encode(info.SerializeToString()).decode()


def _heure(v) -> str:
    # Google omet les composantes nulles : [8] = 08:00, [None, 31] = 00:31
    p = [*(v or []), None, None]
    return f"{p[0] or 0:02d}:{p[1] or 0:02d}"


def _segment(s) -> list | None:
    """Identifiant d'un tronçon, tel qu'il faut le renvoyer à Google pour le sélectionner."""
    if not s[22]:
        return None
    return [s[3], "{:04d}-{:02d}-{:02d}".format(*s[20]), s[6], s[22][0], s[22][1]]


def _tendance(bloc) -> dict:
    """Encart « Tendances des prix » : prix habituel et fourchette habituelle de Google pour ces dates.

    Bloc observé : [niveau 1-5, [_, prix actuel], [_, prix habituel], [_, écart], [_, bas], [_, haut], ...].
    """
    try:
        return {"typique": bloc[2][1], "typique_bas": bloc[4][1], "typique_haut": bloc[5][1]}
    except (IndexError, TypeError):
        return {"typique": None, "typique_bas": None, "typique_haut": None}


def analyser(html: str) -> list[dict]:
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise Blocage("Google n'a pas renvoyé de résultats (blocage ou page de vérification)")
    try:
        data = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
        if data.endswith("errorHasStatus: true"):
            return []
        payload = json.loads(data)
        tendance = _tendance(payload[5])

        options = []
        for bloc in (payload[2], payload[3]):
            if not bloc or not bloc[0]:
                continue
            for item in bloc[0]:
                prix = item[1][0][1] if item[1] and item[1][0] else None
                if prix is None:
                    continue
                vol, segments = item[0], item[0][2]
                ids = [_segment(s) for s in segments]
                option = {
                    "prix": prix,
                    "compagnies": ", ".join(vol[1]),
                    "escales": len(segments) - 1,
                    "via": ", ".join(s[6] for s in segments[:-1]),
                    "heure_depart": _heure(segments[0][8]),
                    "heure_arrivee": _heure(segments[-1][10]),
                    "arrivee_lendemain": tuple(segments[-1][21]) != tuple(segments[0][20]),
                    "duree_min": vol[9],
                    "escale_max_min": max((e[0] for e in vol[13] or []), default=0),
                    "vols": " + ".join(f"{i[3]}{i[4]}" if i else "?" for i in ids),
                    "segments": ids if all(ids) else None,
                    **tendance,
                }
                if not all(isinstance(option[k], int) for k in ("prix", "duree_min", "escale_max_min")):
                    raise TypeError("prix ou durée non numérique")
                options.append(option)
        return options
    except (IndexError, KeyError, TypeError, ValueError) as e:
        raise FormatInconnu(f"format de page Google inconnu ({type(e).__name__})") from e


def _attendre_son_tour():
    global _derniere_requete
    with _verrou_rythme:
        attente = _derniere_requete + INTERVALLE_MIN - time.monotonic()
        if attente > 0:
            time.sleep(attente)
        _derniere_requete = time.monotonic()


def _telecharger(client: Client, params: dict) -> str:
    _attendre_son_tour()
    try:
        res = client.get(URL, params=params)
    except Exception as e:
        raise ConnectionError(f"erreur réseau ({type(e).__name__})") from e
    if res.status_code == 429 or "/sorry/" in str(res.url):
        raise Blocage("Google limite temporairement les requêtes")
    if res.status_code != 200:
        raise ConnectionError(f"réponse HTTP {res.status_code}")
    return res.content.decode("utf-8")


def chercher(c: Combo, cr: Criteres, sans_cache: bool = False, aller=()) -> list[dict]:
    """Options du premier trajet non sélectionné : les allers, ou les retours si `aller` est fourni."""
    t = tfs(c, cr, aller)
    en_cache = _cache.get(t)
    if not sans_cache and en_cache and time.time() - en_cache[0] < DUREE_CACHE:
        return en_cache[1]

    params = {"tfs": t, "hl": "fr", "curr": cr.devise}
    client = Client(impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True, timeout=30)
    for attente in (3, 10, None):
        try:
            options = analyser(_telecharger(client, params))
            break
        except (ConnectionError, Blocage):
            if attente is None:
                raise
            time.sleep(attente + random.random())

    lien = f"{URL}/search?tfs={t}&hl=fr&curr={cr.devise}"
    for o in options:
        o["lien"] = lien
        # Aller simple : lien direct vers la page de réservation du vol
        if not c.retour and o["segments"]:
            o["lien"] = f"{URL_RESERVATION}?tfs={tfs(c, cr, o['segments'])}&hl=fr&curr={cr.devise}"
    _cache[t] = (time.time(), options)
    return options


def chercher_avec_bagages(c: Combo, cr: Criteres, sans_cache: bool = False) -> list[dict]:
    """Ajoute à chaque option les frais de bagages estimés par Google (écart avec le prix sans bagage)."""
    options = chercher(c, cr, sans_cache)
    if not cr.bagages_soute:
        return [{**o, "bagages": 0} for o in options]
    sans_bagage = chercher(c, replace(cr, bagages_soute=0), sans_cache)
    base = {o["vols"]: o["prix"] for o in sans_bagage}
    frais = [o["prix"] - base[o["vols"]] if o["vols"] in base else None for o in options]
    # Sur certaines routes (ex. Amérique latine), Google n'applique pas les frais : tous les écarts sont nuls
    if not any(frais):
        frais = [None] * len(options)
    # Le prix habituel se compare au prix hors bagages : on garde celui de la recherche sans bagage
    tendance = {k: sans_bagage[0][k] for k in ("typique", "typique_bas", "typique_haut")} if sans_bagage else {}
    return [{**o, **tendance, "bagages": f} for o, f in zip(options, frais)]


def chercher_retours(c: Combo, cr: Criteres, aller: dict, sans_cache: bool = False) -> list[dict]:
    """Itinéraires complets (aller imposé + chaque retour proposé), au prix aller-retour exact."""
    retours = chercher(c, cr, sans_cache, aller=aller["segments"])
    if retours and retours[0]["segments"] and retours[0]["segments"][0][1] != c.retour.isoformat():
        raise FormatInconnu("Google a ignoré le vol aller sélectionné")
    itineraires = []
    for r in retours:
        lien = r["lien"]
        if r["segments"]:
            lien = f"{URL_RESERVATION}?tfs={tfs(c, cr, aller['segments'], r['segments'])}&hl=fr&curr={cr.devise}"
        itineraires.append({
            "prix": r["prix"],
            # Frais mesurés à l'étape 1 sur ce vol aller ; Google les applique à tout l'itinéraire
            "bagages": aller["bagages"],
            **{f"aller_{k}": aller[k] for k in CHAMPS_VOL},
            **{f"retour_{k}": r[k] for k in CHAMPS_VOL},
            "lien": lien,
        })
    return itineraires


def _executer(taches: list[tuple], fonction, paralleles: int = 3):
    """Exécute `fonction(*args)` pour chaque (cle, args) et génère (cle, resultat, erreur).

    Après plusieurs échecs consécutifs, les tâches restantes sont annulées
    pour ne pas aggraver un blocage de Google.
    """
    with ThreadPoolExecutor(max_workers=paralleles) as pool:
        futurs = {pool.submit(fonction, *args): cle for cle, args in taches}
        consecutifs = 0
        for f in as_completed(futurs):
            if f.cancelled():
                yield futurs[f], [], "annulée après plusieurs échecs consécutifs"
                continue
            try:
                resultat = f.result()
                consecutifs = 0
                yield futurs[f], resultat, None
            except Exception as e:
                consecutifs += 1
                if consecutifs >= ECHECS_AVANT_ARRET:
                    for autre in futurs:
                        autre.cancel()
                yield futurs[f], [], str(e)


def rechercher(combos: list[Combo], cr: Criteres, sans_cache: bool = False):
    """Étape 1 : génère (combo, vols aller, erreur)."""
    return _executer([(c, (c, cr, sans_cache)) for c in combos], chercher_avec_bagages)


def approfondir(candidats: list[tuple[Combo, dict]], cr: Criteres, sans_cache: bool = False):
    """Étape 2 : génère ((combo, aller), itinéraires complets, erreur)."""
    return _executer([((c, a), (c, cr, a, sans_cache)) for c, a in candidats], chercher_retours)
