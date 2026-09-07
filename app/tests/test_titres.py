"""Hierarchie et reecriture des titres, sans corpus ni modele externe."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.pdf.titres import _TitresPdf, _reconcilier_niveaux, _titres_de_la_page
from modules.traitements.titres import _caler_titres
from modules.traitements.texte import _norme_titre


class HierarchieTest(unittest.TestCase):
    grand = (20, True, False, 0)
    chapitre = (12, True, False, 145)
    section = (12, False, False, 145)
    objectif = (9, True, False, 145)
    intertitre = (9, True, False, 0)

    def test_intertitres_declares_racines_et_branches_repetitives(self):
        titres = [(1, self.grand), (2, self.chapitre),
                  (1, self.intertitre), (1, self.intertitre),
                  (2, self.section), (3, self.objectif),
                  (2, self.section), (3, self.objectif),
                  (2, self.chapitre), (1, self.intertitre)]
        self.assertEqual(_reconcilier_niveaux(titres, 9, 0),
                         [1, 2, 3, 3, 3, 4, 3, 4, 2, 3])

    def test_balises_coherentes_conservees_malgre_variations_visuelles(self):
        titres = [(1, self.grand), (2, self.chapitre),
                  (2, self.section), (3, self.objectif)]
        self.assertEqual(_reconcilier_niveaux(titres, 9, 0), [1, 2, 2, 3])

    def test_style_identique_peut_servir_a_deux_niveaux(self):
        titres = [(1, self.grand), (2, self.chapitre),
                  (3, self.intertitre), (4, self.intertitre)]
        self.assertEqual(_reconcilier_niveaux(titres, 9, 0), [1, 2, 3, 4])

    def test_variation_isolee_ne_reclasse_pas_le_document(self):
        titres = [(1, self.grand), (2, self.chapitre), (1, self.intertitre)]
        self.assertEqual(_reconcilier_niveaux(titres, 9, 0), [1, 2, 1])

    def test_profondeur_limitee_aux_six_niveaux_markdown(self):
        titres = [(1, self.grand), (2, self.chapitre),
                  (1, self.intertitre), (1, self.intertitre)]
        titres += [(3, (8 - i / 2, True, False, 0)) for i in range(10)]
        self.assertLessEqual(max(_reconcilier_niveaux(titres, 9, 0)), 6)


class ReecritureTest(unittest.TestCase):
    def caler(self, md):
        cle = _norme_titre('Une rubrique')
        return _caler_titres(md, ({cle: '### '}, {cle: {0}}))[0]

    def test_titre_colle_a_la_liste_suivante(self):
        self.assertEqual(self.caler('Une rubrique\n- Premier item'),
                         '### Une rubrique\n- Premier item')

    def test_titre_colle_au_paragraphe_precedent(self):
        self.assertEqual(self.caler('Fin du paragraphe.\nUne rubrique'),
                         'Fin du paragraphe.\n### Une rubrique')

    def test_niveau_faux_corrige(self):
        self.assertEqual(self.caler('# Une rubrique'), '### Une rubrique')

    def test_occurrence_citee_ne_devient_pas_un_titre(self):
        resultat = self.caler('Une rubrique\n\nUne rubrique')
        self.assertEqual(resultat, '### Une rubrique\n\nUne rubrique')

    def test_sommaire_seul_sans_titre_reel(self):
        md, nombre = _caler_titres('# Une rubrique', ({}, {_norme_titre('Une rubrique'): set()}))
        self.assertEqual((md, nombre), ('Une rubrique', 1))

    def test_code_ignore_sans_consommer_occurrence(self):
        for cloture in ('```', '~~~'):
            with self.subTest(cloture=cloture):
                code = f'{cloture}text\n# Une rubrique\n{cloture}'
                self.assertEqual(self.caler(code + '\nUne rubrique'), code + '\n### Une rubrique')

    def test_paragraphe_contenant_un_titre_reste_intact(self):
        texte = 'Consultez Une rubrique pour plus de precisions.'
        self.assertEqual(self.caler(texte), texte)


class LecturePdfTest(unittest.TestCase):
    def test_mentions_italiques_repetitives_et_titre_italique_unique(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=750)
            declares = []
            for i, (texte, font, taille, niveau) in enumerate([
                    ('Chapitre', 'hebo', 18, 1), ('Section', 'hebo', 12, 2),
                    ('Mention recurrente', 'heit', 9, 3),
                    ('Mention recurrente', 'heit', 9, 3),
                    ('Mention recurrente', 'heit', 9, 3),
                    ('Rubrique distincte', 'heit', 9, 3)]):
                y = 30 + 30 * i
                page.insert_text((30, y), texte, fontname=font, fontsize=taille)
                declares.append((niveau, [(30, y)]))
            for y in range(250, 650, 18):
                page.insert_text((30, y), 'Texte ordinaire suffisamment abondant pour definir le corps.', fontsize=9)
            with patch('modules.pdf.titres.balises.hierarchie', return_value={0: declares}):
                titres = _TitresPdf(doc)
            trouves, positions = _titres_de_la_page(page, titres)
            self.assertNotIn(_norme_titre('Mention recurrente'), trouves)
            self.assertEqual(positions[_norme_titre('Mention recurrente')], set())
            self.assertEqual(trouves[_norme_titre('Rubrique distincte')], '### ')

    def test_repli_applique_les_titres_et_emphases_du_pdf(self):
        from modules.moteurs.albert_vision_figures.transcription import _repli_couche_texte
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((30, 30), 'Section retrouvee', fontname='hebo', fontsize=16)
            page.insert_text((30, 60), 'Une mention en italique', fontname='heit', fontsize=9)
            page.insert_text((30, 90), 'Texte du paragraphe.', fontsize=9)
            titres = SimpleNamespace(balises={0: [(2, [(30, 30)])]}, rangs={}, corps=9)
            rendu, repris, _ = _repli_couche_texte(page, 1, 'timeout simule', titres_pdf=titres)
            self.assertTrue(repris)
            self.assertIn('## Section retrouvee', rendu)
            self.assertIn('*Une mention en italique*', rendu)
            self.assertIn('Texte du paragraphe.', rendu)

    def test_libelle_de_cellule_ne_consomme_pas_le_vrai_titre(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=400, height=400)
            page.draw_rect((20, 20, 350, 80))
            page.draw_line((180, 20), (180, 80))
            page.insert_text((30, 40), 'Rubrique', fontname='hebo', fontsize=9)
            page.insert_text((190, 40), 'Valeur', fontname='hebo', fontsize=9)
            page.insert_text((30, 120), 'Rubrique', fontname='hebo', fontsize=12)
            titres = SimpleNamespace(balises={0: [(2, [(30, 120)])]}, rangs={}, corps=9)
            trouves, positions = _titres_de_la_page(page, titres)
            cle = _norme_titre('Rubrique')
            self.assertEqual(positions[cle], {0})
            table = '<table><tr><td>Rubrique</td><td>Valeur</td></tr></table>'
            md, _ = _caler_titres(table + '\n\n**Rubrique**', (trouves, positions))
            self.assertEqual(md, table + '\n\n## Rubrique')

    def test_sommaire_continue_et_vrai_titre_sur_la_meme_page(self):
        with pymupdf.open() as doc:
            doc.new_page(width=400, height=400)
            doc.new_page(width=400, height=400)
            page = doc[0]
            for i, texte in enumerate(('Rubrique', 'Renvoi A', 'Renvoi B', 'Renvoi C', 'Renvoi D')):
                y = 30 + i * 22
                page.insert_text((30, y), texte, fontname='hebo', fontsize=9)
                page.insert_link({'kind': pymupdf.LINK_GOTO,
                                  'from': pymupdf.Rect(28, y - 12, 180, y + 3), 'page': 1})
            page.insert_text((30, 220), 'Rubrique', fontname='hebo', fontsize=9)
            page = doc.reload_page(page)
            titres = SimpleNamespace(balises={0: []}, rangs={}, corps=9, a_sommaire=True,
                                     rangs_balises={(9.0, True, False, 0): 3})
            trouves, positions = _titres_de_la_page(page, titres)
            cle = _norme_titre('Rubrique')
            self.assertEqual(trouves, {cle: '### '})
            self.assertEqual(positions[cle], {1})
            md, _ = _caler_titres('# Rubrique\n\nRubrique', (trouves, positions))
            self.assertEqual(md, 'Rubrique\n\n### Rubrique')

    def test_styles_calibres_sur_vrai_pdf_et_intertitre_non_balise(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=600, height=700)
            declarations = []

            def ligne(y, texte, taille, niveau=None, gras=False, couleur=(0, 0, 0)):
                page.insert_text((30, y), texte, fontsize=taille,
                                 fontname='hebo' if gras else 'helv', color=couleur)
                if niveau:
                    declarations.append((niveau, [(30, y)]))

            ligne(30, 'Document', 20, 1, True)
            ligne(65, 'Chapitre', 12, 2, True, (0, 0, .5))
            ligne(95, 'Premiere remarque', 9, 1, True)
            ligne(120, 'Deuxieme remarque', 9, 1, True)
            ligne(145, 'Troisieme remarque', 9, gras=True)
            for y in range(180, 600, 18):
                ligne(y, 'Texte ordinaire suffisamment abondant pour definir la taille du corps.', 9)
            with patch('modules.pdf.titres.balises.hierarchie', return_value={0: declarations}):
                titres = _TitresPdf(doc)
            trouves, positions = _titres_de_la_page(page, titres)
            for texte in ('Premiere remarque', 'Deuxieme remarque', 'Troisieme remarque'):
                self.assertEqual(trouves[_norme_titre(texte)], '### ')
            self.assertEqual(trouves[_norme_titre('Chapitre')], '## ')
            self.assertEqual(len(trouves), 5)


if __name__ == '__main__':
    unittest.main()
