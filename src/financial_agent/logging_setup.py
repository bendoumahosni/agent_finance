"""Configuration du logging.

Les logs partent sur la sortie standard : c'est ce que collectent Docker,
Kubernetes et la plupart des hébergeurs. On n'écrit pas dans un fichier.
"""

import logging


def setup_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        force=True,
    )
    # Les bibliothèques HTTP sont bavardes en INFO.
    logging.getLogger("httpx").setLevel(logging.WARNING)
