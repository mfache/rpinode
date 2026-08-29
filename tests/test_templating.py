import unittest
import tempfile
import shutil
from pathlib import Path
from src.web.templating import TemplateEngine

class TestTemplating(unittest.TestCase):
    def setUp(self):
        # Création d'un dossier temporaire pour les templates
        self.test_dir = Path(tempfile.mkdtemp())
        (self.test_dir / "layout.html").write_text("<html>$content</html>", encoding="utf-8")
        (self.test_dir / "widget.html").write_text("<div>$data</div>", encoding="utf-8")
        (self.test_dir / "simple.html").write_text("Hello $name!", encoding="utf-8")
        self.engine = TemplateEngine(templates_dir=self.test_dir)

    def tearDown(self):
        # Nettoyage du dossier temporaire
        shutil.rmtree(self.test_dir)

    def test_template_rendering(self):
        # Test rendu simple
        result = self.engine.render("simple.html", name="World")
        self.assertIn("Hello World!", result)
        self.assertIn("<!-- START simple.html -->", result)

    def test_russian_doll_rendering(self):
        # Rendu imbriqué
        widget_html = self.engine.render("widget.html", data="Some Data")
        final_html = self.engine.render("layout.html", content=widget_html)
        
        self.assertIn("<html>", final_html)
        self.assertIn("<div>Some Data</div>", final_html)
        self.assertIn("<!-- START layout.html -->", final_html)
        self.assertIn("<!-- START widget.html -->", final_html)

    def test_missing_template(self):
        result = self.engine.render("missing.html")
        self.assertIn("TEMPLATE INTROUVABLE: missing.html", result)

    def test_safe_substitute(self):
        # $name n'est pas fourni, il doit rester tel quel (safe_substitute)
        result = self.engine.render("simple.html")
        self.assertIn("$name", result)

if __name__ == '__main__':
    unittest.main()
