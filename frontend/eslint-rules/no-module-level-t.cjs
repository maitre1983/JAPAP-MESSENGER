/**
 * iter209 — ESLint rule: forbid `t(...)` and `i18n.t(...)` calls at module
 * scope (outside any function/class).
 *
 * The iter205/206 i18n codemod injected `t('xxx')` into top-level constants
 * which crashed at module load (`Can't find variable: t`). This rule
 * prevents the regression. `i18n.t(...)` IS allowed at module level — the
 * singleton from `i18next` is initialized synchronously by `/src/i18n.js`.
 *
 * To opt out for a specific line, use:
 *   // eslint-disable-next-line local-rules/no-module-level-t
 */
'use strict';

module.exports = {
  meta: {
    type: 'problem',
    docs: {
      description:
        "Forbid bare `t('…')` calls outside of a function/class scope. " +
        "Use `i18n.t('…')` (from `import i18n from 'i18next'`) at module " +
        "level, or move the constant into a React component that calls " +
        "`useTranslation()`.",
      recommended: true,
    },
    schema: [],
    messages: {
      moduleLevelT:
        "Appel `t(...)` interdit au niveau module — `t` n'existe que dans " +
        "un composant React via `useTranslation()`. " +
        "Utiliser `i18n.t(...)` ou déplacer la constante dans un composant.",
    },
  },

  create(context) {
    /** Walk up the AST and return true if the node is inside any
     *  function- or class-scoped construct. */
    function insideFunctionOrClass(node) {
      let cur = node.parent;
      while (cur) {
        switch (cur.type) {
          case 'FunctionDeclaration':
          case 'FunctionExpression':
          case 'ArrowFunctionExpression':
          case 'MethodDefinition':
          case 'ClassDeclaration':
          case 'ClassExpression':
          case 'PropertyDefinition':
            return true;
          default:
            cur = cur.parent;
        }
      }
      return false;
    }

    return {
      CallExpression(node) {
        const callee = node.callee;
        const isBareT =
          callee.type === 'Identifier' && callee.name === 't';
        // We DO allow `i18n.t(...)` and similar member calls at module level —
        // the i18next singleton is initialized eagerly.
        if (!isBareT) return;
        if (insideFunctionOrClass(node)) return;
        context.report({ node, messageId: 'moduleLevelT' });
      },
    };
  },
};
