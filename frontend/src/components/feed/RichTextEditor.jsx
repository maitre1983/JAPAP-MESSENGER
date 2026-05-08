/**
 * iter237r — RichTextEditor
 *
 * WYSIWYG inline editor pour le composer du feed. Utilise un
 * `contentEditable` + `document.execCommand` pour appliquer le
 * formatage en temps réel (gras / italique / souligné) — exactement
 * comme dans Word ou Gmail.
 *
 * Pourquoi `execCommand` malgré le statut "deprecated" ?
 *   - C'est l'API la plus stable cross-browser pour B/I/U.
 *   - Les remplacements modernes (Selection.modify + InputEvent) sont
 *     fragmentés et nécessitent une lib externe (Slate / Lexical, ~200KB).
 *   - 100% des navigateurs modernes (incluant iOS Safari & Android Chrome)
 *     supportent encore execCommand pour bold/italic/underline en 2026.
 *   - Migration future : remplacer cette couche par Lexical ou Slate
 *     quand un éditeur plus riche sera nécessaire (sondages, listes…).
 *
 * Sortie :
 *   - `onChange(html)` à chaque édition — le HTML est NETTOYÉ via
 *     DOMPurify avant de quitter ce composant.
 *
 * Sécurité :
 *   - Input HTML : rejeté en colle (paste handler convertit tout en
 *     plain text → `document.execCommand('insertText')`).
 *   - Output HTML : whitelist stricte de balises (`b`, `strong`, `i`,
 *     `em`, `u`, `br`).
 */
import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import DOMPurify from 'dompurify';

const ALLOWED_TAGS = ['b', 'strong', 'i', 'em', 'u', 'br'];

// Strict sanitizer — accepts only the inline formatting tags we need.
function sanitize(html) {
  return DOMPurify.sanitize(html, {
    ALLOWED_TAGS,
    ALLOWED_ATTR: [],
    KEEP_CONTENT: true,
  });
}

// Convert <div>/<p> blocks (which contenteditable inserts on Enter) into
// simple <br> separators so the stored HTML stays compact.
function normalizeBlocks(html) {
  return (html || '')
    .replace(/<div><br\s*\/?><\/div>/gi, '<br>')
    .replace(/<\/?(div|p)>/gi, (m) => (m.startsWith('</') ? '<br>' : ''))
    .replace(/(<br\s*\/?>\s*){3,}/gi, '<br><br>');
}

const RichTextEditor = forwardRef(function RichTextEditor(
  {
    value = '',
    onChange,
    placeholder = '',
    onFocus,
    onBlur,
    onSubmitShortcut,
    minHeight = 44,
    maxLength = 500,
    testId = 'rich-text-editor',
    className = '',
    style = {},
  },
  ref
) {
  const editorRef = useRef(null);

  // Expose imperative API to parent (focus, getPlainText, clear).
  useImperativeHandle(ref, () => ({
    focus: () => editorRef.current?.focus(),
    getPlainText: () => editorRef.current?.innerText || '',
    getHtml: () => sanitize(normalizeBlocks(editorRef.current?.innerHTML || '')),
    clear: () => {
      if (editorRef.current) {
        editorRef.current.innerHTML = '';
        onChange?.('');
      }
    },
    insertText: (text) => {
      editorRef.current?.focus();
      // execCommand still required for cursor-aware insertion.
      // eslint-disable-next-line no-restricted-syntax
      document.execCommand('insertText', false, text);
      flush();
    },
    applyFormat: (cmd) => {
      editorRef.current?.focus();
      // eslint-disable-next-line no-restricted-syntax
      document.execCommand(cmd, false, null);
      flush();
    },
  }));

  // Sync the contentEditable's innerHTML when the external `value` prop
  // changes (e.g. when AI Improve replaces the whole content). We avoid
  // re-syncing while the user is typing to prevent caret jumps.
  useEffect(() => {
    const el = editorRef.current;
    if (!el) return;
    const current = sanitize(normalizeBlocks(el.innerHTML || ''));
    if (sanitize(value || '') !== current) {
      el.innerHTML = sanitize(value || '');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const flush = () => {
    const el = editorRef.current;
    if (!el) return;
    const next = sanitize(normalizeBlocks(el.innerHTML || ''));
    onChange?.(next);
  };

  // Paste handler — neutralizes pasted HTML to prevent injection.
  const onPaste = (e) => {
    e.preventDefault();
    const text = (e.clipboardData || window.clipboardData).getData('text/plain');
    // eslint-disable-next-line no-restricted-syntax
    document.execCommand('insertText', false, text);
  };

  // Submit shortcut (Ctrl/Cmd + Enter). Bold/Italic/Underline are
  // handled natively by the browser through contentEditable.
  const onKeyDown = (e) => {
    const meta = e.ctrlKey || e.metaKey;
    if (meta && e.key === 'Enter') {
      e.preventDefault();
      onSubmitShortcut?.();
    }
  };

  // Hard char-cap. We measure innerText (excluding HTML overhead).
  const onBeforeInput = (e) => {
    const el = editorRef.current;
    if (!el || !maxLength) return;
    const current = el.innerText || '';
    // Allow deletions / formatting commands.
    if (e.inputType?.startsWith('delete') || e.inputType === 'historyUndo' || e.inputType === 'historyRedo') {
      return;
    }
    if (current.length >= maxLength) {
      e.preventDefault();
    }
  };

  return (
    <div
      ref={editorRef}
      contentEditable
      suppressContentEditableWarning
      role="textbox"
      aria-multiline="true"
      aria-label={placeholder}
      data-testid={testId}
      data-placeholder={placeholder}
      onFocus={onFocus}
      onBlur={onBlur}
      onInput={flush}
      onKeyDown={onKeyDown}
      onBeforeInput={onBeforeInput}
      onPaste={onPaste}
      className={`jp-rte ${className}`}
      style={{
        minHeight,
        outline: 'none',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
        ...style,
      }}
    />
  );
});

export default RichTextEditor;
