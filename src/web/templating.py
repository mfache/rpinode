import string
import html
import logging
from core.paths import TEMPLATES_DIR

logger = logging.getLogger(__name__)

class TemplateEngine:
    """
    Système de templates "à la poupée russe".
    Chaque template HTML contient des variables `$nom` (ou `${nom}`).
    Cette classe permet de charger, mettre en cache et rendre ces templates en y injectant 
    d'autres templates déjà rendus ou des données simples.
    """
    def __init__(self, templates_dir=TEMPLATES_DIR):
        self.templates_dir = templates_dir
        self._cache = {}

    def _load_template(self, name: str) -> string.Template:
        if name in self._cache:
            return self._cache[name]
            
        path = self.templates_dir / name
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read()
            # Ajout d'un commentaire HTML pour debug (optionnel mais très pratique)
            tpl = string.Template(f"\n<!-- START {name} -->\n{content}\n<!-- END {name} -->\n")
            self._cache[name] = tpl
            return tpl
        except FileNotFoundError:
            logger.error(f"Template introuvable: {path}")
            return string.Template(f"<!-- TEMPLATE INTROUVABLE: {name} -->")

    def render(self, name: str, **kwargs) -> str:
        """
        Rend un template.
        Ex: render("page.html", content=render("widget.html", value="123"))
        """
        tpl = self._load_template(name)
        # safe_substitute permet de ne pas crasher si une variable manque.
        # Les variables non fournies resteront sous la forme `$variable`.
        return tpl.safe_substitute(**kwargs)

    @staticmethod
    def escape(text: str) -> str:
        """Échappe le texte pour l'injection sécurisée dans le HTML."""
        if text is None:
            return ""
        return html.escape(str(text))

# Instance globale par défaut
engine = TemplateEngine()
render = engine.render
escape = engine.escape
