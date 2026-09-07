"""Rapport de conversion : ce qui s'est passe, page par page.

Les avertissements partent au journal du serveur, ou personne ne les relit
apres coup. Ce rapport les rassemble en un tableau Markdown ecrit a cote du
sidecar, dans `_metadata/<stem>.rapport.md` : on sait alors, sans relire le
document entier, quelles pages meritent un coup d'oeil et pourquoi.

Il ne juge pas, il mesure. Une page signalee n'est pas forcement fausse ; une
page muette n'est pas garantie juste. C'est un ordre de lecture, pas un
verdict.
"""
from __future__ import annotations

from typing import Any

# En dessous, la page a perdu du texte : le moteur n'a pas rendu ce que la
# couche texte du PDF contient. Meme seuil que la conversion elle-meme.
COUVERTURE_MIN = 0.85


def _souci(p: dict[str, Any]) -> str:
    """Ce qui merite un coup d'oeil sur cette page, en clair."""
    if p.get("sommaire"):
        # Ni un souci ni un echec : le fichier declarait son sommaire, on l'a
        # lu. C'est ici qu'on le dit, parce que le Markdown ne porte plus de
        # commentaire expliquant d'ou vient une page.
        return f"sommaire repris du PDF ({p['sommaire']} entrées), sans appel au modèle"
    if p.get("echec"):
        # Le repli est signale meme lorsque la mise en forme a ete recuperee.
        if p.get("repli"):
            detail = "texte du PDF repris"
            if p.get("tableaux_repris"):
                detail = (f"texte et {p['tableaux_repris']} tableau(x) "
                          "repris du PDF")
            return f"{p['echec']} — {detail}"
        return f"ÉCHEC : {p['echec']}"
    soucis: list[str] = []
    couv = p.get("couverture")
    if couv is not None and couv < COUVERTURE_MIN:
        soucis.append(f"texte incomplet ({couv:.0%})")
    if p.get("colonne_unique"):
        soucis.append("tableau réduit à une colonne")
    if p.get("eparpilles"):
        soucis.append(f"{p['eparpilles']} balise(s) de tableau repliées")
    incidents = [e for e in p.get("incidents", []) if e.get("erreur")]
    if incidents:
        soucis.append(f"{len(incidents)} incident(s) d'appel (voir diagnostic)")
    return " ; ".join(soucis)


def rendre(journal: list[dict[str, Any]], *, source: str, moteur: str,
           duree: float) -> str:
    """Rapport Markdown d'une conversion. Rend "" si le journal est vide."""
    if not journal:
        return ""
    lignes = [f"# Conversion de {source}", "",
              f"- Moteur : `{moteur}`",
              f"- Pages : {len(journal)}",
              f"- Durée : {duree:.0f} s "
              f"({duree / max(len(journal), 1):.1f} s par page)"]

    a_relire = [p for p in journal if _souci(p)]
    if a_relire:
        lignes += ["", f"**{len(a_relire)} page(s) à relire** : "
                   + ", ".join(str(p["page"]) for p in a_relire) + "."]
    else:
        lignes += ["", "Aucune page signalée."]

    lignes += ["", "| Page | Secondes | Appels | Couverture | Caractères "
               "| Tableaux du PDF | Figures | À relire |",
               "|---:|---:|---:|---:|---:|---:|---:|---|"]
    for p in journal:
        if p.get("echec"):
            duree_page = f"{p['secondes']:.1f}" if "secondes" in p else "–"
            lignes.append(f"| {p['page']} | {duree_page} | {p.get('appels', '–')} | – | – | – | – | "
                          f"**{_souci(p)}** |")
            continue
        couv = p.get("couverture")
        # Une page peut n'avoir aucune couverture a montrer : un sommaire
        # reconstruit depuis le PDF n'est compare a rien, puisqu'il ne vient
        # pas du modele. Formater None faisait tomber TOUTE la conversion sur
        # « unsupported format string passed to NoneType.__format__ », a
        # l'assemblage, apres soixante-trois pages abouties.
        couv_txt = f"{couv:.0%}" if isinstance(couv, (int, float)) else "–"
        marque = _souci(p)
        lignes.append(
            f"| {p['page']} | {p.get('secondes', 0):.1f} | {p.get('appels', 1)} "
            f"| {couv_txt} | {p.get('caracteres', 0)} "
            f"| {p.get('tableaux_pdf', 0)} | {p.get('figures', 0)} "
            f"| {'**' + marque + '**' if marque else ''} |")

    # Ce que le tableau ne dit pas d'un coup d'oeil.
    total_appels = sum(p.get("appels", 0 if p.get("echec") else 1) for p in journal)
    reprises = sum(e.get("tentative", 1) > 1 and e.get('erreur') != 'HTTP429'
                  for p in journal for e in p.get("incidents", []))
    reprises += sum(bool(e.get('reprise_limitation')) for p in journal for e in p.get('incidents', []))
    couvs = [p["couverture"] for p in journal if p.get("couverture") is not None]
    # La moyenne ne porte que sur les pages abouties : l'annoncer seule sur un
    # document ampute donnerait « 99 % » pour deux pages entierement perdues.
    echecs = [p for p in journal if p.get("echec") and not p.get("repli")]
    replis = [p for p in journal if p.get("repli")]
    lignes += ["", "## Totaux", ""]
    if echecs:
        lignes.append(
            f"- **Pages perdues : {len(echecs)}** sur {len(journal)} — "
            + ", ".join(str(p["page"]) for p in echecs)
            + ". Leur contenu est absent du Markdown.")
    if replis:
        lignes.append(
            f"- Pages reprises de la couche texte du PDF : {len(replis)} — "
            + ", ".join(str(p["page"]) for p in replis)
            + ". Titres, emphases et tableaux sont repris du PDF lorsque disponibles.")
    sommaires = [p for p in journal if p.get("sommaire")]
    if sommaires:
        lignes.append(
            f"- Sommaires lus dans le PDF : {len(sommaires)} — page(s) "
            + ", ".join(str(p["page"]) for p in sommaires)
            + ". Une entrée par ligne, sans points de conduite, "
              "et sans appel au modèle.")
    lignes += [f"- Appels au modèle : **{total_appels}**"
               + (f", dont {reprises} reprise(s)" if reprises else ""),
               (f"- Couverture moyenne : **{sum(couvs) / len(couvs):.0%}**"
                + (f" (sur les {len(couvs)} pages abouties seulement)"
                   if echecs or replis else "")) if couvs else "",
               f"- Figures posées : {sum(p.get('figures', 0) for p in journal)}",
               f"- Tableaux repris du PDF : "
               f"{sum(p.get('tableaux_pdf', p.get('tableaux_repris', 0)) for p in journal)}"]
    pages_incidentes = [p for p in journal if any(e.get('erreur') for e in p.get('incidents', []))]
    if pages_incidentes:
        lignes += ["", "## Diagnostic des appels au modèle", "",
                   "Les incidents résolus sont conservés. Les durées incluent l'attente de la réponse ; "
                   "elles ne mesurent pas le seul calcul du modèle. Une surcharge interne du serveur "
                   "ne peut pas être déduite d'un délai dépassé.", "",
                   "| Page | Objet | Modèle | Tentative | Secondes | Délai de lecture (s) | Constat | Suite |",
                   "|---:|---|---|---:|---:|---:|---|---|"]
        def cellule(valeur: Any) -> str:
            return str(valeur).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')

        for p in pages_incidentes:
            for e in p['incidents']:
                constat = e.get('cause', 'réponse reçue')
                if e.get('http'):
                    constat += f" (HTTP {e['http']})"
                if e.get('erreur') and e['erreur'] != '_VisionHTTP':
                    constat += f" ({e['erreur']})"
                valeurs = (p['page'], e.get('objet', ''), e.get('modele', ''), e['tentative'],
                           e['secondes'], e.get('delai_lecture', ''), constat, e['resultat'])
                lignes.append('| ' + ' | '.join(cellule(v) for v in valeurs) + ' |')
        for p in pages_incidentes:
            issue = ('texte du PDF repris' if p.get('repli') else
                     'page non convertie' if p.get('echec') else 'page convertie')
            lignes += ["", f"Page {p['page']} : {issue}.", ""]
    return "\n".join(l for l in lignes if l is not None) + "\n"
