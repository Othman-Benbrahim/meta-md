"""Numeros de sections : identite, profondeur et distinction avec les listes."""
from pathlib import Path
import sys
import unittest

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.pdf.titres import _TitresPdf, _titres_de_la_page
from modules.traitements.numerotation import _numero_de_titre
from modules.traitements.texte import _norme_titre
from modules.traitements.titres import _caler_titres
from modules.traitements.tableaux_html import _retirer_clotures_de_tableaux


class NumerotationTest(unittest.TestCase):
    def test_formes_et_profondeurs(self):
        for texte, chemin in [('1. Rubrique', (1,)), ('1) Rubrique', (1,)),
                               ('1.Rubrique', (1,)), ('1.1.Rubrique', (1, 1)),
                               ('1.1 Rubrique', (1, 1)), ('1.1.1. Rubrique', (1, 1, 1))]:
            with self.subTest(texte=texte):
                self.assertEqual(_numero_de_titre(texte), (chemin, 'Rubrique'))

    def test_dates_nombres_et_ordinaux_ne_sont_pas_des_prefixes(self):
        for texte in ('02.12.2025', '2025. Compte rendu', '3.14', '1er bilan',
                      '10 personnes presentes', 'Version 1.2'):
            with self.subTest(texte=texte):
                self.assertIsNone(_numero_de_titre(texte))

    def test_meme_libelle_sous_deux_numeros_reste_distinct(self):
        self.assertNotEqual(_norme_titre('1.1. Validation'), _norme_titre('2.1. Validation'))
        self.assertEqual(_norme_titre('1) Validation'), _norme_titre('1. Validation'))

    def test_correspondance_approchee_ne_change_pas_de_numero(self):
        cle = _norme_titre('1.1. Approbation du compte rendu de la reunion precedente')
        md = '1.2. Approbation du compte rendu de la reunion precedente'
        self.assertEqual(_caler_titres(md, ({cle: '## '}, {cle: {0}}))[0], md)

    def test_numero_conserve_lors_de_la_promotion(self):
        for texte in ('1. Rubrique', '1) Rubrique', '1.1. Rubrique'):
            with self.subTest(texte=texte):
                cle = _norme_titre(texte)
                self.assertEqual(_caler_titres(texte, ({cle: '## '}, {cle: {0}}))[0], '## ' + texte)

    def test_libelles_identiques_sur_une_page(self):
        titres = {_norme_titre('1. Validation'): '# ', _norme_titre('1.1. Validation'): '## '}
        md = '### 1. Validation\n\n# 1.1. Validation'
        self.assertEqual(_caler_titres(md, (titres, {c: {0} for c in titres}))[0],
                         '# 1. Validation\n\n## 1.1. Validation')


class PdfNumeroteTest(unittest.TestCase):
    def test_hierarchie_traverse_pages_et_ignore_listes_et_tableaux(self):
        with pymupdf.open() as doc:
            doc.new_page(width=600, height=700)
            doc.new_page(width=600, height=700)

            def texte(p, y, contenu, taille=10, gras=False, couleur=(0, 0, 0)):
                doc[p].insert_text((30, y), contenu, fontsize=taille,
                                   fontname='hebo' if gras else 'helv', color=couleur)

            texte(0, 30, 'Compte rendu', 20, True)
            texte(0, 70, '1) Administration', 12, True)
            long = '1.1. Approbation du compte rendu de la precedente reunion du comite'
            texte(0, 110, long, gras=True)
            texte(0, 145, '1.1.1. Annexes', gras=True)
            texte(0, 180, '1.2. Ordre du jour', gras=True, couleur=(0, 0, .5))
            texte(0, 215, '1) Page 3 :')
            texte(0, 250, '2) Proposition de modification :')
            texte(1, 40, '1.3. Validation', gras=True)
            texte(1, 80, '12.5 kg')
            doc[1].draw_rect((20, 100, 550, 140))
            doc[1].draw_line((350, 100), (350, 140))
            texte(1, 125, '1.4. Libelle dans une cellule', gras=True)
            doc[1].insert_text((370, 125), 'Valeur', fontsize=10)
            for p in (0, 1):
                for y in range(300, 650, 20):
                    texte(p, y, 'Texte ordinaire suffisamment abondant pour definir le corps du document.')
            titres = _TitresPdf(doc)
            a = _titres_de_la_page(doc[0], titres)[0]
            b = _titres_de_la_page(doc[1], titres)[0]
            self.assertEqual(a[_norme_titre('1) Administration')], '## ')
            self.assertEqual(a[_norme_titre(long)], '### ')
            self.assertEqual(a[_norme_titre('1.1.1. Annexes')], '#### ')
            self.assertEqual(a[_norme_titre('1.2. Ordre du jour')], '### ')
            self.assertEqual(b[_norme_titre('1.3. Validation')], '### ')
            for texte_exclu in ('1) Page 3 :', '2) Proposition de modification :',
                                '12.5 kg', '1.4. Libelle dans une cellule'):
                self.assertNotIn(_norme_titre(texte_exclu), a | b)

    def test_liste_numerotee_seule_ne_cree_pas_de_branche(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            for y, contenu in ((30, '1) Premier element'), (60, '2) Deuxieme element')):
                page.insert_text((30, y), contenu, fontsize=10)
            self.assertEqual(_TitresPdf(doc).numerotes, {})


class CloturesDeTableauTest(unittest.TestCase):
    table = '<table><tr><td>Effectifs scolaires</td><td>Nombre de familles</td></tr></table>'

    def test_titre_apres_tableau_est_libere_et_corrige(self):
        corps = self.table + '\n\n**1.2. Questions diverses**'
        md = 'Introduction\n\n```html\n' + corps + '\n```'
        corrige = _retirer_clotures_de_tableaux(md, 'Effectifs scolaires Nombre de familles 1.2. Questions diverses')
        self.assertEqual(corrige, 'Introduction\n\n' + corps)
        cle = _norme_titre('1.2. Questions diverses')
        resultat, _ = _caler_titres(corrige, ({cle: '## '}, {cle: {0}}))
        self.assertIn('## 1.2. Questions diverses', resultat)

    def test_veritable_code_html_present_dans_pdf_conserve(self):
        md = '```html\n' + self.table + '\n```'
        self.assertEqual(_retirer_clotures_de_tableaux(md, self.table), md)

    def test_sans_preuve_du_pdf_le_bloc_reste_intact(self):
        md = '```html\n' + self.table + '\n```'
        for texte in ('', 'Un autre contenu sans rapport avec les cellules'):
            with self.subTest(texte=texte):
                self.assertEqual(_retirer_clotures_de_tableaux(md, texte), md)

    def test_code_sans_tableau_conserve(self):
        md = '```markdown\n# 1.2. Questions diverses\n```'
        self.assertEqual(_retirer_clotures_de_tableaux(md, '1.2. Questions diverses'), md)


if __name__ == '__main__':
    unittest.main()
