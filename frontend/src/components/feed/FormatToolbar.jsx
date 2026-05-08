/**
 * iter237q — FormatToolbar
 *
 * Barre de formatage Markdown léger pour le composer du feed.
 * Boutons : Gras / Italique / Souligné / Hashtag / Mention / Emoji / Lien.
 *
 * Comportement :
 *  - Travaille sur la sélection actuelle du textarea (via un ref).
 *  - Si rien n'est sélectionné, insère un placeholder éditable.
 *  - Mobile-first : chaque bouton a une zone tactile ≥ 44×44 px.
 *  - Markdown stocké tel quel : `**gras**`, `_italique_`, `<u>souligné</u>`,
 *    `#tag`, `@user`, `[texte](https://url)`. Le rendu se fait dans
 *    PostContent.jsx (parser Markdown léger côté affichage).
 *
 * Props :
 *   textareaRef  — Ref vers le <textarea> du composer
 *   text         — Valeur courante (string)
 *   setText      — Setter React de la valeur
 *   onEmojiClick — Callback qui ouvre le picker emoji externe (P2)
 */
import { TextB, TextItalic, TextUnderline, Hash, At, Smiley, LinkSimple } from '@phosphor-icons/react';

const ACTIONS = [
  { id: 'bold',      Icon: TextB,        title: 'Gras (Ctrl+B)' },
  { id: 'italic',    Icon: TextItalic,   title: 'Italique (Ctrl+I)' },
  { id: 'underline', Icon: TextUnderline,title: 'Souligné (Ctrl+U)' },
  { id: 'hashtag',   Icon: Hash,         title: 'Hashtag' },
  { id: 'mention',   Icon: At,           title: 'Mentionner' },
  { id: 'emoji',     Icon: Smiley,       title: 'Emoji' },
  { id: 'link',      Icon: LinkSimple,   title: 'Insérer un lien' },
];

export function applyFormat(textareaRef, text, setText, action) {
  const el = textareaRef?.current;
  if (!el) return;
  const start = el.selectionStart ?? text.length;
  const end   = el.selectionEnd   ?? text.length;
  const sel   = text.slice(start, end);

  // Wrappers Markdown / placeholders.
  const RULES = {
    bold:      { wrap: ['**', '**'],     placeholder: 'gras'      },
    italic:    { wrap: ['_', '_'],       placeholder: 'italique'  },
    underline: { wrap: ['<u>', '</u>'],  placeholder: 'souligné'  },
    hashtag:   { wrap: ['#', ''],        placeholder: 'tag'       },
    mention:   { wrap: ['@', ''],        placeholder: 'utilisateur' },
    link:      { wrap: ['[', '](https://)'], placeholder: 'texte du lien' },
  };

  const rule = RULES[action];
  if (!rule) return;
  const [pre, post] = rule.wrap;
  const inner = sel || rule.placeholder;
  const next  = text.slice(0, start) + pre + inner + post + text.slice(end);
  setText(next);

  // Replace cursor cleverly so the user can immediately keep typing.
  requestAnimationFrame(() => {
    el.focus();
    const cursorStart = start + pre.length;
    const cursorEnd   = cursorStart + inner.length;
    el.setSelectionRange(cursorStart, cursorEnd);
  });
}

export default function FormatToolbar({ textareaRef, text, setText, onEmojiClick, editorApi }) {
  const handle = (id) => {
    if (id === 'emoji') { onEmojiClick?.(); return; }
    // iter237r — When a WYSIWYG `editorApi` is provided, route bold /
    // italic / underline through the editor (execCommand for live
    // visual formatting). Hashtag / mention / link still insert plain
    // markers via insertText.
    if (editorApi) {
      if (id === 'bold')      return editorApi.applyFormat('bold');
      if (id === 'italic')    return editorApi.applyFormat('italic');
      if (id === 'underline') return editorApi.applyFormat('underline');
      const placeholders = {
        hashtag: '#tag',
        mention: '@utilisateur',
        link:    '[texte](https://)',
      };
      if (placeholders[id]) return editorApi.insertText(placeholders[id]);
      return;
    }
    applyFormat(textareaRef, text, setText, id);
  };

  return (
    <div
      className="flex items-center gap-1 mt-2 flex-wrap"
      data-testid="composer-format-toolbar"
    >
      {ACTIONS.map(({ id, Icon, title }) => (
        <button
          key={id}
          type="button"
          title={title}
          aria-label={title}
          data-testid={`format-btn-${id}`}
          // Mobile-friendly: 44×44 tactile zone (Apple HIG / Material guideline).
          // Visual icon stays compact with padding.
          onPointerDown={(e) => e.preventDefault()}
          onClick={() => handle(id)}
          className="rounded-lg flex items-center justify-center transition-all"
          style={{
            minWidth: 44,
            minHeight: 44,
            background: 'rgba(15,5,107,0.04)',
            color: '#0F056B',
            border: '1px solid rgba(15,5,107,0.08)',
          }}
        >
          <Icon size={18} weight="bold" />
        </button>
      ))}
    </div>
  );
}
