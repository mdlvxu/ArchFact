<script setup lang="ts">
/** 文档页缩略图：用轻量 SVG 模拟扫描件，避免依赖外部图片资源 */
interface Props {
  page: number
  active?: boolean
  compact?: boolean
}

withDefaults(defineProps<Props>(), {
  active: false,
  compact: false,
})
</script>

<template>
  <svg
    class="document-sheet"
    :class="{ 'document-sheet--active': active, 'document-sheet--compact': compact }"
    viewBox="0 0 180 250"
    role="img"
    :aria-label="`文档第 ${page} 页预览`"
  >
    <rect
      width="180"
      height="250"
      rx="2"
      fill="#fff"
    />
    <g
      stroke="#b7b3ab"
      stroke-width="1"
      fill="none"
    >
      <path d="M38 22h104M58 28h64" />
      <path d="M28 42h124v112H28z" />
      <path d="M38 52l22 12 18-8 16 13 25-9 23 14" />
      <path d="M38 82l18-9 14 13 17-4 11 18 18-10 20 18" />
      <path d="M40 132l20-24 20 14 15-18 27 12 18-23" />
      <path
        d="M53 46v104M88 46v104M119 46v104"
        opacity=".45"
      />
      <path d="M30 169h120M30 176h120M30 183h96M30 190h120M30 197h115" />
      <path d="M30 208h120M30 215h108M30 222h120M30 229h76" />
    </g>
    <text
      x="90"
      y="17"
      text-anchor="middle"
      font-size="7"
      fill="#6b6862"
    >
      ARCHITECTURAL DOCUMENT
    </text>
  </svg>
</template>

<style scoped lang="scss">
.document-sheet {
  display: block;
  width: 100%;
  height: auto;
  border: 1px solid #dedbd4;
  background: #fff;
  box-shadow: 0 1px 3px rgb(85 65 45 / 8%);
}

.document-sheet--active {
  border-color: #c77b27;
}

.document-sheet--compact {
  max-height: 122px;
}
</style>
