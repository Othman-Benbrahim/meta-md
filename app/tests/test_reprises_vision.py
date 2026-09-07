"""Pannes reseau simulees : reprises bornees et diagnostic sans donnees privees."""
from pathlib import Path
import sys
import unittest
import tempfile
from unittest.mock import Mock, patch

import httpx
import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from modules.moteurs.albert_vision_figures.transcription import _avec_reprises, _VisionHTTP
from modules.traitements.rapport import rendre
from modules.moteurs.albert_vision_figures.transcription import _convert_vision


class ReprisesVisionTest(unittest.TestCase):
    def test_conversion_ne_se_replie_qu_apres_trois_echecs(self):
        with tempfile.TemporaryDirectory() as dossier:
            pdf = Path(dossier) / 'page.pdf'
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((30, 40), 'Titre de secours', fontname='hebo', fontsize=18)
                for y in range(80, 200, 20):
                    page.insert_text((30, y), 'Texte ordinaire de cette page.', fontsize=9)
                doc.save(pdf)
            def repondre(requete):
                raise httpx.ReadTimeout('message prive', request=requete)
            client = httpx.Client(transport=httpx.MockTransport(repondre))
            journal = []
            with patch('modules.moteurs.albert_vision_figures.transcription.httpx.Client', return_value=client), \
                    patch('modules.api.ratelimit.get_limiter', return_value=Mock()), \
                    patch('modules.moteurs.albert_vision_figures.transcription.time.sleep'):
                md = _convert_vision(pdf, 'cle-factice', journal=journal)
            self.assertEqual(journal[0]['appels'], 3)
            self.assertTrue(journal[0]['repli'])
            self.assertEqual(len(journal[0]['incidents']), 3)
            self.assertIn('3 tentatives', journal[0]['echec'])
            self.assertIn('# Titre de secours', md)

    def test_conversion_et_rapport_apres_timeout_et_limitation_resolus(self):
        with tempfile.TemporaryDirectory() as dossier:
            pdf = Path(dossier) / 'page.pdf'
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((30, 50), 'Texte de la page.')
                doc.save(pdf)
            compteur = 0
            def repondre(requete):
                nonlocal compteur
                compteur += 1
                if compteur == 1:
                    raise httpx.ReadTimeout('message prive', request=requete)
                if compteur == 2:
                    return httpx.Response(429, headers={'Retry-After': '2'})
                return httpx.Response(200, json={'choices': [{'message': {'content': 'Texte de la page.'}}]})
            client = httpx.Client(transport=httpx.MockTransport(repondre))
            journal, progression = [], Mock()
            with patch('modules.moteurs.albert_vision_figures.transcription.httpx.Client', return_value=client), \
                    patch('modules.api.ratelimit.get_limiter', return_value=Mock()), \
                    patch('modules.moteurs.albert_vision_figures.transcription.time.sleep'):
                md = _convert_vision(pdf, 'cle-factice-secrete', journal=journal, progress_callback=progression)
            self.assertIn('Texte de la page.', md)
            self.assertEqual(journal[0]['appels'], 3)
            self.assertNotIn('echec', journal[0])
            rapport = rendre(journal, source='page.pdf', moteur='vision', duree=10)
            self.assertIn('ReadTimeout', rapport)
            self.assertIn('HTTP 429', rapport)
            self.assertIn('HTTP 200', rapport)
            self.assertIn('Appels au modèle : **3**, dont 2 reprise(s)', rapport)
            self.assertNotIn('secrete', rapport)
            self.assertNotIn('message prive', rapport)
            self.assertIn('tentative 2/3', str(progression.call_args_list))

    def test_timeout_puis_succes_conserve_incident(self):
        evenements, annonces = [], []
        appel = Mock(side_effect=[httpx.ReadTimeout('secret'), 'page obtenue'])
        with patch('modules.moteurs.albert_vision_figures.transcription.time.sleep') as pause:
            self.assertEqual(_avec_reprises(appel, annonces.append, evenements.append), 'page obtenue')
        self.assertEqual(annonces, [1, 2])
        self.assertEqual(evenements[0]['erreur'], 'ReadTimeout')
        self.assertEqual(evenements[1]['http'], 200)
        self.assertNotIn('secret', str(evenements))
        pause.assert_called_once_with(5)
        rapport = rendre([{'page': 2, 'appels': 2, 'incidents': evenements}],
                         source='exemple.pdf', moteur='vision', duree=10)
        self.assertIn('ReadTimeout', rapport)
        self.assertIn('page convertie', rapport)
        self.assertIn('nouvelle tentative', rapport)

    def test_epuisement_ne_boucle_pas_et_rapporte_les_echecs(self):
        evenements = []
        appel = Mock(side_effect=httpx.ConnectTimeout('secret'))
        with patch('modules.moteurs.albert_vision_figures.transcription.time.sleep'):
            with self.assertRaises(httpx.ConnectTimeout):
                _avec_reprises(appel, observer=evenements.append)
        self.assertEqual(appel.call_count, 3)
        self.assertEqual(len(evenements), 3)
        rapport = rendre([{'page': 3, 'appels': 3, 'secondes': 90,
                           'echec': 'delai depasse', 'repli': True, 'incidents': evenements}],
                         source='exemple.pdf', moteur='vision', duree=90)
        self.assertIn('ConnectTimeout', rapport)
        self.assertIn('Appels au modèle : **3**', rapport)
        self.assertIn('| 3 | 90.0 | 3 |', rapport)
        self.assertIn('texte du PDF repris', rapport)

    def test_http_transitoire_repris_et_refus_non_repris(self):
        for code, attendu in ((503, 3), (401, 1), (403, 1), (400, 1), (429, 1)):
            with self.subTest(code=code):
                appel = Mock(side_effect=_VisionHTTP(code, 'secret'))
                evenements = []
                with patch('modules.moteurs.albert_vision_figures.transcription.time.sleep'):
                    with self.assertRaises(_VisionHTTP):
                        _avec_reprises(appel, observer=evenements.append)
                self.assertEqual(appel.call_count, attendu)
                self.assertEqual(evenements[-1]['http'], code)
                self.assertNotIn('secret', str(evenements))

    def test_appel_figure_n_est_pas_une_reprise(self):
        rapport = rendre([{'page': 1, 'appels': 2, 'figures': 1, 'couverture': 1}],
                         source='exemple.pdf', moteur='vision', duree=10)
        self.assertNotIn('reprise(s)', rapport)
        self.assertNotIn('tentatives', rapport)


if __name__ == '__main__':
    unittest.main()
