"""Figures dans une grille PDF, sans modele ni document externe."""
from pathlib import Path
import sys
import unittest

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.moteurs.albert_vision_figures.decoupage import _figures_de_la_page, _poser_figures
from modules.traitements.tableaux_html import _cellules, _lignes_du_tableau
from modules.traitements.tableaux_reparation import _merge_table_fragments, _reverser_continuations
from modules.traitements.texte import _assembler_pages


class FiguresCellulesTest(unittest.TestCase):
    def page(self, doc, texte=False):
        page = doc.new_page(width=400, height=400)
        page.draw_rect((20, 20, 380, 220))
        page.draw_line((170, 20), (170, 220))
        if texte:
            page.insert_text((30, 45), 'Premiere colonne')
            page.insert_text((180, 45), 'Illustration associee')
        page.draw_oval((210, 70, 330, 160), color=(1, 0, 0), fill=(1, 0, 0))
        return page

    def test_grille_sans_texte_ne_part_pas_dans_image(self):
        with pymupdf.open() as doc:
            page = self.page(doc)
            self.assertEqual(len(page.find_tables().tables), 0)
            boites = _figures_de_la_page(page)
            self.assertEqual(len(boites), 1)
            self.assertEqual(tuple(boites[0]), (210, 70, 330, 160))
            md, nombre = _poser_figures('', page, 2)
            self.assertEqual(nombre, 1)
            cellules = _cellules(_lignes_du_tableau(md)[0])
            self.assertEqual(cellules[0][2], '')
            self.assertIn('<img src="data:image/png;base64,', cellules[1][2])
            avant = '<table><tr><td>Objectif</td><td>Exemple illustre :</td></tr></table>'
            rendu = _merge_table_fragments(_assembler_pages([avant, md]), True)
            self.assertEqual(rendu.count('<table>'), 1)
            self.assertEqual(rendu.count('<tr>'), 1)
            self.assertIn('Exemple illustre :', rendu)
            self.assertEqual(rendu.count('<img '), 1)

    def test_image_reste_dans_tableau_texte_existant(self):
        with pymupdf.open() as doc:
            page = self.page(doc, texte=True)
            table = '<table><tr><td>Premiere colonne</td><td>Illustration associee</td></tr></table>'
            rendu, n = _poser_figures(table, page, decrire=lambda *_: 'Forme "rouge" & ronde')
            self.assertEqual(n, 1)
            self.assertEqual(rendu.count('<table>'), 1)
            cellules = _cellules(_lignes_du_tableau(rendu)[0])
            self.assertEqual(cellules[0][2], 'Premiere colonne')
            self.assertIn('Illustration associee<img ', cellules[1][2])
            self.assertIn('&quot;rouge&quot; &amp; ronde', cellules[1][2])

    def test_figure_hors_grille_reste_image_markdown(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.draw_oval((50, 50, 170, 140))
            md, n = _poser_figures('Texte', page)
            self.assertEqual(n, 1)
            self.assertIn('![Figure 1]', md)
            self.assertNotIn('<table>', md)

    def test_quadrillage_interne_de_figure_est_conserve(self):
        with pymupdf.open() as doc:
            page = self.page(doc, texte=True)
            page.draw_rect((200, 70, 340, 180))
            page.draw_line((270, 70), (270, 180))
            boites = _figures_de_la_page(page)
            self.assertEqual(len(boites), 1)
            self.assertEqual(tuple(boites[0]), (200, 70, 340, 180))

    def test_image_colonne_gauche_ne_compte_pas_comme_case_vide(self):
        table = ('<table><tr><td>Avant</td><td>Donnee</td></tr>'
                 '<tr><td><img src="x"></td><td>Nouvelle donnee</td></tr></table>')
        self.assertEqual(_reverser_continuations(table), (table, 0))


if __name__ == '__main__':
    unittest.main()
