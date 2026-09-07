"""Titres proches et grilles incompletes : preuves positives et ambiguite."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.traitements.texte import _norme_titre
from modules.traitements.titres import _caler_titres
from modules.traitements.tableaux_reparation import _remplacer_tableaux, _retirer_debris_de_tableau


def table(texte, tag='td'):
    return f'<table><tr><{tag}>{texte}</{tag}><{tag}>Valeur</{tag}></tr></table>'


class TitresProchesTest(unittest.TestCase):
    def test_nombres_internes_et_petits_mots_conserves(self):
        self.assertNotEqual(_norme_titre('Avant 4 ans'), _norme_titre('Avant 5 ans'))
        self.assertNotEqual(_norme_titre('Avec un plan'), _norme_titre('Sans un plan'))
        self.assertEqual(_norme_titre('Avant 4ans'), _norme_titre('Avant 4 ans'))

    def test_ages_distincts_sur_meme_page(self):
        textes = ['A partir de 4 ans lorsque les apprentissages sont acquis',
                  'A partir de 5 ans lorsque les apprentissages sont acquis']
        titres = {_norme_titre(t): '## ' for t in textes}
        rendu, n = _caler_titres('\n\n'.join(textes), (titres, {k: {0} for k in titres}))
        self.assertEqual(n, 2)
        self.assertEqual(rendu, '\n\n'.join('## ' + t for t in textes))

    def test_correspondance_approchee_ne_change_pas_age(self):
        titre = 'A partir de 4 ans lorsque les apprentissages precedents sont acquis'
        autre = titre.replace('4', '5')
        self.assertEqual(_caler_titres(autre, ({_norme_titre(titre): '## '}, {})), (autre, 0))

    def test_citation_isolee_confirmee_comme_titre(self):
        t = 'Une rubrique'
        rendu, n = _caler_titres('> **' + t + '**', ({_norme_titre(t): '### '}, {_norme_titre(t): {0}}))
        self.assertEqual((rendu, n), ('### Une rubrique', 1))

    def test_occurrences_comptees_aussi_apres_petite_erreur_de_transcription(self):
        vrai = 'Les apprentissages fondamentaux des eleves'
        approche = vrai.replace('apprentissages', 'apprentisages')
        cle = _norme_titre(vrai)
        md = approche + '\n\n' + vrai
        rendu, n = _caler_titres(md, ({cle: '## '}, {cle: {1}}))
        self.assertEqual((rendu, n), (approche + '\n\n## ' + vrai, 1))

    def test_citation_ordinaire_et_bloc_multiligne_preserves(self):
        for md in ('> Un propos cite', '> Une rubrique\n> Suite de la citation'):
            self.assertEqual(_caler_titres(md, ({_norme_titre('Une rubrique'): '### '}, {})), (md, 0))


class TableauxPartielsTest(unittest.TestCase):
    a = 'Observer les plantes vivantes et decrire leurs feuilles'
    b = 'Comparer les distances parcourues et mesurer plusieurs longueurs'

    def test_un_fragment_manquant_ne_bloque_pas_autre_tableau(self):
        rendu_modele = table(self.b)
        declare = table('<ul><li>' + self.b + '</li></ul>')
        rendu, n = _remplacer_tableaux('Section\n' + rendu_modele, [table(self.a), declare])
        self.assertEqual((rendu, n), ('Section\n' + declare, 1))

    def test_correspondance_ambigue_ne_duplique_pas_tableau(self):
        md = table(self.a)
        self.assertEqual(_remplacer_tableaux(md, [md, md]), (md, 0))
        self.assertEqual(_remplacer_tableaux(md + '\n' + md, [md]), (md + '\n' + md, 0))

    def test_entete_isole_du_modele_retire(self):
        entete = '<tr><th>Objectifs</th><th>Exemples</th></tr>'
        grille = '<table>' + entete + '<tr><td>' + self.a + '</td><td>' + self.b + '</td></tr></table>'
        md = '<table>' + entete + '</table>\n\n' + grille
        rendu, n = _remplacer_tableaux(md, [grille])
        self.assertEqual(n, 1)
        self.assertEqual(rendu.count('<table>'), 1)
        self.assertEqual(rendu.count('<th>Objectifs'), 1)

    def test_entete_separe_par_un_titre_ou_declare_reste(self):
        entete = table('Objectifs', 'th')
        grille = table(self.a)
        md = entete + '\n# Section\n' + grille
        self.assertIn(entete, _remplacer_tableaux(md, [grille])[0])
        self.assertIn(entete, _remplacer_tableaux(entete + '\n' + grille, [entete, grille])[0])

    def test_petites_cellules_doublees_sous_forme_de_liste(self):
        a, b = 'Lire les nombres.', 'Compter les objets.'
        grille = table(a + '<br>' + b)
        liste = '\n- ' + a + '\n- ' + b
        rendu, n = _retirer_debris_de_tableau(grille + liste, texte_pdf=a + ' ' + b)
        self.assertEqual((rendu, n), (grille, 2))

    def test_repetition_dans_pdf_est_preservee(self):
        a, b = 'Lire les nombres.', 'Compter les objets.'
        grille = table(a + '<br>' + b)
        md = grille + '\n- ' + a + '\n- ' + b
        self.assertEqual(_retirer_debris_de_tableau(md, texte_pdf=(a + ' ' + b + ' ') * 2), (md, 0))


if __name__ == '__main__':
    unittest.main()
