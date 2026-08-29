import unittest
import os
from pathlib import Path
from src.core.paths import PROJECT_ROOT, DATA_DIR, TEMPLATES_DIR, STATIC_DIR

class TestPaths(unittest.TestCase):
    def test_paths_existence(self):
        """Vérifie que les dossiers de base existent."""
        self.assertTrue(PROJECT_ROOT.exists())
        self.assertTrue(PROJECT_ROOT.is_dir())
        
        self.assertTrue(DATA_DIR.exists())
        self.assertTrue(DATA_DIR.is_dir())
        
        self.assertTrue(TEMPLATES_DIR.exists())
        self.assertTrue(TEMPLATES_DIR.is_dir())
        
        self.assertTrue(STATIC_DIR.exists())
        self.assertTrue(STATIC_DIR.is_dir())

    def test_project_structure(self):
        """Vérifie que certains fichiers clés sont à leur place."""
        self.assertTrue((PROJECT_ROOT / "src" / "main.py").exists())
        self.assertTrue((PROJECT_ROOT / "src" / "core" / "paths.py").exists())

if __name__ == '__main__':
    unittest.main()
