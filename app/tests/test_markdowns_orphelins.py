"""Un Markdown pose dans `2-Conversions/` existe, meme sans source appariee.

Le telechargement et le remplissage partaient jusqu'ici des couples
(source, conversion). Un document dont la source avait ete renommee, retiree
de `1-Sources/` ou marquee « a ne pas traiter » perdait son couple : son `.md`
etait sur le disque, deja converti, mais invisible des deux etapes. Ces tests
fixent la regle inverse — c'est le fichier produit qui fait foi.
"""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.corpus import documents, metadata, topics


def _corpus(racine: Path) -> Path:
    theme = racine / "essai"
    (theme / "1-Sources").mkdir(parents=True)
    (theme / "2-Conversions" / "_metadata").mkdir(parents=True)
    (theme / "schema.yaml").write_text(
        "locked: true\n"
        "fields:\n"
        "- {key: titre, label: Titre, description: '', type: text}\n",
        encoding="utf-8")
    return theme


def _pose_md(theme: Path, stem: str, avec_source: bool = True) -> None:
    (theme / "2-Conversions" / f"{stem}.md").write_text(
        f"# {stem}\n", encoding="utf-8")
    if avec_source:
        (theme / "1-Sources" / f"{stem}.pdf").write_bytes(b"%PDF-1.4\n")


class ListeDesMarkdownsTest(unittest.TestCase):

    def test_liste_les_md_meme_sans_source(self):
        with tempfile.TemporaryDirectory() as d:
            theme = _corpus(Path(d))
            _pose_md(theme, "avec-source")
            _pose_md(theme, "orphelin", avec_source=False)
            self.assertEqual(documents.list_markdowns(theme),
                             ["avec-source", "orphelin"])

    def test_ecarte_les_annexes_et_les_dossiers_prefixes(self):
        with tempfile.TemporaryDirectory() as d:
            theme = _corpus(Path(d))
            _pose_md(theme, "doc")
            # Un rapport decrit une fabrication : il n'est pas un document.
            (theme / "2-Conversions" / "doc.rapport.md").write_text(
                "rapport", encoding="utf-8")
            anciens = theme / "2-Conversions" / "_anciens-moteurs" / "pymupdf"
            anciens.mkdir(parents=True)
            (anciens / "doc.md").write_text("vieux", encoding="utf-8")
            self.assertEqual(documents.list_markdowns(theme), ["doc"])

    def test_corpus_sans_conversion(self):
        with tempfile.TemporaryDirectory() as d:
            theme = _corpus(Path(d))
            self.assertEqual(documents.list_markdowns(theme), [])


class SourceRetrouveeTest(unittest.TestCase):

    def test_prend_la_copie_posee_a_cote_du_md(self):
        with tempfile.TemporaryDirectory() as d:
            theme = _corpus(Path(d))
            _pose_md(theme, "doc", avec_source=False)
            copie = theme / "2-Conversions" / "doc.pdf"
            copie.write_bytes(b"%PDF-1.4\n")
            self.assertEqual(documents.source_for_stem(theme, "doc"), copie)

    def test_retombe_sur_1_sources(self):
        with tempfile.TemporaryDirectory() as d:
            theme = _corpus(Path(d))
            _pose_md(theme, "doc")
            self.assertEqual(documents.source_for_stem(theme, "doc"),
                             theme / "1-Sources" / "doc.pdf")

    def test_rend_none_quand_la_source_a_disparu(self):
        with tempfile.TemporaryDirectory() as d:
            theme = _corpus(Path(d))
            _pose_md(theme, "doc", avec_source=False)
            self.assertIsNone(documents.source_for_stem(theme, "doc"))

    def test_nom_avec_crochets(self):
        # `Fiche [2024]` est un nom courant, et une classe de caracteres
        # pour glob : sans echappement, la source ne serait jamais retrouvee.
        with tempfile.TemporaryDirectory() as d:
            theme = _corpus(Path(d))
            _pose_md(theme, "Fiche [2024]")
            self.assertEqual(documents.source_for_stem(theme, "Fiche [2024]"),
                             theme / "1-Sources" / "Fiche [2024].pdf")


class EtapeRemplissageTest(unittest.TestCase):

    def test_compte_les_md_sans_source(self):
        with tempfile.TemporaryDirectory() as d:
            theme = _corpus(Path(d))
            _pose_md(theme, "avec-source")
            _pose_md(theme, "orphelin", avec_source=False)
            etat = topics._fill_status(theme)
            # Les deux ouvrent l'etape : c'est ce qui rend le bouton
            # cliquable, la ou le couple source/conversion en cachait un.
            self.assertEqual(etat["docs_converted"], 2)
            self.assertEqual(etat["docs_incomplete"], 2)

    def test_un_document_exclu_ne_compte_pas(self):
        with tempfile.TemporaryDirectory() as d:
            theme = _corpus(Path(d))
            _pose_md(theme, "garde")
            _pose_md(theme, "ecarte")
            metadata.update_by_stem(theme, "ecarte", {"excluded": True})
            self.assertEqual(topics._fill_status(theme)["docs_converted"], 1)


class CorpusDeSiteTest(unittest.TestCase):
    """Un corpus `11ty` ou `mkdocs` a des `.md` pour sources.

    Rien n'y est converti : les Markdown du depot sont importes. La source
    d'une page porte donc le meme nom que sa conversion, et l'archive ne
    doit pas embarquer les deux — deux entrees de meme nom dans un ZIP.
    """

    def test_la_source_est_un_md(self):
        with tempfile.TemporaryDirectory() as d:
            theme = _corpus(Path(d))
            (theme / "corpus.yaml").write_text("source: mkdocs\n", encoding="utf-8")
            (theme / "1-Sources" / "page.md").write_text("# page", encoding="utf-8")
            (theme / "2-Conversions" / "page.md").write_text("# page", encoding="utf-8")
            self.assertEqual(documents.list_markdowns(theme), ["page"])
            source = documents.source_for_stem(theme, "page")
            self.assertEqual(source, theme / "1-Sources" / "page.md")
            # C'est cette extension qui fait ecarter la source de l'archive.
            self.assertEqual(source.suffix.lower(), ".md")


if __name__ == "__main__":
    unittest.main()
