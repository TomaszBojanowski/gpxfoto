"""Compile the translations in po/ into the package while building it,
and copy the application icon among the files of the browser page.

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
ICON = os.path.join(ROOT, "data", "icons", "hicolor", "scalable", "apps",
                    "io.github.tomaszbojanowski.Gpxfoto.svg")


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


def copy_icon(package_dir):
    """The application icon, unchanged, as the icon of the browser page."""
    shutil.copyfile(ICON, os.path.join(package_dir, "web", "favicon.svg"))


class BuildPyWithTranslations(build_py):
    def run(self):
        super().run()
        # An editable install runs the code from the source tree
        package_dir = os.path.join(ROOT if self.editable_mode else self.build_lib, "gpxfoto")
        compile_translations(package_dir)
        copy_icon(package_dir)


setup(cmdclass={"build_py": BuildPyWithTranslations})
