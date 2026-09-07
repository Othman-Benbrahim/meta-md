from __future__ import annotations

import unittest
import hashlib
import io
import tempfile
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "app"))

from modules import updates


class UpdatesTest(unittest.TestCase):
    def setUp(self) -> None:
        updates._cache = None
        updates._cache_time = 0.0
        # La verification est desactivee dans cette version (voir le docstring
        # de `modules.updates`), mais son code est conserve intact. Ces tests
        # le couvrent donc en la reactivant le temps du cas : sans cela, le
        # mecanisme ne serait plus teste du tout et se degraderait en silence
        # jusqu'au jour ou l'on voudrait le rallumer.
        interrupteur = patch.object(updates, "VERIFICATION_ACTIVE", True)
        interrupteur.start()
        self.addCleanup(interrupteur.stop)
        # Le cache disque doit vivre dans un dossier jetable : sans cela les
        # tests ecrivent dans le vrai `data/` et dependent de son contenu.
        self._dossier = tempfile.TemporaryDirectory()
        self.addCleanup(self._dossier.cleanup)
        self.cache = Path(self._dossier.name) / "update-cache.json"
        patcheur = patch.object(updates, "_cache_file", lambda: self.cache)
        patcheur.start()
        self.addCleanup(patcheur.stop)

    def test_version_tuple_orders_numeric_versions(self) -> None:
        self.assertGreater(
            updates._version_tuple("1.10.0"),
            updates._version_tuple("1.9.9"),
        )

    def test_update_available(self) -> None:
        manifest = {
            "latest": "1.0.1",
            "summary": "Correction",
            "release_url": (
                "https://forge.apps.education.fr/meta-md/meta-md/-/releases/1.0.1"
            ),
        }
        with patch.object(updates, "_read_manifest", return_value=manifest):
            status = updates.update_status(force=True)
        self.assertTrue(status["ok"])
        self.assertTrue(status["update_available"])
        self.assertEqual(status["latest"], "1.0.1")

    def test_invalid_release_host_is_rejected(self) -> None:
        manifest = {
            "latest": "1.0.1",
            "release_url": "https://example.org/faux-paquet",
        }
        with patch.object(updates, "_read_manifest", return_value=manifest):
            status = updates.update_status(force=True)
        self.assertFalse(status["ok"])
        self.assertFalse(status["update_available"])

    def test_result_is_cached(self) -> None:
        manifest = {"latest": "1.0.0"}
        with patch.object(updates, "_read_manifest", return_value=manifest) as read:
            updates.update_status()
            updates.update_status()
        read.assert_called_once()

    def test_cache_survives_a_restart(self) -> None:
        """Le compteur de 24 h vit sur le disque, pas seulement en memoire."""
        manifest = {"latest": "1.0.0"}
        with patch.object(updates, "_read_manifest", return_value=manifest) as read:
            updates.update_status()
            self.assertTrue(self.cache.is_file())
            updates._cache = None          # comme apres un redemarrage
            updates._cache_time = 0.0
            etat = updates.update_status()
        read.assert_called_once()
        self.assertEqual(etat["latest"], "1.0.0")

    def test_network_failure_is_never_persisted(self) -> None:
        """Une coupure ne doit pas rendre META-MD muet une journee entiere."""
        with patch.object(updates, "_read_manifest", side_effect=OSError("hors ligne")):
            etat = updates.update_status(force=True)
        self.assertFalse(etat["ok"])
        self.assertFalse(self.cache.exists())

    def test_manifest_is_fetched_outside_the_lock(self) -> None:
        """Tenir le verrou pendant les 3 s d'attente serialisait tout le reste."""
        vu = {}

        def faux_manifeste():
            vu["libre"] = updates._cache_lock.acquire(blocking=False)
            if vu["libre"]:
                updates._cache_lock.release()
            return {"latest": "1.0.0"}

        with patch.object(updates, "_read_manifest", side_effect=faux_manifeste):
            updates.update_status(force=True)
        self.assertTrue(vu.get("libre"))

    def test_complete_package_is_installable(self) -> None:
        manifest = updates._normalise_manifest({
            "latest": "1.2.3",
            "package_url": (
                "https://forge.apps.education.fr/meta-md/meta-md/"
                "-/releases/1.2.3/downloads/metamd-1.2.3.zip"
            ),
            "sha256": "a" * 64,
            "size": 42,
        })
        self.assertTrue(manifest["installable"])

    def test_registry_url_is_installable(self) -> None:
        """Le paquet de MAJ vit au registre, plus dans les assets de release."""
        manifest = updates._normalise_manifest({
            "latest": "1.2.3",
            "package_url": (
                "https://forge.apps.education.fr/api/v4/projects/42"
                "/packages/generic/metamd/1.2.3/metamd-1.2.3.zip"
            ),
            "sha256": "a" * 64,
            "size": 42,
        })
        self.assertTrue(manifest["installable"])

    def test_foreign_package_url_is_refused(self) -> None:
        for url in (
            "https://mechant.example.org/metamd.zip",
            "https://forge.apps.education.fr.mechant.org/meta-md/meta-md/-/releases/x",
            "https://forge.apps.education.fr/api/v4/projects/42"
            "/packages/generic/autre/1.2.3/x.zip",
        ):
            with self.subTest(url=url):
                manifest = updates._normalise_manifest({
                    "latest": "1.2.3", "package_url": url,
                    "sha256": "a" * 64, "size": 42,
                })
                self.assertFalse(manifest["installable"])

    def test_download_checks_size_and_hash(self) -> None:
        content = b"a valid zip payload"
        digest = hashlib.sha256(content).hexdigest()
        manifest = {
            "latest": "1.2.3",
            "package_url": (
                "https://forge.apps.education.fr/meta-md/meta-md/"
                "-/releases/1.2.3/downloads/metamd-1.2.3.zip"
            ),
            "sha256": digest,
            "size": len(content),
            "installable": True,
        }

        class Response(io.BytesIO):
            headers = {"Content-Length": str(len(content))}
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                self.close()

        with tempfile.TemporaryDirectory() as raw, patch.object(
                updates, "urlopen", return_value=Response(content)):
            package = updates.download_package(manifest, Path(raw) / "telechargements")
            self.assertEqual(package.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()


class VerificationDesactiveeTest(unittest.TestCase):
    """L'interrupteur du fork, teste tel qu'il est livre.

    `VERIFICATION_ACTIVE` n'est pas patche ici : ces cas verifient l'etat
    reel de cette version, la ou `UpdatesTest` couvre le mecanisme conserve.
    """

    def test_aucun_appel_reseau(self) -> None:
        self.assertFalse(updates.VERIFICATION_ACTIVE)
        with patch.object(updates, "_read_manifest",
                          side_effect=AssertionError("le reseau a ete sollicite")):
            status = updates.update_status(force=True)
        self.assertTrue(status["ok"])
        self.assertFalse(status["update_available"])
        self.assertFalse(status["checked"])

    def test_le_telechargement_est_refuse(self) -> None:
        manifest = {
            "latest": "9.9.9",
            "installable": True,
            "package_url": (
                "https://forge.apps.education.fr/meta-md/meta-md/-/releases/9.9.9"
            ),
            "sha256": "0" * 64,
            "size": 1,
        }
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                updates.download_package(manifest, Path(d))
