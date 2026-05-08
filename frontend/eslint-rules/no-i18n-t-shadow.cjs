/**
 * iter213 — ESLint rule: forbid shadowing the i18n `t` function with a
 * loop / callback iteration variable.
 *
 * Real-world bug pattern this rule prevents:
 *
 *     function MyComponent() {
 *       const { t } = useTranslation();   //  ← outer `t`
 *       return tiers.map((t, i) => (      //  ← inner `t` SHADOWS the outer
 *         <option>{t('some.key')}</option> //  ← TypeError: t is not a function
 *       ));
 *     }
 *
 * In a minified production build the inner `t` becomes `e`, producing the
 * generic error "e is not a function" which is impossible to debug from
 * a Sentry stack trace.
 *
 * This rule fires whenever a variable named `t` is introduced as a
 * function parameter (or `for-of` / destructuring) inside a scope that
 * already binds a `t` from `useTranslation()` higher up in the same file.
 *
 * To opt out for a specific line, use:
 *   // eslint-disable-next-line local-rules/no-i18n-t-shadow
 */
'use strict';

module.exports = {
  meta: {
    type: 'problem',
    docs: {
      description:
        'Forbid shadowing the i18n `t` function with a loop/callback ' +
        'iteration variable in any file that destructures `t` from ' +
        '`useTranslation()`. In a minified prod build this triggers ' +
        '"e is not a function" on every JSX call to t() inside the loop.',
      recommended: true,
    },
    schema: [],
    messages: {
      shadowsT:
        'Variable `t` masque la fonction de traduction `t` issue de ' +
        '`useTranslation()`. Renommer cette variable (ex : `tier`, `ticket`, ' +
        '`item`) pour éviter le crash "e is not a function" en production.',
    },
  },

  create(context) {
    const sourceCode = context.getSourceCode();
    // Cheap heuristic: only enable on files that destructure `t` from
    // useTranslation. Avoids false positives in helper modules where `t`
    // is just a generic local variable name with no React i18n in scope.
    const text = sourceCode.getText();
    const hasUseTranslationT =
      /\{\s*[^}]*\bt\b[^}]*\}\s*=\s*useTranslation\s*\(/.test(text);
    if (!hasUseTranslationT) return {};

    function reportIfNamedT(idNode) {
      if (!idNode || idNode.type !== 'Identifier') return;
      if (idNode.name !== 't') return;
      context.report({ node: idNode, messageId: 'shadowsT' });
    }

    function checkParams(params) {
      for (const p of params) {
        if (!p) continue;
        if (p.type === 'Identifier') {
          reportIfNamedT(p);
        } else if (p.type === 'AssignmentPattern') {
          // (t = defaultValue) =>
          reportIfNamedT(p.left);
        } else if (p.type === 'RestElement') {
          reportIfNamedT(p.argument);
        }
        // ObjectPattern / ArrayPattern with `t` inside are extremely rare
        // and not worth the false-positive risk.
      }
    }

    return {
      ArrowFunctionExpression(node) { checkParams(node.params); },
      FunctionExpression(node)      { checkParams(node.params); },
      // Skip FunctionDeclaration: a top-level helper named `t` is fine,
      // and component declarations use `function Comp()` with no `t` param.
      ForOfStatement(node) {
        // for (const t of arr) { ... t('key') ... }
        const left = node.left;
        if (left && left.type === 'VariableDeclaration') {
          for (const d of left.declarations || []) {
            if (d.id && d.id.type === 'Identifier') reportIfNamedT(d.id);
          }
        } else if (left && left.type === 'Identifier') {
          // for (t of arr) — assignment to outer `t` is even worse.
          reportIfNamedT(left);
        }
      },
      ForInStatement(node) {
        const left = node.left;
        if (left && left.type === 'VariableDeclaration') {
          for (const d of left.declarations || []) {
            if (d.id && d.id.type === 'Identifier') reportIfNamedT(d.id);
          }
        }
      },
    };
  },
};
