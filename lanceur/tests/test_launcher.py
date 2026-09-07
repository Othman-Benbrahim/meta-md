from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from lanceur import (build_installer, build_manifest, build_package,
                     state, updater)


class LauncherStateTest(unittest.TestCase):
    def test_invalid_selection_falls_back_to_root(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            state_file = root / "_metamd" / "active.json"
            state.write_state(state_file, {"active": "../../danger"})
            selected, version = state.selected_app(root, state_file)
            self.assertEqual(selected, root / "app")
            self.assertIsNone(version)

    def test_valid_installed_version_is_selected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            app_dir = root / "app" / "versions" / "1.2.3"
            app_dir.mkdir(parents=True)
            (app_dir / "server.py").write_text("", encoding="utf-8")
            (app_dir / "VERSION").write_text("1.2.3", encoding="utf-8")
            state_file = root / "_metamd" / "active.json"
            state.write_state(state_file, {"active": "1.2.3"})
            selected, version = state.selected_app(root, state_file)
            self.assertEqual(selected, app_dir)
            self.assertEqual(version, "1.2.3")


class UpdaterTest(unittest.TestCase):
    def _archive(self, root: Path, version: str = "1.2.3") -> Path:
        package = root / "metamd.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("VERSION", version)
            archive.writestr("server.py", "print('server')")
            archive.writestr("modules/__init__.py", "")
        return package

    def test_install_is_atomic_and_activates_version(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            package = self._archive(root)
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            versions = root / "app" / "versions"
            state_file = root / "app" / "active.json"
            with (
                patch.object(updater, "VERSIONS_DIR", versions),
                patch.object(updater, "STATE_FILE", state_file),
            ):
                installed = updater.install(package, digest)
            self.assertEqual(installed, "1.2.3")
            self.assertTrue((versions / "1.2.3" / "server.py").is_file())
            saved = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["active"], "1.2.3")

    def test_wrong_hash_does_not_install(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            package = self._archive(Path(raw))
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                updater.install(package, "0" * 64)

    def test_parent_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            package = Path(raw) / "bad.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("../outside.txt", "danger")
            with zipfile.ZipFile(package) as archive:
                with self.assertRaisesRegex(ValueError, "chemin interdit"):
                    updater._safe_members(archive)


    def _install_context(self, root: Path):
        return (
            patch.object(updater, "VERSIONS_DIR", root / "app" / "versions"),
            patch.object(updater, "STATE_FILE", root / "app" / "active.json"),
        )

    def test_failed_version_can_be_reinstalled(self) -> None:
        """Une version qui n'a pas demarre ne doit pas bloquer sa remplacante."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            package = self._archive(root)
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            versions = root / "app" / "versions"
            state_file = root / "app" / "active.json"
            vers, etat = self._install_context(root)
            with vers, etat:
                updater.install(package, digest)
                # Le lanceur constate que 1.2.3 ne demarre pas et revient en arriere.
                state.write_state(state_file, {"active": None, "failed": "1.2.3"})
                temoin = versions / "1.2.3" / "temoin.txt"
                temoin.write_text("installation defaillante", encoding="utf-8")
                reinstalle = updater.install(package, digest)
            self.assertEqual(reinstalle, "1.2.3")
            self.assertFalse(temoin.exists())
            saved = json.loads(state_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["active"], "1.2.3")
            self.assertNotIn("failed", saved)

    def test_installed_version_is_not_overwritten(self) -> None:
        """Hors cas d'echec, une version deja posee reste intouchable."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            package = self._archive(root)
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            vers, etat = self._install_context(root)
            with vers, etat:
                updater.install(package, digest)
                with self.assertRaises(FileExistsError):
                    updater.install(package, digest)
    def _archive_avec_socle(self, root: Path, version: str = "1.2.3") -> Path:
        """Un paquet 0.8.4 : l'application a la racine, le socle a cote."""
        package = root / "metamd.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr("VERSION", version)
            archive.writestr("server.py", "print('server')")
            archive.writestr("modules/__init__.py", "")
            archive.writestr("lanceur/launch.py", "# socle neuf\n")
            archive.writestr("lanceur/updater.py", "# updater neuf\n")
        return package

    def test_socle_is_installed_with_a_backup(self) -> None:
        """Le socle porte le retour arriere : on garde de quoi le restaurer."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            socle = root / "lanceur"
            socle.mkdir()
            (socle / "launch.py").write_text("# socle ancien\n", encoding="utf-8")
            (socle / "updater.py").write_text("# updater ancien\n", encoding="utf-8")
            # Le runtime Python ne doit surtout pas etre touche.
            (socle / "python").mkdir()
            (socle / "python" / "python.exe").write_text("binaire", encoding="utf-8")

            package = self._archive_avec_socle(root)
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            vers, etat = self._install_context(root)
            with vers, etat,                  patch.object(updater, "SOCLE_DIR", socle),                  patch.object(updater, "SAUVEGARDE_SOCLE", socle / "precedent"):
                updater.install(package, digest)

            self.assertEqual((socle / "launch.py").read_text(encoding="utf-8"),
                             "# socle neuf\n")
            self.assertEqual(
                (socle / "precedent" / "launch.py").read_text(encoding="utf-8"),
                "# socle ancien\n", "l'ancien socle doit rester restaurable")
            self.assertTrue((socle / "python" / "python.exe").is_file(),
                            "le runtime Python ne doit jamais etre touche")
            # Le socle ne doit pas non plus rester dans le dossier de version.
            self.assertFalse((root / "app" / "versions" / "1.2.3" / "lanceur").exists())

    def test_package_without_socle_still_installs(self) -> None:
        """Les paquets d'avant la 0.8.4 n'en portent pas : rien ne doit casser."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            package = self._archive(root)          # sans lanceur/
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            vers, etat = self._install_context(root)
            with vers, etat:
                self.assertEqual(updater.install(package, digest), "1.2.3")

    def test_old_versions_are_purged(self) -> None:
        """Sans purge, `app/versions/` grossit sans limite sur le poste."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            versions = root / "app" / "versions"
            state_file = root / "app" / "active.json"
            versions.mkdir(parents=True)
            for ancienne in ("0.1.0", "0.2.0"):
                (versions / ancienne).mkdir()
            state.write_state(state_file, {"active": "0.2.0"})
            package = self._archive(root)              # 1.2.3
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            vers, etat = self._install_context(root)
            with vers, etat:
                updater.install(package, digest)
            restantes = sorted(d.name for d in versions.iterdir() if d.is_dir())
            self.assertEqual(restantes, ["0.2.0", "1.2.3"])   # precedente + active

    def test_malformed_package_is_reported_clearly(self) -> None:
        """Une archive sans VERSION doit donner le message prevu, pas une trace."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            package = root / "vide.zip"
            with zipfile.ZipFile(package, "w") as archive:
                archive.writestr("lisezmoi.txt", "rien d'utile")
            digest = hashlib.sha256(package.read_bytes()).hexdigest()
            vers, etat = self._install_context(root)
            with vers, etat:
                with self.assertRaisesRegex(ValueError, "pas une version META-MD valide"):
                    updater.install(package, digest)


class ManifestTest(unittest.TestCase):
    def _paquet(self, root: Path, version: str) -> Path:
        package = root / "metamd.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr(f"metamd-{version}/VERSION", version)
            archive.writestr(f"metamd-{version}/server.py", "")
        return package

    def test_version_comes_from_the_package(self) -> None:
        """Lire `app/VERSION` laissait diverger le manifeste et l'archive."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            sortie = root / "update-manifest.json"
            manifest = build_manifest.build_manifest(
                self._paquet(root, "2.0.0"),
                "https://forge.apps.education.fr/meta-md/meta-md"
                "/-/releases/v2.0.0/downloads/metamd.zip",
                "https://forge.apps.education.fr/meta-md/meta-md/-/releases/v2.0.0",
                sortie, "Test")
            self.assertEqual(manifest["latest"], "2.0.0")
            ecrit = json.loads(sortie.read_text(encoding="utf-8"))
            self.assertEqual(ecrit["latest"], "2.0.0")
            self.assertFalse(ecrit["critical"])

    def test_critical_flag_reaches_the_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            sortie = root / "update-manifest.json"
            manifest = build_manifest.build_manifest(
                self._paquet(root, "2.0.0"),
                "https://forge.apps.education.fr/meta-md/meta-md"
                "/-/releases/v2.0.0/downloads/metamd.zip",
                "https://forge.apps.education.fr/meta-md/meta-md/-/releases/v2.0.0",
                sortie, "Test", critical=True)
            self.assertTrue(manifest["critical"])


class InstallerTest(unittest.TestCase):
    """Le paquet d'installation doit demarrer seul ; celui de MAJ non."""

    def test_installer_package_is_self_sufficient(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            sortie = Path(raw)
            package, _digest, taille = build_installer.build(sortie)
            self.assertGreater(taille, 0)
            with zipfile.ZipFile(package) as archive:
                noms = archive.namelist()
            prefixe = noms[0].split("/")[0]

            for attendu in ("META-MD-win-1-installer.bat", "META-MD-win-2-ouvrir.bat",
                            "META-MD-linux-1-installer.sh", "META-MD-linux-2-ouvrir.sh",
                            "python.bat", "python.sh", "README.md", "LICENSE",
                            "app/server.py", "app/VERSION",
                            "lanceur/launch.py", "lanceur/updater.py"):
                self.assertIn(f"{prefixe}/{attendu}", noms, attendu)

            # Ni le runtime, ni les donnees, ni l'outillage de release.
            self.assertFalse([n for n in noms if "lanceur/python/" in n])
            self.assertFalse([n for n in noms if "data/CORPUS" in n])
            self.assertFalse([n for n in noms if "build_" in n])

    def test_installer_folder_has_no_version(self) -> None:
        """Le dossier est permanent : un numero y serait faux des la 1re MAJ."""
        with tempfile.TemporaryDirectory() as raw:
            package, _d, _t = build_installer.build(Path(raw))
            with zipfile.ZipFile(package) as archive:
                noms = archive.namelist()
                prefixe = noms[0].split("/")[0]
                version = archive.read(f"{prefixe}/app/VERSION").decode().strip()
            self.assertEqual(prefixe, "META-MD")
            self.assertNotIn(version, prefixe)
            self.assertFalse([n for n in noms if n.endswith("lanceur/VERSION")],
                             "le socle ne doit plus porter sa propre version")

    def test_both_packages_carry_the_same_application(self) -> None:
        """Une installation neuve et une mise a jour doivent tenir le meme code."""
        with tempfile.TemporaryDirectory() as raw:
            sortie = Path(raw)
            maj, _d, _t = build_package.build(sortie)
            install, _d2, _t2 = build_installer.build(sortie)
            with zipfile.ZipFile(maj) as a:
                noms_maj = a.namelist()
            with zipfile.ZipFile(install) as a:
                noms_install = a.namelist()

            # L'application, de part et d'autre. Dans le paquet de mise a jour
            # elle est a la racine — les updaters 0.8.0 a 0.8.3 l'y cherchent.
            app_maj = {n.split("/", 1)[1] for n in noms_maj
                       if "/" in n and "/lanceur/" not in n}
            app_install = {n.split("/app/", 1)[1] for n in noms_install
                           if "/app/" in n}
            self.assertEqual(app_maj, app_install,
                             "l'application differe entre installation et mise a jour")

            # Et le socle : depuis la 0.8.4 il voyage aussi dans la mise a jour.
            socle_maj = {n.split("/lanceur/", 1)[1] for n in noms_maj
                         if "/lanceur/" in n}
            socle_install = {n.split("/lanceur/", 1)[1] for n in noms_install
                             if "/lanceur/" in n}
            self.assertTrue(socle_maj, "la mise a jour n'emporte pas le socle")
            self.assertEqual(socle_maj, socle_install,
                             "le socle differe entre installation et mise a jour")


if __name__ == "__main__":
    unittest.main()
