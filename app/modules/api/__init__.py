"""Acces aux fournisseurs distants : ce qui ne bouge pas.

La cle vit dans `config.json`, hors du depot ; ces modules ne font que
porter l'appel et en tenir le rythme.

    ratelimit       file d'attente et reprise sur 429, cote Albert
    albert_client   client generique — inutilise aujourd'hui, garde comme
                    reference du contrat de la passerelle
"""
