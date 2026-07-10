<template>
  <button @click="onClick">{{ label }}</button>
</template>

<script>
import { formatLabel } from './helpers';

export default {
  data() {
    return { label: formatLabel('go') };
  },
  methods: {
    onClick() {
      return handleClick();
    },
  },
};

function handleClick() {
  return 'clicked';
}

function neverUsedInScript() {
  return 'dead inside the SFC script';
}
</script>

<style scoped>
button { color: red; }
</style>
