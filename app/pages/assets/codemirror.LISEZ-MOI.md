# CodeMirror 6, vendoré

`codemirror.js` est un bundle généré une fois et commité, comme `marked.js` ou
`purify.min.js` : la CSP `default-src 'self'` interdit tout CDN, et le projet
n'a pas de chaîne de build. **Ne pas l'éditer** — il est minifié et illisible.
Ce qui se modifie est `codemirror.entree.js`, à côté, qui est la source du
bundle et qui est lisible.

## Pourquoi cette dépendance

Un `<textarea>` affiche sa valeur brute : impossible d'y replier une image en
`data:` — jusqu'à 116 000 caractères pour une seule figure, un quart du
document — tout en gardant copie et suppression fidèles. Il faut un éditeur
qui sache les *décorations atomiques*. C'est ce que `codemirror.entree.js`
utilise : le curseur franchit l'image d'un bloc, `Suppr` l'emporte entière, et
la copie rend le base64 complet parce que **le document n'est jamais modifié**,
seul son affichage l'est.

`markdown()` n'apporte que l'analyse du document. CodeMirror 6 sépare le
langage de sa mise en couleur : sans `syntaxHighlighting`, l'éditeur reste
uniformément noir. D'où `@codemirror/language` et `@lezer/highlight`, qui
portent le `HighlightStyle` défini dans `codemirror.entree.js` — teintes
prises aux variables CSS de la palette, marqueurs (`#`, `**`, backticks)
grisés plutôt que cachés, puisqu'on édite du Markdown brut.

## Régénérer

```sh
mkdir cm && cd cm && npm init -y
npm install codemirror@6 @codemirror/view@6 @codemirror/state@6 \
            @codemirror/lang-markdown@6 @codemirror/commands@6 \
            @codemirror/search@6 @codemirror/language@6 \
            @lezer/highlight@1 esbuild
cp <depot>/app/pages/assets/codemirror.entree.js entree.js
npx esbuild entree.js --bundle --format=iife --global-name=MetaMDEditor \
            --minify --target=es2020 --outfile=codemirror.js
cp codemirror.js <depot>/app/pages/assets/
```

## À vérifier après chaque régénération

Le bundle ne doit contenir **aucun appel réseau**, sans quoi la CSP le bloque
en silence :

```sh
node -e "const s=require('fs').readFileSync('codemirror.js','utf8');
console.log([...new Set(s.match(/https?:\/\/[^\"'\`\s)]+/g)||[])]);
console.log(/\b(fetch\(|XMLHttpRequest|WebSocket)\b/.test(s));"
```

Attendu : la seule URL est `http://www.w3.org/2000/svg`, un espace de noms, et
`false` pour la seconde ligne.

## Coût mesuré

522 Ko bruts, 179 Ko une fois compressés — ce que pèse le paquet de mise à
jour, qui est un zip. La coloration a coûté 3,6 Ko bruts.
