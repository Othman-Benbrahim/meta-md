"""Sommaire sans modele : liens qui debordent, niveaux et page mixte."""
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import patch
from types import SimpleNamespace

import pymupdf
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.pdf.sommaire import entrees_de_la_page, rendre


class SommaireTest(unittest.TestCase):
    textes = ('Partie generale', 'Premiere section', 'Un premier objectif',
              'Une autre section', 'Un second objectif')

    def document(self):
        doc = pymupdf.open()
        doc.new_page(width=500, height=500)
        doc.new_page(width=500, height=500)
        for i, texte in enumerate(self.textes):
            y = 35 + i * 18
            doc[0].insert_text((30, y), texte, fontsize=10)
            # Le rectangle recouvre le debut de la ligne suivante. Une
            # extraction par get_textbox melange alors les deux intitules.
            doc[0].insert_link({'kind': pymupdf.LINK_GOTO, 'page': 1,
                                'from': pymupdf.Rect(28, y-12, 280, y+14)})
            doc[1].insert_text((30, y), texte, fontsize=12, fontname='hebo')
        doc[0].insert_text((30, 175), 'Introduction', fontsize=16, fontname='hebo')
        doc[0].insert_text((30, 200), 'Le texte commence apres le sommaire.', fontsize=10)
        # Plusieurs lignes d'un meme paragraphe, a conserver ensemble.
        doc[0].insert_text((30, 235), 'Un paragraphe sur deux lignes\nqui reste un seul paragraphe.', fontsize=10)
        doc.reload_page(doc[0])
        return doc

    def titres(self):
        return SimpleNamespace(balises={0: [(1, [(30, 175)])],
            1: [(n, [(30, 35+i*18)]) for i, n in enumerate((1, 2, 3, 2, 3))]},
            rangs={}, corps=10)

    def test_chaque_lien_recupere_uniquement_son_intitule(self):
        with self.document() as doc:
            self.assertEqual(entrees_de_la_page(doc[0]), [(t, 2) for t in self.textes])

    def test_niveaux_et_ordre_du_corps_sur_page_mixte(self):
        with self.document() as doc:
            md = rendre(doc[0], self.titres())
            self.assertIn('- Partie generale - p. 2\n    - Premiere section - p. 2\n        - Un premier objectif', md)
            self.assertLess(md.index('- Un second objectif'), md.index('# Introduction'))
            self.assertIn('# Introduction\n\nLe texte commence', md)
            self.assertIn('Un paragraphe sur deux lignes qui reste un seul paragraphe.', md)
            self.assertEqual(md.count('Introduction'), 1)

    def test_une_suite_commence_sans_retrait_orphelin(self):
        with self.document() as doc:
            titres = self.titres()
            titres.balises[1] = [(n, points) for (ancien, points), n in
                                 zip(titres.balises[1], (2, 3, 3, 1, 2))]
            md = rendre(doc[0], titres)
            self.assertTrue(md.startswith('- Partie generale'))
            self.assertIn('\n    - Premiere section', md)
            self.assertIn('\n- Une autre section', md)

    def test_renvois_peu_nombreux_ne_sont_pas_reconstruits(self):
        with pymupdf.open() as doc:
            doc.new_page()
            doc.new_page()
            doc[0].insert_text((30, 30), 'Renvoi interne')
            doc[0].insert_link({'kind': pymupdf.LINK_GOTO, 'page': 1,
                                'from': pymupdf.Rect(25, 15, 160, 35)})
            doc.reload_page(doc[0])
            self.assertEqual(rendre(doc[0]), '')

    def test_lien_sur_deux_lignes_et_points_de_conduite(self):
        with self.document() as doc:
            page = doc[0]
            page.insert_text((30, 300), 'Un intitule sur deux lignes', fontsize=10)
            page.insert_text((30, 313), 'et sa fin ........ 2', fontsize=10)
            page.insert_link({'kind': pymupdf.LINK_GOTO, 'page': 1,
                              'from': pymupdf.Rect(28, 288, 280, 317)})
            page = doc.reload_page(page)
            self.assertEqual(entrees_de_la_page(page)[-1], ('Un intitule sur deux lignes et sa fin', 2))

    def test_parcours_conversion_preserve_le_titre_apres_le_sommaire(self):
        from modules.moteurs.albert_vision_figures.transcription import _convert_vision
        with tempfile.TemporaryDirectory() as dossier:
            pdf = Path(dossier) / 'sommaire.pdf'
            with self.document() as doc:
                doc.save(pdf)
            reponse = httpx.Response(200, json={'choices': [{'message': {'content': '\n\n'.join(self.textes)}}]})
            with patch('modules.moteurs.albert_vision_figures.transcription._TitresPdf', return_value=self.titres()), \
                    patch('modules.pdf.sommaire.declare_un_sommaire', return_value=True), \
                    patch('modules.api.ratelimit.call_albert_chat_completions', return_value=reponse) as appel:
                journal = []
                md = _convert_vision(pdf, 'cle-factice', journal=journal)
            self.assertEqual(journal[0]['sommaire'], 5)
            self.assertEqual(journal[0]['appels'], 0)
            self.assertEqual(appel.call_count, 1)
            self.assertIn('# Introduction\n\nLe texte commence', md)
            self.assertLess(md.index('- Un second objectif - p.'), md.index('# Introduction'))


if __name__ == '__main__':
    unittest.main()
