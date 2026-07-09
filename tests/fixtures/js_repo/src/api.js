export function fetchUser(id) {
  return buildQuery(id);
}

function buildQuery(id) {
  return { id };
}

function orphanHelper() {
  return 'nobody references me and I am module-private';
}

export function unusedExport() {
  return 'exported but never imported inside this repo';
}
