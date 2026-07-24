import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import tseslint from 'typescript-eslint'

// ESLint 配置：统一 TypeScript 和 Vue 代码规范
export default tseslint.config(
  { ignores: ['dist', 'node_modules', 'src/types/auto-imports.d.ts', 'src/types/components.d.ts'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  ...pluginVue.configs['flat/recommended'],
  {
    files: ['**/*.{ts,vue}'],
    languageOptions: {
      parserOptions: {
        parser: tseslint.parser,
      },
    },
    rules: {
      // TypeScript 已负责解析 DOM 类型；避免 no-undef 将 Event、HTMLElement 等类型误报为变量
      'no-undef': 'off',
      '@typescript-eslint/no-explicit-any': 'error',
      'vue/multi-word-component-names': 'off',
    },
  },
)
