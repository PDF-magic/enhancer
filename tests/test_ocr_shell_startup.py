"""Regression tests for the shell OCR interpreter setup."""

import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "ocr-scanned-pdf.sh"


class OcrShellStartupTests(unittest.TestCase):
    def test_python3_on_path_with_pikepdf_can_start_ocr(self):
        """A bare executable name is valid even when ./python3 does not exist."""
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            commands = directory / "bin"
            commands.mkdir()
            pdf = directory / "source.pdf"
            pdf.write_bytes(b"%PDF-1.7\n")
            python3 = commands / "python3"
            python3.write_text(
                "#!/bin/bash\n"
                'if [[ "${1:-}" == "-c" && "${2:-}" == "import pikepdf" ]]; then exit 0; fi\n'
                f'exec {shlex.quote(sys.executable)} "$@"\n'
            )
            python3.chmod(0o755)
            qpdf = commands / "qpdf"
            qpdf.write_text("#!/bin/bash\necho 1\n")
            qpdf.chmod(0o755)
            ocrmypdf = commands / "ocrmypdf"
            ocrmypdf.write_text(
                "#!/bin/bash\n"
                'echo "REACHED_OCR_COMMAND" >&2\n'
                "exit 47\n"
            )
            ocrmypdf.chmod(0o755)
            environment = {**os.environ, "PATH": str(commands) + os.pathsep + os.environ["PATH"]}
            result = subprocess.run(
                ["/bin/bash", str(SCRIPT), str(pdf), str(directory / "output.pdf"), "--force-ocr"],
                env=environment, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 47, result.stderr)
            self.assertIn("REACHED_OCR_COMMAND", result.stderr)
            self.assertNotIn("Unable to find the Python environment", result.stderr)


if __name__ == "__main__":
    unittest.main()
