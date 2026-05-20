"""Pone `src/` en el path para que los tests importen los módulos del
watcher directamente (`import watcher`, `import history`, etc.)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
