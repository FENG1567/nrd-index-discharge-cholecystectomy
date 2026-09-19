from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STEMS = ["Figure_1", "Figure_2", "Figure_3", "Figure_4", "Figure_5", "Figure_6", "Supplementary_Figure_S1", "Supplementary_Figure_S2", "Supplementary_Figure_S3", "Supplementary_Figure_S4"]


class FigureSmokeTests(unittest.TestCase):
    def test_aggregate_builder_writes_all_formats(self) -> None:
        with tempfile.TemporaryDirectory(prefix="nrd_public_figures_") as temp:
            output = Path(temp)
            command = [sys.executable, str(ROOT / "figures" / "build_public_figures.py"), "--output-dir", str(output), "--dpi", "120"]
            completed = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            for stem in STEMS:
                for extension in ("pdf", "png", "svg"):
                    target = output / f"{stem}.{extension}"
                    self.assertTrue(target.is_file(), target)
                    self.assertGreater(target.stat().st_size, 0, target)


if __name__ == "__main__":
    unittest.main(verbosity=2)
