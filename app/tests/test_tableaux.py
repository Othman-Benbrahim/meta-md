"""Regressions des tableaux, sans modele ni document externe.

Les coordonnees et objets PDF sont des exemples synthetiques : les regles
ne dependent ni d'une phrase, ni d'un numero de page d'un corpus reel.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules.pdf import balises
from modules.pdf.tableaux import (
    _assembler_tableau, _caler_colonnes, _grille_correspondante,
    _tableaux_de_la_page, _texte_des_cellules,
)
from modules.traitements.tableaux_reparation import _merge_table_fragments
from modules.traitements.texte import _assembler_pages
from modules.moteurs.albert_vision_figures.transcription import _repli_couche_texte


class DocumentBalise:
    """La seule interface PyMuPDF necessaire pour lire un arbre de structure."""

    def __init__(self, objets):
        self.objets = objets
        self.pages = [SimpleNamespace(xref=10), SimpleNamespace(xref=20)]
        self.page_count = len(self.pages)

    def __getitem__(self, numero):
        return self.pages[numero]

    def pdf_catalog(self):
        return 1

    def xref_get_key(self, xref, key):
        valeur = self.objets.get(xref, {}).get(key)
        return ("string", valeur) if valeur is not None else ("null", "null")


class PagesDesCellulesTest(unittest.TestCase):
    def test_mcid_reutilise_sur_deux_pages(self):
        doc = DocumentBalise({
            1: {"StructTreeRoot": "2 0 R"},
            2: {"K": "3 0 R"},
            3: {"S": "/Table", "K": "4 0 R"},
            4: {"S": "/TR", "Pg": "10 0 R", "K": "[5 0 R 6 0 R]"},
            5: {"S": "/TD", "K": "7 0 R"},
            6: {"S": "/TD", "K": "1"},
            7: {"S": "/P", "K": "[<< /Type /MCR /Pg 10 0 R /MCID 0 >> "
                                      "<< /Type /MCR /Pg 20 0 R /MCID 0 >>]"},
        })
        positions = [{0: [(12, 30)], 1: [(120, 30)]}, {0: [(12, 40)]}]
        with patch.object(balises, "_positions_par_mcid", side_effect=positions):
            tableaux = balises.tableaux(doc)
        self.assertEqual(set(tableaux), {0, 1})
        self.assertEqual(tableaux[0][0][0][0][0][2], [(12, 30)])
        self.assertEqual(tableaux[1][0][0][0][0][2], [(12, 40)])
        self.assertEqual(tableaux[1][0][0][1], [])

    def test_page_du_descendant_et_mcr_indirect(self):
        doc = DocumentBalise({
            3: {"S": "/TD", "Pg": "10 0 R", "K": "[4 0 R 5 0 R]"},
            4: {"S": "/P", "Pg": "20 0 R", "K": "0"},
            5: {"Type": "/MCR", "Pg": "20 0 R", "MCID": "1"},
        })
        segments = balises._segments(doc, 3, set())
        self.assertEqual([(p, ids) for _, _, p, ids in segments],
                         [("20 0 R", [0]), ("20 0 R", [1])])


def ligne(texte, x, y):
    return ((x, y - 5, x + len(texte), y + 1),
            [((x + i, y - 5, x + i + 1, y + 1), c)
             for i, c in enumerate(texte)])


class ColonnesTest(unittest.TestCase):
    def setUp(self):
        self.grille = SimpleNamespace(
            col_count=2, bbox=(0, 0, 200, 100),
            rows=[SimpleNamespace(cells=[(0, 0, 100, 100), (100, 0, 200, 100)])])

    def test_balise_dans_mauvaise_colonne_et_faux_label(self):
        tableau = [[[], [(balises.PUCE, 1, [(10, 20)]),
                         (balises.ITEM, 1, [(10, 40)])]]]
        grille = _grille_correspondante(tableau, [self.grille])
        self.assertIs(grille, self.grille)
        corrige = _caler_colonnes(tableau, grille)
        cellules = _texte_des_cellules(corrige, [ligne("suite de la", 10, 20),
                                                ligne("description.", 10, 40)], grille.bbox)
        self.assertEqual(cellules, [["suite de la description.", ""]])

    def test_une_puce_reelle_reste_dans_une_colonne_seule_remplie(self):
        tableau = [[[(balises.PUCE, 1, [(10, 20)]),
                     (balises.ITEM, 1, [(15, 20)])], []]]
        cellules = _texte_des_cellules(tableau, [ligne("-", 10, 20), ligne("Premier item", 15, 20)])
        self.assertEqual(cellules, [["<ul><li>Premier item</li></ul>", ""]])

    def test_largeur_geometrique_incompatible_ne_corrige_rien(self):
        tableau = [[[(balises.TEXTE, 0, [(10, 20)])], [], []]]
        self.assertIsNone(_grille_correspondante(tableau, [self.grille]))
        self.assertIs(_caler_colonnes(tableau, None), tableau)

    def test_la_grille_exclut_le_texte_voisin(self):
        tableau = [[[(balises.TEXTE, 0, [(10, 20)])], []]]
        cellules = _texte_des_cellules(tableau,
                                      [ligne("contenu", 10, 20), ligne("voisin", 210, 20)],
                                      self.grille.bbox)
        self.assertEqual(cellules, [["contenu", ""]])


class EntetesTest(unittest.TestCase):
    def test_entete_explicitement_declare_sans_gras(self):
        html = _assembler_tableau([["Nom", "Valeur"], ["A", "B"]], 2, {0})
        self.assertIn("<th>Nom</th><th>Valeur</th>", html)

    def test_donnees_courtes_sans_indice_d_entete(self):
        html = _assembler_tableau([["A", "B"], ["C", "D"]], 2, set())
        self.assertNotIn("<th", html)

    def test_ligne_courte_apres_donnees_ne_devient_pas_entete(self):
        html = _assembler_tableau([
            ["<ul><li>Premiere action.</li></ul>", "<ul><li>Premier exemple.</li></ul>"],
            ["Suite des actions", "Suite des exemples"],
            ["Action courte", "Exemple court"],
        ], largeur=2)
        self.assertNotIn("<th", html)

    def test_vrai_entete_apres_bande_pleine_largeur(self):
        html = _assembler_tableau([["Bandeau"], ["Nom", "Valeur"], ["A", "B"]], largeur=2)
        self.assertIn('<td colspan="2">Bandeau</td>', html)
        self.assertIn("<th>Nom</th><th>Valeur</th>", html)
        self.assertIn("<td>A</td><td>B</td>", html)

    def test_entete_initial_conserve(self):
        html = _assembler_tableau([["Nom", "Valeur"], ["A", "B"]], largeur=2)
        self.assertEqual(html.count("<th>"), 2)
        self.assertIn("<thead>", html)


class RaccordsTest(unittest.TestCase):
    def fusionner(self, avant, apres, marque="<!-- page 2 -->"):
        return _merge_table_fragments(avant + "\n\n" + marque + "\n\n" + apres, True)

    def test_suite_colonne_gauche_dans_le_dernier_item(self):
        avant = '<table><tr><td><ul><li>Premier.</li><li>Lire la</li></ul></td><td>Termine.</td></tr></table>'
        apres = '<table><tr><td>suite du document.</td><td></td></tr></table>'
        resultat = self.fusionner(avant, apres)
        self.assertIn('<li>Lire la suite du document.</li>', resultat)
        self.assertEqual(resultat.count('<tr>'), 1)
        self.assertEqual(resultat.count('suite du document.'), 1)
        self.assertIn('<!-- page 2 -->', resultat)

    def test_suite_colonne_droite_dans_la_sous_liste(self):
        avant = '<table><tr><td>Termine.</td><td><ul><li>Exemples :<ul><li>debut de</li></ul></li></ul></td></tr></table>'
        apres = '<table><tr><td></td><td>la suite.</td></tr></table>'
        resultat = self.fusionner(avant, apres)
        self.assertIn('<li>debut de la suite.</li></ul></li></ul>', resultat)

    def test_ligne_nouvelle_preservee_dans_tableau_reuni(self):
        avant = '<table><thead><tr><th>A</th><th>B</th></tr></thead><tr><td>Fini.</td><td>Fini.</td></tr></table>'
        apres = '<table><tr><td><ul><li>Nouvelle action.</li></ul></td><td>Nouvel exemple.</td></tr></table>'
        resultat = self.fusionner(avant, apres)
        self.assertEqual(resultat.count('<table>'), 1)
        self.assertEqual(resultat.count('<tr>'), 3)
        self.assertEqual(resultat.count('<th>'), 2)

    def test_suite_gauche_non_devinee_sans_saut_de_page(self):
        avant = '<table><tr><td>categorie</td><td>A</td></tr></table>'
        apres = '<table><tr><td>autre categorie</td><td></td></tr></table>'
        self.assertEqual(self.fusionner(avant, apres, '').count('<tr>'), 2)

    def test_nouveau_titre_empeche_la_fusion(self):
        avant = '<table><tr><td>A</td><td>B</td></tr></table>'
        apres = '<table><tr><td>suite</td><td></td></tr></table>'
        self.assertEqual(self.fusionner(avant, apres, '<!-- page 2 -->\n\n## Nouvelle section').count('<table>'), 2)

    def test_cellule_fusionnee_ne_decoule_pas_d_un_alignement_de_colonnes(self):
        avant = '<table><tr><td colspan="2">Texte</td></tr></table>'
        apres = '<table><tr><td>suite</td><td></td></tr></table>'
        resultat = self.fusionner(avant, apres)
        self.assertIn('<td colspan="2">Texte</td>', resultat)
        self.assertEqual(resultat.count('<tr>'), 2)

    def test_trois_pages_conservent_les_deux_raccords(self):
        a = '<table><tr><td><ul><li>debut de</li></ul></td><td>Fini.</td></tr></table>'
        b = '<table><tr><td>la suite et</td><td></td></tr></table>'
        c = '<table><tr><td>de la fin.</td><td></td></tr></table>'
        resultat = _merge_table_fragments(a + '\n<!-- page 2 -->\n' + b + '\n<!-- page 3 -->\n' + c, True)
        self.assertEqual(resultat.count('<tr>'), 1)
        self.assertIn('<li>debut de la suite et de la fin.</li>', resultat)
        self.assertEqual(resultat.count('<!-- page '), 2)


class ParcoursPdfTest(unittest.TestCase):
    def test_entete_geometrique_apres_ligne_vide(self):
        with pymupdf.open() as doc:
            page = doc.new_page(width=300, height=250)
            page.draw_rect((20, 20, 280, 120))
            page.draw_line((150, 20), (150, 120))
            for y in (50, 85):
                page.draw_line((20, y), (280, y))
            for x, titre, valeur in ((30, 'Nom', 'A'), (160, 'Valeur', 'B')):
                page.insert_text((x, 70), titre, fontname='hebo', fontsize=10)
                page.insert_text((x, 105), valeur, fontsize=10)
            tableaux = _tableaux_de_la_page(page)
            self.assertEqual(len(tableaux), 1)
            self.assertIn('<th>Nom</th><th>Valeur</th>', tableaux[0])
            self.assertIn('<td>A</td><td>B</td>', tableaux[0])

    def test_cellule_sur_deux_pages_du_flux_pdf_au_markdown(self):
        # Vrai PDF minuscule, construit en memoire. Le meme MCID 0 designe
        # des textes differents sur les deux pages, comme dans tout PDF.
        with pymupdf.open() as doc:
            for _ in range(2):
                doc.new_page(width=300, height=250)

            def texte(page, x, y, contenu, mcid):
                page.insert_text((x, y), contenu, fontsize=10)
                stream = page.get_contents()[-1]
                doc.update_stream(stream, b'/P <</MCID ' + str(mcid).encode() + b'>> BDC\n'
                                  + doc.xref_stream(stream) + b'\nEMC')

            for page in doc:
                page.draw_rect((20, 20, 280, 80))
                page.draw_line((150, 20), (150, 80))
            texte(doc[0], 30, 40, 'Lire la', 0)
            texte(doc[0], 160, 40, 'Exemple termine.', 1)
            texte(doc[1], 30, 40, 'suite du document.', 0)

            def objet(contenu):
                xref = doc.get_new_xref()
                doc.update_object(xref, contenu)
                return xref

            gauche = objet(f'<< /S /TD /K [<< /Type /MCR /Pg {doc[0].xref} 0 R /MCID 0 >> '
                           f'<< /Type /MCR /Pg {doc[1].xref} 0 R /MCID 0 >>] >>')
            droite = objet(f'<< /S /TD /Pg {doc[0].xref} 0 R /K 1 >>')
            rang = objet(f'<< /S /TR /K [{gauche} 0 R {droite} 0 R] >>')
            table = objet(f'<< /S /Table /K {rang} 0 R >>')
            racine = objet(f'<< /Type /StructTreeRoot /K {table} 0 R >>')
            doc.xref_set_key(doc.pdf_catalog(), 'StructTreeRoot', f'{racine} 0 R')

            for balise in (True, False):
                with self.subTest(balise=balise):
                    if not balise:
                        doc.xref_set_key(doc.pdf_catalog(), 'StructTreeRoot', 'null')
                    declares = balises.tableaux(doc) or {}
                    pages = []
                    for i, page in enumerate(doc):
                        md, repris, nombre = _repli_couche_texte(page, i + 1, 'timeout simule', declares.get(i))
                        self.assertTrue(repris)
                        self.assertEqual(nombre, 1)
                        pages.append(md)
                    resultat = _merge_table_fragments(_assembler_pages(pages), balise)
                    self.assertEqual(resultat.count('<table>'), 1)
                    self.assertIn('<td>Lire la suite du document.</td><td>Exemple termine.</td>', resultat)
                    self.assertEqual(resultat.count('suite du document.'), 1)


if __name__ == '__main__':
    unittest.main()
