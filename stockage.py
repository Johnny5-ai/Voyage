"""Sauvegarde locale en fichiers JSON (dossier data/)."""

import json
import os
import threading
from datetime import datetime
from pathlib import Path

DOSSIER = Path(__file__).parent / "data"
FRAIS = DOSSIER / "frais_bagages.json"
RECHERCHES = DOSSIER / "recherches.json"

# Les onglets du navigateur sont des fils d'un même processus Streamlit
_verrou = threading.RLock()
alertes: list[str] = []


def lire(fichier: Path) -> dict:
    with _verrou:
        if not fichier.exists():
            return {}
        try:
            return json.loads(fichier.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            copie = fichier.with_name(f"{fichier.stem}.corrompu-{datetime.now():%Y%m%d-%H%M%S}.json")
            fichier.replace(copie)
            alertes.append(f"Le fichier {fichier.name} était illisible : il a été mis de côté sous {copie.name}.")
            return {}


def ecrire(fichier: Path, donnees: dict):
    with _verrou:
        DOSSIER.mkdir(exist_ok=True)
        temporaire = fichier.with_suffix(".tmp")
        temporaire.write_text(json.dumps(donnees, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        os.replace(temporaire, fichier)


def modifier(fichier: Path, changement) -> dict:
    """Lit, applique `changement(donnees)` et réécrit, sans qu'un autre onglet s'intercale."""
    with _verrou:
        donnees = lire(fichier)
        changement(donnees)
        ecrire(fichier, donnees)
        return donnees
