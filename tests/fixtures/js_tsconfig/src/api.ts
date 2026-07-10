export function fetchUser(id: number) {
  return { id, name: 'x' };
}

export function unusedApi() {
  return 'aliased import never targets me';
}
