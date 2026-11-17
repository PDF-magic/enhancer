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


    def test_project_venv_wins_over_python_on_path(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            script = directory / "ocr-scanned-pdf.sh"
            script.write_bytes(SCRIPT.read_bytes())
            (directory / "tag-ocr-pdf.py").write_text("# placeholder\n")
            venv = directory / ".venv" / "bin"
            venv.mkdir(parents=True)
            venv_python = venv / "python"
            venv_python.write_text(
                "#!/bin/bash\n"
                'if [[ "${1:-}" == "-c" ]]; then touch "$PDF_MAGIC_TEST_VENV_MARKER"; exit 0; fi\n'
                "exit 83\n"
            )
            venv_python.chmod(0o755)
            commands = directory / "bin"
            commands.mkdir()
            for name, code in {
                "qpdf": "#!/bin/bash\necho 1\n",
                "ocrmypdf": '#!/bin/bash\necho "REACHED_OCR" >&2\nexit 47\n',
                "python3": '#!/bin/bash\ntouch "$PDF_MAGIC_TEST_SYSTEM_MARKER"\nexit 0\n',
            }.items():
                executable = commands / name
                executable.write_text(code)
                executable.chmod(0o755)
            pdf = directory / "scan.pdf"
            pdf.write_bytes(b"%PDF-1.7\n")
            env = {
                **os.environ, "PATH": str(commands) + os.pathsep + os.environ["PATH"],
                "PDF_MAGIC_TEST_VENV_MARKER": str(directory / "venv-selected"),
                "PDF_MAGIC_TEST_SYSTEM_MARKER": str(directory / "system-selected"),
            }
            result = subprocess.run(
                ["/bin/bash", str(script), str(pdf), str(directory / "enhanced.pdf")],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 47, result.stderr)
            self.assertTrue((directory / "venv-selected").exists())
            self.assertFalse((directory / "system-selected").exists())

    def test_incomplete_project_venv_fails_without_falling_back_to_system_python(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            script = directory / "ocr-scanned-pdf.sh"
            script.write_bytes(SCRIPT.read_bytes())
            (directory / "tag-ocr-pdf.py").write_text("# placeholder\n")
            venv = directory / ".venv" / "bin"
            venv.mkdir(parents=True)
            executable = venv / "python"
            executable.write_text("#!/bin/bash\nexit 1\n")
            executable.chmod(0o755)
            commands = directory / "bin"
            commands.mkdir()
            for name in ("qpdf", "ocrmypdf", "python3"):
                shim = commands / name
                shim.write_text("#!/bin/bash\nexit 0\n")
                shim.chmod(0o755)
            pdf = directory / "scan.pdf"
            pdf.write_bytes(b"%PDF-1.7\n")
            env = {**os.environ, "PATH": str(commands) + os.pathsep + os.environ["PATH"]}
            result = subprocess.run(
                ["/bin/bash", str(script), str(pdf)],
                env=env, capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("setup-python-env.sh", result.stderr)

if __name__ == "__main__":
    unittest.main()
