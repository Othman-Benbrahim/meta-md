// Point d'entree du bundle CodeMirror vendore pour META-MD.
// Regenerer : voir app/pages/assets/codemirror.LISEZ-MOI.md
import {EditorView, keymap, highlightActiveLine, drawSelection,
        Decoration, ViewPlugin, WidgetType} from "@codemirror/view";
import {EditorState, RangeSetBuilder} from "@codemirror/state";
import {defaultKeymap, history, historyKeymap, indentWithTab} from "@codemirror/commands";
import {searchKeymap, highlightSelectionMatches} from "@codemirror/search";
import {markdown} from "@codemirror/lang-markdown";
import {syntaxHighlighting, HighlightStyle} from "@codemirror/language";
import {tags} from "@lezer/highlight";

// Une image en `data:` occupe jusqu'a 116 000 caracteres — un quart du
// document pour une seule figure. On la replie a l'affichage SANS toucher au
// texte : `atomicRanges` fait que le curseur la franchit d'un bloc, que
// `Suppr` l'emporte entiere, et que la copie rend le base64 complet, parce
// que le document, lui, n'a jamais change.
const IMAGE = /data:image\/[a-z+]+;base64,[A-Za-z0-9+/=]{200,}/g;
const GARDE = 24;   // caracteres montres de chaque cote

class ImageRepliee extends WidgetType {
  constructor(texte) { super(); this.texte = texte; }
  eq(autre) { return autre.texte === this.texte; }
  toDOM() {
    const s = document.createElement("span");
    s.className = "cm-image-repliee";
    const ko = Math.round(this.texte.length / 1024);
    s.textContent = this.texte.slice(0, GARDE) + " … " + ko + " Ko … "
                  + this.texte.slice(-GARDE);
    s.title = "Image encodee, repliee pour la lecture. Copier ou supprimer "
            + "emporte les " + this.texte.length + " caracteres.";
    return s;
  }
  ignoreEvent() { return false; }
}

function decorations(vue) {
  const b = new RangeSetBuilder();
  for (const {from, to} of vue.visibleRanges) {
    const texte = vue.state.doc.sliceString(from, to);
    IMAGE.lastIndex = 0;
    let m;
    while ((m = IMAGE.exec(texte))) {
      b.add(from + m.index, from + m.index + m[0].length,
            Decoration.replace({widget: new ImageRepliee(m[0])}));
    }
  }
  return b.finish();
}

const repliDesImages = ViewPlugin.fromClass(class {
  constructor(vue) { this.decorations = decorations(vue); }
  update(maj) {
    if (maj.docChanged || maj.viewportChanged) this.decorations = decorations(maj.view);
  }
}, {
  decorations: v => v.decorations,
  provide: plugin => EditorView.atomicRanges.of(
    vue => vue.plugin(plugin)?.decorations || Decoration.none),
});

// `markdown()` fournit l'analyse, pas les couleurs : CodeMirror 6 separe le
// langage de sa mise en couleur, et sans `syntaxHighlighting` l'editeur reste
// uniformement noir. Les teintes sont celles de la palette du projet, prises
// aux variables CSS pour qu'un changement de theme les suive.
//
// Les marqueurs (`#`, `**`, les backticks) sont le poste le plus utile : les
// griser fait ressortir le texte qu'ils entourent, sans les cacher — on edite
// du Markdown brut, ils doivent rester visibles et selectionnables.
const styleMarkdown = HighlightStyle.define([
  {tag: tags.heading1, color: "var(--accent-strong, #0a58ca)", fontWeight: "700"},
  {tag: tags.heading2, color: "var(--accent-strong, #0a58ca)", fontWeight: "700"},
  {tag: tags.heading3, color: "var(--accent, #0d6efd)", fontWeight: "600"},
  {tag: [tags.heading4, tags.heading5, tags.heading6, tags.heading],
   color: "var(--accent, #0d6efd)", fontWeight: "600"},
  {tag: tags.processingInstruction, color: "var(--muted, #6b7280)"},
  {tag: tags.strong, color: "var(--text, #1a1d21)", fontWeight: "700"},
  {tag: tags.emphasis, color: "var(--text-soft, #40474f)", fontStyle: "italic"},
  {tag: tags.strikethrough, color: "var(--muted, #6b7280)", textDecoration: "line-through"},
  {tag: tags.link, color: "var(--accent, #0d6efd)"},
  {tag: tags.url, color: "var(--accent, #0d6efd)", textDecoration: "underline"},
  {tag: tags.monospace, color: "var(--success, #198754)"},
  {tag: tags.quote, color: "var(--muted-strong, #4b5563)", fontStyle: "italic"},
  {tag: tags.list, color: "var(--accent, #0d6efd)"},
  {tag: tags.contentSeparator, color: "var(--border-strong, #c0c7d0)"},
]);

export function creer(hote, valeur, {onChange} = {}) {
  const vue = new EditorView({
    parent: hote,
    state: EditorState.create({
      doc: valeur || "",
      extensions: [
        history(), drawSelection(), highlightActiveLine(),
        highlightSelectionMatches(),
        keymap.of([...defaultKeymap, ...historyKeymap, ...searchKeymap, indentWithTab]),
        markdown(),
        syntaxHighlighting(styleMarkdown),
        EditorView.lineWrapping,
        repliDesImages,
        EditorView.updateListener.of(u => { if (u.docChanged && onChange) onChange(); }),
      ],
    }),
  });
  return {
    vue,
    getValue: () => vue.state.doc.toString(),
    setValue: (t) => vue.dispatch({
      changes: {from: 0, to: vue.state.doc.length, insert: t || ""}}),
    focus: () => vue.focus(),
    detruire: () => vue.destroy(),
  };
}
