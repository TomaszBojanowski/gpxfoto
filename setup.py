"""Compile the translations in po/ into the package while building it.

Everything else is configured in pyproject.toml.
"""
import os
import shutil
import subprocess

from setuptools import setup
from setuptools.command.build_py import build_py

ROOT = os.path.dirname(os.path.abspath(__file__))
PO_DIR = os.path.join(ROOT, "po")
DOMAIN = "gpxfoto"


def languages():
    with open(os.path.join(PO_DIR, "LINGUAS"), encoding="utf-8") as f:
        return [lang for line in f for lang in line.split("#", 1)[0].split()]


def compile_translations(package_dir):
    msgfmt = shutil.which("msgfmt")
    if msgfmt is None:
        raise SystemExit("msgfmt from GNU gettext is needed to build the translations "
                         "(on Fedora: sudo dnf install gettext)")
    for lang in languages():
        target = os.path.join(package_dir, "locale", lang, "LC_MESSAGES")
        os.makedirs(target, exist_ok=True)
        subprocess.run([msgfmt, "--check", "--output-file", os.path.join(target, DOMAIN + ".mo"),
                        os.path.join(PO_DIR, lang + ".po")], check=True)


class BuildPyWithTranslations(build_py):
    def run(self):
        super().run()
        # An editable install runs the code from the source tree
        if self.editable_mode:
            compile_translations(os.path.join(ROOT, "gpxfoto"))
        else:
            compile_translations(os.path.join(self.build_lib, "gpxfoto"))


setup(cmdclass={"build_py": BuildPyWithTranslations})
