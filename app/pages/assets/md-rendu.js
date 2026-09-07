/**
 * RENDU MARKDOWN — LA SEULE FONCTION QUI TRANSFORME UN `.md` EN HTML.
 *
 * Expose `window.rendreMarkdown(md)`. Toutes les surfaces passent par elle :
 *
 *   - META-MD    visualiseur   `#md-content`
 *   - EduMD     lecture       `#viewer-body`   (le bouton « Lire »)
 *   - EduMD     visualiseur   `#md-content`
 *
 * L'analyse du Markdown est celle de marked.js (`assets/marked.js`), la
 * bibliotheque, et non un parseur maison : les cas tordus — emphase collee a
 * la ponctuation, liste imbriquee, HTML dans une cellule — sont son travail,
 * pas le notre.
 *
 * Ce fichier ne fait que trois ajouts, tous propres au corpus, et tous
 * declares a marked au lieu d'etre bricoles autour :
 *
 *   1. `<!-- page N -->`, pose par la conversion a chaque frontiere de page,
 *      devient un repere visible et cliquable.
 *   2. Les autres commentaires HTML — une page en echec, un timeout — sortent
 *      en note lisible. Sans cela, ils disparaitraient : un commentaire, en
 *      HTML, ne s'affiche pas, et c'est la seule trace de ce qui manque.
 *   3. Un lien s'ouvre dans un nouvel onglet : on lit un document, on ne
 *      quitte pas le visualiseur pour suivre une reference.
 *
 * Le fichier est le MEME octet pour octet dans les deux projets, comme
 * `md-rendu.css` : c'est ce qui fait qu'un document se rend pareil partout.
 *
 * L'apparence, elle, n'est pas ici : elle est dans `md-rendu.css`.
 */
(function (root) {
    'use strict';

    /**
     * Un `.md` entierement emballe dans une cloture de code.
     *
     * Certains modeles rendent la page dans un bloc ```markdown malgre la
     * consigne. Sans ce retrait, le document entier s'affiche en code.
     */
    function retirerClotureEnglobante(md) {
        const nu = (md || '').trim();
        if (!nu.startsWith('```')) return md || '';
        let m = nu.match(/^```[a-zA-Z]*\s*\n([\s\S]*?)\n```\s*$/);
        if (m) return m[1];
        m = nu.match(/^```[a-zA-Z]*\s*\n([\s\S]*)$/);   // cloture jamais fermee
        if (m) return m[1].replace(/`+\s*$/, '');
        return md;
    }

    function echapper(s) {
        return String(s == null ? '' : s)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    /**
     * Extension marked : un commentaire HTML seul sur sa ligne.
     *
     * marked laisse passer les commentaires tels quels — ils sont donc
     * invisibles. On les intercepte au niveau bloc, avant son propre
     * tokenizer HTML, pour en faire soit un repere de page, soit une note.
     */
    const commentaireDeConversion = {
        name: 'commentaireDeConversion',
        level: 'block',
        start(src) {
            const i = src.indexOf('<!--');
            return i < 0 ? undefined : i;
        },
        tokenizer(src) {
            const m = /^ {0,3}<!--([\s\S]*?)-->[ \t]*(?:\n+|$)/.exec(src);
            if (!m) return undefined;
            const texte = m[1].trim();
            const page = /^page\s+(\d+)$/i.exec(texte);
            const token = {
                type: 'commentaireDeConversion',
                raw: m[0],
                page: page ? page[1] : null,
                texte: texte,
                tokens: []
            };
            if (!token.page) this.lexer.inline(token.texte, token.tokens);
            return token;
        },
        renderer(token) {
            if (token.page) {
                return '<div class="md-page-mark" data-page="' + echapper(token.page) +
                    '"><span>page ' + echapper(token.page) + '</span></div>\n';
            }
            return '<p class="md-comment">' + this.parser.parseInline(token.tokens) + '</p>\n';
        }
    };

    let configure = false;

    function configurer() {
        if (configure || !root.marked || typeof root.marked.use !== 'function') return;
        root.marked.use({
            // `gfm` donne les tableaux pipe et les barres de texte ; `breaks`
            // reste faux, un retour a la ligne simple dans un paragraphe est
            // de la mise en page, pas un saut voulu.
            gfm: true,
            breaks: false,
            extensions: [commentaireDeConversion],
            renderer: {
                link(token) {
                    const titre = token.title ? ' title="' + echapper(token.title) + '"' : '';
                    return '<a href="' + echapper(token.href) + '"' + titre +
                        ' target="_blank" rel="noopener">' +
                        this.parser.parseInline(token.tokens) + '</a>';
                }
            }
        });
        configure = true;
    }

    /* --- Enrichissement du HTML pose dans la page ---------------------- */

    // Langages proposes a la reconnaissance automatique. La liste est bornee
    // a dessein : laissee libre, highlight.js identifie du C++ dans une
    // fonction Python de cinq lignes (mesure). Bornee a ce que ces documents
    // contiennent vraiment, il repond juste. A completer si un corpus amene
    // un autre langage.
    const LANGAGES = ['python', 'javascript', 'sql', 'html', 'css', 'bash', 'json'];

    // Les deux ecritures des maths coexistent dans le corpus : `$…$` que le
    // prompt de Vision reclame, `\(…\)` que rend l'OCR de Document AI. On
    // accepte les deux plutot que d'en imposer une au fichier.
    const DELIMITEURS = [
        { left: '$$', right: '$$', display: true },
        { left: '\\[', right: '\\]', display: true },
        { left: '$', right: '$', display: false },
        { left: '\\(', right: '\\)', display: false }
    ];

    /**
     * Colore le code et compose les formules d'un rendu deja insere.
     *
     * Se fait sur l'element, et non sur la chaine : les deux bibliotheques
     * travaillent sur des noeuds. A appeler apres avoir pose le HTML.
     *
     * Les deux sont facultatives : sans elles, le code reste lisible en
     * chasse fixe et la formule s'affiche en LaTeX brut. Rien ne casse.
     */
    function enrichirRendu(element) {
        if (!element) return;
        if (root.hljs && typeof root.hljs.highlightAuto === 'function') {
            element.querySelectorAll('pre > code').forEach(function (bloc) {
                // Une clôture qui declare deja son langage est respectee.
                const declare = /\blanguage-(\w+)/.exec(bloc.className);
                const res = declare && root.hljs.getLanguage(declare[1])
                    ? root.hljs.highlight(bloc.textContent, { language: declare[1] })
                    : root.hljs.highlightAuto(bloc.textContent, LANGAGES);
                bloc.innerHTML = res.value;
                bloc.classList.add('hljs');
                if (res.language) bloc.dataset.langage = res.language;
            });
        }
        if (typeof root.renderMathInElement === 'function') {
            root.renderMathInElement(element, {
                delimiters: DELIMITEURS,
                // Une formule fausse s'affiche en rouge plutot que d'arreter
                // le rendu de tout le document.
                throwOnError: false
            });
        }
    }

    /**
     * Rend un Markdown en HTML. Le front matter doit avoir ete retire en
     * amont : chaque surface l'affiche a sa facon, ce n'est pas du document.
     *
     * Si marked manque — fichier absent, chargement en echec — le texte est
     * rendu tel quel plutot que rien : un document doit rester lisible.
     */
    function rendreMarkdown(md) {
        const texte = retirerClotureEnglobante(md);
        if (!root.marked || typeof root.marked.parse !== 'function') {
            return '<pre>' + echapper(texte) + '</pre>';
        }
        if (!root.DOMPurify || !root.DOMPurify.isSupported) {
            return '<pre>' + echapper(texte) + '</pre>';
        }
        configurer();
        return root.DOMPurify.sanitize(root.marked.parse(texte), {
            USE_PROFILES: { html: true },
            FORBID_TAGS: [
                'style', 'form', 'input', 'button', 'textarea', 'select',
                'option', 'iframe', 'object', 'embed', 'link', 'meta', 'base'
            ],
            FORBID_ATTR: ['style', 'srcdoc'],
            ADD_ATTR: ['target', 'rel', 'data-page'],
            SANITIZE_NAMED_PROPS: true
        });
    }

    /**
     * Rend un Markdown DANS un element : pose le HTML, puis l'enrichit.
     *
     * C'est la forme a preferer — elle garantit qu'on n'oublie pas
     * l'enrichissement. `prefixe` sert aux surfaces qui affichent quelque
     * chose au-dessus du document, comme le front matter du visualiseur.
     */
    function rendreMarkdownDans(element, md, prefixe) {
        if (!element) return;
        element.innerHTML = (prefixe || '') + rendreMarkdown(md);
        enrichirRendu(element);
    }

    root.rendreMarkdown = rendreMarkdown;
    root.enrichirRendu = enrichirRendu;
    root.rendreMarkdownDans = rendreMarkdownDans;
})(window);
