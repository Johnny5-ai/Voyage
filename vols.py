"""Moteur de recherche de vols aller-retour sur des dates flexibles.

Source : Google Flights (gratuit), via l'encodeur de requêtes de fast-flights.
Le HTML est analysé ici, car fast-flights ignore le bloc « meilleurs vols »
où se trouve souvent l'option la moins chère.
"""

import json
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import date, timedelta

from fast_flights import FlightQuery, Passengers, create_query
from primp import Client
from selectolax.lexbor import LexborHTMLParser

URL = "https://www.google.com/travel/flights"
DUREE_CACHE = 10 * 60
INTERVALLE_MIN = 0.5  # secondes entre deux requêtes, tous fils confondus
ECHECS_AVANT_ARRET = 5
_cache: dict[str, tuple[float, list[dict]]] = {}
_verrou_rythme = threading.Lock()
_derniere_requete = 0.0


class Blocage(Exception):
    """Google refuse temporairement de répondre (trop de requêtes)."""


class FormatInconnu(Exception):
    """La page de Google n'a plus la structure attendue."""


@dataclass(frozen=True)
class Combo:
    origine: str
    destination: str
    depart: date
    retour: date

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


def requete(c: Combo, cr: Criteres):
    return create_query(
        flights=[
            FlightQuery(date=c.depart.isoformat(), from_airport=c.origine, to_airport=c.destination),
            FlightQuery(date=c.retour.isoformat(), from_airport=c.destination, to_airport=c.origine),
        ],
        trip="round-trip",
        seat=cr.classe,
        passengers=Passengers(adults=cr.adultes, children=cr.enfants),
        max_stops=cr.escales_max,
        checked_bags=cr.bagages_soute,
        language="fr",
        currency=cr.devise,
    )


def _heure(v) -> str:
    # Google omet les composantes nulles : [8] = 08:00, [None, 31] = 00:31
    p = [*(v or []), None, None]
    return f"{p[0] or 0:02d}:{p[1] or 0:02d}"


def analyser(html: str) -> list[dict]:
    script = LexborHTMLParser(html).css_first(r"script.ds\:1")
    if script is None:
        raise Blocage("Google n'a pas renvoyé de résultats (blocage ou page de vérification)")
    try:
        data = script.text().split("data:", 1)[1].rsplit(",", 1)[0]
        if data.endswith("errorHasStatus: true"):
            return []
        payload = json.loads(data)

        options = []
        for bloc in (payload[2], payload[3]):
            if not bloc or not bloc[0]:
                continue
            for item in bloc[0]:
                prix = item[1][0][1] if item[1] and item[1][0] else None
                if prix is None:
                    continue
                vol, segments = item[0], item[0][2]
                option = {
                    "prix": prix,
                    "compagnies": ", ".join(vol[1]),
                    "escales": len(segments) - 1,
                    "via": ", ".join(s[6] for s in segments[:-1]),
                    "heure_depart": _heure(segments[0][8]),
                    "heure_arrivee": _heure(segments[-1][10]),
                    "arrivee_lendemain": tuple(segments[-1][21]) != tuple(segments[0][20]),
                    "duree_min": vol[9],
                    "vols": " + ".join(f"{s[22][0]}{s[22][1]}" if s[22] else "?" for s in segments),
                }
                if not (isinstance(option["prix"], int) and isinstance(option["duree_min"], int)):
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


def _telecharger(client: Client, q) -> str:
    _attendre_son_tour()
    try:
        res = client.get(URL, params=q.params())
    except Exception as e:
        raise ConnectionError(f"erreur réseau ({type(e).__name__})") from e
    if res.status_code == 429 or "/sorry/" in str(res.url):
        raise Blocage("Google limite temporairement les requêtes")
    if res.status_code != 200:
        raise ConnectionError(f"réponse HTTP {res.status_code}")
    return res.content.decode("utf-8")


def chercher(c: Combo, cr: Criteres, sans_cache: bool = False) -> list[dict]:
    q = requete(c, cr)
    cle = q.to_str()
    en_cache = _cache.get(cle)
    if not sans_cache and en_cache and time.time() - en_cache[0] < DUREE_CACHE:
        return en_cache[1]

    client = Client(impersonate="chrome_145", impersonate_os="macos", referer=True, cookie_store=True, timeout=30)
    for attente in (3, 10, None):
        try:
            options = analyser(_telecharger(client, q))
            break
        except (ConnectionError, Blocage):
            if attente is None:
                raise
            time.sleep(attente + random.random())

    lien = q.url()
    for o in options:
        o["lien"] = lien
    _cache[cle] = (time.time(), options)
    return options


def chercher_avec_bagages(c: Combo, cr: Criteres, sans_cache: bool = False) -> list[dict]:
    """Ajoute à chaque option les frais de bagages estimés par Google (écart avec le prix sans bagage)."""
    options = chercher(c, cr, sans_cache)
    if not cr.bagages_soute:
        return [{**o, "bagages": 0} for o in options]
    base = {o["vols"]: o["prix"] for o in chercher(c, replace(cr, bagages_soute=0), sans_cache)}
    frais = [o["prix"] - base[o["vols"]] if o["vols"] in base else None for o in options]
    # Sur certaines routes (ex. Amérique latine), Google n'applique pas les frais : tous les écarts sont nuls
    if not any(frais):
        frais = [None] * len(options)
    return [{**o, "bagages": f} for o, f in zip(options, frais)]


def rechercher(combos: list[Combo], cr: Criteres, sans_cache: bool = False, paralleles: int = 3):
    """Génère (combo, options, erreur) au fur et à mesure des réponses.

    Après plusieurs échecs consécutifs, les recherches restantes sont annulées
    pour ne pas aggraver un blocage de Google.
    """
    with ThreadPoolExecutor(max_workers=paralleles) as pool:
        futurs = {pool.submit(chercher_avec_bagages, c, cr, sans_cache): c for c in combos}
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
