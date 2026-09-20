<script setup>
defineProps({
  label: { type: String, required: true },
  value: { type: [String, Number], default: "—" },
  unit: { type: String, default: "" },
  hint: { type: String, default: "" },
  tone: { type: String, default: "default" }, // default | success | warning | danger | primary
});
</script>

<template>
  <div class="stat" :class="`tone-${tone}`">
    <div class="stat-label">{{ label }}</div>
    <div class="stat-value">
      <b>{{ value }}</b><span v-if="unit">{{ unit }}</span>
    </div>
    <div v-if="hint" class="stat-hint">{{ hint }}</div>
  </div>
</template>

<style scoped>
.stat {
  min-width: 0; padding: 10px 12px; border: 1px solid var(--c-border);
  border-radius: var(--r-md); background: var(--c-surface); box-shadow: var(--sh-card);
}
.stat-label { color: var(--c-text-3); font-size: var(--fs-xs); white-space: nowrap; }
.stat-value { display: flex; align-items: baseline; gap: 3px; margin-top: 4px; }
.stat-value b {
  font-size: 20px; line-height: 1.1; font-variant-numeric: tabular-nums;
  color: var(--c-text-1); overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.stat-value span { color: var(--c-text-3); font-size: var(--fs-xs); }
.stat-hint { margin-top: 4px; color: var(--c-text-3); font-size: var(--fs-xs); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tone-success .stat-value b { color: var(--c-success); }
.tone-warning .stat-value b { color: var(--c-warning); }
.tone-danger .stat-value b { color: var(--c-danger); }
.tone-primary .stat-value b { color: var(--c-primary); }
</style>
